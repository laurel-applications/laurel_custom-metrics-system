"""Read and serialize the raw data points of a metric.

The query API answers aggregated buckets, so the raw browser needs a separate
read path that preserves every measurement, its ingestion time and its tags.
Keeping that path here lets the JSON API, the HTML page and the CSV download
share exactly the same ordering, value typing and pagination rules.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select

from ..extensions import db
from ..models import DataPoint, Metric

#: How often the long poll checks the database while it waits for a point.
POLL_INTERVAL_S = 0.1

#: The orders accepted by the raw point query.
PointOrder = Literal["asc", "desc"]


@dataclass(frozen=True)
class RawPoint:
    """A metric measurement with the value already typed for its metric."""

    id: int
    ts_ms: int
    received_at_ms: int
    value: float | str | bool
    tags: dict[str, Any]


def _parse_tags(raw: str | None) -> dict[str, Any]:
    """Return the tags of a point, degrading malformed JSON to an empty map."""
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _point_value(row: DataPoint, metric_type: str) -> float | str | bool:
    """Return the stored value in the representation of its metric type."""
    if metric_type == "string":
        return row.value_str or ""

    if metric_type == "bool":
        if row.value_str is not None:
            return row.value_str.strip().lower() == "true"
        return bool(row.value_num)

    return float(row.value_num) if row.value_num is not None else 0.0


def _to_raw_point(row: DataPoint, metric_type: str) -> RawPoint:
    """Return one database row as a typed raw point."""
    return RawPoint(
        id=row.id,
        ts_ms=row.ts_utc_ms,
        received_at_ms=row.received_at_ms,
        value=_point_value(row, metric_type),
        tags=_parse_tags(row.tags),
    )


def list_points(
    metric: Metric,
    from_ms: int,
    to_ms: int,
    limit: int,
    offset: int,
    order: PointOrder = "desc",
) -> tuple[list[RawPoint], int]:
    """Return one page of points and the total count of the selected range.

    The page is selected by the database with ``LIMIT`` and ``OFFSET``; the
    service never materializes the whole window merely to discard most of it.
    """
    direction = DataPoint.ts_utc_ms.asc() if order == "asc" else DataPoint.ts_utc_ms.desc()
    id_direction = DataPoint.id.asc() if order == "asc" else DataPoint.id.desc()

    statement = (
        select(DataPoint)
        .where(
            DataPoint.metric_id == metric.id,
            DataPoint.ts_utc_ms >= from_ms,
            DataPoint.ts_utc_ms <= to_ms,
        )
        .order_by(direction, id_direction)
        .limit(limit)
        .offset(offset)
    )
    rows = db.session.execute(statement).scalars().all()
    points = [_to_raw_point(row, metric.type) for row in rows]
    return points, count_points(metric.id, from_ms, to_ms)


def count_points(metric_id: int, from_ms: int, to_ms: int) -> int:
    """Return the number of points of a metric inside a time range."""
    statement = (
        select(func.count())
        .select_from(DataPoint)
        .where(
            DataPoint.metric_id == metric_id,
            DataPoint.ts_utc_ms >= from_ms,
            DataPoint.ts_utc_ms <= to_ms,
        )
    )
    return int(db.session.execute(statement).scalar_one())


def latest_id(metric_id: int) -> int | None:
    """Return the greatest point id of a metric, or ``None`` when it is empty."""
    statement = select(func.max(DataPoint.id)).where(DataPoint.metric_id == metric_id)
    value = db.session.execute(statement).scalar_one()
    return int(value) if value is not None else None


def wait_for_points(
    metric_id: int,
    since_id: int,
    from_ms: int,
    to_ms: int,
    timeout_s: float,
) -> tuple[list[RawPoint], int]:
    """Wait for points newer than a watermark and return them with the wait time.

    The auto-increment point id is the watermark because it is monotonic.  A
    poll therefore misses no point and repeats none, even when several points
    arrive between two checks.
    """
    metric = db.session.get(Metric, metric_id)
    metric_type = metric.type if metric is not None else "gauge"
    started = time.monotonic()
    deadline = started + max(0.0, timeout_s)

    while True:
        statement = (
            select(DataPoint)
            .where(
                DataPoint.metric_id == metric_id,
                DataPoint.id > since_id,
                DataPoint.ts_utc_ms >= from_ms,
                DataPoint.ts_utc_ms <= to_ms,
            )
            .order_by(DataPoint.ts_utc_ms.desc(), DataPoint.id.desc())
        )
        rows = db.session.execute(statement).scalars().all()
        if rows:
            points = [_to_raw_point(row, metric_type) for row in rows]
            waited_ms = int(round((time.monotonic() - started) * 1000))
            return points, waited_ms

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            waited_ms = int(round((time.monotonic() - started) * 1000))
            return [], waited_ms

        # Ending the read transaction before sleeping lets the next poll see
        # a point committed by another request or another process.
        db.session.rollback()
        time.sleep(min(POLL_INTERVAL_S, remaining))


def _iso_utc(timestamp_ms: int) -> str:
    """Return an epoch millisecond timestamp as an ISO-8601 UTC value."""
    moment = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _csv_value(value: float | str | bool) -> str:
    """Return a typed point value in its stable textual representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def to_csv(points: list[RawPoint]) -> str:
    """Return raw points as CSV with a header and compact JSON tags."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id", "ts_utc", "received_at_utc", "value", "tags"])

    for point in points:
        writer.writerow(
            [
                point.id,
                _iso_utc(point.ts_ms),
                _iso_utc(point.received_at_ms),
                _csv_value(point.value),
                json.dumps(point.tags, ensure_ascii=False, separators=(",", ":")),
            ]
        )

    return output.getvalue()
