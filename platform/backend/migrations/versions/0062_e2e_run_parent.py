"""A test of a suite run gets its own run (recording, phases), linked to the run of the suite."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0062_e2e_run_parent"
down_revision: str | None = "0061_stand_file_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lab_e2e_runs")}
    if "parent_id" in columns:
        return
    op.add_column("lab_e2e_runs", sa.Column("parent_id", sa.String(80), nullable=True))
    op.create_index("ix_lab_e2e_runs_parent_id", "lab_e2e_runs", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_lab_e2e_runs_parent_id", table_name="lab_e2e_runs")
    op.drop_column("lab_e2e_runs", "parent_id")
