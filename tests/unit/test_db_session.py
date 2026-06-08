from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool

from sglang_ops_stack.db.session import create_db_engine


def test_create_db_engine_uses_static_pool_for_memory_sqlite() -> None:
    engine = create_db_engine("sqlite://")

    assert isinstance(engine.pool, StaticPool)
    assert inspect(engine).get_table_names() == []


def test_create_db_engine_configures_file_sqlite() -> None:
    engine = create_db_engine("sqlite:///./example.db")

    assert engine.url.database == "./example.db"
