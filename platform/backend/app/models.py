import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class SyncStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    level: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    symbol: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(String(50))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Relation(Base):
    __tablename__ = "relations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    subject_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    predicate: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    coefficient: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float, default=1)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_relations_subject_predicate", "subject_id", "predicate"),
        Index("ix_relations_object", "object_id"),
    )


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    url: Mapped[str] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(120))
    adapter: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(default=True)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    subject_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    property: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(80))
    actuality: Mapped[str] = mapped_column(String(30))
    epistemic_status: Mapped[str] = mapped_column(String(30))
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    uncertainty_low: Mapped[float | None] = mapped_column(Float)
    uncertainty_high: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)


class ProcessModel(Base):
    __tablename__ = "process_models"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    version: Mapped[str] = mapped_column(String(60))
    owner_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    duration_hours: Mapped[float] = mapped_column(Float)
    yield_factor: Mapped[float] = mapped_column(Float)
    specification: Mapped[dict] = mapped_column(JSONB)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    origin_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    destination_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    mode: Mapped[str] = mapped_column(String(40))
    distance_km: Mapped[float] = mapped_column(Float)
    capacity_per_day: Mapped[float] = mapped_column(Float)
    duration_hours: Mapped[float] = mapped_column(Float)
    availability: Mapped[float] = mapped_column(Float)
    loss_factor: Mapped[float] = mapped_column(Float)
    recovery_hours: Mapped[float] = mapped_column(Float)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    probability: Mapped[float] = mapped_column(Float)
    assumptions: Mapped[dict] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(60))


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    model_version: Mapped[str] = mapped_column(String(60))
    request: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.queued)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    records_seen: Mapped[int] = mapped_column(default=0)
    records_written: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabProject(Base):
    __tablename__ = "lab_projects"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(240), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active_draft_id: Mapped[str | None] = mapped_column(String(80))
    published_version_id: Mapped[str | None] = mapped_column(String(80))
    pack_id: Mapped[str | None] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabModelVersion(Base):
    __tablename__ = "lab_model_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("lab_projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    name: Mapped[str] = mapped_column(String(240))
    timezone: Mapped[str] = mapped_column(String(60), default="Europe/Kyiv")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    architecture_version: Mapped[int] = mapped_column(Integer, default=1)
    semantic_revision: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_lab_project_version"),)


class LabComponentType(Base):
    __tablename__ = "lab_component_types"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    level: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text, default="")
    parameters_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    initial_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    ports: Mapped[list] = mapped_column(JSONB, default=list)
    python_code: Mapped[str] = mapped_column(Text)
    tick_seconds: Mapped[float] = mapped_column(Float, default=3600)
    repairable: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LabNode(Base):
    __tablename__ = "lab_nodes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("lab_model_versions.id", ondelete="CASCADE"), index=True)
    component_type_id: Mapped[str] = mapped_column(ForeignKey("lab_component_types.id"))
    name: Mapped[str] = mapped_column(String(240))
    parent_node_id: Mapped[str | None] = mapped_column(String(80), index=True)
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    initial_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    python_code: Mapped[str | None] = mapped_column(Text)
    refinement_contract: Mapped[dict] = mapped_column(JSONB, default=dict)
    instance_key: Mapped[str | None] = mapped_column(String(120), index=True)
    port_overrides: Mapped[dict] = mapped_column(JSONB, default=dict)
    observable_overrides: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("model_version_id", "instance_key", name="uq_lab_node_instance_key"),
    )


