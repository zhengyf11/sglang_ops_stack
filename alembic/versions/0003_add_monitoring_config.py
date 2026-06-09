"""add monitoring config

Revision ID: 0003_add_monitoring_config
Revises: 0002_add_deployments
Create Date: 2026-06-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_monitoring_config"
down_revision: str | None = "0002_add_deployments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_configs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("grafana_base_url", sa.String(length=512), nullable=True),
        sa.Column("prometheus_base_url", sa.String(length=512), nullable=True),
        sa.Column("default_dashboard_path", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_monitoring_configs_id", "monitoring_configs", ["id"])


def downgrade() -> None:
    op.drop_index("ix_monitoring_configs_id", table_name="monitoring_configs")
    op.drop_table("monitoring_configs")
