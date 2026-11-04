import json

import pytest
from fastapi import HTTPException

from app.api.e2e import claim_e2e_run, e2e_run, e2e_run_events, list_e2e_runs, start_e2e_run, stop_e2e_run
from app.api.model_lab import create_workflow, publish_workflow, save_workflow_bpmn
from app.db import SessionLocal
from app.models import LabE2eRun, LabWorkflow

BINDINGS = {"e2e-test-order": {"title": "Замовлення", "phases": ["order", "issue"], "runner": {"cases": ["case-x"]}}}

XML = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:aftertime="https://aftertime.local/bpmn" id="d">
  <bpmn:process id="e2e-test-process" name="Тест прогонів" aftertime:testBinding="{binding}">
    <bpmn:startEvent id="start" />
    <bpmn:userTask id="order_task" name="Оформити" aftertime:testPhase="order" />
    <bpmn:userTask id="issue_task" name="Видати" aftertime:testPhase="issue" />
    <bpmn:endEvent id="end" />
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="order_task" />
    <bpmn:sequenceFlow id="f2" sourceRef="order_task" targetRef="issue_task" />
    <bpmn:sequenceFlow id="f3" sourceRef="issue_task" targetRef="end" />
  </bpmn:process>
</bpmn:definitions>"""


@pytest.fixture
def bindings(tmp_path, monkeypatch):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "bindings.json").write_text(json.dumps(BINDINGS), encoding="utf-8")
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AFTERTIME_PACKS_DIR", str(tmp_path / "no-packs"))


@pytest.fixture
def published(bindings):
    with SessionLocal() as db:
        db.query(LabE2eRun).filter(LabE2eRun.status.in_(["queued", "claimed", "waiting", "running"])).delete()
        db.commit()
        created = create_workflow({"name": "Тест прогонів E2E"}, db)
        version_id = created["version"]["id"]
        save_workflow_bpmn(version_id, {"bpmn_xml": XML.replace("{binding}", "e2e-test-order")}, db)
        publish_workflow(version_id, db)
        yield version_id
        db.delete(db.get(LabWorkflow, created["version"]["workflow_id"]))
        db.commit()


def test_run_lifecycle_from_request_to_painted_result(published):
    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        assert run["status"] == "queued"
        assert [(item["phase"], item["element"], item["state"]) for item in run["states"]] == [
            ("order", "order_task", "pending"), ("issue", "issue_task", "pending"),
        ]
        with pytest.raises(HTTPException) as busy:
            start_e2e_run(published, db)
        assert busy.value.status_code == 409

        claimed = claim_e2e_run(db)
        assert claimed["id"] == run["id"] and claimed["status"] == "claimed"
        assert claim_e2e_run(db).status_code == 204

        reply = e2e_run_events(run["id"], {"events": [
            {"type": "queued", "job": "job-7", "ahead": 1},
            {"type": "status", "status": "running"},
            {"type": "log", "line": "== suite started"},
            {"type": "phase", "phase": "order", "state": "passed", "inferred": False, "evidence": "verify-order.json"},
            {"type": "phase", "phase": "issue", "state": "active", "inferred": True},
        ]}, db)
        assert reply == {"status": "running", "stop_requested": False}
        current = e2e_run(run["id"], 0, db)
        assert current["center_job_id"] == "job-7"
        assert current["lines"] == ["== suite started"] and current["next"] == 1
        assert [item["state"] for item in current["states"]] == ["passed", "active"]
        assert current["states"][0]["element"] == "order_task"

        stop_e2e_run(run["id"], db)
        assert e2e_run_events(run["id"], {"events": []}, db)["stop_requested"] is True
        e2e_run_events(run["id"], {"events": [{"type": "finished", "status": "stopped", "folder": None, "states": [
            {"phase": "order", "state": "passed"}, {"phase": "issue", "state": "stopped"},
        ]}]}, db)
        finished = e2e_run(run["id"], 1, db)
        assert finished["status"] == "stopped" and finished["finished_at"]
        assert finished["lines"] == []
        assert list_e2e_runs(published, db)[0]["id"] == run["id"]


def test_unclaimed_run_stops_immediately(published):
    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        stopped = stop_e2e_run(run["id"], db)
        assert stopped["status"] == "stopped"
        assert {item["state"] for item in stopped["states"]} == {"not-reached"}


def test_only_published_and_fully_covered_processes_can_run(bindings):
    with SessionLocal() as db:
        created = create_workflow({"name": "Неопублікований тест"}, db)
        version_id = created["version"]["id"]
        save_workflow_bpmn(version_id, {"bpmn_xml": XML.replace("{binding}", "e2e-test-order")}, db)
        with pytest.raises(HTTPException) as draft:
            start_e2e_run(version_id, db)
        assert draft.value.status_code == 409
        db.delete(db.get(LabWorkflow, created["version"]["workflow_id"]))
        db.commit()


@pytest.mark.anyio
async def test_frames_record_the_running_phase_and_skip_identical_screens(published, tmp_path, monkeypatch):
    from app.api.e2e import add_frame, frame_image, frames

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    class Body:
        def __init__(self, data, at):
            self.data, self.headers = data, {"x-captured-at": at}

        async def body(self):
            return self.data

    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        e2e_run_events(run["id"], {"events": [{"type": "phase", "phase": "order", "state": "active"}]}, db)
        first = await add_frame(run["id"], Body(bytes([0xFF, 0xD8]) + b"one", "2026-11-04T10:00:00+00:00"), db)
        same = await add_frame(run["id"], Body(bytes([0xFF, 0xD8]) + b"one", "2026-11-04T10:00:03+00:00"), db)
        e2e_run_events(run["id"], {"events": [{"type": "phase", "phase": "order", "state": "passed"},
                                              {"type": "phase", "phase": "issue", "state": "active"}]}, db)
        second = await add_frame(run["id"], Body(bytes([0xFF, 0xD8]) + b"two", "2026-11-04T10:00:06+00:00"), db)
        assert first["phase"] == "order" and same == {"stored": False} and second["phase"] == "issue"
        assert [item["phase"] for item in frames(run["id"], db=db)] == ["order", "issue"]
        assert frame_image(first["id"], db).media_type == "image/jpeg"
        with pytest.raises(HTTPException):
            await add_frame(run["id"], Body(b"not a jpeg", "2026-11-04T10:00:09+00:00"), db)
        stop_e2e_run(run["id"], db)


def test_timeline_records_each_phase_change_once(published):
    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        e2e_run_events(run["id"], {"events": [
            {"type": "status", "status": "running"},
            {"type": "phase", "phase": "order", "state": "active"},
            {"type": "phase", "phase": "order", "state": "active"},
            {"type": "phase", "phase": "order", "state": "passed"},
            {"type": "finished", "status": "failed", "states": [
                {"phase": "order", "state": "passed"}, {"phase": "issue", "state": "failed"}]},
        ]}, db)
        timeline = e2e_run(run["id"], 0, db)["timeline"]
        assert [item.get("status") or f"{item['phase']}={item['state']}" for item in timeline] == [
            "running", "order=active", "order=passed", "issue=failed",
        ]
        assert all(item["t"] for item in timeline)


@pytest.mark.anyio
async def test_recording_is_packed_and_live_frames_reduced_to_one_per_phase(published, tmp_path, monkeypatch):
    from app.api.e2e import add_frame, add_recording, frames, pack_recording
    from app.services.recordings import video_path
    from tests.test_recordings import jpeg, recording_zip

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    class Body:
        def __init__(self, data, headers):
            self.data, self.headers = data, headers

        async def body(self):
            return self.data

    class Background:
        def __init__(self):
            self.tasks = []

        def add_task(self, func, *args):
            self.tasks.append((func, args))

    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        e2e_run_events(run["id"], {"events": [{"type": "phase", "phase": "order", "state": "active"}]}, db)
        for second, colour in enumerate(["red", "blue", "green"]):
            await add_frame(run["id"], Body(jpeg(colour), {"x-captured-at": f"2026-11-04T10:00:0{second}+00:00"}), db)
        background = Background()
        reply = await add_recording(run["id"], Body(recording_zip([0, 1000, 2000]),
                                                    {"x-recording-started-at": "2026-11-04T10:00:00+00:00"}), background, db)
        assert reply["status"] == "packing" and background.tasks[0][0] is pack_recording
        pack_recording(run["id"])
        detail = e2e_run(run["id"], 0, db)
        db.expire_all()
        detail = e2e_run(run["id"], 0, db)
        assert detail["recording"]["status"] == "ready"
        assert detail["recording"]["started_at"] == "2026-11-04T10:00:00+00:00"
        assert video_path(run["id"]).exists()
        assert len(frames(run["id"], db=db)) == 1  # one picture of the phase stays
        stop_e2e_run(run["id"], db)


@pytest.mark.anyio
async def test_every_recorded_window_keeps_its_own_frames_and_video(published, tmp_path, monkeypatch):
    from app.api.e2e import add_frame, add_recording, frames, pack_recording, recording_video
    from tests.test_recordings import jpeg, recording_zip

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    class Body:
        def __init__(self, data, headers):
            self.data, self.headers = data, headers

        async def body(self):
            return self.data

    class Background:
        def add_task(self, func, *args):
            func(*args)

    with SessionLocal() as db:
        run = start_e2e_run(published, db)
        web = {"x-stream": "web", "x-stream-title": "%D0%92%D0%B5%D0%B1", "x-captured-at": "2026-11-04T10:00:00+00:00"}
        await add_frame(run["id"], Body(jpeg("red"), {"x-captured-at": "2026-11-04T10:00:00+00:00"}), db)
        await add_frame(run["id"], Body(jpeg("red"), web), db)  # the same picture in another window is stored
        assert [item["stream"] for item in frames(run["id"], db=db)] == ["screen", "web"]
        assert [item["id"] for item in frames(run["id"], "web", db)] != []
        with pytest.raises(HTTPException):
            await add_frame(run["id"], Body(jpeg("red"), {"x-stream": "../etc"}), db)
        await add_recording(run["id"], Body(recording_zip([0, 1000]), {"x-stream": "web"}), Background(), db)
        db.expire_all()
        streams = {item["id"]: item for item in e2e_run(run["id"], 0, db)["streams"]}
        assert streams["web"]["title"] == "Веб"
        assert streams["web"]["recording"]["status"] == "ready"
        assert streams["screen"]["recording"] is None and e2e_run(run["id"], 0, db)["recording"] is None
        assert str(recording_video(run["id"], "web").path).endswith(".web.mp4")
        stop_e2e_run(run["id"], db)
