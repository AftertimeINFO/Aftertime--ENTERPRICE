"""The end-to-end test suite inside Aftertime: catalog, per-test history and consolidated results.

The catalog comes from the control center (suites → cases); results come from imported reports,
where a result's ``fullName`` is the case id.  A test that left the catalog stays known as retired.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LabTest, ReportLaunch, ReportResult

HISTORY_WINDOW = 10
GOOD = {"passed"}
BAD = {"failed", "broken"}


def sync_catalog(db: Session, catalog: dict[str, Any]) -> int:
    """Upsert the cases of the control center catalog; cases no longer there become inactive."""
    suites = catalog.get("suites") if isinstance(catalog, dict) else None
    if not isinstance(suites, list) or not suites:
        return 0  # an empty or broken snapshot must not retire the whole suite
    seen: dict[str, dict[str, Any]] = {}
    for suite in suites:
        for case in suite.get("cases") or []:
            if not case.get("id"):
                continue
            entry = seen.setdefault(case["id"], {"title": case.get("title") or case["id"], "suites": [],
                                                 "feature": case.get("feature"), "number": case.get("number")})
            entry["suites"].append({"id": suite.get("id"), "title": suite.get("title") or suite.get("id")})
    now = datetime.now(UTC)
    existing = {row.id: row for row in db.scalars(select(LabTest))}
    for case_id, entry in seen.items():
        row = existing.get(case_id) or LabTest(id=case_id, first_seen=now)
        row.title, row.suites, row.feature = entry["title"], entry["suites"], entry["feature"]
        row.number = entry["number"] if isinstance(entry["number"], int) else None
        row.active, row.last_seen = True, now
        db.add(row)
    for case_id, row in existing.items():
        if case_id not in seen and row.active:
            row.active = False
    db.commit()
    return len(seen)


def stability(statuses: list[str]) -> dict[str, Any]:
    """From the newest-first statuses of a test: pass rate and flakiness of its recent runs."""
    window = [status for status in statuses[:HISTORY_WINDOW] if status in GOOD | BAD]
    flips = sum(1 for newer, older in zip(window, window[1:]) if (newer in GOOD) != (older in GOOD))
    return {
        "runs": len(window),
        "pass_rate": round(sum(1 for status in window if status in GOOD) / len(window), 2) if window else None,
        "flaky": flips >= 2,
    }


def results_by_test(db: Session, test_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """Newest-first results of every test, with the launch they belong to."""
    rows = db.execute(
        select(ReportResult.id, ReportResult.full_name, ReportResult.status, ReportResult.start, ReportResult.duration_ms,
               ReportResult.message, ReportResult.category, ReportResult.launch_id, ReportLaunch.name.label("launch"))
        .join(ReportLaunch, ReportLaunch.id == ReportResult.launch_id)
        .where(ReportResult.full_name.in_(list(test_ids)))
        .order_by(ReportResult.start.desc().nulls_last())
    ).mappings()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["full_name"]].append(dict(row))
    return grouped


def summarize_test(test: LabTest, history: list[dict[str, Any]], binding: str | None) -> dict[str, Any]:
    last = history[0] if history else None
    return {
        "id": test.id,
        "title": test.title,
        "suites": test.suites,
        "feature": test.feature,
        "number": test.number,
        "active": test.active,
        "binding": binding,
        "status": last["status"] if last else "never",
        "last": {key: last[key] for key in ("id", "status", "start", "duration_ms", "message", "category", "launch")} if last else None,
        "trend": [item["status"] for item in history[:HISTORY_WINDOW]][::-1],
        **stability([item["status"] for item in history]),
    }
