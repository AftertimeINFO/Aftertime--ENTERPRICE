import json
from pathlib import Path

import pytest

from app.api.packs import packs
from app.db import SessionLocal
from app.models import LabPack, LabWorkflow
from app.pack_install import install_pack
from app.services.bpmn_profile import build_bpmn_xml
from app.services.packs import discover
from app.services.test_bindings import load_bindings


def write_example_pack(root: Path) -> None:
    """The same shape as platform/packs.example: one process bound to an E2E case."""
    nodes = [
        {"node_key": "start", "block_type": "start_event", "name": "Надійшло замовлення", "x": 80, "y": 120,
         "config": {"event_definition": "message", "event_type": "order.received"}},
        {"node_key": "order", "block_type": "user_task", "name": "Оформити", "x": 240, "y": 120, "config": {}},
        {"node_key": "issue", "block_type": "user_task", "name": "Видати", "x": 420, "y": 120, "config": {}},
        {"node_key": "return", "block_type": "user_task", "name": "Прийняти повернення", "x": 600, "y": 120, "config": {}},
        {"node_key": "end", "block_type": "end_event", "name": "Закрито", "x": 780, "y": 120, "config": {"event_definition": "none"}},
    ]
    flows = [{"flow_key": f"f{index}", "source_key": a, "target_key": b}
             for index, (a, b) in enumerate([("start", "order"), ("order", "issue"), ("issue", "return"), ("return", "end")], 1)]
    xml = build_bpmn_xml("example-order", "Приклад: замовлення", nodes, flows)
    xml = xml.replace('<bpmn:process id="example-order"', '<bpmn:process id="example-order" aftertime:testBinding="example-order"', 1)
    for phase in ("order", "issue", "return"):
        xml = xml.replace(f'id="{phase}" name=', f'id="{phase}" aftertime:testPhase="{phase}" name=', 1)
    pack = root / "example-orders"
    (pack / "processes").mkdir(parents=True)
    (pack / "e2e").mkdir()
    (pack / "processes" / "example-order.bpmn").write_text(xml, encoding="utf-8")
    (pack / "aftertime-pack.json").write_text(json.dumps({"id": "example", "title": "Приклад", "version": "0.1.0",
        "processes": "processes/*.bpmn", "bindings": "e2e/bindings.json"}), encoding="utf-8")
    (pack / "e2e" / "bindings.json").write_text(json.dumps({"example-order": {"title": "Приклад",
        "phases": ["order", "issue", "return"], "runner": {"cases": ["example-order-case"]}}}), encoding="utf-8")


@pytest.fixture
def packs_root(tmp_path, monkeypatch):
    write_example_pack(tmp_path / "packs")
    (tmp_path / "packs" / "broken").mkdir()
    (tmp_path / "packs" / "broken" / "aftertime-pack.json").write_text("{не json", encoding="utf-8")
    (tmp_path / "config" / "e2e").mkdir(parents=True)
    monkeypatch.setenv("AFTERTIME_PACKS_DIR", str(tmp_path / "packs"))
    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def test_discovery_skips_broken_manifests(packs_root):
    found = discover()
    assert list(found) == ["example"]
    assert found["example"].summary()["processes"] == ["example-order.bpmn"]


def test_stand_settings_override_runner_of_pack_bindings(packs_root):
    (packs_root / "config" / "e2e" / "bindings.json").write_text(json.dumps(
        {"example-order": {"runner": {"live": True}}, "extra": {"title": "Лише стенд", "phases": []}}), encoding="utf-8")
    bindings = load_bindings()
    assert bindings["example-order"]["phases"] == ["order", "issue", "return"]
    assert bindings["example-order"]["runner"] == {"cases": ["example-order-case"], "live": True}
    assert bindings["example-order"]["pack"] == "example"
    assert "extra" in bindings


def test_install_imports_publishes_and_owns_processes_idempotently(packs_root):
    pack = discover()["example"]
    with SessionLocal() as db:
        try:
            first = install_pack(db, pack)
            assert [(item["process"], item["action"]) for item in first["processes"]] == [("example-order", "published")]
            assert db.get(LabWorkflow, "example-order").pack_id == "example"
            again = install_pack(db, pack)
            assert again["processes"][0]["action"] == "unchanged"
            listed = next(item for item in packs(db) if item["id"] == "example")
            assert listed["installed"] and listed["available"] and listed["workflows"] == ["example-order"]
        finally:
            workflow = db.get(LabWorkflow, "example-order")
            if workflow:
                db.delete(workflow)
            row = db.get(LabPack, "example")
            if row:
                db.delete(row)
            db.commit()


def test_settings_name_the_default_contour(tmp_path, monkeypatch):
    from app.api.packs import settings

    monkeypatch.setenv("AFTERTIME_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AFTERTIME_DEFAULT_PACK", raising=False)
    assert settings() == {"default_pack": "", "default_process": ""}
    (tmp_path / "aftertime.json").write_text(json.dumps({"defaultPack": "rental", "defaultProcess": "rental-issue"}), encoding="utf-8")
    assert settings() == {"default_pack": "rental", "default_process": "rental-issue"}
    monkeypatch.setenv("AFTERTIME_DEFAULT_PACK", "energy")
    assert settings()["default_pack"] == "energy"
