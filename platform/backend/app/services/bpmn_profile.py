"""Aftertime's executable BPMN 2.0 profile.

The BPMN document is canonical.  The relational workflow nodes and transitions are
an execution projection retained for the simulator and the existing API surface.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape
import re
from typing import Any, Iterable
from xml.etree import ElementTree


BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
AFTERTIME_NS = "https://aftertime.local/bpmn"

NS = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS, "dc": DC_NS, "di": DI_NS}

ElementTree.register_namespace("bpmn", BPMN_NS)
ElementTree.register_namespace("bpmndi", BPMNDI_NS)
ElementTree.register_namespace("dc", DC_NS)
ElementTree.register_namespace("di", DI_NS)
ElementTree.register_namespace("aftertime", AFTERTIME_NS)
ElementTree.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

START_DEFINITIONS = {
    "message": "messageEventDefinition",
    "timer": "timerEventDefinition",
    "conditional": "conditionalEventDefinition",
    "signal": "signalEventDefinition",
}
END_DEFINITIONS = {
    "message": "messageEventDefinition",
    "signal": "signalEventDefinition",
    "error": "errorEventDefinition",
    "escalation": "escalationEventDefinition",
    "terminate": "terminateEventDefinition",
}
INTERMEDIATE_CATCH_DEFINITIONS = {
    "message": "messageEventDefinition",
    "timer": "timerEventDefinition",
    "conditional": "conditionalEventDefinition",
    "signal": "signalEventDefinition",
}
INTERMEDIATE_THROW_DEFINITIONS = {
    "message": "messageEventDefinition",
    "signal": "signalEventDefinition",
    "escalation": "escalationEventDefinition",
}

FLOW_NODE_TAGS = {
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "boundaryEvent",
    "task",
    "serviceTask",
    "userTask",
    "manualTask",
    "scriptTask",
    "businessRuleTask",
    "sendTask",
    "receiveTask",
    "callActivity",
    "subProcess",
    "exclusiveGateway",
    "inclusiveGateway",
    "parallelGateway",
    "eventBasedGateway",
}

TAG_TO_BLOCK = {
    "startEvent": "start_event",
    "endEvent": "end_event",
    "intermediateCatchEvent": "intermediate_catch_event",
    "intermediateThrowEvent": "intermediate_throw_event",
    "boundaryEvent": "boundary_event",
    "task": "task",
    "serviceTask": "service_task",
    "userTask": "user_task",
    "manualTask": "manual_task",
    "scriptTask": "script_task",
    "businessRuleTask": "business_rule_task",
    "sendTask": "send_task",
    "receiveTask": "receive_task",
    "callActivity": "call_activity",
    "subProcess": "sub_process",
    "exclusiveGateway": "exclusive_gateway",
    "inclusiveGateway": "inclusive_gateway",
    "parallelGateway": "parallel_gateway",
    "eventBasedGateway": "event_based_gateway",
}

LEGACY_TO_TAG = {
    "start": "startEvent",
    "start_event": "startEvent",
    "event_trigger": "intermediateCatchEvent",
    "intermediate_catch_event": "intermediateCatchEvent",
    "intermediate_throw_event": "intermediateThrowEvent",
    "timer": "intermediateCatchEvent",
    "wait": "intermediateCatchEvent",
    "boundary_event": "boundaryEvent",
    "component_command": "serviceTask",
    "resource_operation": "serviceTask",
    "python_action": "scriptTask",
    "metric_check": "businessRuleTask",
    "manual_command": "manualTask",
    "task": "task",
    "service_task": "serviceTask",
    "user_task": "userTask",
    "manual_task": "manualTask",
    "script_task": "scriptTask",
    "business_rule_task": "businessRuleTask",
    "send_task": "sendTask",
    "receive_task": "receiveTask",
    "call_activity": "callActivity",
    "sub_process": "subProcess",
    "condition": "exclusiveGateway",
    "exclusive_gateway": "exclusiveGateway",
    "inclusive_gateway": "inclusiveGateway",
    "parallel_split": "parallelGateway",
    "join": "parallelGateway",
    "parallel_gateway": "parallelGateway",
    "event_based_gateway": "eventBasedGateway",
    "success": "endEvent",
    "failure": "endEvent",
    "end_event": "endEvent",
}


TEST_PHASE_PREFIX = "test.phase."


def phase_capability(phase: str) -> str:
    return f"{TEST_PHASE_PREFIX}{phase}"


@dataclass(frozen=True)
class BpmnProjection:
    nodes: list[dict[str, Any]]
    transitions: list[dict[str, Any]]
    errors: list[str]
    warnings: list[str]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _event_definition(element: ElementTree.Element) -> str:
    event_definitions = [child for child in element if _local(child.tag).endswith("EventDefinition")]
    if element.get("parallelMultiple") == "true":
        return "parallel_multiple"
    if len(event_definitions) > 1:
        return "multiple"
    for child in element:
        tag = _local(child.tag)
        for kind, definition in {
            **START_DEFINITIONS,
            **END_DEFINITIONS,
            **INTERMEDIATE_CATCH_DEFINITIONS,
            **INTERMEDIATE_THROW_DEFINITIONS,
        }.items():
            if tag == definition:
                return kind
    return "none"


def _condition_text(flow: ElementTree.Element) -> str:
    expression = flow.find("bpmn:conditionExpression", NS)
    return (expression.text or "").strip() if expression is not None else ""


def parse_bpmn(xml: str) -> BpmnProjection:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, TypeError) as exc:
        return BpmnProjection([], [], [f"Некоректний BPMN XML: {exc}"], [])
    if _local(root.tag) != "definitions" or root.tag != f"{{{BPMN_NS}}}definitions":
        return BpmnProjection([], [], ["Кореневим елементом має бути bpmn:definitions"], [])
    processes = root.findall("bpmn:process", NS)
    if not processes:
        errors.append("BPMN-документ не містить process")
        return BpmnProjection([], [], errors, warnings)

    definition_names = {
        element.get("id"): element.get("name")
        for element in root
        if _local(element.tag) in {"message", "signal", "error", "escalation"}
        and element.get("id")
    }

    bounds: dict[str, tuple[float, float]] = {}
    for shape in root.findall(".//bpmndi:BPMNShape", NS):
        ref = shape.get("bpmnElement")
        box = shape.find("dc:Bounds", NS)
        if ref and box is not None:
            try:
                bounds[ref] = (float(box.get("x", "0")), float(box.get("y", "0")))
            except ValueError:
                warnings.append(f"Елемент {ref} має некоректні BPMN DI координати")

    nodes: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for process in processes:
        process_id = process.get("id") or "Process"
        parent_map = {child: parent for parent in process.iter() for child in parent}
        for element in process.iter():
            tag = _local(element.tag)
            if tag not in FLOW_NODE_TAGS:
                continue
            node_id = element.get("id")
            if not node_id:
                errors.append(f"{tag} у процесі {process_id} не має id")
                continue
            if node_id in node_ids:
                errors.append(f"Дубльований BPMN id: {node_id}")
                continue
            node_ids.add(node_id)
            event_definition = _event_definition(element)
            x, y = bounds.get(node_id, (0.0, 0.0))
            config: dict[str, Any] = {
                "process_id": process_id,
                "event_definition": event_definition,
            }
            parent = parent_map.get(element)
            while parent is not None and parent is not process:
                if _local(parent.tag) == "subProcess":
                    config["parent_subprocess"] = parent.get("id")
                    break
                parent = parent_map.get(parent)
            event_ref = element.get(f"{{{AFTERTIME_NS}}}eventRef")
            if not event_ref:
                definition_element = next((child for child in element if _local(child.tag).endswith("EventDefinition")), None)
                if definition_element is not None:
                    referenced_id = next((definition_element.get(attribute) for attribute in ("messageRef", "signalRef", "errorRef", "escalationRef") if definition_element.get(attribute)), None)
                    event_ref = definition_names.get(referenced_id) or referenced_id
            if event_ref:
                config["event_type"] = event_ref
            timer_definition = next((child for child in element if _local(child.tag) == "timerEventDefinition"), None)
            if timer_definition is not None:
                timer_value = next((child for child in timer_definition if _local(child.tag) in {"timeDate", "timeDuration", "timeCycle"}), None)
                if timer_value is not None:
                    config["timer_kind"] = _local(timer_value.tag)
                    config["timer_value"] = (timer_value.text or "").strip()
                    config.setdefault("event_type", f"timer.{node_id}")
            conditional_definition = next((child for child in element if _local(child.tag) == "conditionalEventDefinition"), None)
            if conditional_definition is not None:
                condition = next((child for child in conditional_definition if _local(child.tag) == "condition"), None)
                if condition is not None:
                    config["condition"] = (condition.text or "").strip()
            target_ref = element.get(f"{{{AFTERTIME_NS}}}targetRef")
            capability = element.get(f"{{{AFTERTIME_NS}}}capability")
            test_phase = element.get(f"{{{AFTERTIME_NS}}}testPhase")
            if test_phase:
                # An element bound to a test phase waits for that phase in a mirror run.
                config["test_phase"] = test_phase
                capability = capability or phase_capability(test_phase)
            if target_ref:
                config["target_ref"] = target_ref
            if capability:
                config["capability"] = capability
            if tag == "boundaryEvent":
                config["attached_to_ref"] = element.get("attachedToRef")
                config["cancel_activity"] = element.get("cancelActivity", "true") != "false"
            if tag == "callActivity":
                config["called_element"] = element.get("calledElement")
            if tag == "subProcess":
                config["triggered_by_event"] = element.get("triggeredByEvent") == "true"
            if tag == "startEvent" and config.get("parent_subprocess"):
                config["is_interrupting"] = element.get("isInterrupting", "true") != "false"
            nodes.append(
                {
                    "node_key": node_id,
                    "block_type": TAG_TO_BLOCK[tag],
                    "name": element.get("name") or node_id,
                    "x": x,
                    "y": y,
                    "config": config,
                    "python_code": (element.findtext("bpmn:script", default="", namespaces=NS) or ""),
                }
            )
        for flow in process.findall(".//bpmn:sequenceFlow", NS):
            flow_id = flow.get("id")
            source = flow.get("sourceRef")
            target = flow.get("targetRef")
            if not flow_id or not source or not target:
                errors.append(f"Sequence Flow у процесі {process_id} має неповні посилання")
                continue
            transitions.append(
                {
                    "flow_key": flow_id,
                    "source_key": source,
                    "target_key": target,
                    "condition_code": _condition_text(flow) or "True",
                    "is_default": False,
                    "flow_type": "sequence",
                    "priority": 100,
                }
            )
    defaults = {
        element.get("default")
        for process in processes
        for element in process.iter()
        if element.get("default")
    }
    for transition in transitions:
        transition["is_default"] = transition["flow_key"] in defaults
    return BpmnProjection(nodes, transitions, errors, warnings)


def validate_bpmn(xml: str) -> dict[str, Any]:
    projection = parse_bpmn(xml)
    errors = list(projection.errors)
    warnings = list(projection.warnings)
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "nodes": 0, "transitions": 0}

    nodes = {node["node_key"]: node for node in projection.nodes}
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for flow in projection.transitions:
        source = flow["source_key"]
        target = flow["target_key"]
        if source not in nodes or target not in nodes:
            errors.append(f"Sequence Flow {flow['flow_key']} посилається на невідомий елемент")
            continue
        if nodes[source]["config"]["process_id"] != nodes[target]["config"]["process_id"]:
            errors.append(f"Sequence Flow {flow['flow_key']} не може перетинати межу process/pool")
        outgoing[source].append(flow)
        incoming[target].append(flow)

    starts = [node for node in nodes.values() if node["block_type"] == "start_event"]
    top_level_starts = [node for node in starts if not node["config"].get("parent_subprocess")]
    ends = [node for node in nodes.values() if node["block_type"] == "end_event"]
    if not top_level_starts:
        errors.append("Потрібен щонайменше один Start Event")
    if not ends:
        errors.append("Потрібен щонайменше один явний End Event")
    for start in starts:
        if incoming[start["node_key"]]:
            errors.append(f"Start Event «{start['name']}» не може мати вхідний Sequence Flow")
        definition = start["config"].get("event_definition", "none")
        if definition not in {"none", "multiple", "parallel_multiple", *START_DEFINITIONS}:
            errors.append(f"Start Event «{start['name']}» має недопустимий тригер {definition}")
        if definition != "none" and not start["config"].get("event_type"):
            errors.append(f"Start Event «{start['name']}» не має налаштованого тригера")
    for end in ends:
        if outgoing[end["node_key"]]:
            errors.append(f"End Event «{end['name']}» не може мати вихідний Sequence Flow")
        definition = end["config"].get("event_definition", "none")
        if definition not in {"none", "multiple", *END_DEFINITIONS}:
            errors.append(f"End Event «{end['name']}» має недопустимий результат {definition}")

    reachable: set[str] = set()
    queue = deque(start["node_key"] for start in starts)
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(flow["target_key"] for flow in outgoing[current])
    for node in nodes.values():
        if node["block_type"] == "boundary_event" and node["config"].get("attached_to_ref") in reachable:
            reachable.add(node["node_key"])
    unreachable = sorted(set(nodes) - reachable)
    if unreachable:
        errors.append(f"Недосяжні BPMN-елементи: {', '.join(unreachable)}")

    can_reach_end = {end["node_key"] for end in ends}
    queue = deque(can_reach_end)
    while queue:
        current = queue.popleft()
        for flow in incoming[current]:
            if flow["source_key"] not in can_reach_end:
                can_reach_end.add(flow["source_key"])
                queue.append(flow["source_key"])
    dead_paths = sorted(reachable - can_reach_end)
    if dead_paths:
        errors.append(f"Шляхи без досяжного End Event: {', '.join(dead_paths)}")

    for node in nodes.values():
        node_id = node["node_key"]
        block = node["block_type"]
        if block in {"exclusive_gateway", "inclusive_gateway"} and len(outgoing[node_id]) > 1:
            conditional = [flow for flow in outgoing[node_id] if flow["condition_code"] != "True"]
            defaults = [flow for flow in outgoing[node_id] if flow["is_default"]]
            if len(conditional) + len(defaults) != len(outgoing[node_id]):
                errors.append(f"Gateway «{node['name']}» має гілку без умови або default")
            if len(defaults) > 1:
                errors.append(f"Gateway «{node['name']}» має більше одного default flow")
        if block == "boundary_event":
            attached = node["config"].get("attached_to_ref")
            if attached not in nodes:
                errors.append(f"Boundary Event «{node['name']}» не прикріплено до Activity")
        if block == "call_activity" and not node["config"].get("called_element"):
            errors.append(f"Call Activity «{node['name']}» не має calledElement")

    root = ElementTree.fromstring(xml)
    participants = {
        item.get("id"): item.get("processRef")
        for item in root.findall(".//bpmn:participant", NS)
        if item.get("id")
    }
    known_refs = set(nodes) | set(participants)
    process_by_ref = {
        **{node_id: node["config"].get("process_id") for node_id, node in nodes.items()},
        **participants,
    }
    for flow in root.findall(".//bpmn:messageFlow", NS):
        source = flow.get("sourceRef")
        target = flow.get("targetRef")
        if source not in known_refs or target not in known_refs:
            errors.append(f"Message Flow {flow.get('id', '')} посилається на невідомого учасника або вузол")
        elif process_by_ref.get(source) == process_by_ref.get(target):
            errors.append(f"Message Flow {flow.get('id', '')} не може з’єднувати елементи одного pool")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "nodes": len(nodes),
        "transitions": len(projection.transitions),
        "profile": "aftertime-executable-1",
    }


def _safe_id(value: str) -> str:
    result = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)
    return result if result and (result[0].isalpha() or result[0] == "_") else f"id_{result}"


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def meaningful_end_name(process_name: str, predecessor_name: str | None) -> str:
    """Turn the last business action into an explicit observable process outcome."""
    text = (predecessor_name or "").strip().rstrip(". ")
    rules = [
        (r"(?i)^restore\s+(.+)$", lambda match: f"{match.group(1)} restored"),
        (r"(?i)^re-?energize\s+(.+)$", lambda match: f"{match.group(1)} re-energized"),
        (r"(?i)^reconnect\s+(.+)$", lambda match: f"{match.group(1)} reconnected"),
        (r"(?i)^stabilize\s+(.+)$", lambda match: f"{match.group(1)} stabilized"),
        (r"(?i)^complete\s+(.+)$", lambda match: f"{match.group(1)} completed"),
        (r"(?i)^confirm\s+(.+)$", lambda match: f"{match.group(1)} confirmed"),
        (r"(?i)^record\s+(.+)$", lambda match: f"{match.group(1)} recorded"),
        (r"(?i)^відновити\s+(.+)$", lambda match: f"{match.group(1)} відновлено"),
        (r"(?i)^виконати\s+(.+)$", lambda match: f"{match.group(1)} виконано"),
        (r"(?i)^завершити\s+(.+)$", lambda match: f"{match.group(1)} завершено"),
        (r"(?i)^підтвердити\s+(.+)$", lambda match: f"{match.group(1)} підтверджено"),
    ]
    for pattern, render in rules:
        match = re.match(pattern, text)
        if match:
            result = render(match)
            return result[0].upper() + result[1:]
    if text:
        return f"{text} — completed"
    return f"{process_name.strip()} — completed"


def _shape_size(tag: str) -> tuple[float, float]:
    if tag.endswith("Event"):
        return 36, 36
    if tag.endswith("Gateway"):
        return 50, 50
    return 150, 80


def _boundary_waypoints(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Connect shape borders, never their centers, for a clean BPMN Sequence Flow."""
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    source_center = (sx + sw / 2, sy + sh / 2)
    target_center = (tx + tw / 2, ty + th / 2)
    dx = target_center[0] - source_center[0]
    dy = target_center[1] - source_center[1]
    if abs(dx) >= abs(dy):
        return (
            (sx + sw if dx >= 0 else sx, source_center[1]),
            (tx if dx >= 0 else tx + tw, target_center[1]),
        )
    return (
        (source_center[0], sy + sh if dy >= 0 else sy),
        (target_center[0], ty if dy >= 0 else ty + th),
    )


