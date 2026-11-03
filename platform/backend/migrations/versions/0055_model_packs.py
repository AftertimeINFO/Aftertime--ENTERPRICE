"""Model packs: registry and pack ownership of system models and processes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0055_model_packs"
down_revision: str | None = "0054_center_launches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # 0001 builds current metadata on a clean install; keep replay safe until 0001 is frozen.
    if not inspector.has_table("lab_packs"):
        op.create_table(
            "lab_packs",
            sa.Column("id", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("version", sa.String(length=40), nullable=False, server_default="0"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source", sa.Text(), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
        )
    for table in ("lab_projects", "lab_workflows"):
        if "pack_id" not in {column["name"] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("pack_id", sa.String(length=80), nullable=True))
            op.create_index(f"ix_{table}_pack_id", table, ["pack_id"])
    # Everything that existed before packs is the built-in energy model; processes bound to
    # tests came from settings and get their pack when the pack is installed.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM lab_projects")).scalar():
        connection.execute(sa.text(
            "INSERT INTO lab_packs (id, title, version, description, source) VALUES "
            "('energy', 'Енергосистема', 'builtin', 'Вбудована модель Model Lab: газ → ТЕС → мережа → споживач', 'core') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        connection.execute(sa.text("UPDATE lab_projects SET pack_id = 'energy' WHERE pack_id IS NULL"))
        connection.execute(sa.text(
            "UPDATE lab_workflows SET pack_id = 'energy' WHERE pack_id IS NULL AND id NOT IN ("
            "SELECT workflow_id FROM lab_workflow_versions WHERE bpmn_xml LIKE '%aftertime:testBinding=%')"
        ))


def downgrade() -> None:
    op.drop_index("ix_lab_workflows_pack_id", table_name="lab_workflows")
    op.drop_column("lab_workflows", "pack_id")
    op.drop_index("ix_lab_projects_pack_id", table_name="lab_projects")
    op.drop_column("lab_projects", "pack_id")
    op.drop_table("lab_packs")