class LabEdge(Base):
    __tablename__ = "lab_edges"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("lab_model_versions.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("lab_nodes.id", ondelete="CASCADE"))
    source_port: Mapped[str] = mapped_column(String(80))
    target_node_id: Mapped[str] = mapped_column(ForeignKey("lab_nodes.id", ondelete="CASCADE"))
    target_port: Mapped[str] = mapped_column(String(80))
    flow_type: Mapped[str] = mapped_column(String(60))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    connection_key: Mapped[str | None] = mapped_column(String(120), index=True)
    domain: Mapped[str] = mapped_column(String(60), default="legacy.energy", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("model_version_id", "connection_key", name="uq_lab_edge_connection_key"),
    )


class LabScenario(Base):
    __tablename__ = "lab_scenarios"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("lab_model_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(40), default="test")
    source_url: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    events: Mapped[list] = mapped_column(JSONB, default=list)
    expected: Mapped[dict] = mapped_column(JSONB, default=dict)
    incident_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pre_event_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    post_event_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    assembly_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("lab_scenario_assembly_versions.id", use_alter=True), index=True
    )


class LabRun(Base):
    __tablename__ = "lab_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("lab_model_versions.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("lab_scenarios.id"))
    assembly_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("lab_scenario_assembly_versions.id"), index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    current_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    speed: Mapped[float] = mapped_column(Float, default=3600)
    idle_speed: Mapped[float] = mapped_column(Float, default=3600)
    event_speed: Mapped[float] = mapped_column(Float, default=60)
    event_speed_duration_seconds: Mapped[float] = mapped_column(Float, default=10)
    event_speed_event_types: Mapped[list] = mapped_column(JSONB, default=list)
    event_speed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seed: Mapped[int] = mapped_column(Integer, default=42)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabRunEvent(Base):
    __tablename__ = "lab_run_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    target_node_id: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[str] = mapped_column(String(40), default="scenario")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_lab_run_event_sequence"),)


class LabCheckpoint(Base):
    __tablename__ = "lab_checkpoints"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    model_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabComponentTest(Base):
    __tablename__ = "lab_component_tests"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    component_type_id: Mapped[str] = mapped_column(ForeignKey("lab_component_types.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    fixture: Mapped[dict] = mapped_column(JSONB, default=dict)
    expected: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_result: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabResourcePool(Base):
    __tablename__ = "lab_resource_pools"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(30), index=True)
    class_name: Mapped[str] = mapped_column(String(160))
    available: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    external_available: Mapped[int] = mapped_column(Integer, default=0)
    lead_time_hours: Mapped[float] = mapped_column(Float, default=0)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabWorkOrder(Base):
    __tablename__ = "lab_work_orders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="diagnosis")
    required_resources: Mapped[dict] = mapped_column(JSONB, default=dict)
    hazard_delay_hours: Mapped[float] = mapped_column(Float, default=0)
    repair_hours: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    workflow_instance_id: Mapped[str | None] = mapped_column(String(80), index=True)
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabScript(Base):
    __tablename__ = "lab_scripts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    description: Mapped[str] = mapped_column(Text, default="")


class LabScriptVersion(Base):
    __tablename__ = "lab_script_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(ForeignKey("lab_scripts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    code: Mapped[str] = mapped_column(Text)
    contract: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("script_id", "version", name="uq_lab_script_version"),)