def repair_bpmn_presentation(xml: str) -> tuple[str, dict[str, str]]:
    """Repair legacy DI center-to-center flows and generic End Event labels in-place."""
    root = ElementTree.fromstring(xml)
    elements = {
        item.get("id"): item
        for item in root.iter()
        if item.get("id")
    }
    predecessors = {
        flow.get("targetRef"): flow.get("sourceRef")
        for flow in root.findall(".//bpmn:sequenceFlow", NS)
    }
    renamed: dict[str, str] = {}
    generic_names = {"", "End", "Process completed", "Процес завершено"}
    for process in root.findall(".//bpmn:process", NS):
        process_name = process.get("name") or process.get("id") or "Process"
        for end in process.findall(".//bpmn:endEvent", NS):
            if (end.get("name") or "").strip() not in generic_names:
                continue
            predecessor = elements.get(predecessors.get(end.get("id"), ""))
            name = meaningful_end_name(
                process_name, predecessor.get("name") if predecessor is not None else None
            )
            end.set("name", name)
            if end.get("id"):
                renamed[end.get("id")] = name

    geometry: dict[str, tuple[float, float, float, float]] = {}
    for shape in root.findall(".//bpmndi:BPMNShape", NS):
        bounds = shape.find("dc:Bounds", NS)
        reference = shape.get("bpmnElement")
        if bounds is None or not reference:
            continue
        geometry[reference] = tuple(
            float(bounds.get(key, "0")) for key in ("x", "y", "width", "height")
        )
    flows = {
        flow.get("id"): flow
        for flow in root.findall(".//bpmn:sequenceFlow", NS)
        if flow.get("id")
    }
    for edge in root.findall(".//bpmndi:BPMNEdge", NS):
        flow = flows.get(edge.get("bpmnElement"))
        if flow is None:
            continue
        source = geometry.get(flow.get("sourceRef", ""))
        target = geometry.get(flow.get("targetRef", ""))
        if source is None or target is None:
            continue
        for waypoint in list(edge.findall("di:waypoint", NS)):
            edge.remove(waypoint)
        (sx, sy), (tx, ty) = _boundary_waypoints(source, target)
        edge.insert(0, ElementTree.Element(f"{{{DI_NS}}}waypoint", {"x": f"{sx:g}", "y": f"{sy:g}"}))
        edge.insert(1, ElementTree.Element(f"{{{DI_NS}}}waypoint", {"x": f"{tx:g}", "y": f"{ty:g}"}))
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True), renamed


