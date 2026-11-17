"""HTML routes of the web UI.

The UI is server rendered: Jinja2 templates produce the pages and a small
amount of vanilla JavaScript turns the stored widget configuration into
charts.  Every failure the user can cause in the browser produces an
explicit message naming the field and the cause -- either by re-rendering the
page with an error block, or by letting the shared error handlers answer
with the standard error page.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import func, select

from ..errors import ApiError
from ..extensions import db
from ..models import Dashboard, DataPoint, Metric, Widget
from . import validation

#: The blueprint owns every HTML route of the application.
bp = Blueprint("web", __name__)


def _now_ms() -> int:
    """Return the current UTC time as epoch milliseconds."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _error_payload(error: ApiError) -> dict[str, Any]:
    """Return the error object of an envelope, ready for the error block."""
    return error.to_envelope()["error"]


def _metric_rows() -> list[dict[str, Any]]:
    """Return the known metrics with their data point count."""
    session = db.session

    metrics = session.execute(select(Metric).order_by(Metric.name)).scalars().all()
    counts = dict(
        session.execute(
            select(DataPoint.metric_id, func.count()).group_by(DataPoint.metric_id)
        ).all()
    )

    return [
        {
            "id": metric.id,
            "name": metric.name,
            "type": metric.type,
            "unit": metric.unit,
            "created_at_ms": metric.created_at_ms,
            "data_point_count": int(counts.get(metric.id, 0)),
        }
        for metric in metrics
    ]


def _dashboard_rows() -> list[dict[str, Any]]:
    """Return the dashboards with their widget count and last update."""
    session = db.session

    dashboards = session.execute(
        select(Dashboard).order_by(Dashboard.created_at_ms, Dashboard.id)
    ).scalars().all()

    counts = dict(
        session.execute(
            select(Widget.dashboard_id, func.count()).group_by(Widget.dashboard_id)
        ).all()
    )
    last_updates = dict(
        session.execute(
            select(Widget.dashboard_id, func.max(Widget.created_at_ms)).group_by(
                Widget.dashboard_id
            )
        ).all()
    )

    return [
        {
            "id": dashboard.id,
            "name": dashboard.name,
            "created_at_ms": dashboard.created_at_ms,
            "widget_count": int(counts.get(dashboard.id, 0)),
            "last_widget_ms": last_updates.get(dashboard.id),
        }
        for dashboard in dashboards
    ]


def _widget_rows(dashboard_id: int) -> list[dict[str, Any]]:
    """Return the widgets of a dashboard, ordered by their position."""
    session = db.session

    statement = (
        select(Widget, Metric)
        .join(Metric, Metric.id == Widget.metric_id)
        .where(Widget.dashboard_id == dashboard_id)
        .order_by(Widget.position, Widget.id)
    )
    rows = session.execute(statement).all()

    widgets: list[dict[str, Any]] = []
    for widget, metric in rows:
        config = validation.read_widget_config(widget.filters)
        widgets.append(
            {
                "id": widget.id,
                "title": widget.title,
                "chart_type": widget.chart_type,
                "agg": widget.agg,
                "bucket": widget.bucket_sec if widget.bucket_sec is not None else "auto",
                "time_range": widget.time_range,
                "group_by": config["group_by"],
                "filters": json.dumps(config["filters"], ensure_ascii=False),
                "position": widget.position,
                "created_at_ms": widget.created_at_ms,
                "metric_id": metric.id,
                "metric_name": metric.name,
                "metric_type": metric.type,
                "unit": metric.unit,
                # The whole configuration of the widget, ready to be handed to
                # the front end as JSON.
                "config": {
                    "id": widget.id,
                    "metric": metric.name,
                    "metric_type": metric.type,
                    "chart_type": widget.chart_type,
                    "agg": widget.agg,
                    "bucket": widget.bucket_sec if widget.bucket_sec is not None else "auto",
                    "time_range": widget.time_range,
                    "group_by": config["group_by"],
                    "filters": config["filters"],
                    "unit": metric.unit,
                },
            }
        )
    return widgets


def _get_dashboard(dashboard_id: int) -> Dashboard:
    """Return the dashboard or raise an explicit 404."""
    dashboard = db.session.get(Dashboard, dashboard_id)
    if dashboard is None:
        raise ApiError(
            "DASHBOARD_NOT_FOUND",
            f"There is no dashboard with the id {dashboard_id}.",
            field="dashboard_id",
            expected="the id of an existing dashboard",
            received=dashboard_id,
            hint="List the dashboards on /dashboards and open one of them.",
            http_status=404,
        )
    return dashboard


