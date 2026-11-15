"""Summary of the last tested configuration version (env_ai_dev#29).

A launch run records the stand it ran on (``lab_e2e_runs.stand.version``); a test of a suite inherits the
version of its suite launch.  The last tested version is the version of the newest report with one; its
summary counts the active tests that have a result on that version and how their latest result ended.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LabE2eRun, ReportLaunch, ReportResult

GOOD = {"passed"}
BAD = {"failed", "broken"}


def run_versions(runs: Iterable[tuple[str, str | None, dict | None]]) -> dict[str, str]:
    """Run id → configuration version; a run without its own reading takes the version of its parent."""
    rows = {run_id: (parent_id, stand) for run_id, parent_id, stand in runs}

    def version(run_id: str | None, depth: int = 0) -> str | None:
        if not run_id or run_id not in rows or depth > 5:
            return None
        parent_id, stand = rows[run_id]
        own = stand.get("version") if isinstance(stand, dict) else None
        return own if isinstance(own, str) and own else version(parent_id, depth + 1)

    return {run_id: found for run_id in rows if (found := version(run_id))}


def summarize_version(results: list[dict[str, Any]], test_ids: list[str]) -> dict[str, Any]:
    """``results``: rows with ``full_name``, ``status``, ``start``, ``version``, ``launch_id``, newest first."""
    total = len(test_ids)
    empty = {"version": None, "at": None, "tests": total, "executed": 0, "percent": 0, "passed": 0, "failed": 0,
             "other": 0, "launches": 0}
    dated = [row for row in results if row.get("version")]
    if not dated:
        return empty
    version = dated[0]["version"]
    wanted = set(test_ids)
    latest: dict[str, dict[str, Any]] = {}
    launches: set[str] = set()
    for row in dated:
        if row["version"] != version:
            continue
        launches.add(row["launch_id"])
        if row["full_name"] in wanted:
            latest.setdefault(row["full_name"], row)
    statuses = [row["status"] for row in latest.values()]
    passed = sum(1 for status in statuses if status in GOOD)
    failed = sum(1 for status in statuses if status in BAD)
    return {
        "version": version,
        "at": dated[0]["start"],
        "tests": total,
        "executed": len(latest),
        "percent": round(len(latest) * 100 / total) if total else 0,
        "passed": passed,
        "failed": failed,
        "other": len(latest) - passed - failed,
        "launches": len(launches),
    }


def result_version(item: dict[str, Any], launch_runs: dict[str, str], versions: dict[str, str]) -> str | None:
    """Version of one history item: a report through its launch run, a live result through its own run."""
    run_id = item.get("run_id") or launch_runs.get(item.get("launch_id") or "")
    return versions.get(run_id) if run_id else None


def annotate_table(db: Session, summaries: list[dict[str, Any]], history: dict[str, list[dict[str, Any]]]) -> None:
    """Configuration version of the last run and the last passed run of every test (env_ai_dev#29)."""
    versions = run_versions(db.execute(select(LabE2eRun.id, LabE2eRun.parent_id, LabE2eRun.stand)).tuples())
    # a Result has keys(): dict() would read it as a mapping, so pairs go through a comprehension
    launch_runs = {launch_id: run_id for launch_id, run_id in db.execute(
        select(ReportLaunch.id, ReportLaunch.e2e_run_id).where(ReportLaunch.e2e_run_id.is_not(None))).tuples()}
    for summary in summaries:
        items = history.get(summary["id"], [])
        if summary.get("last") and items:
            summary["last"]["version"] = result_version(items[0], launch_runs, versions)
        good = next((item for item in items if item["status"] in GOOD), None)
        summary["last_passed"] = {
            "id": good["id"], "start": good["start"], "launch": good.get("launch"),
            "version": result_version(good, launch_runs, versions),
        } if good else None


def version_summary(db: Session, test_ids: list[str]) -> dict[str, Any]:
    versions = run_versions(db.execute(select(LabE2eRun.id, LabE2eRun.parent_id, LabE2eRun.stand)).tuples())
    # Result times come from the clock of the machine under test, which date-shifted chains move: order by the
    # real time of the run, then by the result inside it.
    rows = db.execute(
        select(ReportResult.full_name, ReportResult.status, ReportResult.launch_id, ReportLaunch.e2e_run_id,
               func.coalesce(LabE2eRun.finished_at, LabE2eRun.created_at).label("start"))
        .join(ReportLaunch, ReportLaunch.id == ReportResult.launch_id)
        .join(LabE2eRun, LabE2eRun.id == ReportLaunch.e2e_run_id)
        .order_by(LabE2eRun.created_at.desc(), ReportResult.start.desc().nulls_last())
    ).mappings()
    results = [{**row, "version": versions.get(row["e2e_run_id"])} for row in rows]
    summary = summarize_version(results, test_ids)
    if isinstance(summary["at"], datetime) and summary["at"].tzinfo is None:
        summary["at"] = summary["at"].replace(tzinfo=UTC)
    return summary
