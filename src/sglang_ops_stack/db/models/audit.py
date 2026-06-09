from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from sglang_ops_stack.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_username: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
