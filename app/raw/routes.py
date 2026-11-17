"""JSON routes for browsing the raw points of a metric.

The routes live under ``/api`` so the shared error handler always answers the
canonical six-key envelope, even when a caller omits an ``Accept`` header.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from ..errors import ApiError
from ..query import routes as query_routes
from . import service

#: The blueprint owns the raw point routes of the API.
bp = Blueprint("raw", __name__, url_prefix="/api")

#: The number of points returned when the caller does not choose a page size.
DEFAULT_LIMIT = 50

#: The largest page the raw API can return.
MAX_LIMIT = 500

#: The default number of seconds the long poll waits for a new point.
DEFAULT_TIMEOUT_S = 2.0

#: The largest number of seconds the long poll may hold a request open.
MAX_TIMEOUT_S = 10.0


def _now_ms() -> int:
    """Return the current UTC time as epoch milliseconds."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _parse_limit(raw: str | None) -> int:
    """Return the requested page size after checking its boundaries."""
    value = 50 if raw is None else raw
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 0

    if limit < 1 or limit > MAX_LIMIT:
        raise ApiError(
            "INVALID_PAGE_SIZE",
            "The page size must be between 1 and 500 points.",
            field="limit",
            expected=f"an integer between 1 and {MAX_LIMIT}",
            received=value,
            hint="Choose a page size between 1 and 500, for example 50.",
        )
    return limit


def _parse_offset(raw: str | None) -> int:
    """Return the requested page offset after checking its boundary."""
    value = 0 if raw is None else raw
    try:
        offset = int(value)
    except (TypeError, ValueError):
        offset = -1

    if offset < 0:
        raise ApiError(
            "INVALID_PAGE",
            "The page offset cannot be negative.",
            field="offset",
            expected="a non negative integer",
            received=value,
            hint="Use zero for the first page, or increase the offset to select a later page.",
        )
    return offset


def _parse_order(raw: str | None) -> service.PointOrder:
    """Return the requested point order."""
    value = "desc" if raw is None else raw.strip().lower()
    if value not in ("asc", "desc"):
        raise ApiError(
            "INVALID_ORDER",
            "The point order must be either 'asc' or 'desc'.",
            field="order",
            expected=["asc", "desc"],
            received=value,
            hint="Use 'desc' for the newest points first or 'asc' for the oldest first.",
        )
    return value  # type: ignore[return-value]


def _parse_since_id(raw: str | None) -> int:
    """Return the point id from which the long poll starts."""
    value = 0 if raw is None else raw
    try:
        since_id = int(value)
    except (TypeError, ValueError):
        since_id = -1

    if since_id < 0:
        raise ApiError(
            "INVALID_PAGE",
            "The point watermark cannot be negative.",
            field="since_id",
            expected="a non negative integer point id",
            received=value,
            hint="Use zero to wait for every point, or the latest point id already shown.",
        )
    return since_id


def _parse_timeout(raw: str | None) -> float:
    """Return the long poll timeout after checking its boundaries."""
    value = DEFAULT_TIMEOUT_S if raw is None else raw
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = -1.0

    if not math.isfinite(timeout) or timeout < 0 or timeout > MAX_TIMEOUT_S:
        raise ApiError(
            "INVALID_TIMEOUT",
            "The long poll timeout must be between 0 and 10 seconds.",
            field="timeout",
            expected=f"a number of seconds between 0 and {MAX_TIMEOUT_S:g}",
            received=value,
            hint="Choose a timeout of at most 10 seconds, for example 2.",
        )
    return timeout


def _metric_payload(metric: Any) -> dict[str, Any]:
    """Return the public description of a metric."""
    return {
        "id": metric.id,
        "name": metric.name,
        "type": metric.type,
        "unit": metric.unit,
    }


def _point_payload(point: service.RawPoint) -> dict[str, Any]:
    """Return the public representation of a raw point."""
    return {
        "id": point.id,
        "ts_ms": point.ts_ms,
        "received_at_ms": point.received_at_ms,
        "value": point.value,
        "tags": point.tags,
    }


@bp.get("/raw")
def raw_points() -> Any:
    """Return one page of the raw points of a metric."""
    name = query_routes._metric_name(request.args)
    metric = query_routes._resolve_metric(name)
    from_ms, to_ms = query_routes._time_bounds(request.args, _now_ms())
    limit = _parse_limit(request.args.get("limit"))
    offset = _parse_offset(request.args.get("offset"))
    order = _parse_order(request.args.get("order"))

    points, total = service.list_points(
        metric,
        from_ms,
        to_ms,
        limit,
        offset,
        order,
    )

    return jsonify(
        {
            "metric": _metric_payload(metric),
            "from_ms": from_ms,
            "to_ms": to_ms,
            "order": order,
            "total": total,
            "limit": limit,
            "offset": offset,
            "latest_id": service.latest_id(metric.id),
            "points": [_point_payload(point) for point in points],
        }
    )


@bp.get("/raw/wait")
def raw_wait() -> Any:
    """Hold the request open until a newer point arrives or the timeout ends."""
    name = query_routes._metric_name(request.args)
    metric = query_routes._resolve_metric(name)
    from_ms, to_ms = query_routes._time_bounds(request.args, _now_ms())
    since_id = _parse_since_id(request.args.get("since_id"))
    timeout = _parse_timeout(request.args.get("timeout"))

    points, waited_ms = service.wait_for_points(
        metric.id,
        since_id,
        from_ms,
        to_ms,
        timeout,
    )

    return jsonify(
        {
            "metric": _metric_payload(metric),
            "latest_id": service.latest_id(metric.id),
            "waited_ms": waited_ms,
            "points": [_point_payload(point) for point in points],
        }
    )
