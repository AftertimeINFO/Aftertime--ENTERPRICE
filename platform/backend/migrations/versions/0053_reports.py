"""Store imported test reports in the Allure results format."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0053_reports"
down_revision: str | None = "0052_e2e_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb(default: str) -> dict:
    return dict(type_=postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text(f"'{default}'::jsonb"))


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("report_launches"):
        return
    op.create_table(
        "report_launches",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("executor", **_jsonb("{}")),
        sa.Column("environment", **_jsonb("{}")),
        sa.Column("categories", **_jsonb("[]")),
        sa.Column("stats", **_jsonb("{}")),
        sa.Column("start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop", sa.DateTime(timezone=True), nullable=True),
        sa.Column("e2e_run_id", sa.String(length=80), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_report_launches_start", "report_launches", ["start"])
    op.create_index("ix_report_launches_e2e_run_id", "report_launches", ["e2e_run_id"])
    op.create_table(
        "report_results",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("launch_id", sa.String(length=80), nullable=False),
        sa.Column("history_id", sa.String(length=200), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("parent_suite", sa.Text(), nullable=False, server_default=""),
        sa.Column("suite", sa.Text(), nullable=False, server_default=""),
        sa.Column("sub_suite", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", **_jsonb("{}")),
        sa.ForeignKeyConstraint(["launch_id"], ["report_launches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("launch_id", "history_id", "status", "start"):
        op.create_index(f"ix_report_results_{column}", "report_results", [column])
    op.create_table(
        "report_attachments",
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("source"),
    )


def downgrade() -> None:
    op.drop_table("report_attachments")
    op.drop_table("report_results")
    op.drop_table("report_launches")
