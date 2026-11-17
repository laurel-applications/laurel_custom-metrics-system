"""HTTP routes of the query API.

``GET /api/query`` and ``POST /api/query`` accept exactly the same
parameters and share one code path.  The POST form exists so the UI can
send a JSON body with complex filters without URL encoding gymnastics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from ..errors import ApiError
from ..extensions import db
from ..models import DataPoint, Metric
from . import filters as filter_module
from . import service

#: The blueprint owns the query routes of the API.
bp = Blueprint("query", __name__, url_prefix="/api")

#: Content types that are accepted as a JSON body.
JSON_CONTENT_TYPES = ("application/json", "text/json")

#: The default range of a query without an explicit ``from``/``to``.
DEFAULT_RANGE_MS = 24 * 3600 * 1000


def _describe(value: Any) -> Any:
    """Return a JSON safe description of an arbitrary value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _parse_iso_ms(text: str) -> tuple[int | None, str | None]:
    """Return ``(epoch_ms, error)`` for an ISO-8601 timestamp."""
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        return None, str(exc)

    if parsed.tzinfo is None:
        # A naive timestamp is interpreted as UTC, the only timezone the
        # system stores.
        parsed = parsed.replace(tzinfo=timezone.utc)

    return int(round(parsed.timestamp() * 1000)), None


def _parse_time_ms(raw: Any, field: str) -> int:
    """Return a timestamp, in epoch milliseconds, from a request parameter."""
    expected = "epoch milliseconds as an integer or an ISO-8601 string"

    if isinstance(raw, bool):
        raise ApiError(
            "INVALID_TIMESTAMP",
            "The timestamp must not be a boolean.",
            field=field,
            expected=expected,
            received=_describe(raw),
            hint=f"Send the {field} bound as epoch milliseconds, e.g. 1700000000000.",
        )

    if isinstance(raw, int):
        return int(raw)

    if isinstance(raw, float):
        if not raw.is_integer():
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The timestamp must be a whole number of milliseconds.",
                field=field,
                expected=expected,
                received=_describe(raw),
                hint=f"Send the {field} bound as an integer, e.g. 1700000000000.",
            )
        return int(raw)

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The timestamp must not be empty.",
                field=field,
                expected=expected,
                received=raw,
                hint=f"Send the {field} bound as epoch milliseconds, e.g. 1700000000000.",
            )
        try:
            return int(text)
        except ValueError:
            pass
        value, error = _parse_iso_ms(text)
        if error is not None:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The timestamp could not be parsed as epoch milliseconds nor as "
                "an ISO-8601 date.",
                field=field,
                expected=expected,
                received=raw,
                hint=(
                    f"Use an ISO-8601 timestamp such as 2023-11-14T22:13:20Z, or "
                    f"epoch milliseconds. Parser message: {error}"
                ),
            )
        assert value is not None  # narrowed by the error check above
        return value

    raise ApiError(
        "INVALID_TIMESTAMP",
        "The timestamp must be a number or an ISO-8601 string.",
        field=field,
        expected=expected,
        received=_describe(raw),
        hint=f"Send the {field} bound as epoch milliseconds, e.g. 1700000000000.",
    )


def _parse_agg(raw: Any) -> str:
    """Return the requested aggregation."""
    if raw is None:
        return "avg"

    if not isinstance(raw, str):
        raise ApiError(
            "INVALID_AGGREGATION",
            "The aggregation must be a string.",
            field="agg",
            expected=list(service.AGGREGATIONS),
            received=_describe(raw),
            hint=f"Use one of: {', '.join(service.AGGREGATIONS)}.",
        )

    agg = raw.strip().lower()
    if agg not in service.AGGREGATIONS:
        raise ApiError(
            "INVALID_AGGREGATION",
            f"'{raw}' is not a known aggregation.",
            field="agg",
            expected=list(service.AGGREGATIONS),
            received=raw,
            hint=f"Use one of: {', '.join(service.AGGREGATIONS)}.",
        )
    return agg


