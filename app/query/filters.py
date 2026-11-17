"""Parsing and evaluation of the query filters.

A filter is a small, explicit document:

.. code-block:: json

    {"field": "value", "op": ">", "value": 10}
    {"field": "ts", "op": "between", "value": [1700000000000, 1700003600000]}
    {"field": "tags.host", "op": "contains", "value": "web"}

The very same structure is what the widget builder stores, so the parsing
happens once, here, and the evaluation is a pure function over plain data.
Tags are filtered in Python (and never in SQL) because they live in a
``TEXT`` column holding a JSON document: SQLite and MariaDB do not agree on
JSON operators, so a portable implementation cannot push that work down to
the database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..errors import ApiError

#: Field holding the value of the data point.
VALUE_FIELD = "value"

#: Field holding the timestamp of the data point.
TS_FIELD = "ts"

#: Prefix of a field holding a tag value.
TAG_PREFIX = "tags."

#: Operators available for a numeric field (the value of a numeric metric and
#: the timestamp).
NUMERIC_OPERATORS: tuple[str, ...] = ("=", "!=", ">", ">=", "<", "<=", "between")

#: Operators available for a textual field (the value of a string metric and
#: every tag value).
STRING_OPERATORS: tuple[str, ...] = (
    "=",
    "!=",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "regex",
)

#: Operators available for a boolean field.  Both ignore the filter value.
BOOLEAN_OPERATORS: tuple[str, ...] = ("is_true", "is_false")

#: Metric types whose value is numeric.
NUMERIC_METRIC_TYPES: tuple[str, ...] = ("counter", "gauge", "timer")

#: The field forms understood by the filter language, shown to the caller in
#: the error envelopes.
ALLOWED_FIELDS_TEXT = "value, ts, tags.<key>"

#: The kinds of field the filters distinguish.
KIND_NUMERIC = "numeric"
KIND_STRING = "string"
KIND_BOOLEAN = "boolean"
KIND_TAG = "tag"


def _describe(value: Any) -> Any:
    """Return a JSON safe description of an arbitrary value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def operators_for(kind: str) -> tuple[str, ...]:
    """Return the operators allowed for a field kind."""
    if kind == KIND_NUMERIC:
        return NUMERIC_OPERATORS
    if kind == KIND_BOOLEAN:
        return BOOLEAN_OPERATORS
    return STRING_OPERATORS


def field_kind(field: str, metric_type: str) -> str:
    """Return the kind of a filter field for a metric of that type.

    The kind decides which operators are allowed, so it depends on the type
    of the metric the filter is applied to.
    """
    if field == VALUE_FIELD:
        if metric_type in NUMERIC_METRIC_TYPES:
            return KIND_NUMERIC
        if metric_type == "string":
            return KIND_STRING
        if metric_type == "bool":
            return KIND_BOOLEAN
        raise ApiError(
            "INVALID_METRIC_TYPE",
            f"'{metric_type}' is not a known metric type.",
            field="metric",
            expected="counter, gauge, timer, string, bool",
            received=metric_type,
            hint="Use one of the known metric types.",
        )

    if field == TS_FIELD:
        return KIND_NUMERIC

    if field.startswith(TAG_PREFIX):
        return KIND_TAG

    raise ApiError(
        "INVALID_FILTER_FIELD",
        f"'{field}' is not a field the filters understand.",
        field="filters.field",
        expected=ALLOWED_FIELDS_TEXT,
        received=field,
        hint=(
            "Use 'value' for the metric value, 'ts' for the timestamp or "
            "'tags.<key>' for a tag."
        ),
    )