def _event_xml(kind: str, event_type: str | None = None, unique_id: str = "Event", config: dict[str, Any] | None = None) -> str:
    if kind == "none":
        return ""
    if kind in {"multiple", "parallel_multiple"}:
        base = _safe_id(unique_id)
        return (
            f'<bpmn:messageEventDefinition id="Definition_{base}_message" />'
            f'<bpmn:signalEventDefinition id="Definition_{base}_signal" />'
        )
    definition = {**START_DEFINITIONS, **END_DEFINITIONS, **INTERMEDIATE_CATCH_DEFINITIONS, **INTERMEDIATE_THROW_DEFINITIONS}.get(kind)
    if not definition:
        return ""
    ref = ""
    if kind in {"message", "signal", "error", "escalation"} and event_type:
        attr = {"message": "messageRef", "signal": "signalRef", "error": "errorRef", "escalation": "escalationRef"}[kind]
        prefix = {"message": "Message", "signal": "Signal", "error": "Error", "escalation": "Escalation"}[kind]
        ref = f' {attr}="{prefix}_{_safe_id(event_type)}"'
    content = ""
    config = config or {}
    if kind == "timer" and config.get("timer_value"):
        timer_kind = config.get("timer_kind", "timeDuration")
        if timer_kind not in {"timeDate", "timeDuration", "timeCycle"}:
            timer_kind = "timeDuration"
        content = f'<bpmn:{timer_kind} xsi:type="bpmn:tFormalExpression">{escape(str(config["timer_value"]))}</bpmn:{timer_kind}>'
    elif kind == "conditional" and config.get("condition"):
        content = f'<bpmn:condition xsi:type="bpmn:tFormalExpression">{escape(str(config["condition"]))}</bpmn:condition>'
    return f'<bpmn:{definition} id="Definition_{_safe_id(unique_id)}"{ref}>{content}</bpmn:{definition}>'


