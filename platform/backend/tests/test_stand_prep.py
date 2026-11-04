import json

import pytest
from fastapi import HTTPException

from app.api import stand
from app.api.e2e import apply_event
from app.db import SessionLocal
from app.models import LabE2eRun, LabStandFileVersion
from app.services import stand_prep

PLAN = {"enabled": False, "stages": [
    {"id": "machine", "fixed": True, "file": "suite/run.ps1", "steps": [{"id": "check", "title": "Check", "find": "check"}]},
    {"id": "data", "steps": [{"id": "clean", "title": "Clean", "script": "clean.ps1", "args": ["-Mode", "apply"], "scope": "suite"}]},
]}


@pytest.fixture
def stand_files(tmp_path, monkeypatch):
    config, root = tmp_path / "config", tmp_path / "stand"
    (config / "e2e").mkdir(parents=True)
    (root / "suite" / "data").mkdir(parents=True)
    (config / "e2e" / "preparation.json").write_text(json.dumps(
        {"plan": "suite/data/plan.json", "cases": "suite/data/cases.json", "scripts": "suite", "files": ["suite/*.ps1"]}), encoding="utf-8")
    (root / "suite" / "data" / "plan.json").write_text(json.dumps(PLAN, indent=2), encoding="utf-8")
    (root / "suite" / "data" / "cases.json").write_text(json.dumps({"cases": [{"id": "a", "web": False}]}), encoding="utf-8")
    (root / "suite" / "clean.ps1").write_bytes(stand_prep.BOM + "# очищення\r\nparam($Mode)\r\n".encode("utf-8"))
    (root / "secret.txt").write_text("not for the browser", encoding="utf-8")
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(config))
    monkeypatch.setenv("AFTERTIME_STAND_DIR", str(root))
    monkeypatch.setattr(stand, "_busy", lambda db: None)
    yield root
    with SessionLocal() as db:
        db.query(LabStandFileVersion).filter(LabStandFileVersion.path.like("suite/%")).delete(synchronize_session=False)
        db.commit()


def test_a_script_keeps_its_byte_order_mark_and_line_endings_and_every_save_is_a_version(stand_files):
    with SessionLocal() as db:
        opened = stand.read_file("suite/clean.ps1")
        assert opened["text"] == "# очищення\nparam($Mode)\n" and opened["bom"] and opened["eol"] == "crlf"
        saved = stand.save_file({"path": "suite/clean.ps1", "text": opened["text"] + "Write-Output ok\n", "sha": opened["sha"], "note": "вивід"}, db)
        raw = (stand_files / "suite" / "clean.ps1").read_bytes()
        assert raw.startswith(stand_prep.BOM) and raw.endswith(b"Write-Output ok\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
        versions = stand.file_versions("suite/clean.ps1", db)
        assert [item["note"] for item in versions] == ["вивід", "До першої зміни з Aftertime"]
        assert stand.file_version(versions[1]["id"], db)["text"] == opened["text"]
        with pytest.raises(HTTPException) as stale:
            stand.save_file({"path": "suite/clean.ps1", "text": "x", "sha": opened["sha"]}, db)
        assert stale.value.status_code == 409 and saved["sha"] != opened["sha"]


def test_only_listed_files_inside_the_stand_are_reachable(stand_files):
    for path in ("secret.txt", "../config/e2e/preparation.json", "/etc/passwd", "suite/data/../../secret.txt"):
        with pytest.raises(HTTPException) as refused:
            stand.read_file(path)
        assert refused.value.status_code == 403


def test_a_broken_plan_is_refused_in_words_and_the_file_stays(stand_files):
    with SessionLocal() as db:
        before = (stand_files / "suite" / "data" / "plan.json").read_text(encoding="utf-8")
        broken = {**PLAN, "stages": [PLAN["stages"][0], {"id": "data", "steps": [{"id": "clean", "title": "Clean", "script": "missing.ps1"}]}]}
        for text, words in ((json.dumps(broken), "скрипт не знайдено"), ("{", "коректним JSON"),
                            (json.dumps({**PLAN, "stages": [PLAN["stages"][1], PLAN["stages"][1]]}), "повторюється")):
            with pytest.raises(HTTPException) as refused:
                stand.save_file({"path": "suite/data/plan.json", "text": text}, db)
            assert refused.value.status_code == 422 and words in refused.value.detail
        assert (stand_files / "suite" / "data" / "plan.json").read_text(encoding="utf-8") == before


def test_nothing_is_written_while_a_run_is_on_the_stand(stand_files, monkeypatch):
    monkeypatch.setattr(stand, "_busy", lambda db: {"id": "r", "status": "running", "kind": "center"})
    with SessionLocal() as db, pytest.raises(HTTPException) as busy:
        stand.save_file({"path": "suite/clean.ps1", "text": "x"}, db)
    assert busy.value.status_code == 409


def test_preparation_lists_the_plan_cases_and_step_times(stand_files):
    with SessionLocal() as db:
        data = stand.preparation(db)
    assert data["plan"]["stages"][1]["steps"][0]["id"] == "clean" and data["cases"] == [{"id": "a", "web": False}]
    assert stand_prep.step_stats([[{"t": "2", "prep": {"step": "clean", "state": "ok", "ms": 300}}],
                                  [{"t": "1", "prep": {"step": "clean", "state": "ok", "ms": 100}},
                                   {"t": "1", "prep": {"step": "clean", "state": "start"}}]]) == {
        "clean": {"mean_ms": 200, "runs": 2, "last": {"state": "ok", "changed": None, "ms": 300, "t": "2"}}}


def test_preparation_steps_from_the_log_go_on_the_run_timeline():
    run = LabE2eRun(log=[], timeline=[], states=[])
    apply_event(run, {"type": "log", "line": "  >> PREP|ok|3|18|accounts|1234|changed|Тестові облікові записи (north)"})
    apply_event(run, {"type": "log", "line": "an ordinary line"})
    assert run.timeline[0]["prep"] == {"state": "ok", "n": 3, "total": 18, "step": "accounts", "ms": 1234,
                                       "changed": "changed", "title": "Тестові облікові записи (north)"}
    assert len(run.timeline) == 1 and len(run.log) == 2
