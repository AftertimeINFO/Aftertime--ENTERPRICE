"""Several recorded windows per E2E run: the machine under test and the applications around it."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0060_e2e_streams"
down_revision: str | None = "0059_tests_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    inspector = sa.inspect(op.get_bind())
    if "streams" not in {column["name"] for column in inspector.get_columns("lab_e2e_runs")}:
        op.add_column("lab_e2e_runs", sa.Column("streams", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    if "stream" not in {column["name"] for column in inspector.get_columns("lab_e2e_frames")}:
        op.add_column("lab_e2e_frames", sa.Column("stream", sa.String(length=40), nullable=False, server_default="screen"))
        op.create_index("ix_lab_e2e_frames_stream", "lab_e2e_frames", ["stream"])


def downgrade() -> None:
    op.drop_index("ix_lab_e2e_frames_stream", table_name="lab_e2e_frames")
    op.drop_column("lab_e2e_frames", "stream")
    op.drop_column("lab_e2e_runs", "streams")