def _get_widget(widget_id: int) -> Widget:
    """Return the widget or raise an explicit 404."""
    widget = db.session.get(Widget, widget_id)
    if widget is None:
        raise ApiError(
            "WIDGET_NOT_FOUND",
            f"There is no widget with the id {widget_id}.",
            field="widget_id",
            expected="the id of an existing widget",
            received=widget_id,
            hint="Open the dashboard that holds the widget and edit it from there.",
            http_status=404,
        )
    return widget


def _get_metric(name: str | None) -> Metric:
    """Return the metric with that name or raise an explicit 404."""
    if not name or not name.strip():
        raise ApiError(
            "METRIC_NOT_FOUND",
            "The name of the metric is required.",
            field="metric",
            expected="the name of an existing metric",
            received=None,
            hint="Pick a metric from the list; a metric is created on its first ingestion.",
            http_status=404,
        )

    statement = select(Metric).where(Metric.name == name.strip())
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


def _next_position(dashboard_id: int) -> int:
    """Return the position of the next widget of a dashboard."""
    statement = select(func.max(Widget.position)).where(Widget.dashboard_id == dashboard_id)
    current = db.session.execute(statement).scalar_one_or_none()
    return 0 if current is None else int(current) + 1


def _render_dashboards(error: Mapping[str, Any] | None = None, status: int = 200) -> Any:
    """Render the dashboard list, optionally with an error block."""
    return (
        render_template(
            "dashboards.html",
            dashboards=_dashboard_rows(),
            metrics=_metric_rows(),
            error=error,
        ),
        status,
    )


def _render_widget_form(
    dashboard: Dashboard,
    *,
    widget: Widget | None = None,
    values: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    status: int = 200,
) -> Any:
    """Render the widget form (creation or edition)."""
    metrics = _metric_rows()
    metric_types = {metric["name"]: metric["type"] for metric in metrics}

    form_values: dict[str, Any] = {
        "title": "",
        "metric": "",
        "chart_type": "line",
        "agg": "avg",
        "bucket": "auto",
        "time_range": "24h",
        "group_by": "",
        "filters": "[]",
    }

    if widget is not None:
        metric = db.session.get(Metric, widget.metric_id)
        config = validation.read_widget_config(widget.filters)
        form_values.update(
            {
                "title": widget.title,
                "metric": metric.name if metric is not None else "",
                "chart_type": widget.chart_type,
                "agg": widget.agg,
                "bucket": widget.bucket_sec if widget.bucket_sec is not None else "auto",
                "time_range": widget.time_range,
                "group_by": config["group_by"] or "",
                "filters": json.dumps(config["filters"], ensure_ascii=False),
            }
        )

    if values:
        form_values.update({key: value for key, value in values.items() if key in form_values})

    # The filters are parsed here, because the template has to render one row
    # per filter and Jinja has no JSON decoder of its own.
    try:
        filter_rows = json.loads(form_values["filters"])
    except (TypeError, json.JSONDecodeError):
        filter_rows = []
    if not isinstance(filter_rows, list):
        filter_rows = []

    return (
        render_template(
            "metric_form.html",
            dashboard=dashboard,
            widget=widget,
            metrics=metrics,
            metric_types=metric_types,
            chart_types=validation.CHART_TYPES,
            time_ranges=validation.TIME_RANGES,
            values=form_values,
            filter_rows=filter_rows,
            error=error,
        ),
        status,
    )


def _widget_form_values() -> dict[str, Any]:
    """Return the raw values submitted by the widget form."""
    return {
        "title": request.form.get("title", ""),
        "metric": request.form.get("metric", ""),
        "chart_type": request.form.get("chart_type", ""),
        "agg": request.form.get("agg", ""),
        "bucket": request.form.get("bucket", ""),
        "time_range": request.form.get("time_range", ""),
        "group_by": request.form.get("group_by", ""),
        "filters": request.form.get("filters", ""),
    }


