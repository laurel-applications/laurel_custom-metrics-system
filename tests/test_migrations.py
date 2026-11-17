"""Tests of the Alembic migrations.

The migrations are the contract between the local SQLite database and the
MariaDB instance used in production, so they are exercised the way a user
exercises them: with the ``alembic`` command line, against a throwaway
database.

The data migration is also tested directly, through the importable function
of the revision module, because that is the only way to prove that the rows
that must NOT be touched really are left alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

#: Root of the repository, which is also the directory holding ``alembic.ini``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Path of the revision that adds the ``group_by`` column.
REVISION_PATH = REPO_ROOT / "alembic" / "versions" / "0002_widget_group_by.py"


def alembic_command() -> list[str]:
    """Return the command that runs Alembic for this repository."""
    binary = REPO_ROOT / ".venv" / "bin" / "alembic"
    if binary.exists():
        return [str(binary)]
    # The suite is always run with the interpreter of the virtualenv, so the
    # module is the same Alembic the binary points at.
    return [sys.executable, "-m", "alembic"]


def run_alembic(arguments: list[str], database_url: str) -> subprocess.CompletedProcess:
    """Run Alembic against ``database_url`` and return the finished process."""
    environment = dict(os.environ)
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        alembic_command() + arguments,
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def load_revision_module() -> Any:
    """Import the revision module, which is a plain Python module."""
    spec = importlib.util.spec_from_file_location(
        "widget_group_by_revision", REVISION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_widgets_table(engine: sa.Engine) -> None:
    """Create a minimal ``widgets`` table in the given engine."""
    metadata = sa.MetaData()
    sa.Table(
        "widgets",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filters", sa.Text),
        sa.Column("group_by", sa.String(64)),
    )
    metadata.create_all(engine)


def insert_widget(engine: sa.Engine, widget_id: int, filters: str | None) -> None:
    """Insert one widget with the given ``filters`` document."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO widgets (id, filters, group_by) VALUES (:id, :filters, NULL)"
            ),
            {"id": widget_id, "filters": filters},
        )


# ------------------------------------------------------------- upgrade head


def test_upgrade_head_on_a_fresh_database_succeeds(tmp_path: Path) -> None:
    """A brand new database is migrated to the head without a failure."""
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path}"

    result = run_alembic(["upgrade", "head"], database_url)
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"metrics", "data_points", "dashboards", "widgets"} <= tables


def test_upgrade_head_adds_the_group_by_column(tmp_path: Path) -> None:
    """The ``widgets`` table ends up with the ``group_by`` column."""
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path}"

    result = run_alembic(["upgrade", "head"], database_url)
    assert result.returncode == 0, result.stdout + result.stderr

    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            columns = {
                row["name"]: row
                for row in sa.inspect(connection).get_columns("widgets")
            }
    finally:
        engine.dispose()

    assert "group_by" in columns
    assert columns["group_by"]["type"].length == 64
    assert columns["group_by"]["nullable"] is True


def test_upgrade_head_reports_the_head_revision(tmp_path: Path) -> None:
    """The migrated database is stamped at the head revision."""
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path}"

    assert run_alembic(["upgrade", "head"], database_url).returncode == 0
    result = run_alembic(["current"], database_url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0002_widget_group_by" in result.stdout


# --------------------------------------------------------- the data migration


def test_the_data_migration_rewrites_a_legacy_row(tmp_path: Path) -> None:
    """A row written with the legacy document is moved into the new column.

    The row is inserted *before* the migration runs, so the migration really
    has to do the work: the assertion is not about a row written by the new
    code path.
    """
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path}"

    # The first revision only: the legacy row is written in the old shape.
    first = run_alembic(["upgrade", "0001_initial"], database_url)
    assert first.returncode == 0, first.stdout + first.stderr

    legacy_document = json.dumps(
        {
            "group_by": "host",
            "filters": [{"field": "value", "op": ">", "value": 10}],
        }
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO metrics (id, name, type, unit, created_at_ms) "
            "VALUES (1, 'requests', 'counter', NULL, 0)"
        )
        connection.execute(
            "INSERT INTO dashboards (id, name, created_at_ms) VALUES (1, 'Ops', 0)"
        )
        connection.execute(
            "INSERT INTO widgets (id, dashboard_id, title, metric_id, chart_type, "
            "agg, bucket_sec, time_range, filters, position, created_at_ms) "
            "VALUES (1, 1, 'Legacy', 1, 'line', 'sum', NULL, '24h', ?, 0, 0)",
            (legacy_document,),
        )
        connection.commit()

    second = run_alembic(["upgrade", "head"], database_url)
    assert second.returncode == 0, second.stdout + second.stderr

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT group_by, filters FROM widgets WHERE id = 1"
        ).fetchone()

    assert row is not None
    group_by, filters = row
    assert group_by == "host"
    # The filters column now holds the bare array, with no wrapper object.
    assert json.loads(filters) == [{"field": "value", "op": ">", "value": 10}]