def _parse_bucket(raw: Any) -> int | str:
    """Return the requested bucket size in seconds, or the ``auto`` marker."""
    if raw is None:
        return "auto"

    if isinstance(raw, str):
        text = raw.strip().lower()
        if text == "auto":
            return "auto"
        if not text.lstrip("+").isdigit():
            raise ApiError(
                "INVALID_BUCKET",
                "The bucket size must be a positive integer number of seconds.",
                field="bucket",
                expected="a positive integer number of seconds",
                received=raw,
                hint="Send the bucket size in seconds, for example 300, or 'auto'.",
            )
        raw = int(text)

    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ApiError(
            "INVALID_BUCKET",
            "The bucket size must be a positive integer number of seconds.",
            field="bucket",
            expected="a positive integer number of seconds",
            received=_describe(raw),
            hint="Send the bucket size in seconds, for example 300, or 'auto'.",
        )

    if raw <= 0:
        raise ApiError(
            "INVALID_BUCKET",
            "The bucket size must be greater than zero.",
            field="bucket",
            expected="a positive integer number of seconds",
            received=raw,
            hint="Send a bucket size of at least one second, or 'auto'.",
        )

    return int(raw)


def _parse_group_by(raw: Any) -> str | None:
    """Return the tag key the result is grouped by."""
    if raw is None:
        return None

    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(
            "INVALID_GROUP_BY",
            "The group_by parameter must be the name of a tag.",
            field="group_by",
            expected="the name of a tag, for example 'host'",
            received=_describe(raw),
            hint="Send the name of the tag to group by, or omit the parameter.",
        )

    return raw.strip()


