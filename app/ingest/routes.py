"""HTTP routes of the ingestion API.

``POST /api/ingest`` accepts one metric point per request and
``GET /api/metrics`` lists the known metrics so the UI can populate its
metric picker.
"""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from ..errors import ApiError
from ..extensions import db
from ..models import DataPoint, Metric
from . import validation

#: The blueprint owns every ``/api`` route of the ingestion feature.
bp = Blueprint("ingest", __name__, url_prefix="/api")

#: Content types that are accepted as a JSON body.
JSON_CONTENT_TYPES = ("application/json", "text/json")


def _parse_body() -> dict[str, Any]:
    """Return the JSON object of the request body.

    Every failure mode gets its own code: a body that is not JSON, a body
    that is not an object and a body sent with an unsupported content type.
    """
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
            expected="a JSON object with at least a name and a value",
            received=None,
            hint="send a single JSON object per request",
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
                f"{exc.msg} (line {exc.lineno}, column {exc.colno}); "
                "send a single JSON object per request"
            ),
        ) from exc

    if not isinstance(payload, dict):
        raise ApiError(
            "INVALID_BODY",
            "The request body must be a JSON object.",
            field=None,
            expected="a JSON object",
            received=validation.describe(payload),
            hint="send a single JSON object per request",
        )

    return payload


def _select_metric(session: Any, name: str, for_update: bool = False) -> Metric | None:
    """Return the metric with that name, or ``None`` when it does not exist.

    ``for_update`` turns the read into a locking read.  That matters on
    MariaDB, whose default REPEATABLE READ isolation would otherwise hide a
    row committed by a concurrent transaction that just won the race to
    create the same metric.  SQLite has no ``FOR UPDATE`` clause and the
    dialect simply ignores it.
    """
    statement = select(Metric).where(Metric.name == name)
    if for_update:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def _type_mismatch(existing_type: str, received_type: str) -> ApiError:
    """Return the error raised when the declared type contradicts the metric."""
    return ApiError(
        "METRIC_TYPE_MISMATCH",
        (
            f"The metric already exists with the type '{existing_type}', but the "
            f"payload declares the type '{received_type}'."
        ),
        field="type",
        expected=existing_type,
        received=received_type,
        hint=(
            "The type of a metric is fixed when it is created; send the existing "
            f"type '{existing_type}' or omit the 'type' field to inherit it."
        ),
        http_status=409,
    )


def _resolve_metric_type(
    session: Any, name: str, declared_type: str | None
) -> tuple[Metric | None, str]:
    """Return the existing metric (or ``None``) and the effective metric type.

    The lookup is read only on purpose: the value is validated against the
    effective type before anything is written, so a rejected payload never
    leaves a half created metric behind.
    """
    metric = _select_metric(session, name)
    if metric is not None:
        if declared_type is not None and declared_type != metric.type:
            raise _type_mismatch(metric.type, declared_type)
        return metric, metric.type

    if declared_type is None:
        raise ApiError(
            "MISSING_TYPE",
            f"The metric '{name}' does not exist yet, so its type must be declared.",
            field="type",
            expected=validation.ALLOWED_TYPES_TEXT,
            received=None,
            hint=(
                "the metric does not exist yet; declare its type (counter, gauge, "
                "timer, string or bool) on the first ingestion"
            ),
        )

    return None, declared_type


def _create_metric(
    session: Any,
    name: str,
    declared_type: str,
    unit: str | None,
    created_at_ms: int,
    attempt: int = 0,
) -> Metric:
    """Create the metric row and return it.

    The creation is race safe: the unique constraint on the name turns a
    concurrent creation into a clean conflict that is resolved by re-reading
    the winning row.  The insert is not wrapped in a savepoint on purpose,
    because SQLite commits the transaction when the outermost savepoint is
    released, which would break the atomicity between the metric and its data
    point.  Instead the whole transaction is rolled back and the row is read
    again on a fresh snapshot.
    """
    candidate = Metric(
        name=name,
        type=declared_type,
        unit=unit,
        created_at_ms=created_at_ms,
    )
    session.add(candidate)
    try:
        session.flush()
    except IntegrityError:
        # Another transaction created the same metric first.
        session.rollback()
        existing = _select_metric(session, name, for_update=True)
        if existing is None:
            # The winning transaction rolled back as well, so the name is free
            # again.  The retry is bounded to keep a pathological race from
            # looping forever.
            return _retry_metric_creation(
                session, name, declared_type, unit, created_at_ms, attempt
            )
        if existing.type != declared_type:
            raise _type_mismatch(existing.type, declared_type) from None
        return existing
    except OperationalError as error:
        # SQLite refuses to upgrade a read transaction into a write
        # transaction when another connection committed in between
        # (SQLITE_BUSY_SNAPSHOT).  Rolling back and retrying resolves the
        # conflict, because the re-read then sees the committed row.
        session.rollback()
        existing = _select_metric(session, name, for_update=True)
        if existing is not None:
            if existing.type != declared_type:
                raise _type_mismatch(existing.type, declared_type) from None
            return existing
        if attempt >= 3:
            raise error
        return _create_metric(
            session, name, declared_type, unit, created_at_ms, attempt + 1
        )

    return candidate


