"""Timeline of phase state changes of an E2E run, for replaying it."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0057_e2e_timeline"
down_revision: str | None = "0056_e2e_frames"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if "timeline" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lab_e2e_runs")}:
        return
    op.add_column(
        "lab_e2e_runs",
        sa.Column("timeline", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("lab_e2e_runs", "timeline")
