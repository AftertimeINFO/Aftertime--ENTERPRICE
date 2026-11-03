"""Store end-to-end test runs of test-bound BPMN processes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0052_e2e_runs"
down_revision: str | None = "0051_bpmn_svg_presentation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_e2e_runs"):
        return
    jsonb_list = dict(
        type_=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.create_table(
        "lab_e2e_runs",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=80), nullable=False),
        sa.Column("binding", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("center_job_id", sa.String(length=80), nullable=True),
        sa.Column("queue_ahead", sa.Integer(), nullable=True),
        sa.Column("states", **jsonb_list),
        sa.Column("log", **jsonb_list),
        sa.Column("folder", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["lab_workflow_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lab_e2e_runs_workflow_version_id", "lab_e2e_runs", ["workflow_version_id"])
    op.create_index("ix_lab_e2e_runs_status", "lab_e2e_runs", ["status"])


def downgrade() -> None:
    op.drop_table("lab_e2e_runs")