class LabScriptDependency(Base):
    __tablename__ = "lab_script_dependencies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("lab_script_versions.id", ondelete="CASCADE"), index=True)
    dependency_version_id: Mapped[str] = mapped_column(ForeignKey("lab_script_versions.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(80))


class LabScriptTestCase(Base):
    __tablename__ = "lab_script_test_cases"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("lab_script_versions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(40), default="event")
    fixture: Mapped[dict] = mapped_column(JSONB, default=dict)
    expected: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabScriptTestResult(Base):
    __tablename__ = "lab_script_test_results"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    test_case_id: Mapped[str] = mapped_column(ForeignKey("lab_script_test_cases.id", ondelete="CASCADE"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabWorkflow(Base):
    __tablename__ = "lab_workflows"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    pack_id: Mapped[str | None] = mapped_column(String(80), index=True)


class LabPack(Base):
    """A domain model pack installed into this Aftertime (energy, rental, …)."""

    __tablename__ = "lab_packs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    version: Mapped[str] = mapped_column(String(40), default="0")
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabWorkflowVersion(Base):
    __tablename__ = "lab_workflow_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("lab_workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    bpmn_xml: Mapped[str] = mapped_column(Text, default="")
    bpmn_profile: Mapped[str] = mapped_column(String(80), default="aftertime-executable-1")
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_lab_workflow_version"),)


class LabWorkflowPresentation(Base):
    """SVG drawing of a workflow version; kept apart so workflow rows stay small."""

    __tablename__ = "lab_workflow_presentations"
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("lab_workflow_versions.id", ondelete="CASCADE"), primary_key=True
    )
    svg: Mapped[str] = mapped_column(Text, default="")
    bpmn_sha256: Mapped[str] = mapped_column(String(64), default="")
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LabE2eRun(Base):
    """An end-to-end test run of a bound process, executed by the external bridge."""

    __tablename__ = "lab_e2e_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workflow_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("lab_workflow_versions.id", ondelete="CASCADE"), index=True
    )
    # "binding": the test case bound to a BPMN process; "center": suites or cases chosen by hand.
    kind: Mapped[str] = mapped_column(String(20), default="binding")
    target: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[dict | None] = mapped_column(JSONB)
    # Phase state changes with time, to replay the run: [{"t", "phase", "state"} | {"t", "status"}].
    timeline: Mapped[list] = mapped_column(JSONB, default=list)
    # Screen recording: {"status": packing|ready|failed, "started_at", "duration_ms", "frames", "size", "error"}.
    recording: Mapped[dict | None] = mapped_column(JSONB)
    binding: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    center_job_id: Mapped[str | None] = mapped_column(String(80))
    queue_ahead: Mapped[int | None] = mapped_column(Integer)
    states: Mapped[list] = mapped_column(JSONB, default=list)
    log: Mapped[list] = mapped_column(JSONB, default=list)
    folder: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportLaunch(Base):
    """One imported set of test results in the Allure results format."""

    __tablename__ = "report_launches"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    executor: Mapped[dict] = mapped_column(JSONB, default=dict)
    environment: Mapped[dict] = mapped_column(JSONB, default=dict)
    categories: Mapped[list] = mapped_column(JSONB, default=list)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    stop: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    e2e_run_id: Mapped[str | None] = mapped_column(String(80), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportArchivedTest(Base):
    """A test hidden from the latest results (removed or renamed in the test suite)."""

    __tablename__ = "report_archived_tests"
    history_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabTest(Base):
    """An end-to-end test case of the test suite, kept in Aftertime from the control center catalog."""

    __tablename__ = "lab_tests"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    suites: Mapped[list] = mapped_column(JSONB, default=list)
    feature: Mapped[str | None] = mapped_column(Text)
    number: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CenterSnapshot(Base):
    """Latest state of the external E2E control center, pushed by the bridge."""

    __tablename__ = "center_snapshots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="center")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportResult(Base):
    __tablename__ = "report_results"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    launch_id: Mapped[str] = mapped_column(ForeignKey("report_launches.id", ondelete="CASCADE"), index=True)
    history_id: Mapped[str] = mapped_column(String(200), index=True)
    full_name: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), index=True)
    parent_suite: Mapped[str] = mapped_column(Text, default="")
    suite: Mapped[str] = mapped_column(Text, default="")
    sub_suite: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReportAttachment(Base):
    __tablename__ = "report_attachments"
    source: Mapped[str] = mapped_column(String(200), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    stored: Mapped[bool] = mapped_column(Boolean, default=False)


class LabE2eFrame(Base):
    """A screen frame of the machine under test, taken during an E2E run."""

    __tablename__ = "lab_e2e_frames"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_e2e_runs.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    phase: Mapped[str | None] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer, default=0)


class LabWorkflowDefinitionBackup(Base):
    __tablename__ = "lab_workflow_definition_backups"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workflow_version_id: Mapped[str] = mapped_column(String(80), index=True)
    reason: Mapped[str] = mapped_column(String(120), default="bpmn-2-migration")
    bpmn_xml: Mapped[str] = mapped_column(Text, default="")
    nodes: Mapped[list] = mapped_column(JSONB, default=list)
    transitions: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabProcessSet(Base):
    __tablename__ = "lab_process_sets"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    owner_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    scope_kind: Mapped[str] = mapped_column(String(40), default="asset_class", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabProcessSetVersion(Base):
    __tablename__ = "lab_process_set_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    process_set_id: Mapped[str] = mapped_column(ForeignKey("lab_process_sets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    name: Mapped[str] = mapped_column(String(240))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    checksum: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("process_set_id", "version", name="uq_lab_process_set_version"),)


class LabProcessSetWorkflow(Base):
    __tablename__ = "lab_process_set_workflows"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    process_set_version_id: Mapped[str] = mapped_column(ForeignKey("lab_process_set_versions.id", ondelete="CASCADE"), index=True)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_versions.id"), index=True)
    scope_kind: Mapped[str] = mapped_column(String(40), default="actor")
    scope_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    start_event_type: Mapped[str] = mapped_column(String(80), index=True)
    start_mode: Mapped[str] = mapped_column(String(30), default="message")
    correlation_key: Mapped[str] = mapped_column(String(120), default="scenario_event")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("process_set_version_id", "workflow_version_id", "start_event_type", "scope_kind", "scope_ref", name="uq_lab_process_activation"),
    )


class LabAssetActorRole(Base):
    __tablename__ = "lab_asset_actor_roles"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("lab_model_versions.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("lab_nodes.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    role: Mapped[str] = mapped_column(String(40), index=True)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("model_version_id", "node_id", "entity_id", "role", name="uq_lab_asset_actor_role"),
    )


class LabScenarioAssemblyVersion(Base):
    __tablename__ = "lab_scenario_assembly_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("lab_scenarios.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    name: Mapped[str] = mapped_column(String(240))
    composite_model_version_id: Mapped[str | None] = mapped_column(ForeignKey("lab_model_versions.id"), index=True)
    checksum: Mapped[str | None] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_lab_scenario_assembly_version"),)


class LabScenarioAssemblyMember(Base):
    __tablename__ = "lab_scenario_assembly_members"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    assembly_version_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("lab_model_versions.id"), index=True)
    participant_kind: Mapped[str] = mapped_column(String(40), default="organization")
    role: Mapped[str] = mapped_column(String(40), default="operator")
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("assembly_version_id", "alias", name="uq_lab_assembly_member_alias"),)


