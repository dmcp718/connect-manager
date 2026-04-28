from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_env_url = os.environ.get("DATABASE_URL")
if _env_url:
    # The web/worker apps use postgresql+asyncpg for async runtime. Alembic
    # runs migrations synchronously, so swap to the sync psycopg driver
    # (already in the image's deps via `psycopg[binary]`). Also escape any
    # literal `%` in the password so configparser's BasicInterpolation
    # doesn't try to expand `%foo` as a variable reference.
    sync_url = _env_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    config.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