def _parse_body() -> dict[str, Any]:
    """Return the JSON object of the request body."""
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if (
        content_type
        and not content_type.endswith("+json")
        and content_type not in JSON_CONTENT_TYPES
    ):
        raise ApiError(
            "UNSUPPORTED_MEDIA_TYPE",
            "The request body must be sent as JSON.",
            field=None,
            expected="application/json",
            received=content_type,
            hint="Send the payload with the header Content-Type: application/json.",
            http_status=415,
        )

    raw = request.get_data(cache=True, as_text=True)
    if not raw.strip():
        raise ApiError(
            "INVALID_BODY",
            "The request body is empty.",
            field=None,
            expected="a JSON object with the query parameters",
            received=None,
            hint="Send the query parameters as a JSON object.",
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(
            "INVALID_JSON",
            "The request body is not valid JSON.",
            field=None,
            expected="a JSON object",
            received=exc.msg,
            hint=(
                f"{exc.msg} (line {exc.lineno}, column {exc.colno}); send the query "
                "parameters as a single JSON object"
            ),
        ) from exc

    if not isinstance(payload, dict):
        raise ApiError(
            "INVALID_BODY",
            "The request body must be a JSON object.",
            field=None,
            expected="a JSON object",
            received=_describe(payload),
            hint="Send the query parameters as a single JSON object.",
        )

    return payload


def _metric_name(params: Mapping[str, Any]) -> str:
    """Return the requested metric name."""
    raw = params.get("metric")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ApiError(
            "MISSING_PARAMETER",
            "The name of the metric to query is required.",
            field="metric",
            expected="the name of an existing metric",
            received=_describe(raw),
            hint='Send the metric name, for example {"metric": "requests"}.',
        )

    if not isinstance(raw, str):
        raise ApiError(
            "MISSING_PARAMETER",
            "The name of the metric to query must be a string.",
            field="metric",
            expected="the name of an existing metric",
            received=_describe(raw),
            hint='Send the metric name, for example {"metric": "requests"}.',
        )

    return raw.strip()


def _time_bounds(params: Mapping[str, Any], now_ms: int) -> tuple[int, int]:
    """Return the ``(from_ms, to_ms)`` bounds of the query."""
    raw_from = params.get("from")
    raw_to = params.get("to")

    if raw_to is None:
        to_ms = now_ms
    else:
        to_ms = _parse_time_ms(raw_to, "to")

    if raw_from is None:
        # Without an explicit start the range ends where the requested end
        # is, so a query on a closed interval still spans the last 24 hours.
        from_ms = to_ms - DEFAULT_RANGE_MS
    else:
        from_ms = _parse_time_ms(raw_from, "from")

    if from_ms >= to_ms:
        raise ApiError(
            "INVALID_TIME_RANGE",
            "The start of the range must be earlier than its end.",
            field="from",
            expected="from must be earlier than to",
            received={"from": from_ms, "to": to_ms},
            hint="Move the 'from' bound into the past or the 'to' bound forward.",
        )

    return from_ms, to_ms


def _resolve_metric(name: str) -> Metric:
    """Return the metric with that name."""
    statement = select(Metric).where(Metric.name == name)
    metric = db.session.execute(statement).scalar_one_or_none()

    if metric is None:
        raise ApiError(
            "METRIC_NOT_FOUND",
            f"There is no metric named '{name}'.",
            field="metric",
            expected="the name of an existing metric",
            received=name,
            hint="List the known metrics with GET /api/metrics.",
            http_status=404,
        )

    return metric


def _load_points(metric_id: int, from_ms: int, to_ms: int) -> list[service.Point]:
    """Return the data points of a metric inside the range.

    Only the metric and the time range are pushed down to the database: the
    tags live in a JSON document inside a ``TEXT`` column, so the filtering
    and the grouping by tag happen in Python.
    """
    statement = (
        select(DataPoint)
        .where(
            DataPoint.metric_id == metric_id,
            DataPoint.ts_utc_ms >= from_ms,
            DataPoint.ts_utc_ms <= to_ms,
        )
        .order_by(DataPoint.ts_utc_ms, DataPoint.id)
    )
    rows = db.session.execute(statement).scalars().all()

    points: list[service.Point] = []
    for row in rows:
        points.append(
            service.Point(
                id=row.id,
                ts_ms=row.ts_utc_ms,
                value_num=row.value_num,
                value_str=row.value_str,
                tags=_parse_tags(row.tags),
            )
        )
    return points


def _parse_tags(raw: str | None) -> dict[str, Any]:
    """Return the tags of a data point as a dictionary."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A malformed document must not break the whole query.
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_query(params: Mapping[str, Any]) -> Any:
    """Run a query and return the JSON response.

    Both HTTP methods funnel through here, so they can never drift apart.
    """
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    name = _metric_name(params)
    metric = _resolve_metric(name)

    agg = _parse_agg(params.get("agg"))
    valid_aggs = service.valid_aggregations(metric.type)
    if agg not in valid_aggs:
        raise ApiError(
            "INVALID_AGGREGATION_FOR_TYPE",
            f"The aggregation '{agg}' is not valid for a '{metric.type}' metric.",
            field="agg",
            expected=list(valid_aggs),
            received=agg,
            hint=(
                f"A '{metric.type}' metric only supports: "
                f"{', '.join(valid_aggs)}."
            ),
        )

    from_ms, to_ms = _time_bounds(params, now_ms)

    bucket = _parse_bucket(params.get("bucket"))
    bucket_sec = (
        service.auto_bucket_sec(to_ms - from_ms) if bucket == "auto" else int(bucket)
    )
    bucket_ms = bucket_sec * 1000

    group_by = _parse_group_by(params.get("group_by"))
    specs = filter_module.parse_filters(params.get("filters"), metric.type)

    points = _load_points(metric.id, from_ms, to_ms)
    kept = [
        point
        for point in points
        if filter_module.matches_all(
            specs,
            ts_ms=point.ts_ms,
            value_num=point.value_num,
            value_str=point.value_str,
            tags=point.tags,
        )
    ]

    series = service.build_series(kept, agg, metric.type, bucket_ms, group_by)

    return jsonify(
        {
            "metric": {
                "id": metric.id,
                "name": metric.name,
                "type": metric.type,
                "unit": metric.unit,
            },
            "agg": agg,
            "bucket_sec": bucket_sec,
            "group_by": group_by,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "point_count": len(kept),
            "bucket_count": service.count_buckets(series),
            "series": series,
        }
    )


@bp.get("/query")
def query_get() -> Any:
    """Query a metric with the parameters carried by the query string."""
    return _run_query(request.args)


@bp.post("/query")
def query_post() -> Any:
    """Query a metric with the parameters carried by a JSON body."""
    return _run_query(_parse_body())