class LabScenarioProcessBinding(Base):
    __tablename__ = "lab_scenario_process_bindings"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    assembly_version_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_members.id", ondelete="CASCADE"), index=True)
    process_set_version_id: Mapped[str] = mapped_column(ForeignKey("lab_process_set_versions.id"), index=True)
    __table_args__ = (UniqueConstraint("assembly_version_id", "member_id", "process_set_version_id", name="uq_lab_scenario_process_binding"),)


class LabScenarioAssemblyNodeMap(Base):
    __tablename__ = "lab_scenario_assembly_node_map"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    assembly_version_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_members.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(80))
    source_node_key: Mapped[str] = mapped_column(String(160), index=True)
    composite_node_id: Mapped[str] = mapped_column(ForeignKey("lab_nodes.id", ondelete="CASCADE"), index=True)
    qualified_ref: Mapped[str] = mapped_column(String(260), index=True)
    __table_args__ = (
        UniqueConstraint("assembly_version_id", "qualified_ref", name="uq_lab_assembly_qualified_ref"),
        UniqueConstraint("assembly_version_id", "composite_node_id", name="uq_lab_assembly_composite_node"),
    )


class LabScenarioAssemblyLink(Base):
    __tablename__ = "lab_scenario_assembly_links"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    assembly_version_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), index=True)
    source_member_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_members.id"))
    source_node_key: Mapped[str] = mapped_column(String(160))
    source_port: Mapped[str] = mapped_column(String(80))
    target_member_id: Mapped[str] = mapped_column(ForeignKey("lab_scenario_assembly_members.id"))
    target_node_key: Mapped[str] = mapped_column(String(160))
    target_port: Mapped[str] = mapped_column(String(80))
    flow_type: Mapped[str] = mapped_column(String(60))
    domain: Mapped[str] = mapped_column(String(60), default="legacy.energy")
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabWorkflowNode(Base):
    __tablename__ = "lab_workflow_nodes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_versions.id", ondelete="CASCADE"), index=True)
    node_key: Mapped[str] = mapped_column(String(80))
    block_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    python_code: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("workflow_version_id", "node_key", name="uq_lab_workflow_node_key"),)


class LabWorkflowTransition(Base):
    __tablename__ = "lab_workflow_transitions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_versions.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_nodes.id", ondelete="CASCADE"))
    target_node_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_nodes.id", ondelete="CASCADE"))
    condition_code: Mapped[str] = mapped_column(Text, default="True")
    priority: Mapped[int] = mapped_column(Integer, default=100)


