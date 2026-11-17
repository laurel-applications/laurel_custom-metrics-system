"""Domain models.

Every timestamp is stored as UTC epoch milliseconds in a ``BIGINT`` column:
SQLite and MariaDB disagree on ``DATETIME`` timezone handling and sub-second
precision, and the same column must behave identically on both engines.
Tags and widget filters are JSON documents serialized into ``TEXT`` because
MariaDB aliases ``JSON`` to ``LONGTEXT`` with different operator semantics.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import Base

#: ``BigInteger`` autoincrement primary keys do not auto-populate on SQLite,
#: because SQLite only auto-increments a ``INTEGER PRIMARY KEY`` rowid alias.
#: The variant keeps the column a real ``BIGINT`` on MariaDB while letting
#: SQLite behave like an auto-incrementing integer.
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class Metric(Base):
    """A named metric and the type of the data points it accepts."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Metric id={self.id} name={self.name!r} type={self.type!r}>"


class DataPoint(Base):
    """A single measurement of a metric."""

    __tablename__ = "data_points"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    metric_id: Mapped[int] = mapped_column(
        ForeignKey("metrics.id"), nullable=False, index=True
    )
    ts_utc_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_str: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        # The query path always filters by metric and then scans the time
        # range, so the composite index serves every aggregation.
        Index("ix_data_points_metric_id_ts_utc_ms", "metric_id", "ts_utc_ms"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<DataPoint id={self.id} metric_id={self.metric_id} ts={self.ts_utc_ms}>"


class Dashboard(Base):
    """A named container of widgets."""

    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Dashboard id={self.id} name={self.name!r}>"


class Widget(Base):
    """A visualization of a metric inside a dashboard."""

    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dashboard_id: Mapped[int] = mapped_column(
        ForeignKey("dashboards.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metrics.id"), nullable=False)
    chart_type: Mapped[str] = mapped_column(String(20), nullable=False)
    agg: Mapped[str] = mapped_column(String(20), nullable=False)
    bucket_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_range: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The tag key the widget groups by, or ``None`` when it does not group.
    #: It lives in its own column because it is a first class configuration
    #: value, not part of the filter document.
    group_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The JSON array of filter objects, exactly as the query API expects it.
    filters: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Widget id={self.id} title={self.title!r} position={self.position}>"
