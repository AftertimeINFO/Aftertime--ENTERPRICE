"""Catalog of end-to-end tests, synchronized from the control center."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0059_tests_catalog"
down_revision: str | None = "0058_e2e_recording"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_tests"):
        return
    op.create_table(
        "lab_tests",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("suites", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("feature", sa.Text(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lab_tests_active", "lab_tests", ["active"])


def downgrade() -> None:
    op.drop_table("lab_tests")
