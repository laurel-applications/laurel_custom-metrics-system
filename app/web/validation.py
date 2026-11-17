"""Validation of the forms of the web UI.

The rules of the widget builder are not a second implementation of the query
contract: they *reuse* the very parsers of the query API (``parse_agg``,
``parse_bucket``, ``parse_group_by``) and the very filter parser
(``app.query.filters.parse_filters``).  A widget can therefore never store a
configuration the query API would reject, which is exactly what the UI
promises to the user.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..errors import ApiError
from ..query import filters as filter_module
from ..query import service
from ..query.routes import parse_agg, parse_bucket, parse_group_by

#: The chart types the front end knows how to render.
CHART_TYPES: tuple[str, ...] = ("line", "bar", "table", "stat")

#: The time ranges a widget can cover.
TIME_RANGES: tuple[str, ...] = ("1h", "6h", "24h", "7d", "30d")

#: Maximum length of a dashboard name, matching the column definition.
MAX_NAME_LENGTH = 255

#: Maximum length of a widget title, matching the column definition.
MAX_TITLE_LENGTH = 255


def _describe(value: Any) -> Any:
    """Return a JSON safe description of an arbitrary value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def validate_name(raw: Any, *, field: str = "name") -> str:
    """Return a validated dashboard name."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ApiError(
            "INVALID_NAME",
            "The name is required.",
            field=field,
            expected=f"a non empty name of at most {MAX_NAME_LENGTH} characters",
            received=None,
            hint="Type a name for the dashboard, for example 'Production overview'.",
        )

    if not isinstance(raw, str):
        raise ApiError(
            "INVALID_NAME",
            "The name must be a string.",
            field=field,
            expected=f"a string of at most {MAX_NAME_LENGTH} characters",
            received=_describe(raw),
            hint="Type a name for the dashboard, for example 'Production overview'.",
        )

    name = raw.strip()
    if not name:
        raise ApiError(
            "INVALID_NAME",
            "The name must not be blank.",
            field=field,
            expected=f"a non empty name of at most {MAX_NAME_LENGTH} characters",
            received=raw,
            hint="Type a name for the dashboard, for example 'Production overview'.",
        )

    if len(name) > MAX_NAME_LENGTH:
        raise ApiError(
            "INVALID_NAME",
            f"The name must be at most {MAX_NAME_LENGTH} characters long.",
            field=field,
            expected=f"at most {MAX_NAME_LENGTH} characters",
            received=len(name),
            hint="Shorten the name; it is stored in a 255 character column.",
        )

    return name


def validate_chart_type(raw: Any) -> str:
    """Return the validated chart type."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ApiError(
            "INVALID_CHART_TYPE",
            "The chart type is required.",
            field="chart_type",
            expected=list(CHART_TYPES),
            received=None,
            hint=f"Pick one of: {', '.join(CHART_TYPES)}.",
        )

    chart_type = raw.strip().lower() if isinstance(raw, str) else raw
    if chart_type not in CHART_TYPES:
        raise ApiError(
            "INVALID_CHART_TYPE",
            f"'{_describe(raw)}' is not a known chart type.",
            field="chart_type",
            expected=list(CHART_TYPES),
            received=_describe(raw),
            hint=f"Pick one of: {', '.join(CHART_TYPES)}.",
        )

    return chart_type


def validate_time_range(raw: Any) -> str:
    """Return the validated time range."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ApiError(
            "INVALID_TIME_RANGE",
            "The time range is required.",
            field="time_range",
            expected=list(TIME_RANGES),
            received=None,
            hint=f"Pick one of: {', '.join(TIME_RANGES)}.",
        )

    time_range = raw.strip().lower() if isinstance(raw, str) else raw
    if time_range not in TIME_RANGES:
        raise ApiError(
            "INVALID_TIME_RANGE",
            f"'{_describe(raw)}' is not a known time range.",
            field="time_range",
            expected=list(TIME_RANGES),
            received=_describe(raw),
            hint=f"Pick one of: {', '.join(TIME_RANGES)}.",
        )

    return time_range


def validate_aggregation(raw: Any, metric_type: str) -> str:
    """Return the validated aggregation for a metric of that type.

    The aggregation is parsed exactly like the query API parses it, and then
    checked against the aggregations the metric type supports, so the error
    names the valid aggregations when they do not match.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ApiError(
            "INVALID_AGGREGATION",
            "The aggregation is required.",
            field="agg",
            expected=list(service.AGGREGATIONS),
            received=None,
            hint=f"Pick one of: {', '.join(service.AGGREGATIONS)}.",
        )

    agg = parse_agg(raw)

    valid = service.valid_aggregations(metric_type)
    if agg not in valid:
        raise ApiError(
            "INVALID_AGGREGATION_FOR_TYPE",
            f"The aggregation '{agg}' is not valid for a '{metric_type}' metric.",
            field="agg",
            expected=list(valid),
            received=agg,
            hint=(
                f"A '{metric_type}' metric only supports: {', '.join(valid)}."
            ),
        )

    return agg


