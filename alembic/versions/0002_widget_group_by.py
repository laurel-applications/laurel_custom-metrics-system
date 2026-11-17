"""Give the widgets a real ``group_by`` column.

Until now the whole configuration of a widget lived in the ``filters`` TEXT
column, as a JSON document with two keys::

    {"group_by": "host", "filters": [ ... ]}

That conflated two different concerns and made the column lie about its
name.  This revision adds the ``group_by`` column and moves the value of
the legacy documents into it, leaving the ``filters`` column with the bare
JSON array of filter objects the query API expects.

The data migration is written with the SQLAlchemy Core only (``op.get_bind()``
plus ``sa.select`` / ``sa.update``), so the very same code runs on SQLite and
on MariaDB without a single dialect specific statement.

Revision ID: 0002_widget_group_by
Revises: 0001_initial
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0002_widget_group_by"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Length of the new column, matching the model definition.
GROUP_BY_LENGTH = 64


def _widgets_table() -> Any:
    """Return the lightweight table description of ``widgets``.

    A Core table (instead of the ORM model) is used on purpose: a migration
    must not depend on the current state of the models, because the models
    move on while the migrations keep their history.
    """
    return sa.table(
        "widgets",
        sa.column("id", sa.Integer),
        sa.column("filters", sa.Text),
        sa.column("group_by", sa.String(GROUP_BY_LENGTH)),
    )


def migrate_widget_filters(bind: Any) -> int:
    """Move the legacy ``group_by`` of every widget into its own column.

    A row is migrated only when its ``filters`` column holds a JSON *object*
    with a ``group_by`` key.  Everything else is left untouched: a ``NULL``,
    an empty string, a bare JSON array or a document that is not JSON at all
    keep their exact content, and their ``group_by`` stays ``NULL``.

    The function is importable, so the data migration can be tested against
    an in-memory database without running the whole Alembic machinery.

    Returns the number of migrated rows.
    """
    widgets = _widgets_table()

    rows = bind.execute(
        sa.select(widgets.c.id, widgets.c.filters, widgets.c.group_by)
    ).fetchall()

    migrated = 0
    for row in rows:
        raw = row.filters
        if not isinstance(raw, str) or not raw.strip():
            continue

        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            # A malformed document is not this migration's business: the
            # widget is still readable, it simply has no group by.
            continue

        if not isinstance(decoded, dict) or "group_by" not in decoded:
            continue

        raw_group_by = decoded.get("group_by")
        group_by = (
            raw_group_by.strip()
            if isinstance(raw_group_by, str) and raw_group_by.strip()
            else None
        )

        legacy_filters = decoded.get("filters")
        filters = legacy_filters if isinstance(legacy_filters, list) else []

        bind.execute(
            sa.update(widgets)
            .where(widgets.c.id == row.id)
            .values(
                group_by=group_by,
                filters=json.dumps(filters, ensure_ascii=False),
            )
        )
        migrated += 1

    return migrated


def restore_legacy_widget_filters(bind: Any) -> int:
    """Fold the ``group_by`` column back into the ``filters`` document.

    This is the exact inverse of :func:`migrate_widget_filters`, so a
    downgrade followed by an upgrade gives the very same data back.
    """
    widgets = _widgets_table()

    rows = bind.execute(
        sa.select(widgets.c.id, widgets.c.filters, widgets.c.group_by)
    ).fetchall()

    restored = 0
    for row in rows:
        raw = row.filters
        if isinstance(raw, str) and raw.strip():
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, list):
                filters = decoded
            elif isinstance(decoded, dict) and isinstance(decoded.get("filters"), list):
                filters = decoded["filters"]
            else:
                filters = []
        else:
            filters = []

        document = {"group_by": row.group_by, "filters": filters}
        bind.execute(
            sa.update(widgets)
            .where(widgets.c.id == row.id)
            .values(filters=json.dumps(document, ensure_ascii=False))
        )
        restored += 1

    return restored


def upgrade() -> None:
    """Add the ``group_by`` column and migrate the legacy documents."""
    op.add_column(
        "widgets",
        sa.Column("group_by", sa.String(length=GROUP_BY_LENGTH), nullable=True),
    )

    if context.is_offline_mode():
        # The data migration needs a live connection: in offline mode only the
        # DDL is rendered, and the rows are migrated when the migration is
        # applied for real.
        return

    migrate_widget_filters(op.get_bind())


def downgrade() -> None:
    """Fold the ``group_by`` back into the ``filters`` document and drop it."""
    bind = op.get_bind()

    if not context.is_offline_mode():
        restore_legacy_widget_filters(bind)

    if bind.dialect.name == "sqlite":
        # SQLite only learned ``DROP COLUMN`` recently, so the portable batch
        # mode is used: Alembic recreates the table with the remaining
        # columns and copies the rows.
        with op.batch_alter_table("widgets") as batch:
            batch.drop_column("group_by")
    else:
        op.drop_column("widgets", "group_by")
