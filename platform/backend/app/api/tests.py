"""The test suite and its consolidated results."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LabE2eRun, LabTest, ReportLaunch, ReportResult
from app.services.test_bindings import load_bindings
from app.services.tests_catalog import results_by_test, summarize_test

router = APIRouter(prefix="/api/v1/lab", tags=["tests"])


def _bindings_by_case() -> dict[str, str]:
    return {case: key for key, value in load_bindings().items() for case in (value.get("runner") or {}).get("cases", [])}


def _tests(db: Session, include_retired: bool) -> list[LabTest]:
    query = select(LabTest).order_by(LabTest.number.nulls_last(), LabTest.id)
    if not include_retired:
        query = query.where(LabTest.active.is_(True))
    return list(db.scalars(query))


@router.get("/tests")
def list_tests(include_retired: bool = False, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Every test of the suite with its latest result, recent trend and stability."""
    rows = _tests(db, include_retired)
    history = results_by_test(db, [row.id for row in rows])
    bindings = _bindings_by_case()
    return [summarize_test(row, history.get(row.id, []), bindings.get(row.id)) for row in rows]


@router.get("/tests/{test_id}")
def get_test(test_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(LabTest, test_id)
    if not row:
        raise HTTPException(404, "Тест не знайдено")
    history = results_by_test(db, [test_id]).get(test_id, [])
    suite_ids = {suite.get("id") for suite in row.suites or []}
    binding = _bindings_by_case().get(test_id)
    runs = []
    for run in db.scalars(select(LabE2eRun).order_by(LabE2eRun.created_at.desc()).limit(200)):
        target = run.target or {}
        if (test_id in (target.get("cases") or [])) or (suite_ids & set(target.get("suites") or [])) or (binding and run.binding == binding):
            runs.append({"id": run.id, "kind": run.kind, "status": run.status, "created_at": run.created_at,
                         "finished_at": run.finished_at, "binding": run.binding, "target": target,
                         "recording": (run.recording or {}).get("status")})
        if len(runs) >= 20:
            break
    return {**summarize_test(row, history, binding), "first_seen": row.first_seen, "last_seen": row.last_seen,
            "history": history[:50], "aftertime_runs": runs}


@router.get("/results/summary")
def results_summary(days: int = 14, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Consolidated state of the suite: latest result of every active test, by suite and over time."""
    rows = _tests(db, False)
    history = results_by_test(db, [row.id for row in rows])
    summaries = [summarize_test(row, history.get(row.id, []), None) for row in rows]
    counts = Counter(item["status"] for item in summaries)
    suites: dict[str, dict[str, Any]] = {}
    for row, item in zip(rows, summaries):
        for suite in row.suites or [{"id": "—", "title": "Без набору"}]:
            entry = suites.setdefault(suite["id"], {"id": suite["id"], "title": suite.get("title"), "tests": 0,
                                                    "statuses": Counter(), "last_run": None, "flaky": 0})
            entry["tests"] += 1
            entry["statuses"][item["status"]] += 1
            entry["flaky"] += int(item["flaky"])
            start = item["last"]["start"] if item["last"] else None
            if start and (entry["last_run"] is None or start > entry["last_run"]):
                entry["last_run"] = start
    since = datetime.now(UTC) - timedelta(days=days)
    daily: dict[str, Counter] = defaultdict(Counter)
    for results in history.values():
        for result in results:
            if result["start"] and result["start"] >= since:
                daily[result["start"].date().isoformat()][result["status"]] += 1
    executed = [item for item in summaries if item["status"] != "never"]
    last_launch = db.scalar(select(ReportLaunch.start).order_by(ReportLaunch.start.desc().nulls_last()).limit(1))
    return {
        "tests": len(summaries),
        "executed": len(executed),
        "statuses": dict(counts),
        "pass_rate": round(counts.get("passed", 0) / len(executed), 2) if executed else None,
        "flaky": sum(1 for item in summaries if item["flaky"]),
        "last_launch": last_launch,
        "retired": db.scalar(select(LabTest.id).where(LabTest.active.is_(False)).limit(1)) is not None,
        "suites": [{**entry, "statuses": dict(entry["statuses"])} for entry in suites.values()],
        "daily": [{"day": day, **dict(values)} for day, values in sorted(daily.items())],
        "attention": [item for item in summaries if item["status"] in ("failed", "broken") or item["flaky"]],
    }


@router.get("/results/matrix")
def results_matrix(suite: str | None = None, limit: int = 15, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Tests × their most recent launches: the status each test had in each launch."""
    rows = [row for row in _tests(db, False) if not suite or suite in {item.get("id") for item in row.suites or []}]
    history = results_by_test(db, [row.id for row in rows])
    launches: dict[str, dict[str, Any]] = {}
    for results in history.values():
        for result in results:
            launches.setdefault(result["launch_id"], {"id": result["launch_id"], "name": result["launch"], "start": result["start"]})
    columns = sorted(launches.values(), key=lambda item: item["start"] or datetime.min.replace(tzinfo=UTC))[-limit:]
    wanted = {column["id"] for column in columns}
    matrix = []
    for row in rows:
        cells = {result["launch_id"]: {"status": result["status"], "result_id": result["id"]}
                 for result in history.get(row.id, []) if result["launch_id"] in wanted}
        matrix.append({"id": row.id, "title": row.title, "suites": row.suites, "cells": cells})
    return {"launches": columns, "tests": matrix}
