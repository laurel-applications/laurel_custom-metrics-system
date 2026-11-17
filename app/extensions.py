"""SQLAlchemy plumbing.

Plain SQLAlchemy 2.0 is used on purpose: Flask-SQLAlchemy would hide the
engine and the session lifecycle, and the domain layer has to stay
engine agnostic (SQLite for development, MariaDB in production).
"""

from __future__ import annotations

from typing import Any

from flask import current_app
from flask.globals import app_ctx
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker

from .config import build_engine_options, install_sqlite_pragmas

#: Deterministic constraint names so migrations and the models never drift.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine_for(url: str, **kwargs: Any) -> Engine:
    """Create an engine for ``url`` with the dialect specific options applied."""
    options = build_engine_options(url)
    options.update(kwargs)
    engine = create_engine(url, **options)
    install_sqlite_pragmas(engine)
    return engine


def _app_context_scope() -> Any:
    """Return the current Flask application context as the session scope key.

    Scoping the session to the application context guarantees that every
    request gets its own session and that the session is discarded when the
    request ends.
    """
    return app_ctx._get_current_object()


class SQLAlchemy:
    """Minimal extension object exposing the session and the metadata.

    The engine is stored per application so several applications (tests, the
    Alembic runner, a production instance) can live in the same process
    without overwriting each other's connection pool.
    """

    def __init__(self) -> None:
        self.metadata = Base.metadata
        self._engines: dict[Any, Engine] = {}
        self._sessions: dict[Any, scoped_session[Session]] = {}

    def init_app(self, app: Any) -> None:
        """Create the engine and the session factory for ``app``."""
        url = app.config["DATABASE_URL"]
        engine_options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {}
        engine = create_engine_for(url, **engine_options)

        session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        sessions = scoped_session(session_factory, scopefunc=_app_context_scope)

        self._engines[app] = engine
        self._sessions[app] = sessions

        @app.teardown_appcontext
        def _remove_session(exception: BaseException | None = None) -> None:
            # The session lives exactly as long as the application context, so
            # every request starts from a clean unit of work.
            sessions.remove()

    def _current(self) -> scoped_session[Session]:
        app = current_app._get_current_object()
        sessions = self._sessions.get(app)
        if sessions is None:
            raise RuntimeError(
                "The SQLAlchemy extension was not initialized for the current "
                "application. Call create_app() before using db.session."
            )
        return sessions

    @property
    def session(self) -> Session:
        """Return the session bound to the current application context."""
        return self._current()()

    @property
    def engine(self) -> Engine:
        """Return the engine of the current application context."""
        app = current_app._get_current_object()
        engine = self._engines.get(app)
        if engine is None:
            raise RuntimeError(
                "The SQLAlchemy extension was not initialized for the current "
                "application. Call create_app() before using db.engine."
            )
        return engine

    def remove_session(self) -> None:
        """Discard the session bound to the current application context.

        The teardown handler already does this at the end of every request;
        this method exists for scripts and test teardown that work outside the
        request lifecycle.
        """
        self._current().remove()


#: Application wide extension instance.
db = SQLAlchemy()
