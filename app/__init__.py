"""Application factory for the metrics monolith."""

from __future__ import annotations

import os
from typing import Any, Mapping

from flask import Flask, jsonify

from .config import default_database_url
from .errors import register_error_handlers
from .extensions import db
from .ingest import bp as ingest_bp
from .query import bp as query_bp

#: Blueprints registered by the application.  T2 (ingestion), T3 (query) and
#: T4 (web UI) add their own entry here; keeping the list explicit avoids
#: import cycles between the factory and the packages it serves.
BLUEPRINTS: tuple[Any, ...] = (ingest_bp, query_bp)


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    """Build and configure the Flask application.

    ``config`` overrides the defaults and is mainly used by the test suite to
    point the application at an isolated database.
    """
    app = Flask(__name__, instance_relative_config=True)

    # The instance folder holds the development SQLite database.
    os.makedirs(app.instance_path, exist_ok=True)

    database_url = os.environ.get("DATABASE_URL") or default_database_url(app.instance_path)

    app.config.from_mapping(
        DATABASE_URL=database_url,
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key-change-me"),
        # Responses are rendered in insertion order so the JSON payload of the
        # API keeps the documented field order.
        JSON_SORT_KEYS=False,
        SQLALCHEMY_ENGINE_OPTIONS={},
    )

    if config:
        app.config.update(config)

    # Flask 3 moved the JSON behaviour out of the config into the JSON
    # provider, so the config flag above has to be mirrored here.
    app.json.sort_keys = False

    # Importing the models registers the tables on the shared metadata.
    from . import models  # noqa: F401

    db.init_app(app)

    # The error contract is installed before the blueprints so a failure
    # raised while the factory builds the application is reported with the
    # same envelope as a failure raised while serving a request.
    register_error_handlers(app)

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.get("/health")
    def health() -> Any:
        """Smoke-test endpoint used to verify the application boots."""
        return jsonify({"status": "ok"})

    return app