def validate_filters(raw: Any, metric_type: str) -> tuple[str, list[dict[str, Any]]]:
    """Return the validated filters and their normalized JSON representation.

    The filters are validated with the very parser the query API uses, so a
    broken document is rejected with the parser's own message instead of being
    stored and failing later at query time.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "[]", []

    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(
                "INVALID_FILTERS",
                "The filters are not valid JSON.",
                field="filters",
                expected="a JSON array of filter objects",
                received=exc.msg,
                hint=(
                    f"{exc.msg} (line {exc.lineno}, column {exc.colno}); send the "
                    'filters as a JSON array of objects with field, op and value, '
                    'for example [{"field": "value", "op": ">", "value": 10}]'
                ),
            ) from exc
    else:
        decoded = raw

    if not isinstance(decoded, list):
        raise ApiError(
            "INVALID_FILTERS",
            "The filters must be a JSON array of filter objects.",
            field="filters",
            expected="a JSON array of filter objects",
            received=_describe(decoded),
            hint=(
                "Send the filters as a JSON array, for example "
                '[{"field": "value", "op": ">", "value": 10}].'
            ),
        )

    # The query parser validates every filter against the type of the metric.
    filter_module.parse_filters(decoded, metric_type)

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(decoded):
        if not isinstance(item, Mapping):
            raise ApiError(
                "INVALID_FILTERS",
                "Every filter must be a JSON object.",
                field=f"filters[{index}]",
                expected='{"field": "value", "op": ">", "value": 10}',
                received=_describe(item),
                hint="Send the filters as an array of objects with field, op and value.",
            )
        normalized.append(
            {
                "field": item.get("field"),
                "op": item.get("op"),
                "value": item.get("value"),
            }
        )

    return json.dumps(normalized, ensure_ascii=False), normalized


def serialize_widget_filters(filters_json: str) -> str:
    """Return the JSON array stored in ``Widget.filters``.

    The ``filters`` column holds the bare array of filter objects, exactly
    the shape the query API expects, so a widget is replayable without any
    translation.  The group by lives in its own column.
    """
    try:
        filters = json.loads(filters_json) if filters_json else []
    except json.JSONDecodeError:  # pragma: no cover - the value is validated before
        filters = []

    if not isinstance(filters, list):  # pragma: no cover - defensive
        filters = []

    return json.dumps(filters, ensure_ascii=False)


def read_widget_config(raw: str | None) -> dict[str, Any]:
    """Return the configuration stored in the legacy ``filters`` document.

    Before the ``group_by`` column existed the whole configuration of a
    widget was stored as a JSON object with a ``group_by`` key next to the
    ``filters`` array.  That shape is still understood when reading, so a
    database that was not migrated yet keeps working; a bare JSON array is
    the normal shape now.
    """
    if not raw or not raw.strip():
        return {"group_by": None, "filters": []}

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"group_by": None, "filters": []}

    if isinstance(decoded, list):
        return {"group_by": None, "filters": decoded}

    if isinstance(decoded, Mapping):
        filters = decoded.get("filters")
        group_by = decoded.get("group_by")
        return {
            "group_by": group_by if isinstance(group_by, str) and group_by else None,
            "filters": filters if isinstance(filters, list) else [],
        }

    return {"group_by": None, "filters": []}


def widget_settings(widget: Any) -> dict[str, Any]:
    """Return the ``group_by`` and the filters of a stored widget.

    The ``group_by`` column is the source of truth.  The legacy document is
    only consulted when the column is empty, which keeps a database that has
    not been migrated yet readable.
    """
    legacy = read_widget_config(getattr(widget, "filters", None))
    column = getattr(widget, "group_by", None)

    group_by = column if isinstance(column, str) and column.strip() else None
    if group_by is None:
        group_by = legacy["group_by"]

    return {"group_by": group_by, "filters": legacy["filters"]}


def validate_bucket(raw: Any) -> int | None:
    """Return the bucket size in seconds, or ``None`` for the automatic size."""
    bucket = parse_bucket(raw)
    if bucket == "auto":
        return None
    return int(bucket)


def validate_group_by(raw: Any) -> str | None:
    """Return the tag key the widget groups by, or ``None``."""
    # An empty field means "do not group", which is not an error here even
    # though the query API treats an empty group_by as invalid.
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    group_by = parse_group_by(raw)
    return group_by or None