def _retry_metric_creation(
    session: Any,
    name: str,
    declared_type: str,
    unit: str | None,
    created_at_ms: int,
    attempt: int,
) -> Metric:
    """Retry the creation of a metric whose name was freed by a rollback."""
    if attempt >= 3:  # pragma: no cover - defensive
        raise ApiError(
            "METRIC_CONFLICT",
            f"The metric '{name}' could not be created because of a concurrent write.",
            field="name",
            expected=declared_type,
            received=name,
            hint="Retry the ingestion in a moment.",
            http_status=409,
        )
    return _create_metric(session, name, declared_type, unit, created_at_ms, attempt + 1)


def _numeric(value: float | None) -> Any:
    """Return a number that keeps its natural JSON representation."""
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


def _data_point_value(metric_type: str, value_num: float | None, value_str: str | None) -> Any:
    """Return the value of a data point as it was submitted."""
    if metric_type == "bool":
        return bool(value_num)
    if metric_type == "string":
        return value_str
    return _numeric(value_num)


@bp.post("/ingest")
def ingest() -> Any:
    """Ingest one metric point.

    The metric is created on the first ingestion and its type is fixed from
    that moment on.
    """
    payload = _parse_body()

    name = validation.validate_name(payload)
    declared_type = validation.validate_type(payload)
    unit = validation.validate_unit(payload)
    ts_utc_ms = validation.validate_timestamp(payload)
    tags = validation.validate_tags(payload)

    session = db.session
    received_at_ms = validation.now_ms()

    # The effective type is the declared one for a new metric and the stored
    # one for an existing metric, so the value is always checked against the
    # type the point will really be stored with.
    metric, effective_type = _resolve_metric_type(session, name, declared_type)

    if "value" not in payload:
        raise ApiError(
            "INVALID_VALUE",
            "The value of the data point is required.",
            field="value",
            expected=f"a value of the type '{effective_type}'",
            received=None,
            hint='Send the value in the payload, for example {"value": 42}.',
        )

    value_num, value_str = validation.validate_value(payload["value"], effective_type)

    if metric is None:
        metric = _create_metric(session, name, declared_type, unit, received_at_ms)

    data_point = DataPoint(
        metric_id=metric.id,
        ts_utc_ms=ts_utc_ms,
        value_num=value_num,
        value_str=value_str,
        tags=validation.serialize_tags(tags),
        received_at_ms=received_at_ms,
    )
    session.add(data_point)
    session.commit()

    return (
        jsonify(
            {
                "status": "created",
                "metric": {
                    "id": metric.id,
                    "name": metric.name,
                    "type": metric.type,
                    "unit": metric.unit,
                },
                "data_point": {
                    "id": data_point.id,
                    "ts_utc_ms": data_point.ts_utc_ms,
                    "value": _data_point_value(metric.type, value_num, value_str),
                    "tags": tags,
                },
            }
        ),
        201,
    )


@bp.get("/metrics")
def list_metrics() -> Any:
    """List the known metrics with their data point count."""
    session = db.session

    statement = select(Metric).order_by(Metric.name)
    name_filter = request.args.get("name")
    if name_filter:
        statement = statement.where(Metric.name == name_filter)

    metrics = session.execute(statement).scalars().all()

    counts = dict(
        session.execute(
            select(DataPoint.metric_id, func.count()).group_by(DataPoint.metric_id)
        ).all()
    )

    return jsonify(
        {
            "metrics": [
                {
                    "id": metric.id,
                    "name": metric.name,
                    "type": metric.type,
                    "unit": metric.unit,
                    "created_at_ms": metric.created_at_ms,
                    "data_point_count": int(counts.get(metric.id, 0)),
                }
                for metric in metrics
            ],
            "count": len(metrics),
        }
    )
