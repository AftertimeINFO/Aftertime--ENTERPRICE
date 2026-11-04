"""Versions of test stand files (preparation plan, scripts) edited from Aftertime."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0061_stand_file_versions"
down_revision: str | None = "0060_e2e_streams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if "lab_stand_file_versions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "lab_stand_file_versions",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("path", sa.String(length=400), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("saved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lab_stand_file_versions_path", "lab_stand_file_versions", ["path"])
    op.create_index("ix_lab_stand_file_versions_saved_at", "lab_stand_file_versions", ["saved_at"])


def downgrade() -> None:
    op.drop_table("lab_stand_file_versions")
