"""Initial schema: metrics, data points, dashboards and widgets.

The migration is written with the portable ``op.create_table`` /
``op.create_index`` primitives only, so the very same revision applies to the
local SQLite database and to the MariaDB instance used in production.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: SQLite does not auto-populate a ``BIGINT`` autoincrement primary key, it
#: only auto-increments an ``INTEGER`` rowid alias, hence the variant.
BIGINT_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    """Create the four tables and their indexes."""
    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metrics")),
        sa.UniqueConstraint("name", name=op.f("uq_metrics_name")),
    )

    op.create_table(
        "dashboards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboards")),
    )

    op.create_table(
        "data_points",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("metric_id", sa.Integer(), nullable=False),
        sa.Column("ts_utc_ms", sa.BigInteger(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_str", sa.String(length=1024), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("received_at_ms", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metrics.id"], name=op.f("fk_data_points_metric_id_metrics")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_points")),
    )
    op.create_index("ix_data_points_metric_id", "data_points", ["metric_id"], unique=False)
    op.create_index(
        "ix_data_points_metric_id_ts_utc_ms",
        "data_points",
        ["metric_id", "ts_utc_ms"],
        unique=False,
    )

    op.create_table(
        "widgets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dashboard_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("metric_id", sa.Integer(), nullable=False),
        sa.Column("chart_type", sa.String(length=20), nullable=False),
        sa.Column("agg", sa.String(length=20), nullable=False),
        sa.Column("bucket_sec", sa.Integer(), nullable=True),
        sa.Column("time_range", sa.String(length=20), nullable=False),
        sa.Column("filters", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            ["dashboards.id"],
            name=op.f("fk_widgets_dashboard_id_dashboards"),
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metrics.id"], name=op.f("fk_widgets_metric_id_metrics")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widgets")),
    )
    op.create_index(
        "ix_widgets_dashboard_id", "widgets", ["dashboard_id"], unique=False
    )


def downgrade() -> None:
    """Drop the four tables and their indexes."""
    op.drop_index("ix_widgets_dashboard_id", table_name="widgets")
    op.drop_table("widgets")

    op.drop_index("ix_data_points_metric_id_ts_utc_ms", table_name="data_points")
    op.drop_index("ix_data_points_metric_id", table_name="data_points")
    op.drop_table("data_points")

    op.drop_table("dashboards")
    op.drop_table("metrics")
