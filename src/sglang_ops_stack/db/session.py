from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sglang_ops_stack.config import get_settings


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _engine_kwargs(database_url: str) -> dict[str, object]:
    if database_url == "sqlite://":
        return {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    return {"connect_args": _connect_args(database_url)}


def create_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, **_engine_kwargs(database_url))


engine = create_db_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