def build_bpmn_xml(
    process_id: str,
    process_name: str,
    nodes: Iterable[dict[str, Any]],
    transitions: Iterable[dict[str, Any]],
) -> str:
    node_rows = list(nodes)
    flow_rows = list(transitions)
    process_id = _safe_id(process_id)
    refs: dict[tuple[str, str], None] = {}
    node_xml: list[str] = []
    shape_xml: list[str] = []
    geometry: dict[str, tuple[float, float, float, float]] = {}
    for index, node in enumerate(node_rows):
        key = _safe_id(str(_value(node, "node_key") or _value(node, "id") or f"Node_{index}"))
        block = str(_value(node, "block_type") or "task")
        tag = LEGACY_TO_TAG.get(block, "task")
        config = dict(_value(node, "config") or {})
        name = escape(str(_value(node, "name") or key), quote=True)
        definition = str(config.get("event_definition") or config.get("start_mode") or ("error" if block == "failure" else "none"))
        event_type = config.get("event_type")
        if definition in {"message", "signal", "error", "escalation"} and event_type:
            refs[(definition, str(event_type))] = None
        attrs = [f'id="{key}"', f'name="{name}"']
        if event_type:
            attrs.append(f'aftertime:eventRef="{escape(str(event_type), quote=True)}"')
        if config.get("target_ref"):
            attrs.append(f'aftertime:targetRef="{escape(str(config["target_ref"]), quote=True)}"')
        if config.get("capability"):
            attrs.append(f'aftertime:capability="{escape(str(config["capability"]), quote=True)}"')
        if tag == "boundaryEvent":
            attrs.append(f'attachedToRef="{_safe_id(str(config.get("attached_to_ref") or "missing"))}"')
            attrs.append(f'cancelActivity="{str(bool(config.get("cancel_activity", True))).lower()}"')
        if tag == "callActivity" and config.get("called_element"):
            attrs.append(f'calledElement="{_safe_id(str(config["called_element"]))}"')
        if tag == "subProcess" and config.get("triggered_by_event"):
            attrs.append('triggeredByEvent="true"')
        if tag in {"startEvent", "intermediateCatchEvent"} and definition == "parallel_multiple":
            attrs.append('parallelMultiple="true"')
        body = ""
        if tag in {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}:
            body = _event_xml(definition, str(event_type) if event_type else None, key, config)
        if tag == "scriptTask" and _value(node, "python_code"):
            body += f'<bpmn:script>{escape(str(_value(node, "python_code")))}</bpmn:script>'
        node_xml.append(f'<bpmn:{tag} {" ".join(attrs)}>{body}</bpmn:{tag}>')
        width, height = _shape_size(tag)
        x = float(_value(node, "x", 80 + index * 190))
        y = float(_value(node, "y", 120))
        geometry[key] = (x, y, width, height)
        shape_xml.append(f'<bpmndi:BPMNShape id="Shape_{key}" bpmnElement="{key}"><dc:Bounds x="{x:g}" y="{y:g}" width="{width}" height="{height}" /></bpmndi:BPMNShape>')

    flow_xml: list[str] = []
    edge_xml: list[str] = []
    node_by_id = {str(_value(node, "id")): _safe_id(str(_value(node, "node_key") or _value(node, "id"))) for node in node_rows}
    node_by_key = {str(_value(node, "node_key") or _value(node, "id")): _safe_id(str(_value(node, "node_key") or _value(node, "id"))) for node in node_rows}
    for index, flow in enumerate(flow_rows, 1):
        flow_id = _safe_id(str(_value(flow, "flow_key") or _value(flow, "id") or f"Flow_{index}"))
        source_raw = str(_value(flow, "source_key") or _value(flow, "source_node_id") or "")
        target_raw = str(_value(flow, "target_key") or _value(flow, "target_node_id") or "")
        source = node_by_id.get(source_raw, node_by_key.get(source_raw, _safe_id(source_raw)))
        target = node_by_id.get(target_raw, node_by_key.get(target_raw, _safe_id(target_raw)))
        condition = str(_value(flow, "condition_code") or "True")
        body = "" if condition == "True" else f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{escape(condition)}</bpmn:conditionExpression>'
        flow_xml.append(f'<bpmn:sequenceFlow id="{flow_id}" sourceRef="{source}" targetRef="{target}">{body}</bpmn:sequenceFlow>')
        (sx, sy), (tx, ty) = _boundary_waypoints(
            geometry.get(source, (0, 0, 150, 80)),
            geometry.get(target, (0, 0, 150, 80)),
        )
        edge_xml.append(f'<bpmndi:BPMNEdge id="Edge_{flow_id}" bpmnElement="{flow_id}"><di:waypoint x="{sx:g}" y="{sy:g}" /><di:waypoint x="{tx:g}" y="{ty:g}" /></bpmndi:BPMNEdge>')

    definitions: list[str] = []
    for (kind, ref_name) in refs:
        tag = {"message": "message", "signal": "signal", "error": "error", "escalation": "escalation"}[kind]
        prefix = tag.capitalize()
        definitions.append(f'<bpmn:{tag} id="{prefix}_{_safe_id(ref_name)}" name="{escape(ref_name, quote=True)}" />')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bpmn="{BPMN_NS}" '
        f'xmlns:bpmndi="{BPMNDI_NS}" xmlns:dc="{DC_NS}" xmlns:di="{DI_NS}" xmlns:aftertime="{AFTERTIME_NS}" '
        f'id="Definitions_{process_id}" targetNamespace="{AFTERTIME_NS}">'
        f'{"".join(definitions)}<bpmn:process id="{process_id}" name="{escape(process_name, quote=True)}" isExecutable="true">'
        f'{"".join(node_xml)}{"".join(flow_xml)}</bpmn:process>'
        f'<bpmndi:BPMNDiagram id="Diagram_{process_id}"><bpmndi:BPMNPlane id="Plane_{process_id}" bpmnElement="{process_id}">'
        f'{"".join(shape_xml)}{"".join(edge_xml)}</bpmndi:BPMNPlane></bpmndi:BPMNDiagram></bpmn:definitions>'
    )


