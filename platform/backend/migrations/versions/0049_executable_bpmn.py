"""Store canonical executable BPMN definitions and migration backups."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0049_executable_bpmn"
down_revision: str | None = "0048_workflow_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_workflow_definition_backups"):
        return
    op.add_column(
        "lab_workflow_versions",
        sa.Column("bpmn_xml", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "lab_workflow_versions",
        sa.Column(
            "bpmn_profile",
            sa.String(length=80),
            nullable=False,
            server_default="aftertime-executable-1",
        ),
    )
    op.add_column(
        "lab_workflow_versions",
        sa.Column(
            "validation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "lab_workflow_versions",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "lab_workflow_definition_backups",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False, server_default="bpmn-2-migration"),
        sa.Column("bpmn_xml", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "nodes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "transitions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_lab_workflow_definition_backups_workflow_version_id"),
        "lab_workflow_definition_backups",
        ["workflow_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_lab_workflow_definition_backups_workflow_version_id"),
        table_name="lab_workflow_definition_backups",
    )
    op.drop_table("lab_workflow_definition_backups")
    op.drop_column("lab_workflow_versions", "validated_at")
    op.drop_column("lab_workflow_versions", "validation")
    op.drop_column("lab_workflow_versions", "bpmn_profile")
    op.drop_column("lab_workflow_versions", "bpmn_xml")