def _parse_widget_form(form: Mapping[str, Any], metric: Metric) -> dict[str, Any]:
    """Validate the submitted widget form against the metric.

    Every parser used here is the one the query API uses, so the widget can
    only store a configuration the query API would accept.
    """
    title = validation.validate_name(form.get("title"), field="title")
    chart_type = validation.validate_chart_type(form.get("chart_type"))
    agg = validation.validate_aggregation(form.get("agg"), metric.type)
    bucket_sec = validation.validate_bucket(form.get("bucket"))
    time_range = validation.validate_time_range(form.get("time_range"))
    group_by = validation.validate_group_by(form.get("group_by"))
    filters_json, _ = validation.validate_filters(form.get("filters"), metric.type)

    return {
        "title": title,
        "chart_type": chart_type,
        "agg": agg,
        "bucket_sec": bucket_sec,
        "time_range": time_range,
        "group_by": group_by,
        "filters": validation.serialize_widget_config(group_by, filters_json),
    }


@bp.app_template_filter("ts")
def _format_ts(value: Any) -> str:
    """Format an epoch millisecond timestamp for the UI."""
    if value is None:
        return "-"
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):  # pragma: no cover - defensive
        return "-"
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


@bp.app_template_filter("jvalue")
def _json_value(value: Any) -> str:
    """Render a value of the error envelope as readable text.

    Strings are shown as they are; anything else (a list of valid
    aggregations, a dictionary of expected values) is rendered as JSON so the
    page stays readable.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


@bp.get("/")
def index() -> Any:
    """Show the dashboard list at the root of the application."""
    return _render_dashboards()


@bp.get("/dashboards")
def dashboard_list() -> Any:
    """List the dashboards."""
    return _render_dashboards()


@bp.post("/dashboards")
def dashboard_create() -> Any:
    """Create a dashboard from the form of the list page."""
    try:
        name = validation.validate_name(request.form.get("name"))
    except ApiError as error:
        return _render_dashboards(error=_error_payload(error), status=error.http_status)

    dashboard = Dashboard(name=name, created_at_ms=_now_ms())
    db.session.add(dashboard)
    db.session.commit()

    return redirect(url_for("web.dashboard_detail", dashboard_id=dashboard.id), code=303)


@bp.get("/dashboards/<int:dashboard_id>")
def dashboard_detail(dashboard_id: int) -> Any:
    """Show a dashboard with its widgets."""
    dashboard = _get_dashboard(dashboard_id)
    return render_template(
        "dashboard.html",
        dashboard=dashboard,
        widgets=_widget_rows(dashboard_id),
        metrics=_metric_rows(),
        error=None,
    )


@bp.get("/dashboards/<int:dashboard_id>/widgets/new")
def widget_form_new(dashboard_id: int) -> Any:
    """Show the widget creation form."""
    dashboard = _get_dashboard(dashboard_id)
    values = {"metric": request.args.get("metric", "")}
    return _render_widget_form(dashboard, values=values)


@bp.post("/dashboards/<int:dashboard_id>/widgets")
def widget_create(dashboard_id: int) -> Any:
    """Create a widget from the form."""
    dashboard = _get_dashboard(dashboard_id)
    form = _widget_form_values()

    try:
        metric = _get_metric(form.get("metric"))
        parsed = _parse_widget_form(form, metric)
    except ApiError as error:
        return _render_widget_form(
            dashboard,
            values=form,
            error=_error_payload(error),
            status=error.http_status,
        )

    widget = Widget(
        dashboard_id=dashboard.id,
        metric_id=metric.id,
        title=parsed["title"],
        chart_type=parsed["chart_type"],
        agg=parsed["agg"],
        bucket_sec=parsed["bucket_sec"],
        time_range=parsed["time_range"],
        filters=parsed["filters"],
        position=_next_position(dashboard.id),
        created_at_ms=_now_ms(),
    )
    db.session.add(widget)
    db.session.commit()

    return redirect(url_for("web.dashboard_detail", dashboard_id=dashboard.id), code=303)


@bp.get("/widgets/<int:widget_id>/edit")
def widget_edit_form(widget_id: int) -> Any:
    """Show the widget edition form, pre-filled."""
    widget = _get_widget(widget_id)
    dashboard = _get_dashboard(widget.dashboard_id)
    return _render_widget_form(dashboard, widget=widget)


@bp.post("/widgets/<int:widget_id>")
def widget_update(widget_id: int) -> Any:
    """Update a widget from the form."""
    widget = _get_widget(widget_id)
    dashboard = _get_dashboard(widget.dashboard_id)
    form = _widget_form_values()

    try:
        metric = _get_metric(form.get("metric"))
        parsed = _parse_widget_form(form, metric)
    except ApiError as error:
        return _render_widget_form(
            dashboard,
            widget=widget,
            values=form,
            error=_error_payload(error),
            status=error.http_status,
        )

    widget.title = parsed["title"]
    widget.metric_id = metric.id
    widget.chart_type = parsed["chart_type"]
    widget.agg = parsed["agg"]
    widget.bucket_sec = parsed["bucket_sec"]
    widget.time_range = parsed["time_range"]
    widget.filters = parsed["filters"]
    db.session.commit()

    return redirect(url_for("web.dashboard_detail", dashboard_id=dashboard.id), code=303)


@bp.post("/widgets/<int:widget_id>/delete")
def widget_delete(widget_id: int) -> Any:
    """Delete a widget."""
    widget = _get_widget(widget_id)
    dashboard_id = widget.dashboard_id
    db.session.delete(widget)
    db.session.commit()

    return redirect(url_for("web.dashboard_detail", dashboard_id=dashboard_id), code=303)


@bp.post("/dashboards/<int:dashboard_id>/delete")
def dashboard_delete(dashboard_id: int) -> Any:
    """Delete a dashboard and every widget it holds."""
    dashboard = _get_dashboard(dashboard_id)

    widgets = db.session.execute(
        select(Widget).where(Widget.dashboard_id == dashboard.id)
    ).scalars().all()
    for widget in widgets:
        db.session.delete(widget)
    # The widgets are deleted first: the foreign key of the widget table
    # forbids deleting a dashboard that still holds widgets.
    db.session.flush()
    db.session.delete(dashboard)
    db.session.commit()

    return redirect(url_for("web.dashboard_list"), code=303)


@bp.post("/dashboards/<int:dashboard_id>/order")
def dashboard_order(dashboard_id: int) -> Any:
    """Persist a new widget order.

    The drag & drop script posts the ids in their new visual order.  The set of
    ids must match exactly the widgets of the dashboard, otherwise the order
    would silently drop a widget.
    """
    dashboard = _get_dashboard(dashboard_id)

    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(cache=True, as_text=True)
        try:
            payload = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError as exc:
            raise ApiError(
                "INVALID_JSON",
                "The request body is not valid JSON.",
                field=None,
                expected='{"widget_ids": [3, 1, 2]}',
                received=exc.msg,
                hint=(
                    f"{exc.msg} (line {exc.lineno}, column {exc.colno}); send the "
                    'new order as {"widget_ids": [3, 1, 2]}'
                ),
            ) from exc

    if not isinstance(payload, Mapping) or "widget_ids" not in payload:
        raise ApiError(
            "INVALID_ORDER",
            "The request body must carry the new order in 'widget_ids'.",
            field="widget_ids",
            expected="a JSON array with the id of every widget of the dashboard",
            received=payload,
            hint='Send the new order as {"widget_ids": [3, 1, 2]}.',
        )

    raw_ids = payload["widget_ids"]
    if not isinstance(raw_ids, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in raw_ids
    ):
        raise ApiError(
            "INVALID_ORDER",
            "The new order must be an array of widget ids.",
            field="widget_ids",
            expected="an array of integers",
            received=raw_ids,
            hint='Send the new order as {"widget_ids": [3, 1, 2]}.',
        )

    existing = {
        int(row)
        for row in db.session.execute(
            select(Widget.id).where(Widget.dashboard_id == dashboard.id)
        ).scalars()
    }
    received = set(raw_ids)

    if len(raw_ids) != len(existing) or received != existing:
        raise ApiError(
            "INVALID_ORDER",
            "The new order does not match the widgets of the dashboard.",
            field="widget_ids",
            expected=sorted(existing),
            received=sorted(received),
            hint=(
                "Send exactly one id per widget of this dashboard; reload the "
                "dashboard and try again."
            ),
        )

    widgets = {
        int(widget.id): widget
        for widget in db.session.execute(
            select(Widget).where(Widget.dashboard_id == dashboard.id)
        ).scalars()
    }
    for position, widget_id in enumerate(raw_ids):
        widgets[widget_id].position = position
    db.session.commit()

    return {"status": "ok", "order": [int(widget_id) for widget_id in raw_ids]}


@bp.get("/metrics")
def metrics_overview() -> Any:
    """List the known metrics with their type, unit and data point count."""
    dashboards = _dashboard_rows()
    return render_template(
        "metrics.html",
        metrics=_metric_rows(),
        dashboards=dashboards,
        error=None,
    )