@dataclass(frozen=True)
class FilterSpec:
    """A parsed filter, ready to be evaluated against a data point."""

    field: str
    op: str
    value: Any
    kind: str
    tag_key: str | None = None
    pattern: re.Pattern[str] | None = None

    def matches(
        self,
        *,
        ts_ms: int,
        value_num: float | None,
        value_str: str | None,
        tags: Mapping[str, Any],
    ) -> bool:
        """Return True when the data point satisfies this filter."""
        if self.kind == KIND_NUMERIC:
            if self.field == TS_FIELD:
                left = float(ts_ms)
            else:
                left = float(value_num) if value_num is not None else 0.0
            return _match_numeric(self.op, left, self.value)

        if self.kind == KIND_BOOLEAN:
            is_true = _bool_value(value_num, value_str)
            return is_true if self.op == "is_true" else not is_true

        if self.kind == KIND_STRING:
            return _match_string(self.op, value_str or "", self.value, self.pattern)

        # A tag that is not present on the point never matches: the tag simply
        # does not carry a value to compare.
        if self.tag_key is None or self.tag_key not in tags:
            return False
        raw = tags[self.tag_key]
        if raw is None:
            return False
        return _match_string(self.op, tag_as_text(raw), self.value, self.pattern)


def tag_as_text(value: Any) -> str:
    """Return the textual representation of compare a tag value with."""
    if isinstance(value, bool):
        # The ingestion stores booleans as the words "true" and "false", so the
        # textual form has to match the one the UI shows.
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def _bool_value(value_num: float | None, value_str: str | None) -> bool:
    """Return the boolean carried by a data point."""
    if value_num is not None:
        return bool(value_num)
    return str(value_str).strip().lower() == "true"


def _match_numeric(op: str, left: float, right: Any) -> bool:
    """Apply a numeric operator."""
    if op == "=":
        return left == float(right)
    if op == "!=":
        return left != float(right)
    if op == ">":
        return left > float(right)
    if op == ">=":
        return left >= float(right)
    if op == "<":
        return left < float(right)
    if op == "<=":
        return left <= float(right)
    if op == "between":
        low, high = right
        return float(low) <= left <= float(high)
    raise ValueError(f"unsupported numeric operator {op!r}")  # pragma: no cover


def _match_string(
    op: str, left: str, right: Any, pattern: re.Pattern[str] | None
) -> bool:
    """Apply a textual operator."""
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "contains":
        return str(right) in left
    if op == "not_contains":
        return str(right) not in left
    if op == "starts_with":
        return left.startswith(str(right))
    if op == "ends_with":
        return left.endswith(str(right))
    if op == "regex":
        assert pattern is not None
        return pattern.search(left) is not None
    raise ValueError(f"unsupported string operator {op!r}")  # pragma: no cover


