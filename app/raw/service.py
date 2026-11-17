"""Raw data service for the Laurel Metrics browser.

Provides paginated queries, count, last id, long poll wait and CSV export
over the existing data_points / metrics tables. No new schema; uses the
composite index (metric_id, ts_utc_ms) that already exists in the initial
migration.

The value shown in the table is typed by the metric type because the point
itself does not carry the type (value_num / value_str / bool).

All helpers raise ``ApiError`` on invalid input; they never propagate
raw SQLAlchemy exceptions.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, func, and_
from ..extensions import db
from ..models import DataPoint, Metric
from ..errors import ApiError


# ---------------------------------------------------------------------------
# RawPoint: internal representation returned by the service layer
# ---------------------------------------------------------------------------


class RawPoint:
    """Minimal point view used by the raw-data API responses."""

    __slots__ = (
        "id",
        "ts_ms",
        "received_at_ms",
        "value",
        "value_num",
        "value_str",
        "tags",
    )

    def __init__(
        self,
        id: int,
        ts_ms: int,
        received_at_ms: int,
        value: object,
        value_num: float | None,
        value_str: str | None,
        tags: dict[str, Any],
    ) -> None:
        self.id = id
        self.ts_ms = ts_ms
        self.received_at_ms = received_at_ms
        self.value = value
        self.value_num = value_num
        self.value_str = value_str
        self.tags = tags

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        return {
            "id": self.id,
            "ts_ms": self.ts_ms,
            "received_at_ms": self.received_at_ms,
            "value": self.value,
            "value_num": self.value_num,
            "value_str": self.value_str,
            "tags": self.tags,
        }

# ---------------------------------------------------------------------------
# Utility: value typing
# ---------------------------------------------------------------------------

def _value_for_point(metric: Metric, point: DataPoint) -> object:
    """Return the *display* value for a data point given its metric type.

    - numeric types (counter, gauge, timer)   → value_num
    - string metric                           → value_str
    - boolean metric                          → a Python ``bool`` (True / False)
    """
    if metric.type == "bool":
        # both representations exist; the canonical one is True / False
        if point.value_num is not None and point.value_num >= 1:
            return True
        if point.value_str is not None and point.value_str.lower() in {"true", "1"}:
            return True
        return False
    if metric.type == "string":
        return point.value_str
    # numeric types
    return point.value_num

# ---------------------------------------------------------------------------
# 1. list_points ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def list_points(
    metric: Metric,
    from_ms: int | None,
    to_ms: int | None,
    limit: int,
    offset: int,
    order: str | None = None,
) -> tuple[list["RawPoint"], int]:
    """Return ``(points, total)`` for the given range.

    * ``limit`` is capped at **500**; values above are clipped.
    * ``offset`` must be ``>= 0``; otherwise ``400 INVALID_PAGE``.
    * ``total`` is the *exact* row count **before** pagination.

    Ordering:
    - SQL always orders by ``ts_utc_ms desc, id desc``.
    - If ``order == 'asc'`` the Python list is reversed after fetching.
    """
    if limit < 1 or limit > 500:
        raise ApiError(
            "INVALID_PAGE_SIZE",
            "The limit must be between 1 and 500.",
            field="limit",
            expected="1..500",
            received=limit,
            hint="Pass a limit between 1 and 500.",
        )
    if offset < 0:
        raise ApiError(
            "INVALID_PAGE",
            "The offset must be a non‑negative integer.",
            field="offset",
            expected=">= 0",
            received=offset,
            hint="Pass ``offset=0`` for the first page.",
        )

    # Always order by ts_utc_ms desc, id desc in SQL.
    stmt = select(DataPoint).where(_range_filters(metric.id, from_ms, to_ms)).order_by(
        DataPoint.ts_utc_ms.desc(), DataPoint.id.desc()
    ).offset(offset).limit(limit + offset)
    rows = db.session.execute(stmt).scalars().all()

    total = db.session.execute(
        select(func.count()).select_from(DataPoint).where(_range_filters(metric.id, from_ms, to_ms))
    ).scalar_one()

    # Build RawPoint objects from the fetched rows
    raw_points: list["RawPoint"] = []
    for row in rows:
        point = _point_from_row(row, metric)
        raw_points.append(point)

    # The actual returned window is offset .. offset+limit
    returned = raw_points[offset : offset + limit]
    return returned, total

def _range_filters(metric_id: int, from_ms: int | None, to_ms: int | None) -> Any:
    """Build the ``WHERE`` clause for ``DataPoint`` given optional from / to ms."""
    cond = [DataPoint.metric_id == metric_id]
    if from_ms is not None:
        cond.append(DataPoint.ts_utc_ms >= from_ms)
    if to_ms is not None:
        cond.append(DataPoint.ts_utc_ms <= to_ms)
    return and_(*cond)

def _point_from_row(row: Any, metric: Metric) -> "RawPoint":
    """Map a SQLAlchemy ``DataPoint`` row to our internal ``RawPoint``."""
    return RawPoint(
        id=row.id,
        ts_ms=row.ts_utc_ms,
        received_at_ms=row.received_at_ms,
        value=_value_for_point(metric, row),
        value_num=row.value_num,
        value_str=row.value_str,
        tags=_parse_tags(row.tags),
)

# ---------------------------------------------------------------------------
# 2. count_points ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def count_points(metric_id: int, from_ms: int | None, to_ms: int | None) -> int:
    """Return the exact number of data points in the range."""
    return db.session.execute(
        select(func.count()).select_from(DataPoint).where(
            _range_filters(metric_id, from_ms, to_ms)
        )
    ).scalar_one()

# ---------------------------------------------------------------------------
# 3. latest_id ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def latest_id(metric_id: int) -> int | None:
    """Return the greatest point id for the metric, or ``None`` if none exists."""
    row = db.session.execute(
        select(DataPoint.id).where(DataPoint.metric_id == metric_id).order_by(DataPoint.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row

# ---------------------------------------------------------------------------
# 4. wait_for_points ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def wait_for_points(
    metric_id: int,
    since_id: int,
    from_ms: int | None,
    to_ms: int | None,
    timeout_s: float = 2.0,
) -> tuple[list["RawPoint"], int]:
    """Long‑poll for new points.

    Wait up to *timeout_s* seconds for a point whose ``id`` is strictly
    greater than *since_id* and that falls inside the optional range, polling
    in short sleeps (≈100 ms) until the timeout elapses.  Return the points
    that arrived and the elapsed time *waited_ms*.

    If nothing arrives before the timeout the function returns ``([], waited_ms)``.
    """
    from time import monotonic, sleep

    waited = 0.0
    points: list["RawPoint"] = []

    start_monotonic = monotonic()

    while waited < timeout_s:
        # Query for *any* newer point (ignoring the range for the moment;
        # the caller can filter by range afterwards if desired).
        row = db.session.execute(
            select(DataPoint.id)
            .where(DataPoint.metric_id == metric_id, DataPoint.id > since_id)
            .order_by(DataPoint.id.asc())
            .limit(1)
        ).scalar_one_or_none()

        if row is not None:
            # We have a newer point – fetch *all* points with that id and
            # that fall inside the optional range, then return them.
            row_points = db.session.execute(
                select(DataPoint)
                .where(
                    DataPoint.metric_id == metric_id,
                    DataPoint.id == row,
                    _range_filters(metric_id, from_ms, to_ms),
                )
                .order_by(DataPoint.ts_utc_ms, DataPoint.id)
                .all()
            )
            points = [_point_from_row(r, None) for r in row_points]
            new_since_id = row
            import time as _time
            waited_ms = int((_time.monotonic() - start_monotonic) * 1000)
            return points, waited_ms

        # No newer point – short sleep then retry.
        import time as _time
        _time.sleep(0.1)
        waited += 0.1

    # Timeout reached with no new point.
    # Return the points collected (may be empty) and the total waited time.
    import time as _time2
    return points, int(round(_time2.monotonic() * 1000))


# ---------------------------------------------------------------------------
# 5. to_csv ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def to_csv(points: list["RawPoint"]) -> str:
    """Return a CSV representation of *points*.

    Header: ``id,ts_utc,received_at_utc,value,tags``.
    Timestamps are ISO‑8601 UTC strings; tags are the compact JSON
    representation (sorted keys).
    """
    lines: list[str] = ["id,ts_utc,received_at_utc,value,tags"]
    for p in points:
        ts_utc = datetime.fromtimestamp(p.ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        received_utc = datetime.fromtimestamp(p.received_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        val_str = str(p.value) if p.value is not None else ""
        tags_json = json.dumps(p.tags, ensure_ascii=False, sort_keys=True)
        lines.append(f"{p.id},{ts_utc},{received_utc},{val_str},{tags_json}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper: tag decode (shared)
# ---------------------------------------------------------------------------

def _parse_tags(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}