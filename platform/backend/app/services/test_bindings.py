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
from app.services.process_paths import gaps, parse_graph, process_id, walk_path

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
    return {
        "binding": binding,
        "process": process_id(bpmn_xml),
        "phases": phases,
        "misplaced": misplaced,
        "untested": untested,
    }


def process_bindings(bpmn_xml: str, bindings: dict[str, dict[str, Any]]) -> list[str]:
    """Tests bound to this process: the one it names itself, then those that follow a path through it."""
    annotations = read_test_annotations(bpmn_xml)
    found = [annotations["binding"]] if annotations["binding"] else []
    if annotations["process"]:
        found += sorted(
            key for key, value in bindings.items() if value.get("process") == annotations["process"] and key not in found
        )
    return found


def phase_coverage(
    bpmn_xml: str,
    bindings: dict[str, dict[str, Any]],
    binding_id: str | None = None,
    library: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """How completely a bound process mirrors its test case; None if not bound.

    A binding with ``process`` follows ``path`` through a shared process; any other binding is
    mirrored by the process one to one.  Without ``binding_id`` the first bound test is taken.
    """
    annotations = read_test_annotations(bpmn_xml)
    if binding_id is None:
        candidates = process_bindings(bpmn_xml, bindings)
        binding_id = candidates[0] if candidates else None
    if not binding_id:
        return None
    binding = bindings.get(binding_id)
    if binding and binding.get("process"):
        return _path_coverage(bpmn_xml, annotations, binding_id, binding, library)
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


def _path_coverage(
    bpmn_xml: str,
    annotations: dict[str, Any],
    binding_id: str,
    binding: dict[str, Any],
    library: dict[str, str] | None,
) -> dict[str, Any]:
    """A test that follows ``path`` through the shared process ``binding["process"]``."""
    expected = list(binding.get("phases", []))
    phases = annotations["phases"]
    errors: list[str] = []
    warnings: list[str] = []
    visited: dict[str, list[str]] = {"nodes": [], "flows": []}
    graph = parse_graph(bpmn_xml, binding["process"]) if binding["process"] == annotations["process"] else None
    if graph is None:
        errors.append(
            f"Тест «{binding_id}» проходить процес «{binding['process']}», а це процес «{annotations['process']}»"
        )
    else:
        walked = walk_path(graph, binding.get("path"), library)
        errors += walked["errors"]
        warnings += walked["warnings"]
        passed = set(walked["phases"])
        visited = {"nodes": walked["nodes"], "flows": walked["flows"]}
        absent = [phase for phase in expected if "." not in phase and phase not in phases]
        outside = [phase for phase in expected if phase not in absent and phase not in passed]
        undeclared = sorted(passed - set(expected))
        duplicated = sorted(phase for phase, elements in phases.items() if len(elements) > 1)
        if absent:
            errors.append(f"Фази тесту без елемента BPMN: {', '.join(absent)}")
        if outside:
            errors.append(f"Фази тесту поза обраним шляхом: {', '.join(outside)}")
        if duplicated:
            errors.append(f"Фаза позначена на кількох елементах: {', '.join(duplicated)}")
        if annotations["misplaced"]:
            errors.append(
                "Фазу можна позначати лише на задачі або проміжній події очікування: "
                + ", ".join(annotations["misplaced"])
            )
        if undeclared:
            warnings.append(f"Фази шляху, яких тест не перевіряє: {', '.join(undeclared)}")
        on_path = set(visited["nodes"])
        untested = [
            node["id"]
            for node in graph["nodes"].values()
            if node["id"] in on_path and node["tag"] in ACTIVITY_TAGS and not node["phase"]
        ]
        if untested:
            warnings.append(f"Задачі шляху, не покриті тестом: {', '.join(untested)}")
    return {
        "binding": binding_id,
        "title": binding.get("title", binding_id),
        "mode": "path",
        "process": binding["process"],
        "phases": [{"phase": phase, "element": (phases.get(phase) or [None])[0]} for phase in expected],
        "covered": not errors,
        "errors": errors,
        "warnings": warnings,
        "visited": visited,
    }


def workflow_coverage(
    bpmn_xml: str, bindings: dict[str, dict[str, Any]], library: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Coverage of a process by all the tests bound to it; a single one-to-one test keeps its own shape.

    With tests that follow paths, the result also lists what no test passes (``gaps``).
    """
    ids = process_bindings(bpmn_xml, bindings)
    if not ids:
        return None
    items = [phase_coverage(bpmn_xml, bindings, binding_id, library) for binding_id in ids]
    if len(items) == 1 and items[0].get("mode") != "path":
        return items[0]
    annotations = read_test_annotations(bpmn_xml)
    graph = parse_graph(bpmn_xml, annotations["process"])
    nodes = {key for item in items for key in item.get("visited", {}).get("nodes", [])}
    flows = {key for item in items for key in item.get("visited", {}).get("flows", [])}
    missed = gaps(graph, nodes, flows) if graph else {"tasks": [], "branches": []}
    errors = [f"{item['binding']}: {text}" for item in items for text in item["errors"]]
    warnings = [f"{item['binding']}: {text}" for item in items for text in item["warnings"]]
    if missed["tasks"]:
        warnings.append(f"Задачі, яких не проходить жоден тест: {', '.join(missed['tasks'])}")
    if missed["branches"]:
        warnings.append(f"Гілки гейтвеїв без тесту: {', '.join(missed['branches'])}")
    return {
        "binding": ids[0] if len(ids) == 1 else None,
        "title": items[0]["title"] if len(items) == 1 else annotations["process"],
        "mode": "process",
        "phases": items[0]["phases"] if len(items) == 1 else [],
        "covered": all(item["covered"] for item in items),
        "errors": errors,
        "warnings": warnings,
        "tests": items,
        "gaps": missed,
    }
