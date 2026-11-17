"""Pure aggregation functions.

This module deliberately imports nothing but the standard library: it never
touches Flask and never touches the database.  It receives plain data
points and returns plain data, which is what makes the aggregation
semantics verifiable on their own.

The aggregations are computed on the fly; there is no rollup table and no
scheduler keeping a precomputed state consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Every aggregation the query API understands.
AGGREGATIONS: tuple[str, ...] = ("sum", "avg", "min", "max", "count", "last")

#: Aggregations valid for a numeric metric (counter, gauge, timer).
NUMERIC_AGGREGATIONS: tuple[str, ...] = ("sum", "avg", "min", "max", "count", "last")

#: Aggregations valid for a string metric: the text cannot be added up.
STRING_AGGREGATIONS: tuple[str, ...] = ("count", "last")

#: Aggregations valid for a bool metric.  ``avg`` is the fraction of true
#: points and ``sum`` the number of true points.
BOOL_AGGREGATIONS: tuple[str, ...] = ("sum", "avg", "min", "max", "count", "last")

#: The aggregations valid for each metric type.
VALID_AGGREGATIONS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "counter": NUMERIC_AGGREGATIONS,
    "gauge": NUMERIC_AGGREGATIONS,
    "timer": NUMERIC_AGGREGATIONS,
    "string": STRING_AGGREGATIONS,
    "bool": BOOL_AGGREGATIONS,
}

#: The automatic bucket ladder, as ``(maximum range, bucket size)`` pairs in
#: seconds.  The first band that covers the requested range wins.
BUCKET_LADDER: tuple[tuple[int, int], ...] = (
    (2 * 3600, 60),
    (12 * 3600, 300),
    (2 * 24 * 3600, 900),
    (14 * 24 * 3600, 3600),
    (90 * 24 * 3600, 21600),
)

#: The bucket used for a range longer than the last band of the ladder.
LARGEST_BUCKET_SEC = 86400


@dataclass(frozen=True)
class Point:
    """A data point reduced to the values the aggregation needs."""

    id: int
    ts_ms: int
    value_num: float | None = None
    value_str: str | None = None
    tags: Mapping[str, Any] = field(default_factory=dict)


def auto_bucket_sec(range_ms: int) -> int:
    """Return the bucket size, in seconds, for a requested range.

    The ladder is walked from the shortest band up, so the smallest bucket
    that still covers the requested span is chosen.
    """
    span = max(0, int(range_ms))
    for max_sec, bucket_sec in BUCKET_LADDER:
        if span <= max_sec * 1000:
            return bucket_sec
    return LARGEST_BUCKET_SEC


def bucket_start_ms(ts_ms: int, bucket_ms: int) -> int:
    """Return the start of the bucket a timestamp belongs to.

    Buckets are aligned to the epoch, so two queries with the same bucket
    size always agree on where a bucket starts.
    """
    if bucket_ms <= 0:
        raise ValueError("the bucket size must be a positive number of milliseconds")
    return (int(ts_ms) // bucket_ms) * bucket_ms


def valid_aggregations(metric_type: str) -> tuple[str, ...]:
    """Return the aggregations valid for a metric type."""
    return VALID_AGGREGATIONS_BY_TYPE.get(metric_type, AGGREGATIONS)


def _numeric(point: Point) -> float:
    """Return the numeric value of a point.

    A missing numeric value counts as zero: the ingestion never writes a
    point without its numeric value, and a zero keeps the aggregation total
    instead of failing on a legacy row.
    """
    return float(point.value_num) if point.value_num is not None else 0.0


def aggregate(
    points: Sequence[Point], agg: str, metric_type: str
) -> int | float | str | bool | None:
    """Aggregate the points of a single bucket.

    ``count`` works for every metric type.  ``last`` returns the value of
    the most recent point, ties broken by the identifier.  The numeric
    aggregations read the numeric column, which for a bool metric holds 1.0
    and 0.0 -- so ``avg`` is the fraction of true points and ``sum`` the
    number of true points.
    """
    if agg == "count":
        return len(points)

    if not points:
        return None

    if agg == "last":
        newest = max(points, key=lambda point: (point.ts_ms, point.id))
        if metric_type == "string":
            return newest.value_str
        if metric_type == "bool":
            return bool(_numeric(newest))
        return newest.value_num

    numbers = [_numeric(point) for point in points]

    if agg == "sum":
        return sum(numbers)
    if agg == "avg":
        return sum(numbers) / len(numbers)
    if agg == "min":
        return min(numbers)
    if agg == "max":
        return max(numbers)

    raise ValueError(f"unsupported aggregation {agg!r}")  # pragma: no cover


def group_key(point: Point, group_by: str | None) -> str | None:
    """Return the group a point belongs to.

    ``None`` means the point does not carry the tag, which is a group of its
    own in the response.
    """
    if group_by is None:
        return None

    raw = point.tags.get(group_by)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, str):
        return raw
    return str(raw)


def build_series(
    points: Iterable[Point],
    agg: str,
    metric_type: str,
    bucket_ms: int,
    group_by: str | None = None,
) -> list[dict[str, Any]]:
    """Build the series of the response.

    The result always holds at least one series: an empty result is a
    legitimate answer, not an error.
    """
    groups: dict[str | None, list[Point]] = {}
    for point in points:
        groups.setdefault(group_key(point, group_by), []).append(point)

    if not groups:
        groups = {None: []}

    # The series without a tag comes first, the remaining ones are sorted by
    # their key so the response is stable between two identical requests.
    ordered_keys = sorted(groups, key=lambda key: (key is not None, key or ""))

    series: list[dict[str, Any]] = []
    for key in ordered_keys:
        buckets: dict[int, list[Point]] = {}
        for point in groups[key]:
            buckets.setdefault(bucket_start_ms(point.ts_ms, bucket_ms), []).append(point)

        bucket_points = [
            {
                "ts": start,
                "value": aggregate(buckets[start], agg, metric_type),
                "count": len(buckets[start]),
            }
            for start in sorted(buckets)
        ]
        series.append({"key": key, "points": bucket_points})

    return series


def count_buckets(series: Sequence[Mapping[str, Any]]) -> int:
    """Return the number of distinct buckets present in the series."""
    return len({point["ts"] for serie in series for point in serie["points"]})
