"""Alembic migration environment.

Reads DATABASE_URL from the environment (via pydantic-settings) so
that credentials are never stored in alembic.ini.  All application
models must be imported (directly or transitively) through
``app.models`` so that ``Base.metadata`` is fully populated before
Alembic inspects it.

Uses the asyncpg driver exclusively — no psycopg2 dependency required.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context

# ---------------------------------------------------------------------------
# Load application models so their metadata is registered on Base.
# ---------------------------------------------------------------------------
from app.config import get_settings
from app.models import Base  # noqa: F401 — populates Base.metadata

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to alembic.ini values.
# ---------------------------------------------------------------------------
config = context.config

# Inject the async database URL from pydantic-settings at runtime.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Read the search_path configured in alembic.ini.
_search_path = config.get_main_option("search_path", "costs")

# Attach Python logging configuration from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object that Alembic will diff against the live schema.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required).

    Generates SQL scripts that can be reviewed and applied manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        version_table_schema=_search_path,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    """Run migrations against a live connection (called inside async context)."""
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        version_table_schema=_search_path,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside a sync callback."""
    connectable: AsyncEngine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": _search_path}},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live database connection)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