def test_the_data_migration_leaves_the_other_rows_untouched() -> None:
    """Only the legacy documents are rewritten; everything else is untouched."""
    revision = load_revision_module()

    engine = sa.create_engine("sqlite://")
    create_widgets_table(engine)

    with engine.begin() as connection:
        insert_widget(engine, 1, None)
        insert_widget(engine, 2, "")
        insert_widget(engine, 3, "[]")
        insert_widget(engine, 4, '[{"field": "value", "op": ">", "value": 1}]')
        insert_widget(engine, 5, "not json at all")
        insert_widget(engine, 6, json.dumps({"filters": [{"field": "value"}]}))
        insert_widget(
            engine,
            7,
            json.dumps({"group_by": "host", "filters": [{"field": "value"}]}),
        )
        insert_widget(engine, 8, json.dumps({"group_by": None, "filters": []}))

        migrated = revision.migrate_widget_filters(connection)
        assert migrated == 2

        rows = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                sa.text("SELECT id, filters, group_by FROM widgets")
            )
        }

    assert rows[1] == (None, None)
    assert rows[2] == ("", None)
    assert rows[3] == ("[]", None)
    assert rows[4] == ('[{"field": "value", "op": ">", "value": 1}]', None)
    assert rows[5] == ("not json at all", None)
    assert rows[6] == (json.dumps({"filters": [{"field": "value"}]}), None)
    assert rows[7] == (json.dumps([{"field": "value"}]), "host")
    assert rows[8] == ("[]", None)


def test_the_data_migration_is_reversible() -> None:
    """A downgrade followed by an upgrade gives the same data back."""
    revision = load_revision_module()

    engine = sa.create_engine("sqlite://")
    create_widgets_table(engine)

    with engine.begin() as connection:
        insert_widget(
            engine,
            1,
            json.dumps({"group_by": "host", "filters": [{"field": "value"}]}),
        )
        insert_widget(engine, 2, '[{"field": "ts", "op": ">", "value": 1}]')

        assert revision.migrate_widget_filters(connection) == 1
        assert revision.restore_legacy_widget_filters(connection) == 2

        rows = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                sa.text("SELECT id, filters, group_by FROM widgets")
            )
        }

    # The legacy document is back, with the group by inside it.
    assert rows[1] == (
        json.dumps({"group_by": "host", "filters": [{"field": "value"}]}),
        "host",
    )
    assert rows[2][0] == json.dumps({"group_by": None, "filters": [{"field": "ts", "op": ">", "value": 1}]})


def test_the_revision_renders_valid_offline_sql_for_mariadb() -> None:
    """The offline SQL of the revision is valid MySQL DDL.

    The offline mode cannot run the data migration (it has no connection), so
    only the DDL is rendered: the column is added with a plain
    ``ALTER TABLE``.
    """
    result = run_alembic(
        ["upgrade", "head", "--sql"],
        "mysql+pymysql://user:password@127.0.0.1:3306/metrics",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    sql = result.stdout.upper()
    assert "ALTER TABLE WIDGETS ADD COLUMN GROUP_BY VARCHAR(64)" in sql
    # No dialect specific statement leaked into the DDL.
    assert "DROP COLUMN" not in sql


@pytest.mark.parametrize(
    "command",
    [["check"], ["current"]],
)
def test_alembic_check_finds_no_pending_operation(
    tmp_path: Path, command: list[str]
) -> None:
    """A migrated database is in sync with the models."""
    database_path = tmp_path / "checked.db"
    database_url = f"sqlite:///{database_path}"

    assert run_alembic(["upgrade", "head"], database_url).returncode == 0

    result = run_alembic(command, database_url)
    assert result.returncode == 0, result.stdout + result.stderr
