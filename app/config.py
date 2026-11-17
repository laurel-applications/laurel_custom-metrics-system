"""Engine configuration helpers.

The application must behave identically on SQLite (local development) and MariaDB
(production).  The only engine-specific behaviour we need is the SQLite pair of
pragmas -- WAL journaling and enforced foreign keys -- and those must never be
sent to a MariaDB server, which would fail the connection with a syntax error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

#: Name of the SQLite file used when ``DATABASE_URL`` is not set.
DEFAULT_DATABASE_FILENAME = "metrics.db"

#: Pragmas that every SQLite connection must run right after it is opened.
SQLITE_PRAGMAS = ("PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON")


def default_database_url(instance_path: str | Path) -> str:
    """Return the local SQLite URL used when ``DATABASE_URL`` is unset.

    ``instance_path`` is the Flask instance folder, which keeps the development
    database out of the source tree and lets ``.gitignore`` exclude it.
    """
    instance_path = Path(instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{instance_path / DEFAULT_DATABASE_FILENAME}"


def is_sqlite_url(url: str) -> bool:
    """Return True when ``url`` points at a SQLite database."""
    return url.startswith("sqlite")


def build_engine_options(url: str) -> dict[str, Any]:
    """Return the ``create_engine`` keyword arguments for ``url``.

    Only SQLite receives dialect specific options.  MariaDB URLs are handed to
    SQLAlchemy untouched so the same code path works with any MySQL driver.
    """
    if not is_sqlite_url(url):
        return {}

    options: dict[str, Any] = {
        # The dev server and the threaded worker both reuse connections, and
        # SQLite's default driver refuses to be used from a second thread.
        "connect_args": {"check_same_thread": False},
    }

    # An in-memory database only exists for the lifetime of a single connection,
    # so the pool must hand out the very same connection every time.
    if ":memory:" in url or "mode=memory" in url:
        from sqlalchemy.pool import StaticPool

        options["poolclass"] = StaticPool

    return options


def install_sqlite_pragmas(engine: Engine) -> None:
    """Run the SQLite pragmas on every new connection of ``engine``.

    The listener is attached to the concrete engine instance so the dialect can
    be checked through ``engine.dialect.name``.  A class level ``connect``
    listener would not receive the engine at all, which would force the
    dialect to be guessed from the DBAPI driver.
    """

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        if engine.dialect.name != "sqlite":
            return

        cursor = dbapi_connection.cursor()
        try:
            for pragma in SQLITE_PRAGMAS:
                cursor.execute(pragma)
        finally:
            cursor.close()
