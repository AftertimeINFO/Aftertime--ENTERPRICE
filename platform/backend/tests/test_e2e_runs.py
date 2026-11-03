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
