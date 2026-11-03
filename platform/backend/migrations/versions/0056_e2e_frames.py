"""Screen frames of the machine under test during E2E runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0056_e2e_frames"
down_revision: str | None = "0055_model_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_e2e_frames"):
        return
    op.create_table(
        "lab_e2e_frames",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(length=80), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["run_id"], ["lab_e2e_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lab_e2e_frames_run_id", "lab_e2e_frames", ["run_id"])
    op.create_index("ix_lab_e2e_frames_captured_at", "lab_e2e_frames", ["captured_at"])


def downgrade() -> None:
    op.drop_table("lab_e2e_frames")
