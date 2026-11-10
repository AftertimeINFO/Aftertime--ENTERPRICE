"""Paths through a BPMN process: which activities and gateway branches a test passes.

A test follows one path of a shared process.  ``decisions`` maps a gateway to the branch the
test takes there (a sequence flow id or the id of the node it leads to).  Gateways and phases of
a process called through ``callActivity`` are addressed as ``<called process id>.<element id>``.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from xml.etree import ElementTree

from app.services.bpmn_profile import AFTERTIME_NS, BPMN_NS

BRANCHING = {"exclusiveGateway", "inclusiveGateway", "eventBasedGateway"}
ACTIVITIES = {
    "task",
    "serviceTask",
    "userTask",
    "manualTask",
    "scriptTask",
    "businessRuleTask",
    "sendTask",
    "receiveTask",
}
CONTAINERS = {"callActivity", "subProcess"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def process_id(bpmn_xml: str) -> str | None:
    """Id of the first process of the document."""
    root = ElementTree.fromstring(bpmn_xml)
    return next((item.get("id") for item in root.iter(f"{{{BPMN_NS}}}process")), None)


def parse_graph(bpmn_xml: str, wanted: str | None = None) -> dict[str, Any] | None:
    """Nodes and sequence flows of one process (the first one, or ``wanted``)."""
    root = ElementTree.fromstring(bpmn_xml)
    process = next(
        (item for item in root.iter(f"{{{BPMN_NS}}}process") if wanted in (None, item.get("id"))), None
    )
    if process is None:
        return None
    nodes: dict[str, dict[str, Any]] = {}
    flows: dict[str, dict[str, Any]] = {}
    outgoing: dict[str, list[dict[str, Any]]] = {}

    def collect(parent: ElementTree.Element, owner: str | None) -> None:
        for child in parent:
            if not child.tag.startswith(f"{{{BPMN_NS}}}"):
                continue
            tag = _local(child.tag)
            if tag == "sequenceFlow":
                flow = {"id": child.get("id"), "source": child.get("sourceRef"), "target": child.get("targetRef")}
                flows[flow["id"]] = flow
                outgoing.setdefault(flow["source"], []).append(flow)
            elif tag != "boundaryEvent" and (
                tag in ACTIVITIES or tag in CONTAINERS or tag.endswith("Gateway") or tag.endswith("Event")
            ):
                nodes[child.get("id")] = {
                    "id": child.get("id"),
                    "tag": tag,
                    "phase": child.get(f"{{{AFTERTIME_NS}}}testPhase"),
                    "called": child.get("calledElement"),
                    "owner": owner,
                }
                if tag == "subProcess":
                    collect(child, child.get("id"))

    collect(process, None)
    return {"id": process.get("id"), "nodes": nodes, "flows": flows, "out": outgoing}


def _choose(
    gateway: str, flows: list[dict[str, Any]], decisions: dict[str, Any], used: set[str], errors: list[str]
) -> list[dict[str, Any]]:
    if gateway not in decisions:
        errors.append(f"Для гейтвея «{gateway}» не задано гілку")
        return []
    used.add(gateway)
    wanted = decisions[gateway]
    wanted = list(wanted) if isinstance(wanted, (list, tuple)) else [wanted]
    chosen: list[dict[str, Any]] = []
    for item in wanted:
        flow = next((f for f in flows if item in (f["id"], f["target"])), None)
        if flow is None:
            errors.append(f"Гейтвей «{gateway}» не має гілки «{item}»")
        elif flow not in chosen:
            chosen.append(flow)
    return chosen


def _walk_scope(
    graph: dict[str, Any],
    scope: str | None,
    decisions: dict[str, Any],
    library: dict[str, str],
    prefix: str,
    active: tuple[str, ...],
    result: dict[str, Any],
    used: set[str],
) -> None:
    seen: set[str] = set()
    queue = deque(
        node for node in graph["nodes"].values() if node["tag"] == "startEvent" and node["owner"] == scope
    )
    while queue:
        node = queue.popleft()
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        result["nodes"].append(prefix + node["id"])
        if node["phase"]:
            result["phases"].append(prefix + node["phase"])
        if node["tag"] == "subProcess":
            _walk_scope(graph, node["id"], decisions, library, prefix, active, result, used)
        elif node["tag"] == "callActivity" and node["called"]:
            called, xml = node["called"], library.get(node["called"])
            if called in active:
                result["errors"].append(f"Підпроцес «{called}» викликає сам себе")
            elif xml is None:
                result["warnings"].append(f"Викликаного процесу «{called}» не знайдено серед опублікованих")
            else:
                inner = parse_graph(xml, called)
                if inner is not None:
                    _walk_scope(inner, None, decisions, library, f"{called}.", active + (called,), result, used)
        flows = graph["out"].get(node["id"], [])
        if node["tag"] in BRANCHING and len(flows) > 1:
            flows = _choose(prefix + node["id"], flows, decisions, used, result["errors"])
        for flow in flows:
            result["flows"].append(prefix + flow["id"])
            target = graph["nodes"].get(flow["target"])
            if target is not None:
                queue.append(target)


def walk_path(
    graph: dict[str, Any], decisions: dict[str, Any] | None = None, library: dict[str, str] | None = None
) -> dict[str, Any]:
    """Elements a test passes: ``nodes``, ``flows``, ``phases``, plus ``errors`` and ``warnings``."""
    decisions = dict(decisions or {})
    result: dict[str, Any] = {"nodes": [], "flows": [], "phases": [], "errors": [], "warnings": []}
    used: set[str] = set()
    _walk_scope(graph, None, decisions, library or {}, "", (graph["id"],), result, used)
    unused = sorted(set(decisions) - used)
    if unused:
        result["errors"].append(f"Вибір гілки не стосується пройденого шляху: {', '.join(unused)}")
    return result


def gaps(graph: dict[str, Any], visited_nodes: set[str], visited_flows: set[str]) -> dict[str, list[str]]:
    """Activities and gateway branches of the process that none of the paths passes."""
    tasks = [
        node["id"]
        for node in graph["nodes"].values()
        if (node["tag"] in ACTIVITIES) and node["id"] not in visited_nodes
    ]
    branches = [
        flow["id"]
        for node in graph["nodes"].values()
        if node["tag"] in BRANCHING and len(graph["out"].get(node["id"], [])) > 1
        for flow in graph["out"][node["id"]]
        if flow["id"] not in visited_flows
    ]
    return {"tasks": tasks, "branches": branches}
