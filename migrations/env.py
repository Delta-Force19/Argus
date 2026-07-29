import os

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import argus.models  # noqa: F401
from argus.database import Base, engine

from sqlalchemy.pool import NullPool


config = context.config

if config.config_file_name is not None:
    fileConfig(
        config.config_file_name,
        disable_existing_loggers=False,
    )

target_metadata = Base.metadata

override_url = os.getenv("ARGUS_ALEMBIC_DATABASE_URL")

migration_engine = (
    create_engine(
        override_url,
        poolclass=NullPool,
    )
    if override_url
    else engine
)


def run_migrations_offline() -> None:
    """Generate migration SQL without opening a database connection."""

    context.configure(
        url=str(migration_engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations using the application database engine."""

    with migration_engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
