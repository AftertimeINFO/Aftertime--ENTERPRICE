import pytest
from fastapi import HTTPException

from app.api import e2e
from app.api.e2e import apply_event
from app.db import SessionLocal
from app.models import LabE2eRun


def test_a_run_of_several_suites_marks_where_each_suite_and_its_cases_begin():
    run = LabE2eRun(log=[], timeline=[], states=[])
    for line in ("03:06:42  == suite first: Перший набір",
                 "   the suite runs on its machine",
                 "03:06:42  runner: suite\\run-suite.ps1 -Case chain-a,chain-b -Quiet",
                 "07:28:50  == suite second: Другий набір"):
        apply_event(run, {"type": "log", "line": line})
    marks = [item for item in run.timeline if "suite" in item]
    assert [(item["suite"], item["cases"]) for item in marks] == [("first", ["chain-a", "chain-b"]), ("second", [])]


def test_a_test_of_a_suite_run_gets_its_own_run_linked_to_the_suite(monkeypatch):
    monkeypatch.setattr(e2e, "_bound_version", lambda db, binding: (None, [{"phase": "order"}, {"phase": "issue"}]))
    with SessionLocal() as db:
        parent = LabE2eRun(kind="center", binding="", target={"title": "Набори тестів"}, states=[], log=[], status="running")
        db.add(parent)
        db.commit()
        try:
            child = e2e.start_case_run(parent.id, {"case": "chain-a", "binding": "chain-a"}, db)
            assert child["parent_id"] == parent.id and child["status"] == "running" and child["kind"] == "binding"
            assert [item["state"] for item in child["states"]] == ["pending", "pending"]
            assert child["target"]["case"] == "chain-a" and child["timeline"][0]["status"] == "running"
            with pytest.raises(HTTPException) as bad:
                e2e.start_case_run(parent.id, {"case": "a b", "binding": "x"}, db)
            assert bad.value.status_code == 422
        finally:
            db.query(LabE2eRun).filter(LabE2eRun.id.in_([parent.id, child["id"]])).delete(synchronize_session=False)
            db.commit()
