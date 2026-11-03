"""Add an English description to each BPMN workflow version."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0048_workflow_description"
down_revision: str | None = "0047_participant_processes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if "description" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lab_workflow_versions")}:
        return
    op.add_column(
        "lab_workflow_versions",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("lab_workflow_versions", "description")
