"""create initial tables

Revision ID: 0001_create_initial_tables
Revises:
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_create_initial_tables"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _initial_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "hosts",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("ip", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=120), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("tags", sa.String(length=512), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("os_info", sa.Text(), nullable=True),
        sa.Column("gpu_info", sa.Text(), nullable=True),
        sa.Column("last_check_status", sa.String(length=32), nullable=True),
        sa.Column("environment_status", sa.String(length=32), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_hosts_id", "id"),
        sa.Index("ix_hosts_ip", "ip"),
        sa.Index("ix_hosts_name", "name"),
    )
    sa.Table(
        "jobs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_jobs_id", "id"),
        sa.Index("ix_jobs_status", "status"),
        sa.Index("ix_jobs_target_id", "target_id"),
        sa.Index("ix_jobs_type", "type"),
    )
    sa.Table(
        "job_logs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_job_logs_id", "id"),
        sa.Index("ix_job_logs_job_id", "job_id"),
    )
    return metadata


def upgrade() -> None:
    metadata = _initial_metadata()
    metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata = _initial_metadata()
    metadata.drop_all(bind=op.get_bind(), checkfirst=True)
