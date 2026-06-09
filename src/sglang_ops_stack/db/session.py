from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sglang_ops_stack.config import get_settings


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _engine_kwargs(
    database_url: str,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int | None = None,
    pool_recycle: int | None = None,
) -> dict[str, object]:
    if database_url == "sqlite://":
        return {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    kwargs: dict[str, object] = {"connect_args": _connect_args(database_url)}
    if not database_url.startswith("sqlite"):
        if pool_size is not None:
            kwargs["pool_size"] = pool_size
        if max_overflow is not None:
            kwargs["max_overflow"] = max_overflow
        if pool_timeout is not None:
            kwargs["pool_timeout"] = pool_timeout
        if pool_recycle is not None:
            kwargs["pool_recycle"] = pool_recycle
    return kwargs


def create_db_engine(
    database_url: str,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int | None = None,
    pool_recycle: int | None = None,
) -> Engine:
    return create_engine(
        database_url,
        **_engine_kwargs(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
        ),
    )


_settings = get_settings()
engine = create_db_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_timeout=_settings.db_pool_timeout,
    pool_recycle=_settings.db_pool_recycle,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
