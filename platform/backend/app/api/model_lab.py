import json
import math
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, func, inspect, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.qualification_cases import (
    QUALIFICATION_CASES,
    canonicalize_qualification_package,
)
from app.models import (
    Entity, LabAssetActorRole, LabCheckpoint, LabComponentTest, LabComponentType, LabDashboardDefinition,
    LabEdge, LabEventAction, LabEventProgram, LabFlowSample, LabMetricDefinition,
    LabModelVersion, LabNode,
    LabProject, LabResourcePool, LabResourceTransaction, LabRun, LabRunEvent,
    LabRunSample, LabScenario, LabScenarioEventTemplate, LabScript,
    LabScriptTestResult, LabScriptVersion, LabUserCommand, LabWorkflow,
    LabWorkflowInstance, LabWorkflowNode, LabWorkflowToken,
    LabWorkflowTransition, LabWorkflowVersion, LabWorkOrder, LabEventType,
    LabCepRule, LabMonitorTemplate, LabRunMonitorSnapshot,
    LabQualificationDefinition, LabProcessSet, LabProcessSetVersion,
    LabProcessSetWorkflow, LabScenarioAssemblyLink, LabScenarioAssemblyMember,
    LabScenarioAssemblyNodeMap, LabScenarioAssemblyVersion,
    LabScenarioProcessBinding, LabWorkflowPresentation,
)
from app.services.model_lab import (
    _apply_script_result,
    complete_manual_workflow_task,
    execute_script,
    process_next,
    replay_scenario,
    schedule_timer_start_events,
    validate_graph,
)
from app.services.model_contracts import connection_diagnostics, normalized_ports, observable_catalog, stable_key
from app.services.bpmn_profile import build_bpmn_xml, build_collaboration_xml, parse_bpmn, validate_bpmn
from app.services.bpmn_svg import SvgPresentationError, annotate_svg, bpmn_sha256
from app.services.test_bindings import load_bindings, phase_coverage
from app.services.qualification import (
    replay_qualification_package,
    validate_qualification_package,
)
from app.services.run_queue import SimulationQueueUnavailable, enqueue_simulation

router = APIRouter(prefix="/api/v1/lab")


def _workflow_rows(db: Session, version_id: str) -> tuple[list[LabWorkflowNode], list[LabWorkflowTransition]]:
    nodes = list(
        db.scalars(
            select(LabWorkflowNode).where(
                LabWorkflowNode.workflow_version_id == version_id
            )
        )
    )
    transitions = list(
        db.scalars(
            select(LabWorkflowTransition)
            .where(LabWorkflowTransition.workflow_version_id == version_id)
            .order_by(LabWorkflowTransition.priority, LabWorkflowTransition.id)
        )
    )
    return nodes, transitions


def _refresh_workflow_xml(db: Session, version: LabWorkflowVersion) -> None:
    nodes, transitions = _workflow_rows(db, version.id)
    version.bpmn_xml = build_bpmn_xml(
        version.workflow_id, version.name, nodes, transitions
    )
    version.bpmn_profile = "aftertime-executable-1"
    version.validation = validate_bpmn(version.bpmn_xml)
    version.validated_at = datetime.now(UTC)


def _replace_workflow_projection(
    db: Session, version: LabWorkflowVersion, bpmn_xml: str
) -> dict[str, Any]:
    validation = validate_bpmn(bpmn_xml)
    if not validation["valid"]:
        raise HTTPException(422, validation)
    projection = parse_bpmn(bpmn_xml)
    db.execute(
        delete(LabWorkflowTransition).where(
            LabWorkflowTransition.workflow_version_id == version.id
        )
    )
    db.execute(
        delete(LabWorkflowNode).where(
            LabWorkflowNode.workflow_version_id == version.id
        )
    )
    db.flush()
    node_ids: dict[str, str] = {}
    for spec in projection.nodes:
        node = LabWorkflowNode(
            workflow_version_id=version.id,
            node_key=spec["node_key"],
            block_type=spec["block_type"],
            name=spec["name"],
            x=spec["x"],
            y=spec["y"],
            config=spec["config"],
            python_code=spec["python_code"],
        )
        db.add(node)
        db.flush()
        node_ids[spec["node_key"]] = node.id
    for spec in projection.transitions:
        db.add(
            LabWorkflowTransition(
                workflow_version_id=version.id,
                source_node_id=node_ids[spec["source_key"]],
                target_node_id=node_ids[spec["target_key"]],
                condition_code=spec["condition_code"],
                priority=spec["priority"],
            )
        )
    version.bpmn_xml = bpmn_xml
    version.bpmn_profile = "aftertime-executable-1"
    version.validation = validation
    version.validated_at = datetime.now(UTC)
    return validation


def _presentation_status(db: Session, version: LabWorkflowVersion) -> dict[str, Any]:
    presentation = db.get(LabWorkflowPresentation, version.id)
    current = bpmn_sha256(version.bpmn_xml)
    if not presentation or not presentation.svg:
        return {"status": "missing", "current_bpmn_sha256": current}
    status = presentation.report.get("status", "synced")
    if presentation.bpmn_sha256 != current:
        status = "stale"
    return {
        "status": status,
        "current_bpmn_sha256": current,
        "bpmn_sha256": presentation.bpmn_sha256,
        "missing": presentation.report.get("missing", []),
        "updated_at": presentation.updated_at,
    }


def _store_presentation(
    db: Session, version: LabWorkflowVersion, svg: str
) -> dict[str, Any]:
    try:
        annotated, report = annotate_svg(
            svg,
            version.bpmn_xml,
            process_id=version.workflow_id,
            version_id=version.id,
            version=version.version,
        )
    except SvgPresentationError as exc:
        raise HTTPException(422, str(exc)) from exc
    presentation = db.get(LabWorkflowPresentation, version.id) or LabWorkflowPresentation(
        workflow_version_id=version.id
    )
    presentation.svg = annotated
    presentation.bpmn_sha256 = bpmn_sha256(version.bpmn_xml)
    presentation.report = report
    db.add(presentation)
    return report


def enqueue_run_or_503(db: Session, run: LabRun) -> None:
    try:
        enqueue_simulation(run.id)
    except SimulationQueueUnavailable as exc:
        run.status = "queue_waiting"
        run.error = (
            "Черга симуляції недоступна. Запуск збережено; "
            "відновіть сервіс черги та натисніть «resume»."
        )
        db.commit()
        raise HTTPException(
            503,
            {
                "message": run.error,
                "run_id": run.id,
                "retryable": True,
            },
        ) from exc


def row(item):
    return {
        attribute.columns[0].name: getattr(item, attribute.key)
        for attribute in inspect(item).mapper.column_attrs
    }


def editable_version(db: Session, version_id: str) -> LabModelVersion:
    version = db.get(LabModelVersion, version_id)
    if not version:
        raise HTTPException(404, "Model version not found")
    if version.status != "draft":
        raise HTTPException(409, "Published snapshots are immutable")
    return version


def touch(version: LabModelVersion) -> None:
    version.architecture_version = max(2, version.architecture_version or 1)
    version.semantic_revision = (version.semantic_revision or 0) + 1


EVENT_ACTION_TYPES = {
    "component.capability",
    "component.patch_state",
    "connection.disable",
    "connection.enable",
    "event.emit",
}


def event_program_row(db: Session, program: LabEventProgram) -> dict[str, Any]:
    actions = db.scalars(
        select(LabEventAction)
        .where(LabEventAction.program_id == program.id)
        .order_by(LabEventAction.sequence, LabEventAction.id)
    )
    return {**row(program), "actions": [row(action) for action in actions]}


def event_program_rows(db: Session, version_id: str) -> list[dict[str, Any]]:
    programs = db.scalars(
        select(LabEventProgram)
        .where(LabEventProgram.model_version_id == version_id)
        .order_by(LabEventProgram.name, LabEventProgram.id)
    )
    return [event_program_row(db, program) for program in programs]


def qualification_definition_row(item: LabQualificationDefinition) -> dict[str, Any]:
    return {
        **row(item),
        "document": deepcopy(item.document),
    }


def qualification_definition_for_version(
    db: Session, version_id: str
) -> LabQualificationDefinition | None:
    return db.scalar(
        select(LabQualificationDefinition).where(
            LabQualificationDefinition.model_version_id == version_id
        )
    )


def _required_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(422, f"{field} must be an object")
    return value


def _scenario_offset_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HTTPException(422, "offset_seconds must be a non-negative integer")
    return value


def _json_value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _event_action_target(
    db: Session, program: LabEventProgram, spec: dict[str, Any], target_kind: str
) -> LabNode | LabEdge:
    if spec.get("target_kind") != target_kind or not spec.get("target_id"):
        raise HTTPException(
            422,
            f"{spec['action_type']} requires an explicit {target_kind} target",
        )
    model = LabNode if target_kind == "component" else LabEdge
    target = db.get(model, spec["target_id"])
    if not target or target.model_version_id != program.model_version_id:
        raise HTTPException(
            422,
            f"Selected {target_kind} does not belong to the event program model",
        )
    return target


def validate_event_action(
    db: Session, program: LabEventProgram, spec: dict[str, Any]
) -> dict[str, Any]:
    action_type = spec.get("action_type")
    if action_type not in EVENT_ACTION_TYPES:
        raise HTTPException(422, f"Unsupported event action type: {action_type}")
    config = deepcopy(_required_object(spec.get("config", {}), "config"))

    if action_type == "component.capability":
        node = _event_action_target(db, program, spec, "component")
        capability = config.get("capability")
        if not isinstance(capability, str) or not capability.strip():
            raise HTTPException(422, "config.capability is required")
        declared = set((node.refinement_contract or {}).get("capabilities", []))
        if capability not in declared:
            raise HTTPException(
                422,
                f"Capability '{capability}' is not declared by the selected component",
            )
        config["payload"] = _required_object(config.get("payload", {}), "config.payload")
        forward_payload = config.get("forward_payload", True)
        if not isinstance(forward_payload, bool):
            raise HTTPException(422, "config.forward_payload must be a boolean")
        config["forward_payload"] = forward_payload
        return config

    if action_type == "component.patch_state":
        node = _event_action_target(db, program, spec, "component")
        patch = _required_object(config.get("patch"), "config.patch")
        component = db.get(LabComponentType, node.component_type_id)
        state = {
            **deepcopy(component.initial_state if component else {}),
            **deepcopy(node.initial_state or {}),
        }
        unknown = sorted(set(patch) - set(state))
        if unknown:
            raise HTTPException(
                422,
                {"message": "State patch contains unknown keys", "keys": unknown},
            )
        composite = sorted(
            key
            for key, value in patch.items()
            if isinstance(state[key], dict | list) or isinstance(value, dict | list)
        )
        if composite:
            raise HTTPException(
                422,
                {
                    "message": "State patch supports primitive values only",
                    "keys": composite,
                },
            )
        incompatible = sorted(
            key
            for key, value in patch.items()
            if _json_value_kind(state[key]) != _json_value_kind(value)
        )
        if incompatible:
            raise HTTPException(
                422,
                {
                    "message": "State patch must retain primitive value types",
                    "keys": incompatible,
                },
            )
        config["patch"] = patch
        return config

    if action_type in {"connection.disable", "connection.enable"}:
        _event_action_target(db, program, spec, "connection")
        return config

    if spec.get("target_kind") is not None or spec.get("target_id") is not None:
        raise HTTPException(422, "event.emit cannot have a graph target")
    emitted_type = config.get("event_type")
    if not isinstance(emitted_type, str) or not emitted_type.strip():
        raise HTTPException(422, "config.event_type is required")
    emitted_definition = db.get(LabEventType, emitted_type)
    if not emitted_definition:
        raise HTTPException(422, f"Unknown emitted event type: {emitted_type}")
    downstream_program = db.scalar(
        select(LabEventProgram).where(
            LabEventProgram.model_version_id == program.model_version_id,
            LabEventProgram.event_type_id == emitted_type,
            LabEventProgram.enabled.is_(True),
        )
    )
    if not emitted_definition.allow_broadcast and not downstream_program:
        raise HTTPException(
            422,
            (
                "A targetless emitted event must allow broadcast or have an "
                "enabled event program in this model version"
            ),
        )
    delay = config.get("delay_seconds", 0)
    if (
        isinstance(delay, bool)
        or not isinstance(delay, int | float)
        or not math.isfinite(float(delay))
        or delay < 0
    ):
        raise HTTPException(422, "config.delay_seconds must be a non-negative number")
    config["delay_seconds"] = delay
    config["payload"] = _required_object(config.get("payload", {}), "config.payload")
    return config


