import json
import uuid

import pytest

from app.api.reports import (
    archive_test, archived_tests, attachment, create_launch, overview, restore_test, result, results, upload_attachment,
)
from app.db import SessionLocal
from app.models import ReportAttachment, ReportLaunch
from app.services.reports import categorize, stats

CATEGORIES = [{"name": "Розбіжність з оракулом", "matchedStatuses": ["failed"], "messageRegex": ".*MISMATCH.*"}]


def allure_result(history: str, status: str, start: int, message: str = "", attachment_source: str | None = None) -> dict:
    step = {"name": "Оформити замовлення", "status": status, "start": start, "stop": start + 500, "steps": [],
            "attachments": [{"name": "Скріншот", "type": "image/png", "source": attachment_source}] if attachment_source else []}
    return {
        "uuid": str(uuid.uuid4()), "historyId": history, "fullName": history, "name": f"Кейс {history}",
        "status": status, "start": start, "stop": start + 1000, "statusDetails": {"message": message},
        "labels": [{"name": "parentSuite", "value": "Ланцюжки"}, {"name": "suite", "value": "Віртуальний термінал"}],
        "steps": [step], "attachments": [],
    }


def test_categories_follow_custom_rules_then_allure_defaults():
    assert categorize({"status": "failed", "statusDetails": {"message": "TARIFF MISMATCH"}}, CATEGORIES) == "Розбіжність з оракулом"
    assert categorize({"status": "failed", "statusDetails": {"message": "інше"}}, CATEGORIES) == "Дефекти продукту"
    assert categorize({"status": "broken"}, CATEGORIES) == "Дефекти тестів"
    assert categorize({"status": "passed"}, CATEGORIES) is None
    assert stats([{"status": "passed", "start": 1, "stop": 5}, {"status": "failed", "start": 2, "stop": 9}]) == {
        "passed": 1, "failed": 1, "broken": 0, "skipped": 0, "unknown": 0, "total": 2, "duration_ms": 8,
    }


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def two_launches(reports_dir):
    tag = uuid.uuid4().hex[:8]
    history = f"case-{tag}"
    source = f"{tag}-attachment.png"
    with SessionLocal() as db:
        first = create_launch({"name": f"t-{tag}-1", "categories": CATEGORIES,
                               "environment": {"Hall": "11"}, "results": [allure_result(history, "failed", 1_000, "MISMATCH 1750")]}, db)
        second = create_launch({"name": f"t-{tag}-2", "categories": CATEGORIES,
                                "results": [allure_result(history, "passed", 9_000_000, attachment_source=source)]}, db)
        yield {"history": history, "source": source, "first": first, "second": second}
        for name in (f"t-{tag}-1", f"t-{tag}-2"):
            db.query(ReportLaunch).filter(ReportLaunch.name == name).delete()
        db.query(ReportAttachment).filter(ReportAttachment.source == source).delete()
        db.commit()


def test_import_is_idempotent_and_asks_only_for_missing_attachments(two_launches):
    assert two_launches["first"]["created"] is True
    assert two_launches["second"]["attachments_needed"] == [two_launches["source"]]
    with SessionLocal() as db:
        again = create_launch({"name": db.get(ReportLaunch, two_launches["first"]["launch_id"]).name, "results": []}, db)
    assert again["created"] is False and again["launch_id"] == two_launches["first"]["launch_id"]


@pytest.mark.anyio
async def test_attachment_is_stored_and_served(two_launches, reports_dir):
    class Body:
        async def body(self):
            return b"\x89PNG-test"

    with SessionLocal() as db:
        stored = await upload_attachment(two_launches["source"], Body(), db)
        assert stored["size"] == 9
        response = attachment(two_launches["source"], db)
        assert response.media_type == "image/png"
        assert (reports_dir / two_launches["source"]).read_bytes() == b"\x89PNG-test"


def test_latest_scope_keeps_the_newest_result_of_each_test_and_history(two_launches):
    with SessionLocal() as db:
        latest = [item for item in results(q=two_launches["history"], db=db)]
        assert [item["status"] for item in latest] == ["passed"]
        first = results(launch_id=two_launches["first"]["launch_id"], db=db)
        assert first[0]["category"] == "Розбіжність з оракулом"
        detail = result(latest[0]["id"], db)
        assert [item["status"] for item in detail["history"]] == ["passed", "failed"]
        summary = overview(launch_id=two_launches["first"]["launch_id"], db=db)
        assert summary["stats"]["failed"] == 1 and summary["stats"]["total"] == 1
        assert summary["categories"] == [{"name": "Розбіжність з оракулом", "count": 1}]
        assert summary["launch"]["environment"] == {"Hall": "11"}
        assert json.dumps(summary, default=str)


def test_archived_test_leaves_latest_results_but_keeps_history(two_launches):
    history = two_launches["history"]
    with SessionLocal() as db:
        archive_test({"history_id": history, "name": "Кейс"}, db)
        assert results(q=history, db=db) == []
        assert [item["archived"] for item in results(q=history, include_archived=True, db=db)] == [True]
        assert results(launch_id=two_launches["first"]["launch_id"], db=db)[0]["archived"] is True
        assert history in [item["history_id"] for item in archived_tests(db)]
        restore_test(history, db)
        assert len(results(q=history, db=db)) == 1
