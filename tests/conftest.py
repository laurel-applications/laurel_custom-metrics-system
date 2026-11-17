"""Shared pytest fixtures.

Every test gets its own application bound to a throwaway SQLite database, so
the suite never touches the development database in ``instance/`` and never
leaks state between tests.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.extensions import db
from app.models import Base


@pytest.fixture()
def app(tmp_path) -> Flask:
    """Return an application bound to an isolated SQLite file."""
    database_path = tmp_path / "metrics_test.db"
    application = create_app(
        {
            "DATABASE_URL": f"sqlite:///{database_path}",
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
        }
    )

    with application.app_context():
        Base.metadata.create_all(db.engine)

    yield application

    with application.app_context():
        db.remove_session()
        db.engine.dispose()


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """Return a test client for the application."""
    return app.test_client()
