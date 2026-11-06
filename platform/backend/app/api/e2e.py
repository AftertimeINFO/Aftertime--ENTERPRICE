"""End-to-end test runs of test-bound BPMN processes.

The browser asks for a run; the external e2e-bridge claims it, starts the bound
case in the E2E control center and reports progress back as events.  Phase
states are kept per BPMN element so the editor can paint the running process.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from hashlib import sha1
from urllib.parse import unquote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import CenterSnapshot, LabE2eFrame, LabE2eRun, LabWorkflowVersion, ReportLaunch, ReportResult
from app.services.recordings import MAIN_STREAM, archive_path, pack, video_path
from app.services.report_phases import derive_states
from app.services.stand_prep import parse_prep_line
from app.services.tests_catalog import sync_catalog
from app.services.reports import reports_dir
from app.services.test_bindings import load_bindings, phase_coverage

router = APIRouter(prefix="/api/v1/lab", tags=["e2e"])

ACTIVE = ("queued", "claimed", "waiting", "running")
FINISHED = ("passed", "failed", "stopped", "error")
LOG_LIMIT = 5000
# The control center's orchestrator announces each suite and the cases its runner gets.
SUITE_LINE = re.compile(r"== suite ([\w.-]+):")
CASES_LINE = re.compile(r"runner: .*?-Case ([\w.,-]+)")


def _summary(run: LabE2eRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "workflow_version_id": run.workflow_version_id,
        "parent_id": run.parent_id,
        "binding": run.binding,
        "kind": run.kind,
        "target": run.target,
        "progress": run.progress,
        "status": run.status,
        "center_job_id": run.center_job_id,
        "queue_ahead": run.queue_ahead,
        "states": run.states,
        "folder": run.folder,
        "error": run.error,
        "stop_requested": run.stop_requested,
        "created_at": run.created_at,
        "claimed_at": run.claimed_at,
        "finished_at": run.finished_at,
        "log_lines": len(run.log),
        "timeline": run.timeline or [],
        "recording": run.recording,
        "streams": stream_list(run),
    }


STREAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAIN_TITLE = "Екран машини під тестом"


def stream_list(run: LabE2eRun) -> list[dict[str, Any]]:
    """Recorded windows of a run, the main one first; runs recorded before windows had ids have one."""
    streams = dict(run.streams or {})
    if MAIN_STREAM not in streams and run.recording:
        streams[MAIN_STREAM] = {"title": MAIN_TITLE, "order": 0, "recording": run.recording}
    items = [{"id": key, "title": value.get("title") or key, "order": value.get("order", 99),
              "recording": value.get("recording")} for key, value in streams.items()]
    return sorted(items, key=lambda item: (item["id"] != MAIN_STREAM, item["order"], item["title"]))


def _stream(request: Request) -> str:
    stream = (request.headers.get("x-stream") or MAIN_STREAM).strip().lower()
    if not STREAM_RE.match(stream):
        raise HTTPException(422, "invalid stream id")
    return stream


def _stream_title(request: Request) -> str | None:
    # The bridge percent-encodes titles: HTTP headers carry Latin-1 only.
    title = request.headers.get("x-stream-title")
    return unquote(title) if title else None


def _register_stream(db: Session, run: LabE2eRun, stream: str, title: str | None,
                     recording: dict[str, Any] | None = None) -> None:
    # Windows of one run upload and pack at the same time: read the latest list under a row lock.
    db.refresh(run, attribute_names=["streams", "recording"], with_for_update=True)
    streams = dict(run.streams or {})
    entry = dict(streams.get(stream) or {"title": MAIN_TITLE if stream == MAIN_STREAM else stream, "order": len(streams)})
    if title:
        entry["title"] = title[:120]
    if recording is not None:
        entry["recording"] = recording
        if stream == MAIN_STREAM:
            run.recording = recording
    streams[stream] = entry
    run.streams = streams


def _get_run(db: Session, run_id: str) -> LabE2eRun:
    run = db.get(LabE2eRun, run_id)
    if not run:
        raise HTTPException(404, "E2E run not found")
    return run


@router.post("/workflows/{version_id}/e2e-runs")
def start_e2e_run(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    if version.status != "published":
        raise HTTPException(409, "Тест запускається лише для опублікованої версії процесу")
    coverage = phase_coverage(version.bpmn_xml, load_bindings())
    if not coverage:
        raise HTTPException(409, "Процес не прив'язано до тестів")
    if not coverage["covered"]:
        raise HTTPException(422, {"message": "Процес не покриває фази тесту", "errors": coverage["errors"]})
    _ensure_stand_free(db)
    run = LabE2eRun(
        workflow_version_id=version.id,
        binding=coverage["binding"],
        states=[{**item, "state": "pending"} for item in coverage["phases"]],
        log=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _summary(run)


def _ensure_stand_free(db: Session) -> None:
    active = db.scalar(select(LabE2eRun).where(LabE2eRun.status.in_(ACTIVE)))
    if active:
        raise HTTPException(409, {"message": "Тестовий стенд уже має активний прогін з Aftertime", "run_id": active.id})


ID_RE = re.compile(r"^[\w\-.]+$")
BRIDGE_TIMEOUT = timedelta(seconds=45)


@router.post("/center/runs")
def start_center_run(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """Run chosen suites, groups or cases of the control center, as its launcher does."""
    suites = [item for item in body.get("suites") or [] if isinstance(item, str) and ID_RE.match(item)]
    cases = [item for item in body.get("cases") or [] if isinstance(item, str) and ID_RE.match(item)]
    if not suites and not cases:
        # An empty request would make the control center run everything.
        raise HTTPException(422, "Оберіть набір, групу або кейси")
    _ensure_stand_free(db)
    title = str(body.get("title") or ", ".join(cases or suites))[:200]
    run = LabE2eRun(
        kind="center",
        binding="",
        target={"suites": [] if cases else suites, "cases": cases, "title": title,
                "dryRun": bool(body.get("dryRun")), "stopOnFailure": bool(body.get("stopOnFailure"))},
        states=[],
        log=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _summary(run)


@router.get("/center/runs")
def center_runs(limit: int = 30, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Every run started from Aftertime, newest first."""
    runs = db.scalars(select(LabE2eRun).order_by(LabE2eRun.created_at.desc()).limit(limit))
    return [_summary(run) for run in runs]


