from datetime import UTC, datetime

from app.api.tests import results_version
from app.db import SessionLocal
from app.services.version_summary import run_versions, summarize_version


def test_child_run_takes_the_version_of_its_suite_run():
    versions = run_versions([
        ("suite", None, {"version": "1.75.0.3"}),
        ("case", "suite", None),
        ("old", None, {"error": "500"}),
    ])
    assert versions == {"suite": "1.75.0.3", "case": "1.75.0.3"}


def _row(name, status, version, minute, launch="l1"):
    return {"full_name": name, "status": status, "version": version, "launch_id": launch,
            "start": datetime(2026, 11, 15, 10, minute, tzinfo=UTC)}


def test_summary_of_the_newest_version_counts_latest_result_per_test():
    results = [  # newest first
        _row("a", "passed", "1.75.0.3", 50, "l2"),
        _row("b", "failed", "1.75.0.3", 40, "l2"),
        _row("a", "failed", "1.75.0.3", 30),
        _row("c", "skipped", "1.75.0.3", 20),
        _row("d", "passed", "1.75.0.2", 10),
        _row("retired", "passed", "1.75.0.3", 5),
    ]
    summary = summarize_version(results, ["a", "b", "c", "d"])
    assert summary == {
        "version": "1.75.0.3", "at": datetime(2026, 11, 15, 10, 50, tzinfo=UTC), "tests": 4, "executed": 3,
        "percent": 75, "passed": 1, "failed": 1, "other": 1, "missing": 1, "launches": 2,
        "statuses": {"a": "passed", "b": "failed", "c": "skipped"},
    }


def test_best_version_has_the_most_passed_tests_newer_wins_a_tie():
    from app.services.version_summary import best_version
    results = [  # newest first
        _row("a", "failed", "1.75.0.3", 50),
        _row("a", "passed", "1.75.0.2", 40),
        _row("b", "passed", "1.75.0.2", 30),
        _row("a", "passed", "1.75.0.1", 20),
        _row("b", "passed", "1.75.0.1", 10),
    ]
    best = best_version(results, ["a", "b", "c"])
    assert (best["version"], best["passed"], best["missing"]) == ("1.75.0.2", 2, 1)
    assert summarize_version(results, ["a"], "9.9")["executed"] == 0


def test_summary_without_versioned_results():
    summary = summarize_version([_row("a", "passed", None, 1)], ["a", "b"])
    assert summary["version"] is None and summary["tests"] == 2 and summary["executed"] == 0


def test_endpoint_answers_on_the_live_database():
    with SessionLocal() as db:
        summary = results_version(db=db)
    assert {"version", "tests", "executed", "percent", "passed", "failed"} <= summary.keys()
    assert summary["passed"] + summary["failed"] + summary["other"] == summary["executed"] <= summary["tests"]
    assert summary["executed"] + summary["missing"] == summary["tests"]
    assert summary["best"] is None or summary["best"]["passed"] >= summary["passed"]
    assert summary["versions"][0]["version"] == summary["version"]
    assert summary["best"] is None or summary["best"]["passed"] == max(item["passed"] for item in summary["versions"])


def test_table_rows_carry_version_of_last_run_and_last_passed_run():
    from app.api.tests import list_tests
    with SessionLocal() as db:
        rows = list_tests(False, db)
    assert rows and all("last_passed" in row for row in rows)
    for row in rows:
        if row["last"]:
            assert "version" in row["last"]
        if row["last_passed"]:
            assert row["last_passed"]["start"] and "version" in row["last_passed"]
    assert any(row["last"] and row["last"]["version"] for row in rows), "no row got a version"


def test_result_version_goes_through_launch_or_own_run():
    from app.services.version_summary import result_version
    versions = {"suite": "1.75.0.3", "case": "1.75.0.3"}
    assert result_version({"launch_id": "L"}, {"L": "suite"}, versions) == "1.75.0.3"
    assert result_version({"launch_id": None, "run_id": "case"}, {}, versions) == "1.75.0.3"
    assert result_version({"launch_id": "unknown"}, {}, versions) is None


def test_version_key_groups_fixes_or_minor_lines():
    from app.services.version_summary import version_key
    assert version_key("1.75.0.3", 4) == "1.75.0.3"
    assert version_key("1.75.0.3", 3) == "1.75.0.*"
    assert version_key("1.75.2.1", 2) == "1.75.*"
    assert version_key("1.75", 3) == "1.75" and version_key(None, 3) is None


def test_grouped_summary_takes_the_latest_result_on_any_version_of_the_group():
    results = [  # newest first, already keyed at level 3
        {**_row("a", "failed", "1.75.0.*", 50), "exact": "1.75.0.3"},
        {**_row("b", "passed", "1.75.0.*", 40), "exact": "1.75.0.2"},
        {**_row("a", "passed", "1.75.0.*", 30), "exact": "1.75.0.2"},
    ]
    summary = summarize_version(results, ["a", "b"])
    assert (summary["version"], summary["passed"], summary["failed"]) == ("1.75.0.*", 1, 1)


def test_endpoint_levels_on_the_live_database():
    with SessionLocal() as db:
        exact, fixes, minor = (results_version(level=level, db=db) for level in (4, 3, 2))
    assert fixes["level"] == 3 and fixes["version"].endswith(".*") and fixes["versions"][0]["members"]
    assert fixes["executed"] >= exact["executed"] and minor["executed"] >= fixes["executed"]
    assert len(minor["versions"]) <= len(fixes["versions"]) <= len(exact["versions"])