class LabWorkflowInstance(Base):
    __tablename__ = "lab_workflow_instances"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    current_node_id: Mapped[str | None] = mapped_column(String(80))
    variables: Mapped[dict] = mapped_column(JSONB, default=dict)
    assembly_member_id: Mapped[str | None] = mapped_column(ForeignKey("lab_scenario_assembly_members.id"), index=True)
    process_set_version_id: Mapped[str | None] = mapped_column(ForeignKey("lab_process_set_versions.id"), index=True)
    trigger_event_id: Mapped[str | None] = mapped_column(ForeignKey("lab_run_events.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    target_ref: Mapped[str | None] = mapped_column(String(260), index=True)


class LabWorkflowToken(Base):
    __tablename__ = "lab_workflow_tokens"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(ForeignKey("lab_workflow_instances.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="active")
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabScenarioEventTemplate(Base):
    __tablename__ = "lab_scenario_event_templates"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("lab_scenarios.id", ondelete="CASCADE"), index=True)
    offset_seconds: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    target_node_id: Mapped[str | None] = mapped_column(String(80))
    phase: Mapped[str] = mapped_column(String(40), default="scenario")
    title: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence_kind: Mapped[str] = mapped_column(String(20), default="assumed")
    source_url: Mapped[str | None] = mapped_column(Text)
    source_note: Mapped[str] = mapped_column(Text, default="")


class LabRunSample(Base):
    __tablename__ = "lab_run_samples"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    model_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    resources: Mapped[dict] = mapped_column(JSONB, default=dict)
    emissions: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabResourceTransaction(Base):
    __tablename__ = "lab_resource_transactions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    model_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resource_id: Mapped[str] = mapped_column(String(80), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30))
    workflow_instance_id: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabFlowSample(Base):
    __tablename__ = "lab_flow_samples"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    model_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_node_id: Mapped[str] = mapped_column(String(80))
    target_node_id: Mapped[str] = mapped_column(String(80))
    flow_type: Mapped[str] = mapped_column(String(50))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30))


class LabMetricDefinition(Base):
    __tablename__ = "lab_metric_definitions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(30), default="")
    category: Mapped[str] = mapped_column(String(40), default="system")
    format: Mapped[dict] = mapped_column(JSONB, default=dict)


class LabDashboardDefinition(Base):
    __tablename__ = "lab_dashboard_definitions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    script_version_id: Mapped[str | None] = mapped_column(ForeignKey("lab_script_versions.id"))
    panels: Mapped[list] = mapped_column(JSONB, default=list)


class LabObservableDefinition(Base):
    """A discoverable value exposed by a component, port, connection or system."""

    __tablename__ = "lab_observable_definitions"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    component_type_id: Mapped[str | None] = mapped_column(ForeignKey("lab_component_types.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(30), default="component", index=True)
    port_key: Mapped[str | None] = mapped_column(String(80))
    metric_key: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(40), default="")
    value_type: Mapped[str] = mapped_column(String(30), default="number")
    path: Mapped[str] = mapped_column(String(240))
    aggregation: Mapped[str] = mapped_column(String(30), default="last")
    recording: Mapped[str] = mapped_column(String(30), default="aggregate")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class LabMonitorTemplate(Base):
    __tablename__ = "lab_monitor_templates"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("lab_model_versions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    panels: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabRunMonitorSnapshot(Base):
    __tablename__ = "lab_run_monitor_snapshots"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), unique=True, index=True)
    template_id: Mapped[str | None] = mapped_column(String(80))
    template_revision: Mapped[int] = mapped_column(Integer, default=1)
    panels: Mapped[list] = mapped_column(JSONB, default=list)
    observable_catalog: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabEventType(Base):
    __tablename__ = "lab_event_types"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(30), default="stimulus", index=True)
    target_kinds: Mapped[list] = mapped_column(JSONB, default=list)
    domains: Mapped[list] = mapped_column(JSONB, default=list)
    required_capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    payload_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    allow_broadcast: Mapped[bool] = mapped_column(Boolean, default=False)


class LabEventProgram(Base):
    __tablename__ = "lab_event_programs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("lab_model_versions.id", ondelete="CASCADE"), index=True
    )
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("lab_event_types.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    layout: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "event_type_id", name="uq_lab_event_program_type"
        ),
    )


class LabQualificationDefinition(Base):
    """Editable strict event definition owned by a model-version snapshot."""

    __tablename__ = "lab_qualification_definitions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("lab_model_versions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    package_id: Mapped[str] = mapped_column(String(100), index=True)
    scenario_id: Mapped[str] = mapped_column(String(100), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "model_version_id",
            "package_id",
            name="uq_lab_qualification_definition_package",
        ),
    )


class LabEventAction(Base):
    __tablename__ = "lab_event_actions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("lab_event_programs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(60), index=True)
    target_kind: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(80))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    __table_args__ = (
        UniqueConstraint("program_id", "sequence", name="uq_lab_event_action_sequence"),
    )


class LabCepRule(Base):
    __tablename__ = "lab_cep_rules"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("lab_scenarios.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    condition: Mapped[dict] = mapped_column(JSONB, default=dict)
    emit: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LabUserCommand(Base):
    __tablename__ = "lab_user_commands"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    model_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    command_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
