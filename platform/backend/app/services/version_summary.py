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


def version_key(version: str | None, level: int = 4) -> str | None:
    """A version at a level of detail: 4 - as is (1.75.0.3), 3 - fixes together (1.75.0.*), 2 - the minor line
    together (1.75.*).  A version shorter than the level stays as is."""
    if not version:
        return None
    parts = version.split(".")
    if level >= len(parts) or level >= 4:
        return version
    return ".".join(parts[:max(1, level)]) + ".*"


def summarize_version(results: list[dict[str, Any]], test_ids: list[str], version: str | None = None) -> dict[str, Any]:
    """``results``: rows with ``full_name``, ``status``, ``start``, ``version``, ``launch_id``, newest first.

    ``version``: the version to sum up; None - the newest one.  ``statuses``: test id → its latest status on the
    version (the table filters by it); ``missing``: active tests without a result on the version.
    """
    total = len(test_ids)
    empty = {"version": None, "at": None, "tests": total, "executed": 0, "percent": 0, "passed": 0, "failed": 0,
             "other": 0, "missing": total, "launches": 0, "statuses": {}}
    dated = [row for row in results if row.get("version")]
    if not dated:
        return empty
    version = version or dated[0]["version"]
    on_version = [row for row in dated if row["version"] == version]
    if not on_version:
        return {**empty, "version": version}
    wanted = set(test_ids)
    latest: dict[str, dict[str, Any]] = {}
    for row in on_version:
        if row["full_name"] in wanted:
            latest.setdefault(row["full_name"], row)
    statuses = {test_id: row["status"] for test_id, row in latest.items()}
    passed = sum(1 for status in statuses.values() if status in GOOD)
    failed = sum(1 for status in statuses.values() if status in BAD)
    return {
        "version": version,
        "at": on_version[0]["start"],
        "tests": total,
        "executed": len(latest),
        "percent": round(len(latest) * 100 / total) if total else 0,
        "passed": passed,
        "failed": failed,
        "other": len(latest) - passed - failed,
        "missing": total - len(latest),
        "launches": len({row["launch_id"] for row in on_version}),
        "statuses": statuses,
    }


def all_versions(results: list[dict[str, Any]], test_ids: list[str]) -> list[dict[str, Any]]:
    """Summary of every tested version, the newest first (the version picker of the tests page)."""
    order = list(dict.fromkeys(row["version"] for row in results if row.get("version")))
    return [summarize_version(results, test_ids, version) for version in order]


def best_version(results: list[dict[str, Any]], test_ids: list[str]) -> dict[str, Any] | None:
    """The version with the most passed active tests; on a tie the newer one (results are newest first)."""
    return pick_best(all_versions(results, test_ids))


def pick_best(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(versions, key=lambda item: item["passed"], default=None)  # max keeps the first (newer) of equals


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


def version_summary(db: Session, test_ids: list[str], level: int = 4) -> dict[str, Any]:
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
    # ``level`` groups versions (version_key): the summary of 1.75.0.* takes the latest result of a test on any 1.75.0.x
    results = [{**row, "exact": versions.get(row["e2e_run_id"]),
                "version": version_key(versions.get(row["e2e_run_id"]), level)} for row in rows]
    versions = all_versions(results, test_ids)
    summary = versions[0] if versions else summarize_version(results, test_ids)
    for item in versions:
        # exact versions inside a group, newest first
        item["members"] = list(dict.fromkeys(row["exact"] for row in results if row["version"] == item["version"]))
        if isinstance(item["at"], datetime) and item["at"].tzinfo is None:
            item["at"] = item["at"].replace(tzinfo=UTC)
    # the last tested version stays at the top level (env_ai_dev#29); ``best`` - the version with most passed;
    # ``versions`` - every tested version, newest first, for the picker
    return {**summary, "level": level, "best": pick_best(versions), "versions": versions}
