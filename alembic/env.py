import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from sglang_ops_stack.config import get_settings
from sglang_ops_stack.db.base import Base
from sglang_ops_stack.db.models import (  # noqa: F401
    AuditLog,
    Deployment,
    DeploymentRevision,
    Host,
    Job,
    JobLog,
    MonitoringConfig,
    User,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url", os.getenv("ALEMBIC_DATABASE_URL", get_settings().database_url)
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
