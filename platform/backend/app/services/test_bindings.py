"""Bindings between BPMN processes and external end-to-end test cases.

A process opts in with ``aftertime:testBinding="<binding id>"`` on ``bpmn:process``;
its activities and catch events name the test phase they represent with
``aftertime:testPhase``.  Bindings live outside this repository: a model pack ships
them (``bindings`` in its manifest), and ``$AFTERTIME_CONFIG_DIR/e2e/bindings.json`` may
add bindings or override ``runner`` details for a particular test stand::

    {"<binding id>": {"title": "...", "phases": ["order", "issue"], "runner": {...}}}

``runner`` is opaque here; it belongs to the bridge that starts the tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.services.bpmn_profile import AFTERTIME_NS, BPMN_NS
from app.services.packs import discover

# Elements a token waits on; only these can mirror a test phase.
PHASE_TAGS = {
    "task",
    "serviceTask",
    "userTask",
    "manualTask",
    "scriptTask",
    "businessRuleTask",
    "sendTask",
    "receiveTask",
    "intermediateCatchEvent",
}
ACTIVITY_TAGS = PHASE_TAGS - {"intermediateCatchEvent"}


def bindings_path() -> Path:
    return Path(os.environ.get("AFTERTIME_CONFIG_DIR", "/config")) / "e2e" / "bindings.json"


def _read(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def load_bindings(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Bindings of every pack, then the stand settings on top (``runner`` merged key by key)."""
    if path is not None:
        return _read(path)
    merged: dict[str, dict[str, Any]] = {}
    for pack in discover().values():
        merged.update(pack.bindings_data())
    for key, value in _read(bindings_path()).items():
        base = merged.get(key, {})
        merged[key] = {**base, **value, "runner": {**base.get("runner", {}), **value.get("runner", {})}}
    return merged


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_test_annotations(bpmn_xml: str) -> dict[str, Any]:
    """Binding id and phase → elements, as written in the BPMN document."""
    root = ElementTree.fromstring(bpmn_xml)
    binding = next(
        (
            process.get(f"{{{AFTERTIME_NS}}}testBinding")
            for process in root.iter(f"{{{BPMN_NS}}}process")
            if process.get(f"{{{AFTERTIME_NS}}}testBinding")
        ),
        None,
    )
    phases: dict[str, list[str]] = {}
    misplaced: list[str] = []
    untested: list[str] = []
    for element in root.iter():
        if not element.tag.startswith(f"{{{BPMN_NS}}}"):
            continue
        tag = _local(element.tag)
        phase = element.get(f"{{{AFTERTIME_NS}}}testPhase")
        if phase:
            if tag in PHASE_TAGS:
                phases.setdefault(phase, []).append(element.get("id"))
            else:
                misplaced.append(element.get("id"))
        elif tag in ACTIVITY_TAGS:
            untested.append(element.get("id"))
    return {"binding": binding, "phases": phases, "misplaced": misplaced, "untested": untested}




def phase_coverage(bpmn_xml: str, bindings: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """How completely a bound process mirrors its test case; None if not bound."""
    annotations = read_test_annotations(bpmn_xml)
    binding_id = annotations["binding"]
    if not binding_id:
        return None
    binding = bindings.get(binding_id)
    expected = list(binding.get("phases", [])) if binding else []
    phases = annotations["phases"]
    errors: list[str] = []
    warnings: list[str] = []
    if binding is None:
        errors.append(f"Прив'язку тестів «{binding_id}» не знайдено в налаштуваннях")
    missing = [phase for phase in expected if phase not in phases]
    unknown = sorted(phase for phase in phases if binding is not None and phase not in expected)
    duplicated = sorted(phase for phase, elements in phases.items() if len(elements) > 1)
    if missing:
        errors.append(f"Фази тесту без елемента BPMN: {', '.join(missing)}")
    if unknown:
        errors.append(f"Фази, яких немає в тесті: {', '.join(unknown)}")
    if duplicated:
        errors.append(f"Фаза позначена на кількох елементах: {', '.join(duplicated)}")
    if annotations["misplaced"]:
        errors.append(
            "Фазу можна позначати лише на задачі або проміжній події очікування: "
            + ", ".join(annotations["misplaced"])
        )
    if annotations["untested"]:
        warnings.append(f"Задачі, не покриті тестом: {', '.join(annotations['untested'])}")
    return {
        "binding": binding_id,
        "title": binding.get("title", binding_id) if binding else binding_id,
        "phases": [
            {"phase": phase, "element": (phases.get(phase) or [None])[0]} for phase in expected
        ],
        "covered": not errors,
        "errors": errors,
        "warnings": warnings,
    }
