"""End-to-end test runs of test-bound BPMN processes.

The browser asks for a run; the external e2e-bridge claims it, starts the bound
case in the E2E control center and reports progress back as events.  Phase
states are kept per BPMN element so the editor can paint the running process.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CenterSnapshot, LabE2eRun, LabWorkflowVersion, ReportLaunch
from app.services.test_bindings import load_bindings, phase_coverage

router = APIRouter(prefix="/api/v1/lab", tags=["e2e"])

ACTIVE = ("queued", "claimed", "waiting", "running")
FINISHED = ("passed", "failed", "stopped", "error")
LOG_LIMIT = 5000


def _summary(run: LabE2eRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "workflow_version_id": run.workflow_version_id,
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
    }


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
    return {"ok": True}


@router.get("/center/state")
def center_state(db: Session = Depends(get_db)) -> dict[str, Any]:
    snapshot = db.get(CenterSnapshot, "center")
    if not snapshot:
        return {"online": False, "received_at": None, "data": {}}
    age = datetime.now(UTC) - snapshot.received_at
    return {"online": age < BRIDGE_TIMEOUT, "received_at": snapshot.received_at, "data": snapshot.data}


@router.get("/workflows/{version_id}/e2e-runs")
def list_e2e_runs(version_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    runs = db.scalars(
        select(LabE2eRun)
        .where(LabE2eRun.workflow_version_id == version_id)
        .order_by(LabE2eRun.created_at.desc())
        .limit(20)
    )
    return [_summary(run) for run in runs]


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


def apply_event(run: LabE2eRun, event: dict[str, Any]) -> None:
    kind = event.get("type")
    now = datetime.now(UTC)
    if kind == "queued":
        run.center_job_id = event.get("job")
        run.queue_ahead = event.get("ahead")
        run.status = "waiting"
    elif kind == "status":
        run.status = "running" if event.get("status") == "running" else "waiting"
    elif kind == "progress":
        run.progress = event.get("progress")
    elif kind == "log":
        run.log = (run.log + [str(event.get("line", ""))])[-LOG_LIMIT:]
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
