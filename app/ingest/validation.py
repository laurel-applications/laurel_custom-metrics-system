"""Validation of the ingestion payload.

The rules live in a module of pure functions so they can be unit tested
without a request context and so the route handler only has to orchestrate
them.  Every rejection raises an :class:`~app.errors.ApiError` with a stable
code, the offending field, what was expected and what arrived.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from ..errors import ApiError

#: The metric types the system understands.
METRIC_TYPES: tuple[str, ...] = ("counter", "gauge", "timer", "string", "bool")

#: Human readable list of the allowed types, used in the error envelopes.
ALLOWED_TYPES_TEXT = ", ".join(METRIC_TYPES)

#: A metric name must start with a letter or a digit and may contain dots,
#: underscores and dashes afterwards.  The pattern is intentionally strict so
#: a metric name is always safe to use in a URL or a file name.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")

#: The pattern as shown to the caller in the error hints.
NAME_PATTERN_TEXT = r"^[A-Za-z0-9][A-Za-z0-9._\-]*$"

#: Maximum length of a metric name, matching the column definition.
MAX_NAME_LENGTH = 255

#: Maximum length of the unit column.
MAX_UNIT_LENGTH = 32

#: Maximum length of a string value, matching the column definition.
MAX_STRING_VALUE_LENGTH = 1024

#: Maximum number of tags per data point.
MAX_TAGS = 32

#: Maximum length of a single tag key.
MAX_TAG_KEY_LENGTH = 64

#: Epoch milliseconds below this threshold are almost certainly epoch
#: seconds.  1e11 ms is 1973-03-03, and epoch seconds only reach 1e11 in the
#: year 5138, so the boundary is unambiguous for any realistic payload.
AMBIGUOUS_TIMESTAMP_THRESHOLD = 100_000_000_000

#: Types whose value is stored in the numeric column.
NUMERIC_TYPES = ("counter", "gauge", "timer")


def now_ms() -> int:
    """Return the current UTC time as epoch milliseconds."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def describe(value: Any) -> Any:
    """Return a JSON safe description of an arbitrary value.

    The envelope is serialized to JSON, so an unexpected value (a dict, a list,
    a custom object) must be flattened into something serializable before it
    reaches the caller.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def validate_name(payload: Mapping[str, Any]) -> str:
    """Return the validated metric name of the payload."""
    if "name" not in payload or payload["name"] is None:
        raise ApiError(
            "INVALID_METRIC_NAME",
            "The metric name is required.",
            field="name",
            expected=NAME_PATTERN_TEXT,
            received=None,
            hint=(
                "Send the metric name in the payload, for example "
                '{"name": "requests"}.'
            ),
        )

    raw = payload["name"]
    if not isinstance(raw, str):
        raise ApiError(
            "INVALID_METRIC_NAME",
            "The metric name must be a string.",
            field="name",
            expected=NAME_PATTERN_TEXT,
            received=describe(raw),
            hint="Quote the metric name, for example {\"name\": \"requests\"}.",
        )

    if not 1 <= len(raw) <= MAX_NAME_LENGTH:
        raise ApiError(
            "INVALID_METRIC_NAME",
            f"The metric name must be between 1 and {MAX_NAME_LENGTH} characters long.",
            field="name",
            expected=f"1..{MAX_NAME_LENGTH} characters matching {NAME_PATTERN_TEXT}",
            received=len(raw),
            hint="Shorten the metric name; it is stored in a 255 character column.",
        )

    if not NAME_PATTERN.match(raw):
        raise ApiError(
            "INVALID_METRIC_NAME",
            "The metric name contains characters that are not allowed.",
            field="name",
            expected=NAME_PATTERN_TEXT,
            received=raw,
            hint=(
                f"The name must match {NAME_PATTERN_TEXT}: use letters, digits, dots, "
                "underscores and dashes only, and start with a letter or a digit."
            ),
        )

    return raw


def validate_type(payload: Mapping[str, Any]) -> str | None:
    """Return the declared metric type, or ``None`` when it was omitted."""
    if "type" not in payload or payload["type"] is None:
        return None

    raw = payload["type"]
    if not isinstance(raw, str):
        raise ApiError(
            "INVALID_METRIC_TYPE",
            "The metric type must be a string.",
            field="type",
            expected=ALLOWED_TYPES_TEXT,
            received=describe(raw),
            hint=f"Use one of the allowed types: {ALLOWED_TYPES_TEXT}.",
        )

    if raw not in METRIC_TYPES:
        raise ApiError(
            "INVALID_METRIC_TYPE",
            f"'{raw}' is not a known metric type.",
            field="type",
            expected=ALLOWED_TYPES_TEXT,
            received=raw,
            hint=f"Use one of the allowed types: {ALLOWED_TYPES_TEXT}.",
        )

    return raw


def validate_unit(payload: Mapping[str, Any]) -> str | None:
    """Return the validated unit, or ``None`` when it was omitted."""
    if "unit" not in payload or payload["unit"] is None:
        return None

    raw = payload["unit"]
    if not isinstance(raw, str):
        raise ApiError(
            "INVALID_UNIT",
            "The unit must be a string.",
            field="unit",
            expected=f"a string of at most {MAX_UNIT_LENGTH} characters",
            received=describe(raw),
            hint="Send the unit as a string, for example {\"unit\": \"req\"}.",
        )

    if len(raw) > MAX_UNIT_LENGTH:
        raise ApiError(
            "INVALID_UNIT",
            f"The unit must be at most {MAX_UNIT_LENGTH} characters long.",
            field="unit",
            expected=f"at most {MAX_UNIT_LENGTH} characters",
            received=len(raw),
            hint=f"Shorten the unit; it is stored in a {MAX_UNIT_LENGTH} character column.",
        )

    return raw


def _parse_iso_timestamp(raw: str) -> tuple[int | None, str | None]:
    """Return ``(epoch_ms, error_message)`` for an ISO-8601 timestamp."""
    text = raw.strip()
    if not text:
        return None, "the value is empty"

    candidate = text
    # ``fromisoformat`` understands ``Z`` from Python 3.11 onwards, but the
    # explicit replacement keeps the intent obvious and works on any runtime.
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        return None, str(exc)

    if parsed.tzinfo is None:
        # A naive timestamp is interpreted as UTC, which is the only timezone
        # the system stores.
        parsed = parsed.replace(tzinfo=timezone.utc)

    return int(round(parsed.timestamp() * 1000)), None


def validate_timestamp(payload: Mapping[str, Any]) -> int:
    """Return the timestamp of the payload as epoch milliseconds.

    When the payload carries no timestamp the ingestion time is used.
    """
    if "timestamp" not in payload or payload["timestamp"] is None:
        return now_ms()

    raw = payload["timestamp"]

    if isinstance(raw, bool):
        raise ApiError(
            "INVALID_TIMESTAMP",
            "The timestamp must be a number or an ISO-8601 string, not a boolean.",
            field="timestamp",
            expected="epoch milliseconds as an integer or an ISO-8601 string",
            received=describe(raw),
            hint="Send the timestamp as epoch milliseconds, for example 1700000000000.",
        )

    if isinstance(raw, int) or isinstance(raw, float):
        if isinstance(raw, float):
            if not math.isfinite(raw) or not raw.is_integer():
                raise ApiError(
                    "INVALID_TIMESTAMP",
                    "The timestamp must be a whole number of milliseconds.",
                    field="timestamp",
                    expected="epoch milliseconds as an integer",
                    received=describe(raw),
                    hint="Send the timestamp as an integer, for example 1700000000000.",
                )
        value = int(raw)
        if value < 0:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The timestamp must not be negative.",
                field="timestamp",
                expected="epoch milliseconds as a non negative integer",
                received=value,
                hint="Send the timestamp as epoch milliseconds, for example 1700000000000.",
            )
        if value < AMBIGUOUS_TIMESTAMP_THRESHOLD:
            raise ApiError(
                "AMBIGUOUS_TIMESTAMP",
                "The timestamp looks like epoch seconds instead of epoch milliseconds.",
                field="timestamp",
                expected="epoch milliseconds",
                received=value,
                hint=(
                    "value looks like epoch seconds; convert to milliseconds "
                    "(multiply by 1000)"
                ),
            )
        return value

    if isinstance(raw, str):
        value, error = _parse_iso_timestamp(raw)
        if error is not None:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The timestamp string could not be parsed as an ISO-8601 date.",
                field="timestamp",
                expected="epoch milliseconds as an integer or an ISO-8601 string",
                received=raw,
                hint=(
                    "Use an ISO-8601 timestamp such as 2023-11-14T22:13:20Z, or epoch "
                    f"milliseconds. Parser message: {error}"
                ),
            )
        assert value is not None  # narrowed by the None check above
        return value

    raise ApiError(
        "INVALID_TIMESTAMP",
        "The timestamp must be a number or an ISO-8601 string.",
        field="timestamp",
        expected="epoch milliseconds as an integer or an ISO-8601 string",
        received=describe(raw),
        hint="Send the timestamp as epoch milliseconds, for example 1700000000000.",
    )


def validate_tags(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the validated tags of the payload.

    Tag values must be JSON scalars so the stored JSON document stays flat and
    can be filtered without a JSON path engine.
    """
    if "tags" not in payload or payload["tags"] is None:
        return {}

    raw = payload["tags"]
    if not isinstance(raw, dict):
        raise ApiError(
            "INVALID_TAGS",
            "The tags must be a JSON object of key/value pairs.",
            field="tags",
            expected="an object mapping tag names to scalar values",
            received=describe(raw),
            hint='Send tags as {"host": "web-1", "region": "us"}.',
        )

    if len(raw) > MAX_TAGS:
        raise ApiError(
            "TOO_MANY_TAGS",
            f"A data point accepts at most {MAX_TAGS} tags.",
            field="tags",
            expected=f"at most {MAX_TAGS} tags",
            received=len(raw),
            hint=f"Remove the extra tags; the limit is {MAX_TAGS} tags per data point.",
        )

    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ApiError(
                "INVALID_TAGS",
                "Tag names must be non empty strings.",
                field=f"tags.{key}",
                expected=f"a non empty tag name of at most {MAX_TAG_KEY_LENGTH} characters",
                received=describe(key),
                hint="Rename the tag to a short, non empty string.",
            )
        if len(key) > MAX_TAG_KEY_LENGTH:
            raise ApiError(
                "INVALID_TAGS",
                f"Tag names must be at most {MAX_TAG_KEY_LENGTH} characters long.",
                field=f"tags.{key}",
                expected=f"a tag name of at most {MAX_TAG_KEY_LENGTH} characters",
                received=len(key),
                hint=f"Shorten the tag name; the limit is {MAX_TAG_KEY_LENGTH} characters.",
            )
        if isinstance(value, (dict, list, tuple)):
            raise ApiError(
                "INVALID_TAGS",
                "Tag values must be JSON scalars.",
                field=f"tags.{key}",
                expected="a string, a number or a boolean",
                received=describe(value),
                hint=(
                    "Flatten nested values into separate tags, for example "
                    "tags.host instead of tags.nested.host."
                ),
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ApiError(
                "INVALID_TAGS",
                "Tag values must be finite numbers.",
                field=f"tags.{key}",
                expected="a finite number",
                received=describe(value),
                hint="NaN and Infinity cannot be stored; send a finite value.",
            )
        clean[key] = value

    return clean


def validate_value(raw_value: Any, metric_type: str) -> tuple[float | None, str | None]:
    """Validate the value against the effective metric type.

    Returns the ``(value_num, value_str)`` pair to store.  Numeric types keep
    the number in ``value_num``, ``string`` keeps the text in ``value_str`` and
    ``bool`` keeps both representations so numeric and textual filters work.
    """
    if metric_type in NUMERIC_TYPES:
        # ``bool`` is a subclass of ``int`` in Python, so ``True`` would pass
        # an ``isinstance(value, int)`` check.  Booleans are rejected here on
        # purpose: a boolean is not a measurement.
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ApiError(
                "INVALID_VALUE",
                f"The value of a {metric_type} metric must be a number.",
                field="value",
                expected="a finite number",
                received=describe(raw_value),
                hint=f"Send the measurement as a JSON number, for example 42.",
            )

        number = float(raw_value)
        if not math.isfinite(number):
            raise ApiError(
                "NON_FINITE_VALUE",
                "The value of a numeric metric must be a finite number.",
                field="value",
                expected="a finite number",
                received=describe(raw_value),
                hint="NaN and Infinity cannot be stored; send a finite measurement.",
            )

        if metric_type == "timer" and number < 0:
            raise ApiError(
                "INVALID_VALUE",
                "The value of a timer metric must not be negative.",
                field="value",
                expected="a duration in milliseconds greater than or equal to 0",
                received=number,
                hint=(
                    "timer values are durations in milliseconds and cannot be negative"
                ),
            )

        return number, None

    if metric_type == "string":
        if not isinstance(raw_value, str):
            raise ApiError(
                "INVALID_VALUE",
                "The value of a string metric must be a string.",
                field="value",
                expected=f"a string of at most {MAX_STRING_VALUE_LENGTH} characters",
                received=describe(raw_value),
                hint='Send the value as a JSON string, for example "ok".',
            )
        if len(raw_value) > MAX_STRING_VALUE_LENGTH:
            raise ApiError(
                "INVALID_VALUE",
                f"The value of a string metric must be at most {MAX_STRING_VALUE_LENGTH} characters long.",
                field="value",
                expected=f"a string of at most {MAX_STRING_VALUE_LENGTH} characters",
                received=len(raw_value),
                hint=f"Shorten the value; it is stored in a {MAX_STRING_VALUE_LENGTH} character column.",
            )
        return None, raw_value

    if metric_type == "bool":
        if not isinstance(raw_value, bool):
            raise ApiError(
                "INVALID_VALUE",
                "The value of a bool metric must be a boolean.",
                field="value",
                expected="true or false",
                received=describe(raw_value),
                hint="Send the value as a JSON boolean, for example true.",
            )
        # Both representations are stored so a numeric filter and a textual
        # filter find the same point.
        return (1.0 if raw_value else 0.0), ("true" if raw_value else "false")

    raise ApiError(  # pragma: no cover - defensive, the type is validated first
        "INVALID_METRIC_TYPE",
        f"'{metric_type}' is not a known metric type.",
        field="type",
        expected=ALLOWED_TYPES_TEXT,
        received=metric_type,
        hint=f"Use one of the allowed types: {ALLOWED_TYPES_TEXT}.",
    )


def serialize_tags(tags: Mapping[str, Any]) -> str:
    """Serialize the tags into the JSON document stored in the column."""
    return json.dumps(dict(tags), ensure_ascii=False, allow_nan=False)