def build_collaboration_xml(
    collaboration_id: str,
    participants: Iterable[dict[str, Any]],
    messages: Iterable[dict[str, Any]],
) -> str:
    """Build a standards-based black-box collaboration overview."""
    participant_rows = list(participants)
    message_rows = list(messages)
    collaboration_id = _safe_id(collaboration_id)
    processes: list[str] = []
    participant_xml: list[str] = []
    shapes: list[str] = []
    positions: dict[str, tuple[float, float]] = {}
    for index, participant in enumerate(participant_rows):
        alias = _safe_id(str(participant["id"]))
        process_id = f"Process_{alias}"
        participant_id = f"Participant_{alias}"
        name = escape(str(participant.get("name") or participant["id"]), quote=True)
        processes.append(f'<bpmn:process id="{process_id}" name="{name}" isExecutable="false" />')
        participant_xml.append(f'<bpmn:participant id="{participant_id}" name="{name}" processRef="{process_id}" />')
        x, y = 90.0, 80.0 + index * 150.0
        positions[alias] = (x, y)
        shapes.append(f'<bpmndi:BPMNShape id="Shape_{participant_id}" bpmnElement="{participant_id}" isHorizontal="true"><dc:Bounds x="{x:g}" y="{y:g}" width="760" height="105" /></bpmndi:BPMNShape>')
    message_xml: list[str] = []
    edges: list[str] = []
    for index, message in enumerate(message_rows, 1):
        source = _safe_id(str(message["source"]))
        target = _safe_id(str(message["target"]))
        if source not in positions or target not in positions or source == target:
            continue
        flow_id = f"MessageFlow_{index}"
        name = escape(str(message.get("name") or "Повідомлення"), quote=True)
        message_xml.append(f'<bpmn:messageFlow id="{flow_id}" name="{name}" sourceRef="Participant_{source}" targetRef="Participant_{target}" />')
        sx, sy = positions[source]
        tx, ty = positions[target]
        edges.append(f'<bpmndi:BPMNEdge id="Edge_{flow_id}" bpmnElement="{flow_id}"><di:waypoint x="{sx + 380:g}" y="{sy + 105:g}" /><di:waypoint x="{tx + 380:g}" y="{ty:g}" /></bpmndi:BPMNEdge>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:bpmndi="{BPMNDI_NS}" xmlns:dc="{DC_NS}" xmlns:di="{DI_NS}" id="Definitions_{collaboration_id}" targetNamespace="{AFTERTIME_NS}">'
        f'{"".join(processes)}<bpmn:collaboration id="{collaboration_id}">{"".join(participant_xml)}{"".join(message_xml)}</bpmn:collaboration>'
        f'<bpmndi:BPMNDiagram id="Diagram_{collaboration_id}"><bpmndi:BPMNPlane id="Plane_{collaboration_id}" bpmnElement="{collaboration_id}">{"".join(shapes)}{"".join(edges)}</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>'
        '</bpmn:definitions>'
    )
