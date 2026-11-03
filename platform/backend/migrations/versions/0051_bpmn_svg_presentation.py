"""Store the SVG presentation of each BPMN workflow version."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0051_bpmn_svg_presentation"
down_revision: str | None = "0050_bpmn_presentation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if sa.inspect(op.get_bind()).has_table("lab_workflow_presentations"):
        return
    op.create_table(
        "lab_workflow_presentations",
        sa.Column("workflow_version_id", sa.String(length=80), nullable=False),
        sa.Column("svg", sa.Text(), nullable=False, server_default=""),
        sa.Column("bpmn_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["lab_workflow_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workflow_version_id"),
    )


def downgrade() -> None:
    op.drop_table("lab_workflow_presentations")
