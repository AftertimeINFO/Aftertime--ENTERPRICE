"""Add participant-owned process sets and immutable scenario assemblies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0047_participant_processes"
down_revision: str | None = "0046_label_ukraine_attacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_process_sets"):
        return
    op.create_table(
        "lab_process_sets",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("owner_entity_id", sa.String(80), sa.ForeignKey("entities.id"), nullable=False, index=True),
        sa.Column("name", sa.String(240), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope_kind", sa.String(40), nullable=False, server_default="asset_class", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "lab_process_set_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("process_set_id", sa.String(80), sa.ForeignKey("lab_process_sets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("evidence", JSONB(), nullable=False, server_default="[]"),
        sa.Column("checksum", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("process_set_id", "version", name="uq_lab_process_set_version"),
    )
    op.create_table(
        "lab_process_set_workflows",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("process_set_version_id", sa.String(80), sa.ForeignKey("lab_process_set_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("workflow_version_id", sa.String(80), sa.ForeignKey("lab_workflow_versions.id"), nullable=False, index=True),
        sa.Column("scope_kind", sa.String(40), nullable=False, server_default="actor"),
        sa.Column("scope_ref", sa.String(160), index=True),
        sa.Column("start_event_type", sa.String(80), nullable=False, index=True),
        sa.Column("start_mode", sa.String(30), nullable=False, server_default="message"),
        sa.Column("correlation_key", sa.String(120), nullable=False, server_default="scenario_event"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("process_set_version_id", "workflow_version_id", "start_event_type", "scope_kind", "scope_ref", name="uq_lab_process_activation"),
    )
    op.create_table(
        "lab_asset_actor_roles",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("model_version_id", sa.String(80), sa.ForeignKey("lab_model_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("node_id", sa.String(80), sa.ForeignKey("lab_nodes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entity_id", sa.String(80), sa.ForeignKey("entities.id"), nullable=False, index=True),
        sa.Column("role", sa.String(40), nullable=False, index=True),
        sa.Column("permissions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("model_version_id", "node_id", "entity_id", "role", name="uq_lab_asset_actor_role"),
    )
    op.create_table(
        "lab_scenario_assembly_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("scenario_id", sa.String(80), sa.ForeignKey("lab_scenarios.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("composite_model_version_id", sa.String(80), sa.ForeignKey("lab_model_versions.id"), index=True),
        sa.Column("checksum", sa.String(64)),
        sa.Column("validation", JSONB(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("scenario_id", "version", name="uq_lab_scenario_assembly_version"),
    )
    op.create_table(
        "lab_scenario_assembly_members",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("assembly_version_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("alias", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), sa.ForeignKey("entities.id"), nullable=False, index=True),
        sa.Column("model_version_id", sa.String(80), sa.ForeignKey("lab_model_versions.id"), index=True),
        sa.Column("participant_kind", sa.String(40), nullable=False, server_default="organization"),
        sa.Column("role", sa.String(40), nullable=False, server_default="operator"),
        sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("assembly_version_id", "alias", name="uq_lab_assembly_member_alias"),
    )
    op.create_table(
        "lab_scenario_process_bindings",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("assembly_version_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("member_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_members.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("process_set_version_id", sa.String(80), sa.ForeignKey("lab_process_set_versions.id"), nullable=False, index=True),
        sa.UniqueConstraint("assembly_version_id", "member_id", "process_set_version_id", name="uq_lab_scenario_process_binding"),
    )
    op.create_table(
        "lab_scenario_assembly_node_map",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("assembly_version_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("member_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_members.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_node_id", sa.String(80), nullable=False),
        sa.Column("source_node_key", sa.String(160), nullable=False, index=True),
        sa.Column("composite_node_id", sa.String(80), sa.ForeignKey("lab_nodes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("qualified_ref", sa.String(260), nullable=False, index=True),
        sa.UniqueConstraint("assembly_version_id", "qualified_ref", name="uq_lab_assembly_qualified_ref"),
        sa.UniqueConstraint("assembly_version_id", "composite_node_id", name="uq_lab_assembly_composite_node"),
    )
    op.create_table(
        "lab_scenario_assembly_links",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("assembly_version_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_member_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_members.id"), nullable=False),
        sa.Column("source_node_key", sa.String(160), nullable=False),
        sa.Column("source_port", sa.String(80), nullable=False),
        sa.Column("target_member_id", sa.String(80), sa.ForeignKey("lab_scenario_assembly_members.id"), nullable=False),
        sa.Column("target_node_key", sa.String(160), nullable=False),
        sa.Column("target_port", sa.String(80), nullable=False),
        sa.Column("flow_type", sa.String(60), nullable=False),
        sa.Column("domain", sa.String(60), nullable=False, server_default="legacy.energy"),
        sa.Column("parameters", JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column("lab_scenarios", sa.Column("assembly_version_id", sa.String(80)))
    op.create_index("ix_lab_scenarios_assembly_version_id", "lab_scenarios", ["assembly_version_id"])
    op.create_foreign_key("fk_lab_scenario_assembly", "lab_scenarios", "lab_scenario_assembly_versions", ["assembly_version_id"], ["id"])
    op.add_column("lab_runs", sa.Column("assembly_version_id", sa.String(80)))
    op.create_index("ix_lab_runs_assembly_version_id", "lab_runs", ["assembly_version_id"])
    op.create_foreign_key("fk_lab_run_assembly", "lab_runs", "lab_scenario_assembly_versions", ["assembly_version_id"], ["id"])
    for column, target in [
        ("assembly_member_id", "lab_scenario_assembly_members.id"),
        ("process_set_version_id", "lab_process_set_versions.id"),
        ("trigger_event_id", "lab_run_events.id"),
    ]:
        op.add_column("lab_workflow_instances", sa.Column(column, sa.String(80)))
        op.create_index(f"ix_lab_workflow_instances_{column}", "lab_workflow_instances", [column])
        op.create_foreign_key(f"fk_lab_workflow_instance_{column}", "lab_workflow_instances", target.split(".")[0], [column], [target.split(".")[1]])
    op.add_column("lab_workflow_instances", sa.Column("correlation_id", sa.String(160)))
    op.add_column("lab_workflow_instances", sa.Column("target_ref", sa.String(260)))
    op.create_index("ix_lab_workflow_instances_correlation_id", "lab_workflow_instances", ["correlation_id"])
    op.create_index("ix_lab_workflow_instances_target_ref", "lab_workflow_instances", ["target_ref"])


def downgrade() -> None:
    op.drop_index("ix_lab_workflow_instances_target_ref", table_name="lab_workflow_instances")
    op.drop_index("ix_lab_workflow_instances_correlation_id", table_name="lab_workflow_instances")
    op.drop_column("lab_workflow_instances", "target_ref")
    op.drop_column("lab_workflow_instances", "correlation_id")
    for column in ["trigger_event_id", "process_set_version_id", "assembly_member_id"]:
        op.drop_constraint(f"fk_lab_workflow_instance_{column}", "lab_workflow_instances", type_="foreignkey")
        op.drop_index(f"ix_lab_workflow_instances_{column}", table_name="lab_workflow_instances")
        op.drop_column("lab_workflow_instances", column)
    op.drop_constraint("fk_lab_run_assembly", "lab_runs", type_="foreignkey")
    op.drop_index("ix_lab_runs_assembly_version_id", table_name="lab_runs")
    op.drop_column("lab_runs", "assembly_version_id")
    op.drop_constraint("fk_lab_scenario_assembly", "lab_scenarios", type_="foreignkey")
    op.drop_index("ix_lab_scenarios_assembly_version_id", table_name="lab_scenarios")
    op.drop_column("lab_scenarios", "assembly_version_id")
    op.drop_table("lab_scenario_assembly_links")
    op.drop_table("lab_scenario_assembly_node_map")
    op.drop_table("lab_scenario_process_bindings")
    op.drop_table("lab_scenario_assembly_members")
    op.drop_table("lab_scenario_assembly_versions")
    op.drop_table("lab_asset_actor_roles")
    op.drop_table("lab_process_set_workflows")
    op.drop_table("lab_process_set_versions")
    op.drop_table("lab_process_sets")
