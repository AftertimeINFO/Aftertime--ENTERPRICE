"""Launch arbitrary suites or cases, keep the control center state, archive tests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0054_center_launches"
down_revision: str | None = "0053_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if inspector.has_table("center_snapshots"):
        return
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column("lab_e2e_runs", sa.Column("kind", sa.String(length=20), nullable=False, server_default="binding"))
    op.add_column("lab_e2e_runs", sa.Column("target", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("lab_e2e_runs", sa.Column("progress", jsonb, nullable=True))
    op.alter_column("lab_e2e_runs", "workflow_version_id", nullable=True)
    op.create_table(
        "center_snapshots",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("data", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "report_archived_tests",
        sa.Column("history_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("history_id"),
    )


def downgrade() -> None:
    op.drop_table("report_archived_tests")
    op.drop_table("center_snapshots")
    op.alter_column("lab_e2e_runs", "workflow_version_id", nullable=False)
    op.drop_column("lab_e2e_runs", "progress")
    op.drop_column("lab_e2e_runs", "target")
    op.drop_column("lab_e2e_runs", "kind")
