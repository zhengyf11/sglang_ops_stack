from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sglang_ops_stack.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    ip: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(120), nullable=False, default="root")
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="password")
    tags: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    os_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    gpu_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_check_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    environment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
