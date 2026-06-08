from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sglang_ops_stack.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    container_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    model_path: Mapped[str] = mapped_column(Text, nullable=False)
    served_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bind_host: Mapped[str] = mapped_column(String(255), nullable=False, default="0.0.0.0")
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    tp_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dp_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pp_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mem_fraction_static: Mapped[float | None] = mapped_column(nullable=True)
    docker_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sglang_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    service_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metrics_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_command_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_health_status: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DeploymentRevision(Base):
    __tablename__ = "deployment_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    command_preview: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