@router.post("/center/state")
def push_center_state(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """The bridge reports what the control center shows: catalog, stand health, queue, history."""
    snapshot = db.get(CenterSnapshot, "center") or CenterSnapshot(id="center")
    snapshot.data = body
    snapshot.received_at = datetime.now(UTC)
    db.add(snapshot)
    db.commit()
    # The catalog of the suite lives in Aftertime: tests stay browsable with the bridge offline.
    synced = sync_catalog(db, body.get("catalog") or {})
    return {"ok": True, "tests": synced}


@router.get("/center/state")
def center_state(db: Session = Depends(get_db)) -> dict[str, Any]:
    snapshot = db.get(CenterSnapshot, "center")
    if not snapshot:
        return {"online": False, "received_at": None, "data": {}}
    age = datetime.now(UTC) - snapshot.received_at
    return {"online": age < BRIDGE_TIMEOUT, "received_at": snapshot.received_at, "data": snapshot.data}


def _bound_test(db: Session, version_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    bindings = load_bindings()
    coverage = phase_coverage(version.bpmn_xml, bindings)
    binding = bindings.get(coverage["binding"]) if coverage else None
    return binding, (binding or {}).get("runner", {}).get("cases") or []


@router.get("/workflows/{version_id}/e2e-runs")
def list_e2e_runs(version_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Runs of this process: started from it, or recorded as its test inside a suite run (``parent_id``)."""
    runs = db.scalars(
        select(LabE2eRun).where(LabE2eRun.workflow_version_id == version_id).order_by(LabE2eRun.created_at.desc()).limit(20)
    )
    return [_summary(run) for run in runs]


def _bound_version(db: Session, binding: str) -> tuple[LabWorkflowVersion | None, list[dict[str, Any]]]:
    """The newest published process bound to this test, with its phases as a run of the process has them."""
    bindings = load_bindings()
    versions = db.scalars(select(LabWorkflowVersion).where(LabWorkflowVersion.status == "published")
                          .order_by(LabWorkflowVersion.published_at.desc().nulls_last()))
    for version in versions:
        coverage = phase_coverage(version.bpmn_xml, bindings)
        if coverage and coverage.get("binding") == binding:
            return version, list(coverage.get("phases") or [])
    return None, [{"phase": phase} for phase in (bindings.get(binding) or {}).get("phases") or []]


@router.post("/e2e-runs/{run_id}/cases")
def start_case_run(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """The bridge saw a test of a suite begin: it gets its own run (recording, phases) from this moment."""
    parent = _get_run(db, run_id)
    case, binding = str(body.get("case") or ""), str(body.get("binding") or "")
    if not ID_RE.match(case) or not ID_RE.match(binding):
        raise HTTPException(422, "Потрібні case і binding")
    version, phases = _bound_version(db, binding)
    now = datetime.now(UTC)
    run = LabE2eRun(
        workflow_version_id=version.id if version else None,
        parent_id=parent.id,
        kind="binding",
        binding=binding,
        target={"case": case, "suite_run": parent.id, "title": (parent.target or {}).get("title")},
        status="running",
        center_job_id=parent.center_job_id,
        claimed_at=now,
        states=[{**item, "state": "pending"} for item in phases],
        timeline=[{"t": now.isoformat(), "status": "running"}],
        log=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _summary(run)


@router.get("/workflows/{version_id}/report-results")
def workflow_report_results(version_id: str, limit: int = 10, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Latest report results of the bound test, each with its phases painted from the report steps.

    Covers runs started anywhere: from this process, as part of a suite, or by the control center.
    """
    binding, cases = _bound_test(db, version_id)
    if not cases:
        return []
    rows = db.execute(
        select(ReportResult, ReportLaunch)
        .join(ReportLaunch, ReportLaunch.id == ReportResult.launch_id)
        .where(ReportResult.full_name.in_(cases))
        .order_by(ReportResult.start.desc().nulls_last())
        .limit(max(1, min(limit, 50)))
    ).all()
    return [{
        "id": result.id,
        "case": result.full_name,
        "status": result.status,
        "message": result.message,
        "start": result.start,
        "duration_ms": result.duration_ms,
        "launch": {"id": launch.id, "name": launch.name, "e2e_run_id": launch.e2e_run_id},
        "states": [{key: value for key, value in item.items() if key != "at"}
                   for item in derive_states(binding, result.status, result.data or {}, result.message)],
    } for result, launch in rows]


@router.get("/e2e-runs/{run_id}")
def e2e_run(run_id: str, start: int = 0, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = _get_run(db, run_id)
    reports = list(db.scalars(
        select(ReportLaunch.id).where(ReportLaunch.e2e_run_id == run.id).order_by(ReportLaunch.start.desc().nulls_last())
    ))
    return {**_summary(run), "lines": run.log[start:], "next": len(run.log),
            "report_launch_id": reports[0] if reports else None, "report_launch_ids": reports}


@router.post("/e2e-runs/{run_id}/stop")
def stop_e2e_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = _get_run(db, run_id)
    if run.status in FINISHED:
        return _summary(run)
    run.stop_requested = True
    if run.status == "queued":
        # Nobody has claimed it yet: there is nothing to stop in the control center.
        run.status = "stopped"
        run.finished_at = datetime.now(UTC)
        run.states = [{**item, "state": "not-reached"} for item in run.states]
    db.commit()
    return _summary(run)


@router.post("/e2e-runs/claim", response_model=None)
def claim_e2e_run(db: Session = Depends(get_db)) -> Response | dict[str, Any]:
    """The bridge takes the oldest queued run together with its binding."""
    run = db.scalar(
        select(LabE2eRun)
        .where(LabE2eRun.status == "queued")
        .order_by(LabE2eRun.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not run:
        return Response(status_code=204)
    run.status = "claimed"
    run.claimed_at = datetime.now(UTC)
    db.commit()
    return _summary(run)


def _merge_states(current: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_phase = {item["phase"]: dict(item) for item in current}
    for update in updates:
        phase = update.get("phase")
        if phase in by_phase:
            by_phase[phase].update(
                {key: value for key, value in update.items() if key in {"state", "inferred", "evidence", "detail"}}
            )
    return [by_phase[item["phase"]] for item in current]


def _record_changes(run: LabE2eRun, before: list[dict[str, Any]], now: datetime) -> None:
    """Append every phase whose state changed to the run timeline, for replaying the run later."""
    previous = {item["phase"]: item.get("state") for item in before}
    changed = [
        {"t": now.isoformat(), "phase": item["phase"], "state": item.get("state")}
        for item in run.states
        if previous.get(item["phase"]) != item.get("state")
    ]
    if changed:
        run.timeline = (run.timeline or []) + changed


def apply_event(run: LabE2eRun, event: dict[str, Any]) -> None:
    kind = event.get("type")
    now = datetime.now(UTC)
    before = [dict(item) for item in run.states or []]
    _apply(run, event, kind, now)
    if kind in ("states", "phase", "finished"):
        _record_changes(run, before, now)
    if kind == "status" and event.get("status") == "running" and not any(
        item.get("status") == "running" for item in run.timeline or []
    ):
        run.timeline = (run.timeline or []) + [{"t": now.isoformat(), "status": "running"}]


def _apply(run: LabE2eRun, event: dict[str, Any], kind: Any, now: datetime) -> None:
    if kind == "queued":
        run.center_job_id = event.get("job")
        run.queue_ahead = event.get("ahead")
        run.status = "waiting"
    elif kind == "status":
        run.status = "running" if event.get("status") == "running" else "waiting"
    elif kind == "progress":
        run.progress = event.get("progress")
    elif kind == "log":
        line = str(event.get("line", ""))
        run.log = (run.log + [line])[-LOG_LIMIT:]
        # the stand reports each preparation step: kept on the timeline to replay "preparing the stand" step by step
        prep = parse_prep_line(line)
        if prep:
            run.timeline = (run.timeline or []) + [{"t": now.isoformat(), "prep": prep}]
        # a suite of a center run begins: its cases mark where each test sits in a recording of many suites
        suite = SUITE_LINE.search(line)
        if suite:
            run.timeline = (run.timeline or []) + [{"t": now.isoformat(), "suite": suite.group(1), "cases": []}]
        cases = CASES_LINE.search(line)
        if cases and run.timeline and "suite" in run.timeline[-1] and not run.timeline[-1].get("cases"):
            run.timeline = run.timeline[:-1] + [{**run.timeline[-1], "cases": cases.group(1).split(",")}]
    elif kind == "states":
        run.states = _merge_states(run.states, event.get("states") or [])
    elif kind == "phase":
        run.states = _merge_states(run.states, [event])
    elif kind == "finished":
        run.states = _merge_states(run.states, event.get("states") or [])
        run.status = event.get("status") if event.get("status") in FINISHED else "error"
        run.folder = event.get("folder")
        run.finished_at = now
    elif kind == "error":
        run.status = "error"
        run.error = str(event.get("message", ""))
        run.finished_at = now


@router.post("/e2e-runs/{run_id}/events")
def e2e_run_events(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """Progress from the bridge; the reply tells it whether a stop was requested."""
    run = _get_run(db, run_id)
    for event in body.get("events") or []:
        if isinstance(event, dict):
            apply_event(run, event)
    db.commit()
    return {"status": run.status, "stop_requested": run.stop_requested}


# Frames of a still screen are identical; keep one per change (per API process).
_last_frame: dict[str, str] = {}
FRAME_LIMIT = 5 * 1024 * 1024
JPEG_MAGIC = bytes([0xFF, 0xD8])


def _frame_path(run_id: str, frame_id: str):
    return reports_dir() / "frames" / run_id / f"{frame_id}.jpg"


def _current_phase(run: LabE2eRun) -> str | None:
    states = run.states or []
    active = next((item["phase"] for item in states if item.get("state") == "active"), None)
    if active:
        return active
    done = [item["phase"] for item in states if item.get("state") in ("passed", "failed", "stopped")]
    return done[-1] if done else None


@router.post("/e2e-runs/{run_id}/frames")
async def add_frame(run_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """A JPEG of the machine under test from the bridge; the current phase is recorded with it."""
    run = _get_run(db, run_id)
    stream = _stream(request)
    body = await request.body()
    if not body or len(body) > FRAME_LIMIT or not body.startswith(JPEG_MAGIC):
        raise HTTPException(422, "JPEG frame expected")
    digest = sha1(body).hexdigest()
    key = f"{run_id}:{stream}"
    if _last_frame.get(key) == digest:
        return {"stored": False}
    try:
        captured = datetime.fromisoformat(request.headers.get("x-captured-at", ""))
    except ValueError:
        captured = datetime.now(UTC)
    title = _stream_title(request)
    if stream not in (run.streams or {}) or (title and title != run.streams[stream].get("title")):
        _register_stream(db, run, stream, title)
    frame = LabE2eFrame(run_id=run.id, captured_at=captured, phase=_current_phase(run), stream=stream, size=len(body))
    db.add(frame)
    db.flush()
    path = _frame_path(run.id, frame.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    db.commit()
    _last_frame[key] = digest
    return {"stored": True, "id": frame.id, "phase": frame.phase, "stream": stream}


@router.get("/e2e-runs/{run_id}/frames")
def frames(run_id: str, stream: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    query = select(LabE2eFrame).where(LabE2eFrame.run_id == run_id)
    if stream:
        query = query.where(LabE2eFrame.stream == stream)
    rows = db.scalars(query.order_by(LabE2eFrame.captured_at))
    return [{"id": row.id, "captured_at": row.captured_at, "phase": row.phase, "stream": row.stream} for row in rows]


@router.get("/e2e-frames/{frame_id}")
def frame_image(frame_id: str, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(LabE2eFrame, frame_id)
    path = _frame_path(row.run_id, row.id) if row else None
    if not path or not path.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


RECORDING_LIMIT = 2 * 1024 * 1024 * 1024


def _recording_of(run: LabE2eRun, stream: str) -> dict[str, Any]:
    entry = (run.streams or {}).get(stream) or {}
    if stream == MAIN_STREAM and not entry.get("recording"):
        return dict(run.recording or {})
    return dict(entry.get("recording") or {})


def pack_recording(run_id: str, stream: str = MAIN_STREAM) -> None:
    """Background job: zip of frames → MP4, then drop the live frames the video makes redundant."""
    with SessionLocal() as db:
        run = db.get(LabE2eRun, run_id)
        if not run:
            return
        try:
            result = pack(run_id, stream)
        except Exception as exc:  # the zip stays on disk, so packing can be retried
            _register_stream(db, run, stream, None, {**_recording_of(run, stream), "status": "failed", "error": str(exc)[:500]})
            db.commit()
            return
        _register_stream(db, run, stream, None, {**_recording_of(run, stream), "status": "ready", **result})
        keep: set[str] = set()
        kept_phases: set[str | None] = set()
        rows = list(db.scalars(select(LabE2eFrame).where(LabE2eFrame.run_id == run_id, LabE2eFrame.stream == stream)
                               .order_by(LabE2eFrame.captured_at)))
        for row in rows:
            # The first frame of each phase stays as its picture; the video holds the rest.
            if row.phase not in kept_phases:
                kept_phases.add(row.phase)
                keep.add(row.id)
        for row in rows:
            if row.id not in keep:
                _frame_path(run_id, row.id).unlink(missing_ok=True)
                db.delete(row)
        db.commit()


@router.post("/e2e-runs/{run_id}/recording")
async def add_recording(run_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The whole screen recording of a run (zip of frames named by ms); packed in the background."""
    run = _get_run(db, run_id)
    stream = _stream(request)
    body = await request.body()
    if not body or len(body) > RECORDING_LIMIT or not body.startswith(b"PK"):
        raise HTTPException(422, "zip of JPEG frames expected")
    target = archive_path(run.id, stream)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    recording = {"status": "packing", "started_at": request.headers.get("x-recording-started-at") or datetime.now(UTC).isoformat(),
                 "archive_size": len(body)}
    _register_stream(db, run, stream, _stream_title(request), recording)
    db.commit()
    background.add_task(pack_recording, run.id, stream)
    return recording


@router.post("/e2e-runs/{run_id}/recording/pack")
def repack_recording(run_id: str, background: BackgroundTasks, stream: str = MAIN_STREAM,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Pack again a recording whose packing failed or was interrupted."""
    run = _get_run(db, run_id)
    if not STREAM_RE.match(stream) or not archive_path(run.id, stream).exists():
        raise HTTPException(404, "no recording waiting to be packed")
    recording = {**_recording_of(run, stream), "status": "packing", "error": None}
    _register_stream(db, run, stream, None, recording)
    db.commit()
    background.add_task(pack_recording, run.id, stream)
    return recording


@router.get("/e2e-runs/{run_id}/recording.mp4")
def recording_video(run_id: str, stream: str = MAIN_STREAM) -> FileResponse:
    if not STREAM_RE.match(stream):
        raise HTTPException(404, "video not ready")
    path = video_path(run_id, stream)
    if not path.exists():
        raise HTTPException(404, "video not ready")
    return FileResponse(path, media_type="video/mp4")