def _desired_sequence(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(422, "sequence must be a positive integer")
    return min(value, maximum)


def reorder_event_actions(
    db: Session,
    program_id: str,
    ordered: list[LabEventAction],
) -> None:
    if not ordered:
        return
    floor = min([action.sequence for action in ordered] + [0]) - len(ordered) - 1000
    for index, action in enumerate(ordered):
        action.sequence = floor - index
    db.flush()
    for index, action in enumerate(ordered, 1):
        action.sequence = index
    db.flush()


def clone_event_programs(
    db: Session,
    source_version_id: str,
    target_version_id: str,
    node_mapping: dict[str, str],
    edge_mapping: dict[str, str],
) -> None:
    programs = db.scalars(
        select(LabEventProgram).where(
            LabEventProgram.model_version_id == source_version_id
        )
    )
    for source in programs:
        cloned = LabEventProgram(
            model_version_id=target_version_id,
            event_type_id=source.event_type_id,
            name=source.name,
            description=source.description,
            enabled=source.enabled,
            layout=deepcopy(source.layout),
        )
        db.add(cloned)
        db.flush()
        actions = db.scalars(
            select(LabEventAction)
            .where(LabEventAction.program_id == source.id)
            .order_by(LabEventAction.sequence)
        )
        for action in actions:
            target_id = action.target_id
            if action.target_kind == "component" and target_id:
                target_id = node_mapping[target_id]
            elif action.target_kind == "connection" and target_id:
                target_id = edge_mapping[target_id]
            db.add(
                LabEventAction(
                    program_id=cloned.id,
                    sequence=action.sequence,
                    action_type=action.action_type,
                    target_kind=action.target_kind,
                    target_id=target_id,
                    config=deepcopy(action.config),
                    x=action.x,
                    y=action.y,
                )
            )


def clone_qualification_definition(
    db: Session, source_version_id: str, target_version_id: str
) -> None:
    source = qualification_definition_for_version(db, source_version_id)
    if not source:
        return
    db.add(
        LabQualificationDefinition(
            model_version_id=target_version_id,
            package_id=source.package_id,
            scenario_id=source.scenario_id,
            revision=source.revision,
            document=deepcopy(source.document),
        )
    )


def clone_monitor_templates(
    db: Session, source_version_id: str, target_version_id: str
) -> None:
    templates = list(
        db.scalars(
            select(LabMonitorTemplate).where(
                LabMonitorTemplate.model_version_id == source_version_id
            )
        )
    )
    if not templates:
        fallback = db.scalar(select(LabMonitorTemplate).order_by(LabMonitorTemplate.created_at))
        templates = [fallback] if fallback else []
    for template in templates:
        db.add(
            LabMonitorTemplate(
                model_version_id=target_version_id,
                name=template.name,
                revision=template.revision,
                panels=deepcopy(template.panels),
            )
        )


def graph_catalog(db: Session, version_id: str, *, target_id: str | None = None) -> list[dict[str, Any]]:
    nodes = list(db.scalars(select(LabNode).where(LabNode.model_version_id == version_id)))
    edges = list(db.scalars(select(LabEdge).where(LabEdge.model_version_id == version_id)))
    components = {item.id: item for item in db.scalars(select(LabComponentType))}
    metrics = list(db.scalars(select(LabMetricDefinition)))
    catalog = observable_catalog(nodes, components, metrics, edges)
    return [item for item in catalog if not target_id or item["target_id"] == target_id]


@router.get("/workspace")
def workspace(project_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    project = db.get(LabProject, project_id) if project_id else db.scalar(select(LabProject).order_by(LabProject.created_at).limit(1))
    if not project: raise HTTPException(404, "Model Lab seed is missing")
    version = db.get(LabModelVersion, project.active_draft_id or project.published_version_id)
    components = list(db.scalars(select(LabComponentType).order_by(LabComponentType.category, LabComponentType.name)))
    component_rows = [{**row(item), "ports": normalized_ports(item)} for item in components]
    monitors = [row(x) for x in db.scalars(select(LabMonitorTemplate).where(LabMonitorTemplate.model_version_id == version.id).order_by(LabMonitorTemplate.created_at))]
    return {"project": row(project), "projects": [row(x) for x in db.scalars(select(LabProject).order_by(LabProject.created_at))], "version": row(version), "models": [row(x) for x in db.scalars(select(LabModelVersion).where(LabModelVersion.project_id == project.id).order_by(LabModelVersion.version))], "components": component_rows, "nodes": [row(x) for x in db.scalars(select(LabNode).where(LabNode.model_version_id == version.id))], "edges": [row(x) for x in db.scalars(select(LabEdge).where(LabEdge.model_version_id == version.id))], "event_programs": event_program_rows(db, version.id), "scenarios": [row(x) for x in db.scalars(select(LabScenario).order_by(LabScenario.name))], "resources": [row(x) for x in db.scalars(select(LabResourcePool).order_by(LabResourcePool.resource_kind, LabResourcePool.class_name))], "runs": [row(x) for x in db.scalars(select(LabRun).order_by(LabRun.created_at.desc()).limit(20))], "scripts": [row(x) for x in db.scalars(select(LabScriptVersion).order_by(LabScriptVersion.script_id, LabScriptVersion.version))], "workflows": [row(x) for x in db.scalars(select(LabWorkflowVersion).order_by(LabWorkflowVersion.name))], "metrics": [row(x) for x in db.scalars(select(LabMetricDefinition).order_by(LabMetricDefinition.category, LabMetricDefinition.name))], "dashboards": [row(x) for x in db.scalars(select(LabDashboardDefinition).order_by(LabDashboardDefinition.name))], "monitors": monitors, "event_types": [row(x) for x in db.scalars(select(LabEventType).order_by(LabEventType.name))], "observables": graph_catalog(db, version.id)}


@router.post("/projects")
def create_project(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    project = LabProject(name=body["name"], description=body.get("description", ""))
    db.add(project); db.flush()
    draft = LabModelVersion(project_id=project.id, version=1, status="draft", name=body.get("version_name", "Нова модель"), timezone=body.get("timezone", "Europe/Kyiv"), settings=body.get("settings", {}), architecture_version=2)
    db.add(draft); db.flush(); project.active_draft_id = draft.id
    template_id = body.get("template_version_id")
    if template_id:
        source = db.get(LabModelVersion, template_id)
        if not source: raise HTTPException(404, "Template model version not found")
        node_mapping: dict[str, str] = {}
        edge_mapping: dict[str, str] = {}
        for old in db.scalars(select(LabNode).where(LabNode.model_version_id == source.id)):
            new = LabNode(model_version_id=draft.id, component_type_id=old.component_type_id, name=old.name, instance_key=old.instance_key or old.id, parent_node_id=None, x=old.x, y=old.y, parameters=deepcopy(old.parameters), initial_state=deepcopy(old.initial_state), python_code=old.python_code, refinement_contract=deepcopy(old.refinement_contract), port_overrides=deepcopy(old.port_overrides), observable_overrides=deepcopy(old.observable_overrides))
            db.add(new); db.flush(); node_mapping[old.id] = new.id
        for old in db.scalars(select(LabEdge).where(LabEdge.model_version_id == source.id)):
            new = LabEdge(model_version_id=draft.id, connection_key=old.connection_key or old.id, source_node_id=node_mapping[old.source_node_id], source_port=old.source_port, target_node_id=node_mapping[old.target_node_id], target_port=old.target_port, flow_type=old.flow_type, domain=old.domain, enabled=old.enabled, parameters=deepcopy(old.parameters))
            db.add(new); db.flush(); edge_mapping[old.id] = new.id
        clone_event_programs(
            db, source.id, draft.id, node_mapping, edge_mapping
        )
    db.commit(); db.refresh(project); db.refresh(draft)
    return {"project": row(project), "version": row(draft)}


@router.patch("/dashboards/{dashboard_id}")
def update_dashboard(dashboard_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    dashboard = db.get(LabDashboardDefinition, dashboard_id)
    if not dashboard:
        raise HTTPException(404, "Dashboard not found")
    if "name" in body:
        dashboard.name = str(body["name"])
    if "panels" in body:
        if not isinstance(body["panels"], list):
            raise HTTPException(422, "panels must be a list")
        dashboard.panels = body["panels"]
    db.commit(); db.refresh(dashboard)
    return row(dashboard)


@router.get("/versions/{version_id}/graph")
def model_graph(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabModelVersion, version_id)
    if not version: raise HTTPException(404, "Model version not found")
    return {"version": row(version), "nodes": [row(x) for x in db.scalars(select(LabNode).where(LabNode.model_version_id == version_id))], "edges": [row(x) for x in db.scalars(select(LabEdge).where(LabEdge.model_version_id == version_id))], "event_programs": event_program_rows(db, version_id), "observables": graph_catalog(db, version_id)}


@router.get("/versions/{version_id}/observables")
def observables(version_id: str, target_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    if not db.get(LabModelVersion, version_id):
        raise HTTPException(404, "Model version not found")
    return {"items": graph_catalog(db, version_id, target_id=target_id)}


@router.get("/versions/{version_id}/ports")
def ports(version_id: str, db: Session = Depends(get_db)) -> dict:
    nodes = list(db.scalars(select(LabNode).where(LabNode.model_version_id == version_id)))
    components = {item.id: item for item in db.scalars(select(LabComponentType))}
    return {"items": [{"node_id": node.id, "instance_key": node.instance_key or node.id, "ports": normalized_ports(components[node.component_type_id], node.port_overrides)} for node in nodes if node.component_type_id in components]}


@router.post("/versions/{version_id}/monitors")
def create_monitor(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = editable_version(db, version_id)
    available = {item["address"] for item in graph_catalog(db, version_id)}
    requested = [series.get("address") for panel in body.get("panels", []) for series in panel.get("series", [])]
    missing = sorted({address for address in requested if address not in available})
    if missing:
        raise HTTPException(422, {"message": "Unknown observables", "addresses": missing})
    monitor = LabMonitorTemplate(model_version_id=version_id, name=body.get("name", "Монітор моделі"), panels=body.get("panels", []))
    db.add(monitor); touch(version); db.commit(); db.refresh(monitor)
    return row(monitor)


@router.patch("/monitors/{monitor_id}")
def update_monitor(monitor_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    monitor = db.get(LabMonitorTemplate, monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    version = editable_version(db, monitor.model_version_id)
    if "name" in body: monitor.name = str(body["name"])
    if "panels" in body: monitor.panels = list(body["panels"])
    monitor.revision += 1; touch(version); db.commit(); db.refresh(monitor)
    return row(monitor)


@router.post("/event-types")
def create_event_type(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    event_id = stable_key(body.get("id") or body["name"], f"event-{uuid.uuid4().hex[:8]}")
    if db.get(LabEventType, event_id):
        raise HTTPException(409, "Event type already exists")
    item = LabEventType(
        id=event_id, name=body["name"], kind=body.get("kind", "stimulus"),
        target_kinds=body.get("target_kinds", ["component"]), domains=body.get("domains", []),
        required_capabilities=body.get("required_capabilities", []), payload_schema=body.get("payload_schema", {}),
        severity=body.get("severity", "info"), allow_broadcast=bool(body.get("allow_broadcast", False)),
    )
    db.add(item); db.commit(); db.refresh(item); return row(item)


@router.get("/versions/{version_id}/event-programs")
def event_programs(version_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(LabModelVersion, version_id):
        raise HTTPException(404, "Model version not found")
    return {"items": event_program_rows(db, version_id)}


@router.get("/versions/{version_id}/qualification-definition")
def qualification_definition(
    version_id: str, db: Session = Depends(get_db)
) -> dict:
    if not db.get(LabModelVersion, version_id):
        raise HTTPException(404, "Model version not found")
    item = qualification_definition_for_version(db, version_id)
    return {"item": qualification_definition_row(item) if item else None}


@router.put("/versions/{version_id}/qualification-definition")
def save_qualification_definition(
    version_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    version = editable_version(db, version_id)
    raw_document = body.get("document")
    if not isinstance(raw_document, dict):
        raise HTTPException(422, "document must be an object")
    try:
        document = canonicalize_qualification_package(raw_document)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Invalid qualification document: {exc}") from exc
    package_id = document.get("id")
    scenario_id = document.get("scenario_id")
    if not isinstance(package_id, str) or not package_id.strip():
        raise HTTPException(422, "document.id is required")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise HTTPException(422, "document.scenario_id is required")
    errors = validate_qualification_package(document)
    if errors:
        raise HTTPException(422, {"message": "Qualification definition is invalid", "errors": errors})
    item = qualification_definition_for_version(db, version_id)
    if item:
        if item.package_id != package_id or item.scenario_id != scenario_id:
            raise HTTPException(
                409,
                "Package and scenario identity cannot change inside a model version",
            )
        item.document = document
        item.revision += 1
    else:
        item = LabQualificationDefinition(
            model_version_id=version_id,
            package_id=package_id,
            scenario_id=scenario_id,
            revision=1,
            document=document,
        )
        db.add(item)
    touch(version)
    db.commit()
    db.refresh(item)
    return qualification_definition_row(item)


@router.post("/versions/{version_id}/qualification-definition/replay")
def replay_saved_qualification_definition(
    version_id: str, db: Session = Depends(get_db)
) -> dict:
    if not db.get(LabModelVersion, version_id):
        raise HTTPException(404, "Model version not found")
    item = qualification_definition_for_version(db, version_id)
    if not item:
        raise HTTPException(404, "Qualification definition not found")
    return qualification_pilot_report(deepcopy(item.document))


@router.post("/versions/{version_id}/event-programs")
def create_event_program(
    version_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    version = editable_version(db, version_id)
    event_type_id = body.get("event_type_id")
    definition = db.get(LabEventType, event_type_id)
    if not definition:
        raise HTTPException(422, "Unknown event type")
    existing = db.scalar(
        select(LabEventProgram).where(
            LabEventProgram.model_version_id == version_id,
            LabEventProgram.event_type_id == event_type_id,
        )
    )
    if existing:
        raise HTTPException(409, "This model already has a program for the event type")
    layout = _required_object(body.get("layout", {}), "layout")
    program = LabEventProgram(
        model_version_id=version_id,
        event_type_id=event_type_id,
        name=str(body.get("name") or definition.name),
        description=str(body.get("description", "")),
        enabled=bool(body.get("enabled", True)),
        layout=layout,
    )
    db.add(program)
    touch(version)
    db.commit()
    db.refresh(program)
    return event_program_row(db, program)


@router.patch("/event-programs/{program_id}")
def update_event_program(
    program_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    program = db.get(LabEventProgram, program_id)
    if not program:
        raise HTTPException(404, "Event program not found")
    version = editable_version(db, program.model_version_id)
    if "event_type_id" in body:
        event_type_id = body["event_type_id"]
        if not db.get(LabEventType, event_type_id):
            raise HTTPException(422, "Unknown event type")
        duplicate = db.scalar(
            select(LabEventProgram).where(
                LabEventProgram.model_version_id == program.model_version_id,
                LabEventProgram.event_type_id == event_type_id,
                LabEventProgram.id != program.id,
            )
        )
        if duplicate:
            raise HTTPException(
                409, "This model already has a program for the event type"
            )
        program.event_type_id = event_type_id
    if "name" in body:
        program.name = str(body["name"])
    if "description" in body:
        program.description = str(body["description"])
    if "enabled" in body:
        program.enabled = bool(body["enabled"])
    if "layout" in body:
        program.layout = _required_object(body["layout"], "layout")
    touch(version)
    db.commit()
    db.refresh(program)
    return event_program_row(db, program)


@router.delete("/event-programs/{program_id}", status_code=204)
def delete_event_program(program_id: str, db: Session = Depends(get_db)):
    program = db.get(LabEventProgram, program_id)
    if not program:
        raise HTTPException(404, "Event program not found")
    version = editable_version(db, program.model_version_id)
    db.delete(program)
    touch(version)
    db.commit()


@router.post("/event-programs/{program_id}/actions")
def create_event_action(
    program_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    program = db.get(LabEventProgram, program_id)
    if not program:
        raise HTTPException(404, "Event program not found")
    if body.get("action_type") == "component.patch_state":
        raise HTTPException(
            422,
            (
                "Direct state patches are legacy-only. Use a declared component "
                "capability with a confirmed postcondition."
            ),
        )
    version = editable_version(db, program.model_version_id)
    spec = {
        "action_type": body.get("action_type"),
        "target_kind": body.get("target_kind"),
        "target_id": body.get("target_id"),
        "config": body.get("config", {}),
    }
    config = validate_event_action(db, program, spec)
    ordered = list(
        db.scalars(
            select(LabEventAction)
            .where(LabEventAction.program_id == program.id)
            .order_by(LabEventAction.sequence, LabEventAction.id)
        )
    )
    desired = (
        _desired_sequence(body["sequence"], len(ordered) + 1)
        if "sequence" in body
        else len(ordered) + 1
    )
    temporary = min([action.sequence for action in ordered] + [0]) - 1000
    action = LabEventAction(
        program_id=program.id,
        sequence=temporary,
        action_type=spec["action_type"],
        target_kind=spec["target_kind"],
        target_id=spec["target_id"],
        config=config,
        x=float(body.get("x", 0)),
        y=float(body.get("y", 0)),
    )
    db.add(action)
    db.flush()
    ordered.insert(desired - 1, action)
    reorder_event_actions(db, program.id, ordered)
    touch(version)
    db.commit()
    db.refresh(action)
    return row(action)


@router.patch("/event-actions/{action_id}")
def update_event_action(
    action_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    action = db.get(LabEventAction, action_id)
    if not action:
        raise HTTPException(404, "Event action not found")
    program = db.get(LabEventProgram, action.program_id)
    version = editable_version(db, program.model_version_id)
    spec = {
        "action_type": body.get("action_type", action.action_type),
        "target_kind": body.get("target_kind", action.target_kind),
        "target_id": body.get("target_id", action.target_id),
        "config": body.get("config", action.config),
    }
    if (
        spec["action_type"] == "component.patch_state"
        and action.action_type != "component.patch_state"
    ):
        raise HTTPException(
            422,
            (
                "Direct state patches are legacy-only. Use a declared component "
                "capability with a confirmed postcondition."
            ),
        )
    if (
        action.action_type == "component.patch_state"
        and spec["action_type"] == "component.patch_state"
        and any(field in body for field in ("target_kind", "target_id", "config"))
    ):
        raise HTTPException(
            409,
            "Legacy state patches are read-only; migrate the action or delete it.",
        )
    config = validate_event_action(db, program, spec)
    action.action_type = spec["action_type"]
    action.target_kind = spec["target_kind"]
    action.target_id = spec["target_id"]
    action.config = config
    if "x" in body:
        action.x = float(body["x"])
    if "y" in body:
        action.y = float(body["y"])
    if "sequence" in body:
        ordered = list(
            db.scalars(
                select(LabEventAction)
                .where(LabEventAction.program_id == program.id)
                .order_by(LabEventAction.sequence, LabEventAction.id)
            )
        )
        desired = _desired_sequence(body["sequence"], len(ordered))
        ordered.remove(action)
        ordered.insert(desired - 1, action)
        reorder_event_actions(db, program.id, ordered)
    touch(version)
    db.commit()
    db.refresh(action)
    return row(action)


@router.delete("/event-actions/{action_id}", status_code=204)
def delete_event_action(action_id: str, db: Session = Depends(get_db)):
    action = db.get(LabEventAction, action_id)
    if not action:
        raise HTTPException(404, "Event action not found")
    program = db.get(LabEventProgram, action.program_id)
    version = editable_version(db, program.model_version_id)
    db.delete(action)
    db.flush()
    ordered = list(
        db.scalars(
            select(LabEventAction)
            .where(LabEventAction.program_id == program.id)
            .order_by(LabEventAction.sequence, LabEventAction.id)
        )
    )
    reorder_event_actions(db, program.id, ordered)
    touch(version)
    db.commit()


def validated_event_target(
    db: Session,
    model_version_id: str,
    event_type_id: str,
    body: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    definition = db.get(LabEventType, event_type_id)
    payload = _required_object(body.get("payload", {}), "payload")
    if definition:
        required = definition.payload_schema.get("required", [])
        missing = [name for name in required if name not in payload]
        if missing:
            raise HTTPException(
                422, {"message": "Missing event payload fields", "fields": missing}
            )
    target = body.get("target") or (
        {"kind": "component", "id": body.get("target_node_id")}
        if body.get("target_node_id")
        else None
    )
    if target is None:
        program = db.scalar(
            select(LabEventProgram).where(
                LabEventProgram.model_version_id == model_version_id,
                LabEventProgram.event_type_id == event_type_id,
                LabEventProgram.enabled.is_(True),
            )
        )
        if definition and not definition.allow_broadcast and not program:
            raise HTTPException(422, "This event requires an explicit target")
        return None, {}
    if not isinstance(target, dict):
        raise HTTPException(422, "target must be an object")
    if target.get("kind") != "component":
        if definition and target.get("kind") not in definition.target_kinds:
            raise HTTPException(422, f"Event cannot target {target.get('kind')}")
        return None, target
    node = db.get(LabNode, target.get("id"))
    if not node:
        node = db.scalar(
            select(LabNode).where(
                LabNode.model_version_id == model_version_id,
                LabNode.instance_key == target.get("key"),
            )
        )
    if not node or node.model_version_id != model_version_id:
        raise HTTPException(422, "Event target does not belong to the run model")
    component = db.get(LabComponentType, node.component_type_id)
    if definition:
        domains = {
            port["domain"]
            for port in normalized_ports(component, node.port_overrides)
        }
        if definition.domains and not domains.intersection(definition.domains):
            raise HTTPException(
                422, "Event domain is incompatible with the selected component"
            )
        capabilities = set(node.refinement_contract.get("capabilities", []))
        if not set(definition.required_capabilities).issubset(capabilities):
            raise HTTPException(
                422,
                "Selected component does not provide the required event capability",
            )
    return node.id, {
        "kind": "component",
        "id": node.id,
        "key": node.instance_key or node.id,
    }


@router.post("/scenarios/{scenario_id}/events")
def create_scenario_event(
    scenario_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    scenario = db.get(LabScenario, scenario_id)
    if not scenario or not scenario.model_version_id:
        raise HTTPException(404, "Scenario not found")
    target_id, target = validated_event_target(
        db, scenario.model_version_id, body["event_type"], body
    )
    item = LabScenarioEventTemplate(
        scenario_id=scenario.id,
        offset_seconds=_scenario_offset_seconds(body.get("offset_seconds", 0)),
        priority=int(body.get("priority", 100)),
        event_type=body["event_type"],
        target_node_id=target_id,
        phase=body.get("phase", "scenario"),
        title=body.get("title", body["event_type"]),
        payload={**body.get("payload", {}), "_target": target},
        evidence_kind=body.get("evidence_kind", "assumed"),
        source_url=body.get("source_url"),
        source_note=body.get("source_note", ""),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return row(item)


@router.patch("/scenario-events/{event_id}")
def update_scenario_event(
    event_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    item = db.get(LabScenarioEventTemplate, event_id)
    if not item:
        raise HTTPException(404, "Scenario event not found")
    scenario = db.get(LabScenario, item.scenario_id)
    if not scenario or not scenario.model_version_id:
        raise HTTPException(404, "Scenario not found")
    event_type = body.get("event_type", item.event_type)
    stored_payload = deepcopy(item.payload or {})
    payload = deepcopy(body.get("payload", stored_payload))
    payload.pop("_target", None)
    candidate: dict[str, Any] = {"event_type": event_type, "payload": payload}
    if "target" in body:
        candidate["target"] = body["target"]
    elif "target_node_id" in body:
        candidate["target_node_id"] = body["target_node_id"]
    elif item.target_node_id:
        candidate["target_node_id"] = item.target_node_id
    elif stored_payload.get("_target"):
        candidate["target"] = stored_payload["_target"]
    target_id, target = validated_event_target(
        db, scenario.model_version_id, event_type, candidate
    )
    item.event_type = event_type
    item.target_node_id = target_id
    item.payload = {**payload, "_target": target}
    if "offset_seconds" in body:
        item.offset_seconds = _scenario_offset_seconds(body["offset_seconds"])
    for field in [
        "priority",
        "phase",
        "title",
        "evidence_kind",
        "source_url",
        "source_note",
    ]:
        if field in body:
            setattr(item, field, body[field])
    db.commit()
    db.refresh(item)
    return row(item)


@router.delete("/scenario-events/{event_id}", status_code=204)
def delete_scenario_event(event_id: str, db: Session = Depends(get_db)):
    item = db.get(LabScenarioEventTemplate, event_id)
    if not item:
        raise HTTPException(404, "Scenario event not found")
    scenario = db.get(LabScenario, item.scenario_id)
    if not scenario or not scenario.model_version_id:
        raise HTTPException(404, "Scenario not found")
    db.delete(item)
    db.commit()


@router.post("/scenarios/{scenario_id}/cep-rules")
def create_cep_rule(scenario_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    if not db.get(LabScenario, scenario_id): raise HTTPException(404, "Scenario not found")
    operators = {"all", "any", "not", "compare", "event", "sequence", "count", "average", "min", "max", "duration", "absence", "trend"}
    operator = body.get("condition", {}).get("operator")
    if operator not in operators: raise HTTPException(422, f"Unsupported CEP operator: {operator}")
    item = LabCepRule(scenario_id=scenario_id, name=body["name"], condition=body["condition"], emit=body["emit"], enabled=bool(body.get("enabled", True)))
    db.add(item); db.commit(); db.refresh(item); return row(item)


@router.get("/scripts")
def scripts(db: Session = Depends(get_db)) -> dict:
    return {"scripts": [row(x) for x in db.scalars(select(LabScript).order_by(LabScript.kind, LabScript.name))], "versions": [row(x) for x in db.scalars(select(LabScriptVersion).order_by(LabScriptVersion.script_id, LabScriptVersion.version))]}


@router.post("/scripts")
def create_script(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    script = LabScript(id=body.get("id") or f"script-{uuid.uuid4().hex[:12]}", name=body["name"], kind=body["kind"], description=body.get("description", "")); db.add(script); db.flush()
    version = LabScriptVersion(script_id=script.id, version=1, status="draft", code=body.get("code", ""), contract=body.get("contract", {})); db.add(version); db.commit(); db.refresh(version)
    return {"script": row(script), "version": row(version)}


@router.post("/scripts/{script_id}/versions")
def create_script_version(script_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    script = db.get(LabScript, script_id)
    if not script: raise HTTPException(404, "Script not found")
    source = db.scalar(select(LabScriptVersion).where(LabScriptVersion.script_id == script_id).order_by(LabScriptVersion.version.desc()).limit(1))
    version = LabScriptVersion(script_id=script_id, version=(source.version if source else 0) + 1, status="draft", code=body.get("code", source.code if source else ""), contract=body.get("contract", deepcopy(source.contract) if source else {})); db.add(version); db.commit(); db.refresh(version); return row(version)


@router.patch("/scripts/versions/{version_id}")
def update_script(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabScriptVersion, version_id)
    if not version: raise HTTPException(404, "Script version not found")
    if version.status != "draft": raise HTTPException(409, "Published script versions are immutable")
    for field in ["code", "contract"]:
        if field in body: setattr(version, field, body[field])
    db.commit(); db.refresh(version); return row(version)


@router.post("/scripts/versions/{version_id}/validate")
def validate_script(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabScriptVersion, version_id)
    try:
        compile(version.code, f"<{version.id}>", "exec")
        result = {"valid": True, "errors": []}
    except SyntaxError as exc:
        result = {"valid": False, "errors": [str(exc)]}
    version.validation = result; db.commit(); return result


@router.post("/scripts/versions/{version_id}/test")
def test_script(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabScriptVersion, version_id)
    try:
        return execute_script(version.code, body.get("action", "event"), {"time": body.get("time", datetime.now(UTC).isoformat()), "parameters": body.get("parameters", {}), "state": body.get("state", {}), "inputs": body.get("inputs", {}), "snapshot": body.get("snapshot", {}), "seed": int(body.get("seed", 42))}, event=body.get("event", {}))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/scripts/versions/{version_id}/publish")
def publish_script(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabScriptVersion, version_id)
    result = validate_script(version_id, db)
    if not result["valid"]: raise HTTPException(422, result)
    version.status = "published"; version.published_at = datetime.now(UTC); db.commit(); db.refresh(version); return row(version)


def process_set_version_row(db: Session, version: LabProcessSetVersion) -> dict[str, Any]:
    process_set = db.get(LabProcessSet, version.process_set_id)
    owner = db.get(Entity, process_set.owner_entity_id) if process_set else None
    activations = list(db.scalars(select(LabProcessSetWorkflow).where(LabProcessSetWorkflow.process_set_version_id == version.id).order_by(LabProcessSetWorkflow.priority, LabProcessSetWorkflow.id)))
    return {
        "process_set": row(process_set) if process_set else None,
        "owner": row(owner) if owner else None,
        "version": row(version),
        "workflows": [
            {**row(item), "workflow": row(db.get(LabWorkflowVersion, item.workflow_version_id))}
            for item in activations
        ],
    }


def scenario_assembly_payload(db: Session, assembly: LabScenarioAssemblyVersion) -> dict[str, Any]:
    members = list(db.scalars(select(LabScenarioAssemblyMember).where(LabScenarioAssemblyMember.assembly_version_id == assembly.id).order_by(LabScenarioAssemblyMember.alias)))
    bindings = list(db.scalars(select(LabScenarioProcessBinding).where(LabScenarioProcessBinding.assembly_version_id == assembly.id)))
    process_versions = {item.id: item for item in db.scalars(select(LabProcessSetVersion).where(LabProcessSetVersion.id.in_([binding.process_set_version_id for binding in bindings])))} if bindings else {}
    process_sets = {item.id: item for item in db.scalars(select(LabProcessSet).where(LabProcessSet.id.in_([item.process_set_id for item in process_versions.values()])))} if process_versions else {}
    activations = list(db.scalars(select(LabProcessSetWorkflow).where(LabProcessSetWorkflow.process_set_version_id.in_(list(process_versions))))) if process_versions else []
    activations_by_version: dict[str, list[LabProcessSetWorkflow]] = {}
    for activation in activations:
        activations_by_version.setdefault(activation.process_set_version_id, []).append(activation)
    bindings_by_member: dict[str, list[LabScenarioProcessBinding]] = {}
    for binding in bindings:
        bindings_by_member.setdefault(binding.member_id, []).append(binding)
    node_maps = list(db.scalars(select(LabScenarioAssemblyNodeMap).where(LabScenarioAssemblyNodeMap.assembly_version_id == assembly.id)))
    maps_by_member: dict[str, list[LabScenarioAssemblyNodeMap]] = {}
    for item in node_maps:
        maps_by_member.setdefault(item.member_id, []).append(item)
    result_members = []
    object_rows = []
    for member in members:
        entity = db.get(Entity, member.entity_id)
        model = db.get(LabModelVersion, member.model_version_id) if member.model_version_id else None
        member_processes = []
        for binding in bindings_by_member.get(member.id, []):
            version = process_versions[binding.process_set_version_id]
            process_set = process_sets[version.process_set_id]
            workflow_rows = []
            for activation in sorted(activations_by_version.get(version.id, []), key=lambda item: (item.priority, item.start_event_type)):
                workflow = db.get(LabWorkflowVersion, activation.workflow_version_id)
                workflow_rows.append({**row(activation), "workflow_name": workflow.name if workflow else activation.workflow_version_id, "workflow_status": workflow.status if workflow else "missing", "workflow_validation": deepcopy(workflow.validation) if workflow else {"valid": False}})
            member_processes.append({"binding": row(binding), "process_set": row(process_set), "version": row(version), "workflows": workflow_rows})
        assets = []
        for mapping in sorted(maps_by_member.get(member.id, []), key=lambda item: item.qualified_ref):
            composite_node = db.get(LabNode, mapping.composite_node_id)
            source_node = db.get(LabNode, mapping.source_node_id)
            roles = list(db.scalars(select(LabAssetActorRole).where(LabAssetActorRole.model_version_id == member.model_version_id, LabAssetActorRole.node_id == mapping.source_node_id))) if member.model_version_id else []
            asset = {"mapping": row(mapping), "name": composite_node.name if composite_node else mapping.source_node_key, "component_type_id": composite_node.component_type_id if composite_node else None, "source_node": row(source_node) if source_node else None, "roles": [row(item) for item in roles]}
            assets.append(asset)
            object_rows.append({"member_id": member.id, "participant_alias": member.alias, "entity_id": member.entity_id, "organization": entity.name if entity else member.entity_id, "participant_role": member.role, "model_version_id": member.model_version_id, "asset": asset, "process_sets": member_processes})
        if not assets:
            object_rows.append({"member_id": member.id, "participant_alias": member.alias, "entity_id": member.entity_id, "organization": entity.name if entity else member.entity_id, "participant_role": member.role, "model_version_id": None, "asset": None, "process_sets": member_processes})
        result_members.append({**row(member), "entity": row(entity) if entity else None, "model": row(model) if model else None, "assets": assets, "process_sets": member_processes})
    message_flows: list[dict[str, str]] = []
    for member in members:
        for binding in bindings_by_member.get(member.id, []):
            for activation in activations_by_version.get(binding.process_set_version_id, []):
                workflow = db.get(LabWorkflowVersion, activation.workflow_version_id)
                for emitted in (workflow.settings or {}).get("on_start_emit", []) if workflow else []:
                    if emitted.get("target_member_alias"):
                        message_flows.append({"source": member.alias, "target": emitted["target_member_alias"], "name": emitted.get("event_type", "Повідомлення")})
    collaboration_xml = build_collaboration_xml(
        f"Collaboration_{assembly.scenario_id}",
        [{"id": member.alias, "name": (db.get(Entity, member.entity_id).name if db.get(Entity, member.entity_id) else member.alias)} for member in members],
        message_flows,
    )
    return {"assembly": row(assembly), "participants": result_members, "objects": object_rows, "cross_links": [row(item) for item in db.scalars(select(LabScenarioAssemblyLink).where(LabScenarioAssemblyLink.assembly_version_id == assembly.id))], "message_flows": message_flows, "collaboration_bpmn_xml": collaboration_xml}


@router.get("/scenarios/{scenario_id}/assembly")
def scenario_assembly(scenario_id: str, db: Session = Depends(get_db)) -> dict:
    scenario = db.get(LabScenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    assembly = db.get(LabScenarioAssemblyVersion, scenario.assembly_version_id) if scenario.assembly_version_id else db.scalar(select(LabScenarioAssemblyVersion).where(LabScenarioAssemblyVersion.scenario_id == scenario_id, LabScenarioAssemblyVersion.status == "published").order_by(LabScenarioAssemblyVersion.version.desc()).limit(1))
    if not assembly:
        return {"assembly": None, "participants": [], "objects": [], "cross_links": [], "legacy_workflow_version_id": scenario.configuration.get("workflow_version_id")}
    return scenario_assembly_payload(db, assembly)


def editable_assembly(db: Session, assembly_id: str) -> LabScenarioAssemblyVersion:
    assembly = db.get(LabScenarioAssemblyVersion, assembly_id)
    if not assembly:
        raise HTTPException(404, "Scenario assembly not found")
    if assembly.status != "draft":
        raise HTTPException(409, "Published scenario assemblies are immutable")
    return assembly


@router.post("/scenarios/{scenario_id}/assemblies")
def create_scenario_assembly(scenario_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    scenario = db.get(LabScenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    number = (db.scalar(select(func.max(LabScenarioAssemblyVersion.version)).where(LabScenarioAssemblyVersion.scenario_id == scenario_id)) or 0) + 1
    composite_id = body.get("composite_model_version_id") or scenario.model_version_id
    composite = db.get(LabModelVersion, composite_id) if composite_id else None
    if not composite or composite.status != "published":
        raise HTTPException(422, "Assembly requires a published composite model")
    assembly = LabScenarioAssemblyVersion(scenario_id=scenario_id, version=number, status="draft", name=body.get("name", f"{scenario.name} · composition v{number}"), composite_model_version_id=composite_id)
    db.add(assembly); db.commit(); db.refresh(assembly)
    return scenario_assembly_payload(db, assembly)


@router.post("/assemblies/{assembly_id}/clone")
def clone_scenario_assembly(assembly_id: str, db: Session = Depends(get_db)) -> dict:
    source = db.get(LabScenarioAssemblyVersion, assembly_id)
    if not source:
        raise HTTPException(404, "Scenario assembly not found")
    number = (db.scalar(select(func.max(LabScenarioAssemblyVersion.version)).where(LabScenarioAssemblyVersion.scenario_id == source.scenario_id)) or 0) + 1
    clone = LabScenarioAssemblyVersion(scenario_id=source.scenario_id, version=number, status="draft", name=f"{source.name} · v{number}", composite_model_version_id=source.composite_model_version_id)
    db.add(clone); db.flush()
    member_ids: dict[str, str] = {}
    for item in db.scalars(select(LabScenarioAssemblyMember).where(LabScenarioAssemblyMember.assembly_version_id == source.id)):
        copied = LabScenarioAssemblyMember(assembly_version_id=clone.id, alias=item.alias, entity_id=item.entity_id, model_version_id=item.model_version_id, participant_kind=item.participant_kind, role=item.role, evidence=deepcopy(item.evidence))
        db.add(copied); db.flush(); member_ids[item.id] = copied.id
    for item in db.scalars(select(LabScenarioProcessBinding).where(LabScenarioProcessBinding.assembly_version_id == source.id)):
        db.add(LabScenarioProcessBinding(assembly_version_id=clone.id, member_id=member_ids[item.member_id], process_set_version_id=item.process_set_version_id))
    for item in db.scalars(select(LabScenarioAssemblyNodeMap).where(LabScenarioAssemblyNodeMap.assembly_version_id == source.id)):
        db.add(LabScenarioAssemblyNodeMap(assembly_version_id=clone.id, member_id=member_ids[item.member_id], source_node_id=item.source_node_id, source_node_key=item.source_node_key, composite_node_id=item.composite_node_id, qualified_ref=item.qualified_ref))
    for item in db.scalars(select(LabScenarioAssemblyLink).where(LabScenarioAssemblyLink.assembly_version_id == source.id)):
        db.add(LabScenarioAssemblyLink(assembly_version_id=clone.id, source_member_id=member_ids[item.source_member_id], source_node_key=item.source_node_key, source_port=item.source_port, target_member_id=member_ids[item.target_member_id], target_node_key=item.target_node_key, target_port=item.target_port, flow_type=item.flow_type, domain=item.domain, parameters=deepcopy(item.parameters)))
    db.commit(); db.refresh(clone)
    return scenario_assembly_payload(db, clone)


@router.post("/assemblies/{assembly_id}/members")
def add_assembly_member(assembly_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    assembly = editable_assembly(db, assembly_id)
    if not db.get(Entity, body.get("entity_id")):
        raise HTTPException(404, "Participant entity not found")
    model_id = body.get("model_version_id")
    model = db.get(LabModelVersion, model_id) if model_id else None
    if model_id and (not model or model.status != "published"):
        raise HTTPException(422, "Assembly members require published participant models")
    member = LabScenarioAssemblyMember(assembly_version_id=assembly.id, alias=body["alias"], entity_id=body["entity_id"], model_version_id=model_id, participant_kind=body.get("participant_kind", "organization"), role=body.get("role", "operator"), evidence=body.get("evidence", {}))
    db.add(member); db.commit(); db.refresh(member)
    return row(member)


@router.post("/assemblies/{assembly_id}/process-bindings")
def add_assembly_process_binding(assembly_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    assembly = editable_assembly(db, assembly_id)
    member = db.get(LabScenarioAssemblyMember, body.get("member_id"))
    version = db.get(LabProcessSetVersion, body.get("process_set_version_id"))
    if not member or member.assembly_version_id != assembly.id:
        raise HTTPException(404, "Assembly member not found")
    if not version or version.status != "published":
        raise HTTPException(422, "Assembly requires a published process-set version")
    binding = LabScenarioProcessBinding(assembly_version_id=assembly.id, member_id=member.id, process_set_version_id=version.id)
    db.add(binding); db.commit(); db.refresh(binding)
    return row(binding)


@router.post("/assemblies/{assembly_id}/node-maps")
def add_assembly_node_map(assembly_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    assembly = editable_assembly(db, assembly_id)
    member = db.get(LabScenarioAssemblyMember, body.get("member_id"))
    source_node = db.get(LabNode, body.get("source_node_id"))
    composite_node = db.get(LabNode, body.get("composite_node_id"))
    if not member or member.assembly_version_id != assembly.id:
        raise HTTPException(404, "Assembly member not found")
    if not member.model_version_id or not source_node or source_node.model_version_id != member.model_version_id:
        raise HTTPException(422, "Source asset must belong to the participant model")
    if not composite_node or composite_node.model_version_id != assembly.composite_model_version_id:
        raise HTTPException(422, "Composite asset must belong to the assembly model")
    item = LabScenarioAssemblyNodeMap(assembly_version_id=assembly.id, member_id=member.id, source_node_id=source_node.id, source_node_key=body.get("source_node_key", source_node.instance_key or source_node.id), composite_node_id=composite_node.id, qualified_ref=body.get("qualified_ref", f"{member.alias}:{source_node.instance_key or source_node.id}"))
    db.add(item); db.commit(); db.refresh(item)
    return row(item)


@router.post("/assemblies/{assembly_id}/links")
def add_assembly_link(assembly_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    assembly = editable_assembly(db, assembly_id)
    source_member = db.get(LabScenarioAssemblyMember, body.get("source_member_id"))
    target_member = db.get(LabScenarioAssemblyMember, body.get("target_member_id"))
    if not source_member or not target_member or source_member.assembly_version_id != assembly.id or target_member.assembly_version_id != assembly.id:
        raise HTTPException(422, "Cross-model link members must belong to the assembly")
    link = LabScenarioAssemblyLink(assembly_version_id=assembly.id, source_member_id=source_member.id, source_node_key=body["source_node_key"], source_port=body["source_port"], target_member_id=target_member.id, target_node_key=body["target_node_key"], target_port=body["target_port"], flow_type=body["flow_type"], domain=body.get("domain", "legacy.energy"), parameters=body.get("parameters", {}))
    db.add(link); db.commit(); db.refresh(link)
    return row(link)


def validate_assembly_payload(db: Session, assembly: LabScenarioAssemblyVersion) -> dict[str, Any]:
    members = list(db.scalars(select(LabScenarioAssemblyMember).where(LabScenarioAssemblyMember.assembly_version_id == assembly.id)))
    bindings = list(db.scalars(select(LabScenarioProcessBinding).where(LabScenarioProcessBinding.assembly_version_id == assembly.id)))
    errors = []
    if not members:
        errors.append("Assembly must contain participants")
    bound_members = {item.member_id for item in bindings}
    for member in members:
        if member.id not in bound_members:
            errors.append(f"Participant {member.alias} has no process set")
        if not member.evidence:
            errors.append(f"Participant {member.alias} has no evidence")
        if member.model_version_id:
            model = db.get(LabModelVersion, member.model_version_id)
            if not model or model.status != "published":
                errors.append(f"Participant {member.alias} model is not published")
    for binding in bindings:
        process_version = db.get(LabProcessSetVersion, binding.process_set_version_id)
        if not process_version or process_version.status != "published":
            errors.append(f"Process set {binding.process_set_version_id} is not published")
    return {"valid": not errors, "errors": errors, "members": len(members), "process_bindings": len(bindings), "cross_organization_policy": "roles_and_messages"}


@router.post("/assemblies/{assembly_id}/validate")
def validate_assembly(assembly_id: str, db: Session = Depends(get_db)) -> dict:
    assembly = db.get(LabScenarioAssemblyVersion, assembly_id)
    if not assembly:
        raise HTTPException(404, "Scenario assembly not found")
    return validate_assembly_payload(db, assembly)


@router.post("/assemblies/{assembly_id}/publish")
def publish_assembly(assembly_id: str, db: Session = Depends(get_db)) -> dict:
    assembly = editable_assembly(db, assembly_id)
    validation = validate_assembly_payload(db, assembly)
    if not validation["valid"]:
        raise HTTPException(422, validation)
    payload = scenario_assembly_payload(db, assembly)
    assembly.validation = validation
    assembly.checksum = sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    assembly.status = "published"; assembly.published_at = datetime.now(UTC)
    scenario = db.get(LabScenario, assembly.scenario_id)
    scenario.assembly_version_id = assembly.id
    scenario.model_version_id = assembly.composite_model_version_id
    db.commit(); db.refresh(assembly)
    return scenario_assembly_payload(db, assembly)


@router.get("/versions/{version_id}/asset-roles")
def asset_roles(version_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(LabModelVersion, version_id):
        raise HTTPException(404, "Model version not found")
    items = list(db.scalars(select(LabAssetActorRole).where(LabAssetActorRole.model_version_id == version_id).order_by(LabAssetActorRole.node_id, LabAssetActorRole.role)))
    return {"items": [{**row(item), "entity": row(db.get(Entity, item.entity_id))} for item in items]}


@router.post("/versions/{version_id}/asset-roles")
def add_asset_role(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    editable_version(db, version_id)
    node = db.get(LabNode, body.get("node_id"))
    if not node or node.model_version_id != version_id:
        raise HTTPException(404, "Asset node not found")
    if not db.get(Entity, body.get("entity_id")):
        raise HTTPException(404, "Actor entity not found")
    item = LabAssetActorRole(model_version_id=version_id, node_id=node.id, entity_id=body["entity_id"], role=body["role"], permissions=body.get("permissions", []), evidence=body.get("evidence", {}), valid_from=body.get("valid_from"), valid_to=body.get("valid_to"))
    db.add(item); db.commit(); db.refresh(item)
    return row(item)


@router.post("/process-sets")
def create_process_set(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    if not db.get(Entity, body.get("owner_entity_id")):
        raise HTTPException(422, "Process-set owner entity is required")
    process_set = LabProcessSet(id=body.get("id") or f"process-set-{uuid.uuid4().hex[:12]}", owner_entity_id=body["owner_entity_id"], name=body["name"], description=body.get("description", ""), scope_kind=body.get("scope_kind", "asset_class"))
    db.add(process_set); db.flush()
    version = LabProcessSetVersion(process_set_id=process_set.id, version=1, status="draft", name=body.get("version_name", process_set.name), valid_from=body.get("valid_from"), valid_to=body.get("valid_to"), evidence=body.get("evidence", []))
    db.add(version); db.commit(); db.refresh(version)
    return process_set_version_row(db, version)


@router.post("/process-set-versions/{version_id}/clone")
def clone_process_set_version(version_id: str, db: Session = Depends(get_db)) -> dict:
    source = db.get(LabProcessSetVersion, version_id)
    if not source:
        raise HTTPException(404, "Process-set version not found")
    number = (db.scalar(select(func.max(LabProcessSetVersion.version)).where(LabProcessSetVersion.process_set_id == source.process_set_id)) or 0) + 1
    clone = LabProcessSetVersion(process_set_id=source.process_set_id, version=number, status="draft", name=f"{source.name} · v{number}", valid_from=source.valid_from, valid_to=source.valid_to, evidence=deepcopy(source.evidence))
    db.add(clone); db.flush()
    for item in db.scalars(select(LabProcessSetWorkflow).where(LabProcessSetWorkflow.process_set_version_id == source.id)):
        db.add(LabProcessSetWorkflow(process_set_version_id=clone.id, workflow_version_id=item.workflow_version_id, scope_kind=item.scope_kind, scope_ref=item.scope_ref, start_event_type=item.start_event_type, start_mode=item.start_mode, correlation_key=item.correlation_key, priority=item.priority, evidence=deepcopy(item.evidence), enabled=item.enabled))
    db.commit(); db.refresh(clone)
    return process_set_version_row(db, clone)


@router.get("/process-sets/{version_id}")
def get_process_set(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabProcessSetVersion, version_id)
    if not version:
        raise HTTPException(404, "Process-set version not found")
    return process_set_version_row(db, version)


@router.patch("/process-set-versions/{version_id}")
def update_process_set_version(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabProcessSetVersion, version_id)
    if not version:
        raise HTTPException(404, "Process-set version not found")
    if version.status != "draft":
        raise HTTPException(409, "Published process-set versions are immutable")
    for field in ["name", "valid_from", "valid_to", "evidence"]:
        if field in body:
            setattr(version, field, body[field])
    db.commit(); db.refresh(version)
    return process_set_version_row(db, version)


@router.post("/process-set-versions/{version_id}/workflows")
def bind_process_set_workflow(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabProcessSetVersion, version_id)
    workflow_version = db.get(LabWorkflowVersion, body.get("workflow_version_id"))
    if not version or version.status != "draft":
        raise HTTPException(409, "Only draft process sets can be edited")
    if not workflow_version:
        raise HTTPException(404, "Workflow version not found")
    activation = LabProcessSetWorkflow(process_set_version_id=version_id, workflow_version_id=workflow_version.id, scope_kind=body.get("scope_kind", "actor"), scope_ref=body.get("scope_ref"), start_event_type=body["start_event_type"], start_mode=body.get("start_mode", "message"), correlation_key=body.get("correlation_key", "scenario_event"), priority=int(body.get("priority", 100)), evidence=body.get("evidence", {}), enabled=bool(body.get("enabled", True)))
    db.add(activation); db.commit(); db.refresh(activation)
    return row(activation)


@router.post("/process-set-versions/{version_id}/publish")
def publish_process_set(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabProcessSetVersion, version_id)
    if not version or version.status != "draft":
        raise HTTPException(409, "Only a draft process set can be published")
    activations = list(db.scalars(select(LabProcessSetWorkflow).where(LabProcessSetWorkflow.process_set_version_id == version_id, LabProcessSetWorkflow.enabled.is_(True))))
    errors = []
    if not version.evidence:
        errors.append("Process set must cite evidence")
    if not activations:
        errors.append("Process set must contain an enabled BPMN activation")
    if any(not (workflow := db.get(LabWorkflowVersion, item.workflow_version_id)) or workflow.status != "published" for item in activations):
        errors.append("All bound workflows must be published")
    if errors:
        raise HTTPException(422, {"errors": errors})
    payload = [{key: getattr(item, key) for key in ["workflow_version_id", "scope_kind", "scope_ref", "start_event_type", "start_mode", "correlation_key", "priority"]} for item in activations]
    version.status = "published"; version.published_at = datetime.now(UTC); version.checksum = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    db.commit(); db.refresh(version)
    return process_set_version_row(db, version)


@router.get("/workflows/{version_id}")
def workflow(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version: raise HTTPException(404, "Workflow not found")
    nodes, transitions = _workflow_rows(db, version_id)
    if not version.bpmn_xml and nodes:
        _refresh_workflow_xml(db, version)
        db.commit()
    return {"version": row(version), "nodes": [row(x) for x in nodes], "transitions": [row(x) for x in transitions], "presentation": _presentation_status(db, version), "test": phase_coverage(version.bpmn_xml, load_bindings()) if version.bpmn_xml else None}


@router.get("/test-bindings")
def list_test_bindings() -> list[dict[str, Any]]:
    """Bindings from the settings directory, without runner details."""
    return [
        {"id": key, "title": value.get("title", key), "phases": value.get("phases", [])}
        for key, value in sorted(load_bindings().items())
    ]


@router.post("/workflows")
def create_workflow(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    description = body.get("description") or f"Ця схема BPMN 2.0 описує операційний процес «{body['name']}». Додайте тут його призначення, умову запуску, область дії, відповідального учасника та очікувану послідовність кроків."
    item = LabWorkflow(id=body.get("id") or f"workflow-{uuid.uuid4().hex[:12]}", name=body["name"], description=description); db.add(item); db.flush()
    version = LabWorkflowVersion(workflow_id=item.id, version=1, status="draft", name=body.get("version_name", item.name), description=description, settings=body.get("settings", {})); db.add(version); db.flush()
    start = LabWorkflowNode(workflow_version_id=version.id, node_key="start", block_type="start_event", name="Отримано повідомлення про запуск", x=80, y=120, config={"event_definition": "message", "event_type": body.get("start_event", "workflow.start")}, python_code="")
    end = LabWorkflowNode(workflow_version_id=version.id, node_key="end", block_type="end_event", name="Процес завершено", x=350, y=120, config={"event_definition": "none", "outcome": "completed"}, python_code="")
    db.add_all([start, end]); db.flush()
    db.add(LabWorkflowTransition(workflow_version_id=version.id, source_node_id=start.id, target_node_id=end.id, condition_code="True", priority=100))
    db.flush(); _refresh_workflow_xml(db, version); db.commit()
    return workflow(version.id, db)


@router.patch("/workflows/{version_id}")
def update_workflow(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version: raise HTTPException(404, "Workflow not found")
    if version.status != "draft": raise HTTPException(409, "Published workflow versions are immutable")
    for field in ["name", "description"]:
        if field in body: setattr(version, field, body[field])
    if "name" in body:
        _refresh_workflow_xml(db, version)
    db.commit(); db.refresh(version)
    return workflow(version.id, db)


@router.get("/workflows/{version_id}/bpmn")
def workflow_bpmn(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    if not version.bpmn_xml:
        _refresh_workflow_xml(db, version)
        db.commit()
    return {
        "version_id": version.id,
        "profile": version.bpmn_profile,
        "bpmn_xml": version.bpmn_xml,
        "validation": version.validation,
    }


@router.put("/workflows/{version_id}/bpmn")
def save_workflow_bpmn(
    version_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict[str, Any]:
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    if version.status != "draft":
        raise HTTPException(409, "Only draft workflows can be edited")
    xml = body.get("bpmn_xml")
    if not isinstance(xml, str) or not xml.strip():
        raise HTTPException(422, "bpmn_xml is required")
    validation = _replace_workflow_projection(db, version, xml)
    if isinstance(body.get("description"), str):
        version.description = body["description"].strip()
    if isinstance(body.get("bpmn_svg"), str) and body["bpmn_svg"].strip():
        _store_presentation(db, version, body["bpmn_svg"])
    db.commit()
    return {**workflow(version.id, db), "validation": validation}


@router.put("/workflows/{version_id}/svg")
def save_workflow_svg(
    version_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Attach a drawing to any version, published ones included.

    The SVG is a presentation, not part of the immutable model, so it may be
    regenerated at any time — but only for the exact BPMN document it shows.
    """
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    svg = body.get("bpmn_svg")
    if not isinstance(svg, str) or not svg.strip():
        raise HTTPException(422, "bpmn_svg is required")
    if body.get("bpmn_sha256") != bpmn_sha256(version.bpmn_xml):
        raise HTTPException(409, "SVG was drawn from a different BPMN document")
    _store_presentation(db, version, svg)
    db.commit()
    return _presentation_status(db, version)


@router.get("/workflows/{version_id}/svg")
def workflow_svg(version_id: str, db: Session = Depends(get_db)) -> Response:
    version = db.get(LabWorkflowVersion, version_id)
    presentation = db.get(LabWorkflowPresentation, version_id)
    if not version or not presentation or not presentation.svg:
        raise HTTPException(404, "Workflow SVG not found")
    status = _presentation_status(db, version)["status"]
    return Response(
        presentation.svg,
        media_type="image/svg+xml",
        headers={"X-Aftertime-Svg-Status": status},
    )


@router.get("/workflows/{version_id}/export")
def export_workflow(version_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """BPMN and its SVG as one consistent pair, for files and agents."""
    version = db.get(LabWorkflowVersion, version_id)
    if not version:
        raise HTTPException(404, "Workflow not found")
    if not version.bpmn_xml:
        _refresh_workflow_xml(db, version)
        db.commit()
    presentation = db.get(LabWorkflowPresentation, version_id)
    return {
        "file_stem": f"{version.workflow_id}.v{version.version}",
        "workflow_id": version.workflow_id,
        "version_id": version.id,
        "version": version.version,
        "status": version.status,
        "name": version.name,
        "bpmn_xml": version.bpmn_xml,
        "bpmn_sha256": bpmn_sha256(version.bpmn_xml),
        "bpmn_svg": presentation.svg if presentation else "",
        "presentation": _presentation_status(db, version),
    }


@router.post("/workflows/{version_id}/debug")
def debug_workflow(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    node = db.get(LabWorkflowNode, body.get("node_id"))
    if not node or node.workflow_version_id != version_id: raise HTTPException(404, "Workflow node not found")
    if not node.python_code.strip(): return {"state": body.get("state", {}), "operations": [], "logs": [], "transition": "no_python_action"}
    return execute_script(node.python_code, body.get("action", "event"), {"time": body.get("time", datetime.now(UTC).isoformat()), "parameters": node.config, "state": body.get("state", {}), "inputs": body.get("inputs", {}), "snapshot": body.get("snapshot", {}), "seed": int(body.get("seed", 42))}, event=body.get("event", {}))


@router.post("/workflows/{version_id}/nodes")
def add_workflow_node(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version or version.status != "draft": raise HTTPException(409, "Only draft workflows can be edited")
    node = LabWorkflowNode(workflow_version_id=version_id, node_key=body.get("node_key") or f"{body['block_type']}-{uuid.uuid4().hex[:8]}", block_type=body["block_type"], name=body.get("name", body["block_type"]), x=float(body.get("x", 0)), y=float(body.get("y", 0)), config=body.get("config", {}), python_code=body.get("python_code", "")); db.add(node); db.flush(); _refresh_workflow_xml(db, version); db.commit(); db.refresh(node); return row(node)


@router.patch("/workflows/nodes/{node_id}")
def update_workflow_node(node_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    node = db.get(LabWorkflowNode, node_id); version = db.get(LabWorkflowVersion, node.workflow_version_id) if node else None
    if not node: raise HTTPException(404, "Workflow node not found")
    if version.status != "draft": raise HTTPException(409, "Published workflow versions are immutable")
    for field in ["name", "block_type", "x", "y", "config", "python_code"]:
        if field in body: setattr(node, field, body[field])
    db.flush(); _refresh_workflow_xml(db, version); db.commit(); db.refresh(node); return row(node)


@router.delete("/workflows/nodes/{node_id}", status_code=204)
def delete_workflow_node(node_id: str, db: Session = Depends(get_db)) -> None:
    node = db.get(LabWorkflowNode, node_id)
    version = db.get(LabWorkflowVersion, node.workflow_version_id) if node else None
    if not node: raise HTTPException(404, "Workflow node not found")
    if not version or version.status != "draft": raise HTTPException(409, "Only draft workflows can be edited")
    if node.block_type in {"start", "start_event"}: raise HTTPException(409, "The BPMN start event is required")
    db.execute(delete(LabWorkflowTransition).where(or_(LabWorkflowTransition.source_node_id == node.id, LabWorkflowTransition.target_node_id == node.id)))
    db.delete(node); db.flush(); _refresh_workflow_xml(db, version); db.commit()


@router.post("/workflows/{version_id}/transitions")
def add_workflow_transition(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version or version.status != "draft": raise HTTPException(409, "Only draft workflows can be edited")
    transition = LabWorkflowTransition(workflow_version_id=version_id, source_node_id=body["source_node_id"], target_node_id=body["target_node_id"], condition_code=body.get("condition_code", "True"), priority=int(body.get("priority", 100))); db.add(transition); db.flush(); _refresh_workflow_xml(db, version); db.commit(); db.refresh(transition); return row(transition)


@router.delete("/workflows/transitions/{transition_id}", status_code=204)
def delete_workflow_transition(transition_id: str, db: Session = Depends(get_db)) -> None:
    transition = db.get(LabWorkflowTransition, transition_id)
    version = db.get(LabWorkflowVersion, transition.workflow_version_id) if transition else None
    if not transition: raise HTTPException(404, "Workflow transition not found")
    if not version or version.status != "draft": raise HTTPException(409, "Only draft workflows can be edited")
    db.delete(transition); db.flush(); _refresh_workflow_xml(db, version); db.commit()


@router.post("/workflows/{version_id}/clone")
def clone_workflow(version_id: str, db: Session = Depends(get_db)) -> dict:
    source = db.get(LabWorkflowVersion, version_id)
    if not source: raise HTTPException(404, "Workflow not found")
    number = (db.scalar(select(func.max(LabWorkflowVersion.version)).where(LabWorkflowVersion.workflow_id == source.workflow_id)) or 0) + 1
    draft = LabWorkflowVersion(workflow_id=source.workflow_id, version=number, status="draft", name=f"{source.name} · draft", description=source.description, bpmn_xml=source.bpmn_xml, bpmn_profile=source.bpmn_profile, validation=deepcopy(source.validation), validated_at=source.validated_at, settings=deepcopy(source.settings)); db.add(draft); db.flush()
    mapping = {}
    for old in db.scalars(select(LabWorkflowNode).where(LabWorkflowNode.workflow_version_id == source.id)):
        new = LabWorkflowNode(workflow_version_id=draft.id, node_key=old.node_key, block_type=old.block_type, name=old.name, x=old.x, y=old.y, config=deepcopy(old.config), python_code=old.python_code); db.add(new); db.flush(); mapping[old.id] = new.id
    for old in db.scalars(select(LabWorkflowTransition).where(LabWorkflowTransition.workflow_version_id == source.id)):
        db.add(LabWorkflowTransition(workflow_version_id=draft.id, source_node_id=mapping[old.source_node_id], target_node_id=mapping[old.target_node_id], condition_code=old.condition_code, priority=old.priority))
    source_presentation = db.get(LabWorkflowPresentation, source.id)
    if source_presentation and source_presentation.svg and source_presentation.bpmn_sha256 == bpmn_sha256(draft.bpmn_xml):
        _store_presentation(db, draft, source_presentation.svg)
    db.commit(); db.refresh(draft)
    return workflow(draft.id, db)


@router.post("/workflows/{version_id}/validate")
def validate_workflow(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version: raise HTTPException(404, "Workflow not found")
    nodes, transitions = _workflow_rows(db, version_id)
    if not version.bpmn_xml:
        _refresh_workflow_xml(db, version)
    result = validate_bpmn(version.bpmn_xml)
    errors = list(result["errors"])
    warnings = list(result["warnings"])
    for node in nodes:
        try: compile(node.python_code or "pass", f"<workflow:{node.node_key}>", "exec")
        except SyntaxError as exc: errors.append(f"{node.name}: {exc}")
    coverage = phase_coverage(version.bpmn_xml, load_bindings())
    if coverage:
        errors.extend(coverage["errors"])
        warnings.extend(coverage["warnings"])
    result = {**result, "valid": not errors, "errors": errors, "warnings": warnings, "nodes": len(nodes), "transitions": len(transitions), "test": coverage}
    version.validation = result
    version.validated_at = datetime.now(UTC)
    db.commit()
    return result


@router.post("/workflows/{version_id}/publish")
def publish_workflow(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabWorkflowVersion, version_id)
    if not version or version.status != "draft": raise HTTPException(409, "Only a draft workflow can be published")
    result = validate_workflow(version_id, db)
    if not result["valid"]: raise HTTPException(422, result)
    version.status = "published"; version.published_at = datetime.now(UTC); db.commit(); db.refresh(version); return row(version)


@router.get("/scenarios/{scenario_id}/timeline")
def scenario_timeline(scenario_id: str, db: Session = Depends(get_db)) -> dict:
    scenario = db.get(LabScenario, scenario_id)
    if not scenario: raise HTTPException(404, "Scenario not found")
    return {"start": scenario.start_at, "incident": scenario.incident_at, "end": scenario.start_at + timedelta(seconds=scenario.duration_seconds), "events": [row(x) for x in db.scalars(select(LabScenarioEventTemplate).where(LabScenarioEventTemplate.scenario_id == scenario_id).order_by(LabScenarioEventTemplate.offset_seconds, LabScenarioEventTemplate.priority))]}


def reject_referenced_event_target(
    db: Session, target_kind: str, target_ids: list[str]
) -> None:
    if not target_ids:
        return
    action = db.scalar(
        select(LabEventAction)
        .where(
            LabEventAction.target_kind == target_kind,
            LabEventAction.target_id.in_(target_ids),
        )
        .limit(1)
    )
    if not action:
        return
    program = db.get(LabEventProgram, action.program_id)
    raise HTTPException(
        409,
        {
            "message": (
                "The graph object is used by an event action. "
                "Delete or retarget the action first."
            ),
            "program_id": action.program_id,
            "program_name": program.name if program else None,
            "action_id": action.id,
            "action_type": action.action_type,
            "target_kind": target_kind,
            "target_id": action.target_id,
        },
    )


def qualification_pilot_report(package: dict[str, Any]) -> dict[str, Any]:
    report = replay_qualification_package(package)
    return {
        **report,
        "scenario_id": package["scenario_id"],
        "name": package["name"],
        "definition": {
            "level": package["level"],
            "boundary": package["boundary"],
            "sources": package["sources"],
            "objects": package["objects"],
            "situations": package["situations"],
            "processes": [
                {
                    "id": process["id"],
                    "name": process["name"],
                    "format": process["format"],
                    "steps": process["steps"],
                }
                for process in package["processes"]
            ],
        },
    }


def stored_pilot_packages(db: Session) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for scenario_id in QUALIFICATION_CASES:
        scenario = db.get(LabScenario, scenario_id)
        if not scenario or not scenario.model_version_id:
            continue
        definition = qualification_definition_for_version(
            db, scenario.model_version_id
        )
        if definition:
            packages.append(deepcopy(definition.document))
    return packages


@router.get("/qualification")
def qualification(db: Session = Depends(get_db)) -> dict:
    project = db.scalar(select(LabProject).order_by(LabProject.created_at).limit(1))
    if not project or not project.published_version_id:
        raise HTTPException(409, "Published Model Lab snapshot is missing")
    results = []
    scenarios = [
        scenario
        for scenario in db.scalars(
            select(LabScenario)
            .where(LabScenario.id != "baseline")
            .order_by(LabScenario.name)
        )
        if not scenario.configuration.get("deprecated")
    ]
    for scenario in scenarios:
        replay = replay_scenario(db, scenario, scenario.model_version_id)
        model = db.get(LabModelVersion, scenario.model_version_id)
        checks = {"dedicated_model": bool(model and model.status == "published" and model.settings.get("qualification_scenario") == scenario.id), "historical_origin": scenario.origin == "historical", "source": bool(scenario.source_url), "calendar": len(scenario.events) >= int(scenario.expected.get("minimum_events", 1)), **replay["checks"]}
        results.append({"scenario_id": scenario.id, "name": scenario.name, "passed": all(checks.values()), "checks": checks, "outcome_checks": replay["outcome_checks"], "expected": scenario.expected, "observed": replay["observed"], "trace": replay["trace"]})
    pilots = [qualification_pilot_report(package) for package in stored_pilot_packages(db)]
    legacy_passed = all(item["passed"] for item in results) and len(results) == 15
    required_pilots = len(QUALIFICATION_CASES)
    pilots_passed = (
        all(item["passed"] for item in pilots) and len(pilots) == required_pilots
    )
    return {
        "passed": legacy_passed and pilots_passed,
        "passed_count": sum(item["passed"] for item in results),
        "required_count": 15,
        "results": results,
        "pilot_gate": {
            "passed": pilots_passed,
            "passed_count": sum(item["passed"] for item in pilots),
            "required_count": required_pilots,
            "results": pilots,
        },
    }


@router.get("/qualification/pilots")
def pilot_qualification(db: Session = Depends(get_db)) -> dict:
    results = [
        qualification_pilot_report(package)
        for package in stored_pilot_packages(db)
    ]
    required_count = len(QUALIFICATION_CASES)
    return {
        "passed": (
            all(item["passed"] for item in results)
            and len(results) == required_count
        ),
        "passed_count": sum(item["passed"] for item in results),
        "required_count": required_count,
        "results": results,
    }


@router.post("/versions/{version_id}/nodes")
def add_node(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = editable_version(db, version_id)
    component = db.get(LabComponentType, body.get("component_type_id"))
    if not component: raise HTTPException(404, "Unknown component type")
    name = body.get("name") or component.name
    key = stable_key(body.get("instance_key") or name, f"node-{uuid.uuid4().hex[:8]}")
    if db.scalar(select(LabNode).where(LabNode.model_version_id == version_id, LabNode.instance_key == key)):
        key = f"{key}-{uuid.uuid4().hex[:6]}"
    node = LabNode(model_version_id=version_id, component_type_id=component.id, name=name, instance_key=key, parent_node_id=body.get("parent_node_id"), x=float(body.get("x", 80)), y=float(body.get("y", 80)), parameters=body.get("parameters", deepcopy(component.parameters_schema)), initial_state=body.get("initial_state", deepcopy(component.initial_state)), python_code=body.get("python_code"), refinement_contract=body.get("refinement_contract", {}), port_overrides=body.get("port_overrides", {}), observable_overrides=body.get("observable_overrides", {}))
    db.add(node); touch(version); db.commit(); db.refresh(node); return row(node)


@router.patch("/nodes/{node_id}")
def update_node(node_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    node = db.get(LabNode, node_id)
    if not node: raise HTTPException(404, "Node not found")
    version = db.get(LabModelVersion, node.model_version_id)
    if version.status != "draft": raise HTTPException(409, "Published snapshots are immutable")
    for field in ["name", "x", "y", "parameters", "initial_state", "python_code", "refinement_contract", "parent_node_id", "port_overrides", "observable_overrides"]:
        if field in body: setattr(node, field, body[field])
    if any(field in body for field in ["name", "parameters", "initial_state", "python_code", "refinement_contract", "parent_node_id", "port_overrides", "observable_overrides"]): touch(version)
    db.commit(); db.refresh(node); return row(node)


@router.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: str, db: Session = Depends(get_db)):
    node = db.get(LabNode, node_id)
    if not node: raise HTTPException(404, "Node not found")
    version = db.get(LabModelVersion, node.model_version_id)
    if version.status != "draft": raise HTTPException(409, "Published snapshots are immutable")
    edges = list(db.scalars(select(LabEdge).where((LabEdge.source_node_id == node_id) | (LabEdge.target_node_id == node_id))))
    reject_referenced_event_target(db, "component", [node_id])
    reject_referenced_event_target(db, "connection", [edge.id for edge in edges])
    for edge in edges: db.delete(edge)
    db.delete(node); touch(version); db.commit()


@router.post("/versions/{version_id}/edges")
def add_edge(version_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    version = editable_version(db, version_id)
    source, target = db.get(LabNode, body["source_node_id"]), db.get(LabNode, body["target_node_id"])
    if not source or not target or source.model_version_id != version_id or target.model_version_id != version_id:
        raise HTTPException(422, "Both connection endpoints must belong to this model version")
    source_type, target_type = db.get(LabComponentType, source.component_type_id), db.get(LabComponentType, target.component_type_id)
    diagnostics = connection_diagnostics(source_type, body["source_port"], target_type, body["target_port"], source_overrides=source.port_overrides, target_overrides=target.port_overrides)
    if diagnostics: raise HTTPException(422, {"message": "Incompatible ports", "errors": diagnostics})
    source_contract = next(port for port in normalized_ports(source_type, source.port_overrides) if port["key"] == body["source_port"])
    key = stable_key(body.get("connection_key") or f"{source.instance_key or source.id}-{source_contract['key']}-{target.instance_key or target.id}-{body['target_port']}", f"connection-{uuid.uuid4().hex[:8]}")
    edge = LabEdge(model_version_id=version_id, connection_key=key, source_node_id=source.id, source_port=body["source_port"], target_node_id=target.id, target_port=body["target_port"], flow_type=body.get("flow_type", source_contract["domain"]), domain=source_contract["domain"], enabled=bool(body.get("enabled", True)), parameters=body.get("parameters", {}))
    db.add(edge); touch(version); db.commit(); db.refresh(edge); return row(edge)


@router.patch("/edges/{edge_id}")
def update_edge(edge_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    edge = db.get(LabEdge, edge_id)
    if not edge: raise HTTPException(404, "Connection not found")
    version = editable_version(db, edge.model_version_id)
    for field in ["parameters", "enabled"]:
        if field in body: setattr(edge, field, body[field])
    touch(version); db.commit(); db.refresh(edge); return row(edge)


@router.delete("/edges/{edge_id}", status_code=204)
def delete_edge(edge_id: str, db: Session = Depends(get_db)):
    edge = db.get(LabEdge, edge_id)
    if not edge: raise HTTPException(404, "Connection not found")
    version = editable_version(db, edge.model_version_id)
    reject_referenced_event_target(db, "connection", [edge_id])
    db.delete(edge); touch(version); db.commit()


@router.post("/versions/{version_id}/validate")
def validate(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabModelVersion, version_id)
    result = validate_graph(db, version_id); version.validation = result; db.commit(); return result


@router.post("/versions/{version_id}/publish")
def publish(version_id: str, db: Session = Depends(get_db)) -> dict:
    version = db.get(LabModelVersion, version_id); result = validate_graph(db, version_id)
    if not result["valid"]: raise HTTPException(422, result)
    version.status = "published"; version.validation = result; version.published_at = datetime.now(UTC)
    project = db.get(LabProject, version.project_id); project.published_version_id = version.id; project.active_draft_id = None
    db.commit(); return row(version)


@router.post("/versions/{version_id}/clone")
def clone(version_id: str, db: Session = Depends(get_db)) -> dict:
    source = db.get(LabModelVersion, version_id); project = db.get(LabProject, source.project_id)
    number = (db.scalar(select(func.max(LabModelVersion.version)).where(LabModelVersion.project_id == project.id)) or 0) + 1
    draft = LabModelVersion(project_id=project.id, version=number, status="draft", name=source.name, timezone=source.timezone, settings=deepcopy(source.settings)); db.add(draft); db.flush()
    node_mapping: dict[str, str] = {}
    edge_mapping: dict[str, str] = {}
    for old in db.scalars(select(LabNode).where(LabNode.model_version_id == source.id)):
        new = LabNode(model_version_id=draft.id, component_type_id=old.component_type_id, name=old.name, instance_key=old.instance_key or old.id, parent_node_id=old.parent_node_id, x=old.x, y=old.y, parameters=deepcopy(old.parameters), initial_state=deepcopy(old.initial_state), python_code=old.python_code, refinement_contract=deepcopy(old.refinement_contract), port_overrides=deepcopy(old.port_overrides), observable_overrides=deepcopy(old.observable_overrides)); db.add(new); db.flush(); node_mapping[old.id] = new.id
    for old in db.scalars(select(LabEdge).where(LabEdge.model_version_id == source.id)):
        new = LabEdge(model_version_id=draft.id, connection_key=old.connection_key or old.id, source_node_id=node_mapping[old.source_node_id], source_port=old.source_port, target_node_id=node_mapping[old.target_node_id], target_port=old.target_port, flow_type=old.flow_type, domain=old.domain, enabled=old.enabled, parameters=deepcopy(old.parameters))
        db.add(new); db.flush(); edge_mapping[old.id] = new.id
    for old in db.scalars(select(LabAssetActorRole).where(LabAssetActorRole.model_version_id == source.id)):
        if old.node_id in node_mapping:
            db.add(LabAssetActorRole(model_version_id=draft.id, node_id=node_mapping[old.node_id], entity_id=old.entity_id, role=old.role, permissions=deepcopy(old.permissions), evidence=deepcopy(old.evidence), valid_from=old.valid_from, valid_to=old.valid_to))
    clone_event_programs(db, source.id, draft.id, node_mapping, edge_mapping)
    clone_qualification_definition(db, source.id, draft.id)
    clone_monitor_templates(db, source.id, draft.id)
    project.active_draft_id = draft.id; db.commit(); db.refresh(draft); return row(draft)


@router.post("/components/{component_id}/test")
def test_component(component_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    component = db.get(LabComponentType, component_id)
    context = {"time": body.get("time", datetime.now(UTC).isoformat()), "parameters": body.get("parameters", {}), "state": body.get("state", component.initial_state), "inputs": body.get("inputs", {}), "seed": int(body.get("seed", 42))}
    code = body.get("python_code") or component.python_code
    try:
        initialized = execute_script(code, "initialize", context)
        context["state"] = initialized["state"]
        return execute_script(code, body.get("action", "tick"), context, event=body.get("event"), dt=float(body.get("dt", component.tick_seconds)))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/runs")
def start_run(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    scenario_id = body.get("scenario_id")
    scenario = db.get(LabScenario, scenario_id) if scenario_id else None
    assembly_id = body.get("assembly_version_id") or (scenario.assembly_version_id if scenario else None)
    assembly = db.get(LabScenarioAssemblyVersion, assembly_id) if assembly_id else None
    if assembly_id and (not assembly or assembly.status != "published" or not scenario or assembly.scenario_id != scenario.id):
        raise HTTPException(409, "Run requires the scenario's published assembly snapshot")
    requested_version_id = (
        assembly.composite_model_version_id
        if assembly and assembly.composite_model_version_id
        else scenario.model_version_id
        if scenario and scenario.model_version_id
        else body["model_version_id"]
    )
    version = db.get(LabModelVersion, requested_version_id)
    if not version or version.status != "published":
        raise HTTPException(409, "Run requires a published snapshot")
    start_value = (
        scenario.start_at.isoformat()
        if scenario
        else body.get("start_at") or datetime.now(UTC).isoformat()
    )
    start = datetime.fromisoformat(start_value)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    duration = min(
        int(
            body.get("duration_seconds")
            or (scenario.duration_seconds if scenario else 86400)
        ),
        366 * 86400,
    )
    idle_speed = max(0.001, float(body.get("idle_speed", body.get("speed", 3600))))
    run = LabRun(
        model_version_id=version.id,
        scenario_id=scenario.id if scenario else None,
        assembly_version_id=assembly.id if assembly else None,
        status="queued",
        current_time=start,
        end_time=start + timedelta(seconds=duration),
        speed=idle_speed,
        idle_speed=idle_speed,
        event_speed=max(0.001, float(body.get("event_speed", 60))),
        event_speed_duration_seconds=max(
            0, float(body.get("event_speed_duration_seconds", 10))
        ),
        event_speed_event_types=body.get("event_speed_event_types", []),
        seed=int(body.get("seed", scenario.seed if scenario else 42)),
        state={},
        summary={},
    )
    db.add(run)
    db.flush()
    monitor_template_id = body.get("monitor_template_id")
    monitor = (
        db.get(LabMonitorTemplate, monitor_template_id)
        if monitor_template_id
        else db.scalar(
            select(LabMonitorTemplate)
            .where(LabMonitorTemplate.model_version_id == version.id)
            .order_by(LabMonitorTemplate.created_at)
            .limit(1)
        )
    )
    db.add(
        LabRunMonitorSnapshot(
            run_id=run.id,
            template_id=monitor.id if monitor else None,
            template_revision=monitor.revision if monitor else 1,
            panels=deepcopy(monitor.panels) if monitor else [],
            observable_catalog=graph_catalog(db, version.id),
        )
    )
    scripted = bool(scenario and scenario.configuration.get("scenario_script_version_id"))
    scenario_events: list[dict[str, Any]] = []
    if scenario and not scripted:
        templates = list(
            db.scalars(
                select(LabScenarioEventTemplate)
                .where(LabScenarioEventTemplate.scenario_id == scenario.id)
                .order_by(
                    LabScenarioEventTemplate.offset_seconds,
                    LabScenarioEventTemplate.priority,
                    LabScenarioEventTemplate.id,
                )
            )
        )
        scenario_events = [
            {
                "at": (
                    scenario.start_at
                    + timedelta(
                        seconds=_scenario_offset_seconds(template.offset_seconds)
                    )
                ).isoformat(),
                "priority": template.priority,
                "event_type": template.event_type,
                "target_node_id": template.target_node_id,
                "origin": "scenario",
                "payload": deepcopy(template.payload),
            }
            for template in templates
        ]
    scheduled_events = scenario_events + list(body.get("events", []))
    for index, event in enumerate(scheduled_events, 1):
        at = datetime.fromisoformat(event["at"])
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
        db.add(
            LabRunEvent(
                run_id=run.id,
                sequence=index,
                scheduled_at=at,
                priority=int(event.get("priority", 100)),
                event_type=event["event_type"],
                target_node_id=event.get("target_node_id"),
                origin=event.get("origin", "scenario"),
                payload=event.get("payload", {}),
            )
        )
    db.flush()
    schedule_timer_start_events(db, run)
    db.commit()
    enqueue_run_or_503(db, run)
    return row(run)


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(LabRun, run_id)
    if not run: raise HTTPException(404, "Run not found")
    scenario = db.get(LabScenario, run.scenario_id) if run.scenario_id else None
    workflow_instances = [row(x) for x in db.scalars(select(LabWorkflowInstance).where(LabWorkflowInstance.run_id == run_id))]
    monitor = db.scalar(select(LabRunMonitorSnapshot).where(LabRunMonitorSnapshot.run_id == run_id))
    active_tasks = []
    for instance in db.scalars(select(LabWorkflowInstance).where(LabWorkflowInstance.run_id == run_id)):
        for token in db.scalars(select(LabWorkflowToken).where(LabWorkflowToken.instance_id == instance.id, LabWorkflowToken.status == "active")):
            node = db.get(LabWorkflowNode, token.node_id)
            if node and node.block_type in {"user_task", "manual_task"}:
                active_tasks.append({**row(token), "instance_id": instance.id, "node": row(node)})
    return {**row(run), "incident_at": scenario.incident_at if scenario else None, "events": [row(x) for x in db.scalars(select(LabRunEvent).where(LabRunEvent.run_id == run_id).order_by(LabRunEvent.scheduled_at, LabRunEvent.priority, LabRunEvent.sequence))], "checkpoints": [row(x) for x in db.scalars(select(LabCheckpoint).where(LabCheckpoint.run_id == run_id).order_by(LabCheckpoint.model_time.desc()).limit(20))], "work_orders": [row(x) for x in db.scalars(select(LabWorkOrder).where(LabWorkOrder.run_id == run_id))], "workflow_instances": workflow_instances, "active_workflow_tasks": active_tasks, "resource_transactions": [row(x) for x in db.scalars(select(LabResourceTransaction).where(LabResourceTransaction.run_id == run_id).order_by(LabResourceTransaction.model_time.desc()).limit(100))], "samples": [row(x) for x in db.scalars(select(LabRunSample).where(LabRunSample.run_id == run_id).order_by(LabRunSample.model_time.desc(), LabRunSample.sequence.desc()).limit(240))], "flows": [row(x) for x in db.scalars(select(LabFlowSample).where(LabFlowSample.run_id == run_id, LabFlowSample.model_time == run.current_time))], "monitor": row(monitor) if monitor else None}


@router.get("/runs/{run_id}/timeline")
def run_timeline(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(LabRun, run_id)
    if not run: raise HTTPException(404, "Run not found")
    scenario = db.get(LabScenario, run.scenario_id) if run.scenario_id else None
    return {"start": scenario.start_at if scenario else run.current_time, "incident": scenario.incident_at if scenario else None, "end": run.end_time, "current": run.current_time, "events": [row(x) for x in db.scalars(select(LabRunEvent).where(LabRunEvent.run_id == run_id).order_by(LabRunEvent.scheduled_at, LabRunEvent.priority, LabRunEvent.sequence))], "checkpoints": [row(x) for x in db.scalars(select(LabCheckpoint).where(LabCheckpoint.run_id == run_id).order_by(LabCheckpoint.model_time))]}


@router.get("/runs/{run_id}/samples")
def run_samples(run_id: str, db: Session = Depends(get_db)) -> dict:
    return {"samples": [row(x) for x in db.scalars(select(LabRunSample).where(LabRunSample.run_id == run_id).order_by(LabRunSample.model_time, LabRunSample.sequence))], "flows": [row(x) for x in db.scalars(select(LabFlowSample).where(LabFlowSample.run_id == run_id).order_by(LabFlowSample.model_time))]}


@router.get("/runs/{run_id}/workflows")
def run_workflows(run_id: str, db: Session = Depends(get_db)) -> dict:
    instances = list(db.scalars(select(LabWorkflowInstance).where(LabWorkflowInstance.run_id == run_id)))
    return {"instances": [row(x) for x in instances], "tokens": [row(x) for instance in instances for x in db.scalars(select(LabWorkflowToken).where(LabWorkflowToken.instance_id == instance.id).order_by(LabWorkflowToken.entered_at))]}


@router.post("/runs/{run_id}/workflow-tasks/{token_id}/complete")
def complete_workflow_task(
    run_id: str, token_id: str, body: dict[str, Any], db: Session = Depends(get_db)
) -> dict:
    run = db.get(LabRun, run_id)
    token = db.get(LabWorkflowToken, token_id)
    if not run or not token:
        raise HTTPException(404, "Run or workflow task not found")
    try:
        instance = complete_manual_workflow_task(
            db,
            run,
            token,
            completed_by=str(body.get("completed_by") or "operator"),
            result=body.get("result") or {},
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(instance)
    return {"instance": row(instance), "token": row(token)}


@router.post("/runs/{run_id}/seek")
def seek(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    source = db.get(LabRun, run_id)
    if not source: raise HTTPException(404, "Run not found")
    target = datetime.fromisoformat(body["at"]); target = target.replace(tzinfo=UTC) if target.tzinfo is None else target
    scenario = db.get(LabScenario, source.scenario_id) if source.scenario_id else None
    start = scenario.start_at if scenario else source.current_time
    target = max(start, min(target, source.end_time))
    source.status = "paused"
    if target > source.current_time:
        source.run_until = target; source.status = "running"; db.commit()
        enqueue_run_or_503(db, source)
        return get_run(source.id, db)
    checkpoint = db.scalar(select(LabCheckpoint).where(LabCheckpoint.run_id == run_id, LabCheckpoint.model_time <= target).order_by(LabCheckpoint.model_time.desc()).limit(1))
    if not checkpoint: raise HTTPException(409, "Move the run forward once to create the first checkpoint")
    child = LabRun(model_version_id=source.model_version_id, scenario_id=source.scenario_id, assembly_version_id=source.assembly_version_id, parent_run_id=source.id, status="paused", current_time=checkpoint.model_time, end_time=source.end_time, speed=source.speed, idle_speed=source.idle_speed, event_speed=source.event_speed, event_speed_duration_seconds=source.event_speed_duration_seconds, event_speed_event_types=deepcopy(source.event_speed_event_types), seed=source.seed, state=deepcopy(checkpoint.state), summary=deepcopy(checkpoint.metrics))
    db.add(child); db.flush()
    sequence = 1
    for event in db.scalars(select(LabRunEvent).where(LabRunEvent.run_id == source.id, LabRunEvent.scheduled_at > checkpoint.model_time).order_by(LabRunEvent.scheduled_at, LabRunEvent.priority, LabRunEvent.sequence)):
        db.add(LabRunEvent(run_id=child.id, sequence=sequence, scheduled_at=event.scheduled_at, priority=event.priority, event_type=event.event_type, target_node_id=event.target_node_id, origin="branch", payload=deepcopy(event.payload))); sequence += 1
    if scenario and not source.assembly_version_id and scenario.configuration.get("workflow_version_id"):
        db.add(LabWorkflowInstance(run_id=child.id, workflow_version_id=scenario.configuration["workflow_version_id"], status="ready", variables={"scenario_id": scenario.id, "branched_from": source.id}))
    db.commit()
    while child.current_time < target and child.status not in {"completed", "failed"}:
        process_next(db, child, max_step_seconds=max(.001, (target - child.current_time).total_seconds()))
    child.current_time = target; child.status = "paused"; db.commit()
    return get_run(child.id, db)


@router.post("/runs/{run_id}/user-commands")
def user_command(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    run = db.get(LabRun, run_id); scenario = db.get(LabScenario, run.scenario_id) if run else None
    if not run or not scenario: raise HTTPException(404, "Run not found")
    allowed = []
    workflow_version = db.get(LabWorkflowVersion, scenario.configuration.get("workflow_version_id"))
    if workflow_version: allowed = [item["id"] for item in workflow_version.settings.get("commands", [])]
    if body["command_type"] not in allowed: raise HTTPException(422, "Command is not declared by this workflow")
    command = LabUserCommand(run_id=run.id, model_time=run.current_time, command_type=body["command_type"], payload=body.get("payload", {}), status="processing"); db.add(command); db.flush()
    script = db.get(LabScriptVersion, scenario.configuration.get("process_script_version_id"))
    result = execute_script(script.code, "command", {"time": run.current_time.isoformat(), "parameters": scenario.configuration, "state": {}, "inputs": {}, "snapshot": run.state, "seed": run.seed}, event={"type": command.command_type, "payload": command.payload})
    state = deepcopy(run.state); _apply_script_result(db, run, state, result); run.state = state
    command.status = "completed"; command.result = {"operations": result.get("operations", []), "logs": result.get("logs", [])}; db.commit(); db.refresh(command); return row(command)


@router.post("/runs/{run_id}/commands")
def command(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    run = db.get(LabRun, run_id); action = body["action"]
    if action in {"pause", "resume", "stop", "step"}:
        if action == "pause": run.status = "paused"
        elif action == "resume": run.status = "running"; run.error = None
        elif action == "stop": run.status = "stopped"
        else: run.status = "paused"; process_next(db, run)
    elif action == "speed": run.speed = max(.001, float(body["value"])); run.idle_speed = run.speed
    elif action == "playback_settings":
        run.idle_speed = max(.001, float(body.get("idle_speed", run.idle_speed))); run.speed = run.idle_speed
        run.event_speed = max(.001, float(body.get("event_speed", run.event_speed)))
        run.event_speed_duration_seconds = max(0, float(body.get("event_speed_duration_seconds", run.event_speed_duration_seconds)))
        run.event_speed_event_types = list(body.get("event_speed_event_types", run.event_speed_event_types))
    else: raise HTTPException(422, "Unknown command")
    db.commit()
    if action == "resume":
        enqueue_run_or_503(db, run)
    return row(run)


@router.post("/runs/{run_id}/events")
def inject_event(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    run = db.get(LabRun, run_id)
    if not run: raise HTTPException(404, "Run not found")
    at = datetime.fromisoformat(body.get("at") or run.current_time.isoformat()); at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    if at < run.current_time: raise HTTPException(409, "Past injection requires branching from a checkpoint")
    target, target_ref = validated_event_target(db, run.model_version_id, body["event_type"], body)
    sequence = (db.scalar(select(func.max(LabRunEvent.sequence)).where(LabRunEvent.run_id == run_id)) or 0) + 1
    event = LabRunEvent(run_id=run_id, sequence=sequence, scheduled_at=at, priority=int(body.get("priority", 50)), event_type=body["event_type"], target_node_id=target, origin="manual", payload={**body.get("payload", {}), "_target": target_ref}); db.add(event); db.commit(); db.refresh(event); return row(event)


@router.post("/runs/{run_id}/branch")
def branch_run(run_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    source = db.get(LabRun, run_id)
    checkpoint = db.get(LabCheckpoint, body.get("checkpoint_id")) if body.get("checkpoint_id") else db.scalar(select(LabCheckpoint).where(LabCheckpoint.run_id == run_id).order_by(LabCheckpoint.model_time.desc()).limit(1))
    if not checkpoint: raise HTTPException(409, "No checkpoint available")
    child = LabRun(model_version_id=source.model_version_id, scenario_id=source.scenario_id, parent_run_id=source.id, status="paused", current_time=checkpoint.model_time, end_time=source.end_time, speed=source.speed, idle_speed=source.idle_speed, event_speed=source.event_speed, event_speed_duration_seconds=source.event_speed_duration_seconds, event_speed_event_types=deepcopy(source.event_speed_event_types), seed=source.seed, state=deepcopy(checkpoint.state), summary=deepcopy(checkpoint.metrics))
    db.add(child); db.flush()
    for event in body.get("events", []):
        at = datetime.fromisoformat(event["at"]); at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
        db.add(LabRunEvent(run_id=child.id, sequence=int(event.get("sequence", 1)), scheduled_at=at, priority=int(event.get("priority", 50)), event_type=event["event_type"], target_node_id=event.get("target_node_id"), origin="branch", payload=event.get("payload", {})))
    db.commit(); db.refresh(child); return row(child)


@router.get("/runs/{run_id}/stream")
def stream(run_id: str):
    def events():
        import time
        previous = {}
        for _ in range(3600):
            with SessionLocal() as db:
                run = db.get(LabRun, run_id)
                if not run: break
                latest_event = db.scalar(select(LabRunEvent).where(LabRunEvent.run_id == run_id, LabRunEvent.status == "processed").order_by(LabRunEvent.processed_at.desc()).limit(1))
                latest_sample = db.scalar(select(LabRunSample).where(LabRunSample.run_id == run_id).order_by(LabRunSample.model_time.desc()).limit(1))
                instance = db.scalar(select(LabWorkflowInstance).where(LabWorkflowInstance.run_id == run_id).limit(1))
                latest_token = db.scalar(select(LabWorkflowToken).where(LabWorkflowToken.instance_id == instance.id).order_by(LabWorkflowToken.entered_at.desc()).limit(1)) if instance else None
                latest_resource = db.scalar(select(LabResourceTransaction).where(LabResourceTransaction.run_id == run_id).order_by(LabResourceTransaction.model_time.desc(), LabResourceTransaction.id.desc()).limit(1))
                latest_command = db.scalar(select(LabUserCommand).where(LabUserCommand.run_id == run_id).order_by(LabUserCommand.model_time.desc(), LabUserCommand.id.desc()).limit(1))
                latest_checkpoint = db.scalar(select(LabCheckpoint).where(LabCheckpoint.run_id == run_id).order_by(LabCheckpoint.model_time.desc()).limit(1))
                latest_flows = [row(x) for x in db.scalars(select(LabFlowSample).where(LabFlowSample.run_id == run_id, LabFlowSample.model_time == run.current_time).limit(50))]
                channels = {"clock": {"status": run.status, "current_time": run.current_time.isoformat()}, "metric": row(latest_sample) if latest_sample else run.summary, "flow": latest_flows, "event": row(latest_event) if latest_event else None, "workflow_transition": row(latest_token) if latest_token else None, "resource_transaction": row(latest_resource) if latest_resource else None, "command_result": row(latest_command) if latest_command else None, "checkpoint": row(latest_checkpoint) if latest_checkpoint else None, "status": {"status": run.status, "error": run.error}}
                for channel, value in channels.items():
                    payload = json.dumps(value, default=str)
                    if payload != previous.get(channel):
                        yield f"event: {channel}\ndata: {payload}\n\n"; previous[channel] = payload
                if run.status in {"completed", "failed", "stopped"}: break
            time.sleep(.5)
    return StreamingResponse(events(), media_type="text/event-stream")
