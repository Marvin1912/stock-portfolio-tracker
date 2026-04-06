"""Alembic migration environment.

Reads DATABASE_SYNC_URL from the environment (via pydantic-settings) so
that credentials are never stored in alembic.ini.  All application
models must be imported (directly or transitively) through
``app.models`` so that ``Base.metadata`` is fully populated before
Alembic inspects it.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

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

# Inject the sync database URL from pydantic-settings at runtime.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_sync_url)

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


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live database connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"options": f"-csearch_path={_search_path}"},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            version_table_schema=_search_path,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
