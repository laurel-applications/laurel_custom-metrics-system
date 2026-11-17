"""Alembic environment.

The migration runner and the application must always point at the same
database, so the URL resolution lives in one place: the ``DATABASE_URL``
environment variable, falling back to the local SQLite file used by the
application in development.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Make the application package importable when Alembic is invoked from the
# repository root as well as from anywhere else.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import default_database_url  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: The metadata Alembic compares the database against.
target_metadata = Base.metadata


def get_database_url() -> str:
    """Return the database URL the migrations must run against."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return default_database_url(REPO_ROOT / "instance")


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting to a database."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url_is_sqlite(),
    )

    with context.begin_transaction():
        context.run_migrations()


def url_is_sqlite() -> bool:
    """Return True when the configured database is a SQLite file."""
    return get_database_url().startswith("sqlite")


def run_migrations_online() -> None:
    """Run the migrations against a live database."""
    url = get_database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