def _is_number(value: Any) -> bool:
    """Return True when the value is a real number (a boolean is not)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _invalid_value(field: str, value: Any, reason: str) -> ApiError:
    """Return the error raised when a filter value does not fit its operator."""
    return ApiError(
        "INVALID_FILTER_VALUE",
        f"The value of the filter on '{field}' is not valid: {reason}",
        field=field,
        expected="a value that matches the operator",
        received=f"{_describe(value)} ({reason})",
        hint=f"Fix the filter value: {reason}.",
    )


def _parse_filter(item: Any, metric_type: str, index: int) -> FilterSpec:
    """Parse one filter object into a :class:`FilterSpec`."""
    location = f"filters[{index}]"

    if not isinstance(item, Mapping):
        raise ApiError(
            "INVALID_FILTERS",
            "Every filter must be a JSON object.",
            field=location,
            expected='{"field": "value", "op": ">", "value": 10}',
            received=_describe(item),
            hint="Send the filters as an array of objects with field, op and value.",
        )

    raw_field = item.get("field")
    if not isinstance(raw_field, str) or not raw_field.strip():
        raise ApiError(
            "INVALID_FILTER_FIELD",
            "The field of a filter must be a non empty string.",
            field=f"{location}.field",
            expected=ALLOWED_FIELDS_TEXT,
            received=_describe(raw_field),
            hint=(
                "Use 'value' for the metric value, 'ts' for the timestamp or "
                "'tags.<key>' for a tag."
            ),
        )

    field = raw_field.strip()
    kind = field_kind(field, metric_type)

    raw_op = item.get("op")
    allowed = operators_for(kind)
    if not isinstance(raw_op, str) or raw_op not in allowed:
        raise ApiError(
            "INVALID_FILTER_OPERATOR",
            f"'{_describe(raw_op)}' is not an operator allowed on the field '{field}'.",
            field=f"{location}.op",
            expected=list(allowed),
            received=_describe(raw_op),
            hint=f"Use one of the allowed operators: {', '.join(allowed)}.",
        )

    op = raw_op
    raw_value = item.get("value")
    tag_key: str | None = None
    pattern: re.Pattern[str] | None = None

    if kind == KIND_NUMERIC:
        if op == "between":
            if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 2:
                raise _invalid_value(
                    location,
                    raw_value,
                    "the 'between' operator needs an array of exactly two numbers",
                )
            if not all(_is_number(bound) for bound in raw_value):
                raise _invalid_value(
                    location, raw_value, "both bounds of 'between' must be numbers"
                )
            low, high = float(raw_value[0]), float(raw_value[1])
            if low > high:
                raise _invalid_value(
                    location,
                    raw_value,
                    "the low bound of 'between' must not be greater than the high bound",
                )
            value: Any = [low, high]
        else:
            if not _is_number(raw_value):
                raise _invalid_value(
                    location, raw_value, f"the operator '{op}' needs a number"
                )
            value = float(raw_value)

    elif kind == KIND_BOOLEAN:
        # The value is ignored on purpose: 'is_true' and 'is_false' fully
        # describe the condition to match.
        value = None

    else:
        if not isinstance(raw_value, str):
            raise _invalid_value(
                location, raw_value, f"the operator '{op}' needs a string"
            )
        value = raw_value
        if op == "regex":
            try:
                pattern = re.compile(raw_value)
            except re.error as exc:
                raise _invalid_value(
                    location, raw_value, f"the regular expression is invalid: {exc}"
                ) from exc
        if kind == KIND_TAG:
            tag_key = field[len(TAG_PREFIX) :]
            if not tag_key:
                raise ApiError(
                    "INVALID_FILTER_FIELD",
                    "The tag of a filter must have a name.",
                    field=f"{location}.field",
                    expected="tags.<key>",
                    received=field,
                    hint="Name the tag, for example 'tags.host'.",
                )

    return FilterSpec(
        field=field,
        op=op,
        value=value,
        kind=kind,
        tag_key=tag_key,
        pattern=pattern,
    )


def parse_filters(raw: Any, metric_type: str) -> list[FilterSpec]:
    """Parse the ``filters`` parameter into a list of filter specifications.

    ``raw`` is either ``None``, a list of filter objects or a JSON encoded
    string holding such a list.  Filters are ANDed by the caller.
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ApiError(
                "INVALID_FILTERS",
                "The filters parameter is not valid JSON.",
                field="filters",
                expected="a JSON array of filter objects",
                received=exc.msg,
                hint=(
                    f"{exc.msg} (line {exc.lineno}, column {exc.colno}); send the "
                    "filters as a JSON array of objects with field, op and value"
                ),
            ) from exc

    if not isinstance(raw, (list, tuple)):
        raise ApiError(
            "INVALID_FILTERS",
            "The filters parameter must be an array of filter objects.",
            field="filters",
            expected="a JSON array of filter objects",
            received=_describe(raw),
            hint=(
                "Send the filters as an array, for example "
                '[{"field": "value", "op": ">", "value": 10}].'
            ),
        )

    return [_parse_filter(item, metric_type, index) for index, item in enumerate(raw)]


def matches_all(
    specs: Sequence[FilterSpec],
    *,
    ts_ms: int,
    value_num: float | None,
    value_str: str | None,
    tags: Mapping[str, Any],
) -> bool:
    """Return True when the data point satisfies every filter.

    Filters are ANDed: a point is kept only when it satisfies all of them.
    """
    return all(
        spec.matches(
            ts_ms=ts_ms, value_num=value_num, value_str=value_str, tags=tags
        )
        for spec in specs
    )
