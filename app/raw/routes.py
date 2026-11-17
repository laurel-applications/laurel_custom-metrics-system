"""Raw data API routes for the Laurel Metrics deployment.

All endpoints return JSON via the shared error envelope and live under
``/api/raw`` (blueprint registered with ``url_prefix="/api"``).
"""

from __future__ import annotations

import json
from flask import Blueprint, request, jsonify, abort
from ..extensions import db
from ..models import DataPoint, Metric
from ..errors import ApiError
from ..raw.service import list_points, count_points, latest_id, wait_for_points, to_csv

bp = Blueprint("raw", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Helper: resolve metric and abort with 404 if missing
# ---------------------------------------------------------------------------

def _get_metric(name: str) -> Metric:
    metric = db.session.query(Metric).filter(Metric.name == name).one_or_none()
    if metric is None:
        from ..errors import ApiError as _ApiError
        raise _ApiError(
            "METRIC_NOT_FOUND",
            f"Metric {name!r} not found.",
            field="metric",
            expected=f"existing metric name",
            received=None,
            hint=f"Send a point for {name!r} first, or check the spelling.",
        )
    return metric


# ---------------------------------------------------------------------------
# 1. GET /api/raw  ── paginated points
# ---------------------------------------------------------------------------

@bp.get("/raw")
def raw_list() -> Any:
    metric_name = request.args.get("metric")
    if not metric_name:
        raise ApiError(
            "MISSING_PARAMETER",
            "The metric name is required.",
            field="metric",
            expected="a metric name",
            received=None,
            hint="Send ``?metric=temperature``.",
        )

    metric = _get_metric(metric_name)

    # ---- pagination params ----
    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        raise ApiError(
            "INVALID_PAGE_SIZE",
            "The limit must be an integer.",
            field="limit",
            expected="an integer between 1 and 500",
            received=None,
            hint="Pass ``?limit=50``.",
        )
    if limit < 1 or limit > 500:
        raise ApiError(
            "INVALID_PAGE_SIZE",
            "The limit must be between 1 and 500.",
            field="limit",
            expected="1..500",
            received=limit,
            hint="Pass ``?limit=50``.",
        )

    try:
        offset = int(request.args.get("offset") or 0)
    except ValueError:
        raise ApiError(
            "INVALID_PAGE",
            "The offset must be a non‑negative integer.",
            field="offset",
            expected="a non‑negative integer",
            received=None,
            hint="Pass ``?offset=0``.",
        )
    if offset < 0:
        raise ApiError(
            "INVALID_PAGE",
            "The offset must be a non‑negative integer.",
            field="offset",
            expected=">= 0",
            received=offset,
            hint="Pass ``?offset=0``.",
        )

    # ---- order ----------
    order = request.args.get("order", "desc").lower()
    if order not in ("asc", "desc"):
        raise ApiError(
            "INVALID_ORDER",
            "The order must be ``asc`` or ``desc``.",
            field="order",
            expected="``asc`` or ``desc``",
            received=order,
            hint="Pass ``?order=asc`` or ``?order=desc``.",
        )

    # ---- range ----
    from_ms: int | None = None
    to_ms: int | None = None
    raw_from = request.args.get("from")
    raw_to = request.args.get("to")
    if raw_from:
        try:
            from_ms = int(raw_from)
        except ValueError:
            try:
                from_ms = int(round(datetime.fromisoformat(raw_from.replace("Z", "")).timestamp() * 1000))
            except Exception:
                raise ApiError(
                    "INVALID_TIMESTAMP",
                    "The ``from`` value could not be parsed as epoch ms or ISO‑8601.",
                    field="from",
                    expected="epoch ms or ISO‑8601 string",
                    received=raw_from,
                    hint="Pass epoch ms (e.g. ``1794891881993``) or ISO‑8601 (``2023-10-01T12:00:00Z``).",
                )
    if raw_to:
        try:
            to_ms = int(raw_to)
        except ValueError:
            try:
                to_ms = int(round(datetime.fromisoformat(raw_to.replace("Z", "")).timestamp() * 1000))
            except Exception:
                raise ApiError(
                    "INVALID_TIMESTAMP",
                    "The ``to`` value could not be parsed as epoch ms or ISO‑8601.",
                    field="to",
                    expected="epoch ms or ISO‑8601 string",
                    received=raw_to,
                    hint="Pass epoch ms (e.g. ``1794891881993``) or ISO‑8601 (``2023-10-01T12:00:00Z``).",
                )

    # Clamp limit to 500 (already validated in the function, but here too)
    points, total = list_points(
        metric=metric,
        from_ms=from_ms,
        to_ms=to_ms,
        limit=limit,
        offset=offset,
        order=request.args.get("order", "desc"),
    )

    _last_id = latest_id(metric.id) if total else None
    return {
        "metric": {"id": metric.id, "name": metric.name, "type": metric.type, "unit": metric.unit},
        "from_ms": from_ms,
        "to_ms": to_ms,
        "order": request.args.get("order", "desc"),
        "total": total,
        "limit": limit,
        "offset": offset,
        "latest_id": _last_id,
        "points": [p.to_dict() for p in points],
    }


# ---------------------------------------------------------------------------
# 2. GET /api/raw/wait  ── long poll
# ---------------------------------------------------------------------------

@bp.get("/raw/wait")
def raw_wait() -> Any:
    metric_name = request.args.get("metric")
    if not metric_name:
        raise ApiError(
            "MISSING_PARAMETER",
            "The metric name is required.",
            field="metric",
            expected="a metric name",
            received=None,
            hint="Send ``?metric=temperature``.",
        )

    metric = _get_metric(metric_name)

    # since_id : monotonic id watermark
    since_id = request.args.get("since_id", type=int, default=0)

    # optional range (the caller may restrict the window)
    from_ms: int | None = None
    to_ms: int | None = None
    raw_from = request.args.get("from")
    raw_to = request.args.get("to")
    if raw_from:
        try:
            from_ms = int(raw_from)
        except ValueError:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The ``from`` value could not be parsed as epoch ms or ISO‑8601.",
                field="from",
                expected="epoch ms or ISO‑8601 string",
                received=raw_from,
                hint="Pass epoch ms or ISO‑8601.",
            )
    if raw_to:
        try:
            to_ms = int(raw_to)
        except ValueError:
            raise ApiError(
                "INVALID_TIMESTAMP",
                "The ``to`` value could not be parsed as epoch ms or ISO‑8601.",
                field="to",
                expected="epoch ms or ISO‑8601 string",
                received=raw_to,
                hint="Pass epoch ms or ISO‑8601.",
            )

    # timeout: default 2 s, max 10 s
    try:
        timeout = int(request.args.get("timeout") or 2)
    except ValueError:
        raise ApiError(
            "INVALID_TIMEOUT",
            "The timeout must be an integer.",
            field="timeout",
            expected="an integer between 1 and 10",
            received=None,
            hint="Pass ``?timeout=2``.",
        )
    if timeout < 1 or timeout > 10:
        raise ApiError(
            "INVALID_TIMEOUT",
            "The timeout must be between 1 and 10 seconds.",
            field="timeout",
            expected="1..10",
            received=timeout,
            hint="Pass ``?timeout=2``.",
        )

    points, waited_ms = wait_for_points(
        metric.id, since_id, from_ms, to_ms, timeout_s=timeout
    )

    return {
        "metric": {"id": metric.id, "name": metric.name, "type": metric.type, "unit": metric.unit},
        "latest_id": latest_id(metric.id),
        "waited_ms": waited_ms,
        "points": [p.to_dict() for p in points],
    }


# ---------------------------------------------------------------------------
# 3. GET /metrics/<metric_name>/raw  ── CSV download (handled in web routes)
# ---------------------------------------------------------------------------
# (The CSV endpoint lives in the ``web`` blueprint because it returns a file
#  download rather than JSON; see `app/web/routes.py`)


# ---------------------------------------------------------------------------
# Blueprint registration – do NOT register twice
# ---------------------------------------------------------------------------

# The Blueprint is already registered in ``app/__init__.py`` via
# ``BLUEPRINTS`` tuple. No further action is required here.