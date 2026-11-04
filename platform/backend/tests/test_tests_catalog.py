import uuid

import pytest

from app.api.reports import create_launch
from app.api.tests import get_test, list_tests, results_matrix, results_summary
from app.db import SessionLocal
from app.models import LabTest, ReportLaunch
from app.services.tests_catalog import stability, sync_catalog


def test_stability_counts_passes_and_flips_of_recent_runs():
    assert stability(["passed", "passed", "passed"]) == {"runs": 3, "pass_rate": 1.0, "flaky": False}
    assert stability(["failed", "passed", "failed", "passed"])["flaky"] is True
    assert stability(["failed", "failed", "passed"]) == {"runs": 3, "pass_rate": 0.33, "flaky": False}
    assert stability(["skipped", "unknown"]) == {"runs": 0, "pass_rate": None, "flaky": False}


@pytest.fixture
def suite():
    tag = uuid.uuid4().hex[:6]
    ids = [f"t-{tag}-a", f"t-{tag}-b", f"t-{tag}-c"]
    catalog = {"suites": [
        {"id": f"suite-{tag}", "title": "Набір", "cases": [{"id": ids[0], "title": "Видача", "number": 1}, {"id": ids[1], "title": "Повернення"}]},
        {"id": f"other-{tag}", "title": "Інший", "cases": [{"id": ids[1], "title": "Повернення"}, {"id": ids[2], "title": "Оплата"}]},
    ]}
    launches = []
    with SessionLocal() as db:
        existing = [row.id for row in db.query(LabTest).filter(LabTest.active.is_(True)).all()]
        for index, statuses in enumerate([("passed", "failed"), ("failed", "passed"), ("passed", "failed")]):
            name = f"tc-{tag}-{index}"
            launches.append(name)
            create_launch({"name": name, "results": [
                {"uuid": str(uuid.uuid4()), "fullName": ids[0], "historyId": ids[0], "status": statuses[0], "start": 1_000_000 + index * 1000, "stop": 1_000_500 + index * 1000},
                {"uuid": str(uuid.uuid4()), "fullName": ids[1], "historyId": ids[1], "status": statuses[1], "start": 1_000_000 + index * 1000, "stop": 1_000_900 + index * 1000},
            ]}, db)
        yield {"tag": tag, "ids": ids, "catalog": catalog, "existing": existing}
        db.query(LabTest).filter(LabTest.id.in_(ids)).delete()
        # sync_catalog retires what a snapshot lacks: give the real suite back its state.
        db.query(LabTest).filter(LabTest.id.in_(existing)).update({LabTest.active: True})
        db.query(ReportLaunch).filter(ReportLaunch.name.in_(launches)).delete()
        db.commit()


def test_catalog_sync_keeps_shared_cases_once_and_retires_missing_ones(suite):
    with SessionLocal() as db:
        assert sync_catalog(db, suite["catalog"]) == 3
        shared = db.get(LabTest, suite["ids"][1])
        assert [item["title"] for item in shared.suites] == ["Набір", "Інший"]
        assert sync_catalog(db, {"suites": []}) == 0  # an empty snapshot changes nothing
        assert db.get(LabTest, suite["ids"][2]).active is True
        smaller = {"suites": [suite["catalog"]["suites"][0]]}
        sync_catalog(db, smaller)
        db.expire_all()
        assert db.get(LabTest, suite["ids"][2]).active is False
        assert suite["ids"][2] not in [item["id"] for item in list_tests(db=db)]
        assert suite["ids"][2] in [item["id"] for item in list_tests(include_retired=True, db=db)]


def test_tests_show_latest_status_trend_and_flakiness(suite):
    with SessionLocal() as db:
        sync_catalog(db, suite["catalog"])
        listed = {item["id"]: item for item in list_tests(db=db)}
        first, second, third = (listed[test_id] for test_id in suite["ids"])
        assert first["status"] == "passed" and first["trend"] == ["passed", "failed", "passed"] and first["flaky"] is True
        assert second["status"] == "failed" and second["pass_rate"] == 0.33
        assert third["status"] == "never" and third["last"] is None
        detail = get_test(suite["ids"][0], db)
        assert len(detail["history"]) == 3 and detail["aftertime_runs"] == [] and detail["history"][0]["launch"].endswith("-2")


def test_consolidated_summary_and_matrix(suite):
    with SessionLocal() as db:
        sync_catalog(db, suite["catalog"])
        summary = results_summary(db=db)
        mine = {entry["id"]: entry for entry in summary["suites"] if entry["id"].endswith(suite["tag"])}
        assert mine[f"suite-{suite['tag']}"]["tests"] == 2
        assert mine[f"other-{suite['tag']}"]["statuses"].get("never") == 1
        assert any(item["id"] == suite["ids"][1] for item in summary["attention"])
        matrix = results_matrix(suite=f"suite-{suite['tag']}", db=db)
        assert [row["id"] for row in matrix["tests"]] == suite["ids"][:2]
        assert len(matrix["launches"]) == 3
        assert matrix["tests"][0]["cells"][matrix["launches"][-1]["id"]]["status"] == "passed"
