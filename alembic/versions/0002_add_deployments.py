"""add deployment tables

Revision ID: 0002_add_deployments
Revises: 0001_create_initial_tables
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_deployments"
down_revision: str | None = "0001_create_initial_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "host_id",
            sa.Integer(),
            sa.ForeignKey("hosts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("container_name", sa.String(length=128), nullable=False),
        sa.Column("image", sa.String(length=512), nullable=False),
        sa.Column("model_path", sa.Text(), nullable=False),
        sa.Column("served_model_name", sa.String(length=255), nullable=True),
        sa.Column("bind_host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("tp_size", sa.Integer(), nullable=False),
        sa.Column("dp_size", sa.Integer(), nullable=False),
        sa.Column("pp_size", sa.Integer(), nullable=False),
        sa.Column("mem_fraction_static", sa.Float(), nullable=True),
        sa.Column("docker_config", sa.JSON(), nullable=False),
        sa.Column("sglang_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("service_url", sa.String(length=512), nullable=True),
        sa.Column("metrics_url", sa.String(length=512), nullable=True),
        sa.Column("last_command_preview", sa.Text(), nullable=True),
        sa.Column("last_health_status", sa.JSON(), nullable=True),
        sa.Column("current_revision_id", sa.Integer(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("last_job_id", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployments_id", "deployments", ["id"])
    op.create_index("ix_deployments_host_id", "deployments", ["host_id"])
    op.create_index("ix_deployments_name", "deployments", ["name"])
    op.create_index("ix_deployments_container_name", "deployments", ["container_name"])
    op.create_index("ix_deployments_status", "deployments", ["status"])

    op.create_table(
        "deployment_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "deployment_id",
            sa.Integer(),
            sa.ForeignKey("deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("command_preview", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployment_revisions_id", "deployment_revisions", ["id"])
    op.create_index(
        "ix_deployment_revisions_deployment_id",
        "deployment_revisions",
        ["deployment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_revisions_deployment_id", table_name="deployment_revisions")
    op.drop_index("ix_deployment_revisions_id", table_name="deployment_revisions")
    op.drop_table("deployment_revisions")
    op.drop_index("ix_deployments_status", table_name="deployments")
    op.drop_index("ix_deployments_container_name", table_name="deployments")
    op.drop_index("ix_deployments_name", table_name="deployments")
    op.drop_index("ix_deployments_host_id", table_name="deployments")
    op.drop_index("ix_deployments_id", table_name="deployments")
    op.drop_table("deployments")
