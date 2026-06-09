from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from sglang_ops_stack.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class MonitoringConfig(Base):
    __tablename__ = "monitoring_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    grafana_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prometheus_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_dashboard_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
