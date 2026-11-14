"""A run keeps the stand it was executed on (the configuration version of the system under test)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0063_e2e_run_stand"
down_revision: str | None = "0062_e2e_run_parent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lab_e2e_runs")}
    if "stand" in columns:
        return
    op.add_column("lab_e2e_runs", sa.Column("stand", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("lab_e2e_runs", "stand")
