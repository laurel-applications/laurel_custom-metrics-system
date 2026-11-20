"""Tests of the web UI.

The UI is exercised through the test client: the HTML pages are rendered and
the forms are submitted the way a browser would submit them.  Every failure
the user can cause in the browser must produce an explicit message naming the
field and the cause, so the assertions look for the code, the message and the
hint in the rendered HTML.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.extensions import db
from app.models import Dashboard, Metric, Widget

#: A timestamp inside the last hour, so the widget time ranges cover it.
NOW_MS = 1_700_000_000_000


def ingest(
    client: FlaskClient,
    name: str = "requests",
    metric_type: str = "counter",
    value: Any = 1,
    tags: dict[str, Any] | None = None,
    unit: str | None = None,
) -> Any:
    """Ingest one data point through the public API."""
    payload: dict[str, Any] = {"name": name, "type": metric_type, "value": value}
    if tags is not None:
        payload["tags"] = tags
    if unit is not None:
        payload["unit"] = unit
    response = client.post("/api/ingest", json=payload)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response


def create_dashboard(client: FlaskClient, name: str = "Operations") -> int:
    """Create a dashboard through the form and return its id."""
    response = client.post("/dashboards", data={"name": name})
    assert response.status_code == 303, response.get_data(as_text=True)
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def widget_form(**overrides: Any) -> dict[str, Any]:
    """Return the payload of the widget form, with the given overrides."""
    form = {
        "title": "Requests",
        "metric": "requests",
        "chart_type": "line",
        "agg": "sum",
        "bucket": "auto",
        "time_range": "24h",
        "group_by": "",
        "filters": "[]",
    }
    form.update(overrides)
    return form


def widgets_of(app: Flask, dashboard_id: int) -> list[Widget]:
    """Return the widgets of a dashboard."""
    with app.app_context():
        statement = (
            select(Widget).where(Widget.dashboard_id == dashboard_id).order_by(Widget.id)
        )
        return list(db.session.execute(statement).scalars())


def assert_error_block(html: str, code: str) -> None:
    """Assert the HTML shows the error code, its message and its hint."""
    assert code in html, html
    assert 'data-error-code="' in html
    assert "data-error-field=\"message\"" in html
    assert "data-error-field=\"hint\"" in html
    assert "data-error-field=\"expected\"" in html
    assert "data-error-field=\"received\"" in html


# --------------------------------------------------------------- dashboards


def test_dashboard_list_renders_empty_state(client: FlaskClient) -> None:
    """The list page renders and explains how to start."""
    response = client.get("/dashboards")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "There is no dashboard yet" in html
    assert "curl -X POST" in html
    assert "api/ingest" in html


def test_index_renders_the_dashboard_list(client: FlaskClient) -> None:
    """The root of the application shows the dashboard list."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboards" in response.get_data(as_text=True)


def test_create_dashboard_persists_and_redirects(
    app: Flask, client: FlaskClient
) -> None:
    """A valid name creates the dashboard and redirects to it."""
    response = client.post("/dashboards", data={"name": "Operations"})
    assert response.status_code == 303
    assert "/dashboards/" in response.headers["Location"]

    with app.app_context():
        dashboards = db.session.execute(select(Dashboard)).scalars().all()
        assert [dashboard.name for dashboard in dashboards] == ["Operations"]


def test_create_dashboard_with_invalid_name_renders_the_error(
    client: FlaskClient,
) -> None:
    """An invalid name re-renders the list with an explicit error block."""
    response = client.post("/dashboards", data={"name": "   "})
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert_error_block(html, "INVALID_NAME")
    assert "The name is required" in html


def test_dashboard_detail_renders_its_widgets(
    app: Flask, client: FlaskClient
) -> None:
    """The dashboard detail shows the widgets it holds."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="Requests")
    )
    assert response.status_code == 303

    detail = client.get(f"/dashboards/{dashboard_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Requests" in html
    assert "requests" in html
    assert "Add widget" in html
    assert "Delete dashboard" in html


def test_unknown_dashboard_renders_an_explicit_404(client: FlaskClient) -> None:
    """An unknown dashboard answers 404 with a visible message."""
    response = client.get("/dashboards/4242")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert_error_block(html, "DASHBOARD_NOT_FOUND")
    assert "There is no dashboard with the id 4242" in html
    assert "List the dashboards" in html


# ------------------------------------------------------------------ rename








def test_the_dashboard_page_offers_the_time_picker(client: FlaskClient) -> None:
    """The dashboard header carries the range picker of the whole dashboard."""
    dashboard_id = create_dashboard(client, name="Operations")

    response = client.get(f"/dashboards/{dashboard_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'data-role="time-picker"' in html
    assert 'data-role="time-picker-button"' in html
    assert 'data-role="time-picker-menu"' in html
    # The widest window the picker offers is one week.
    assert 'data-range="7d"' in html
    assert 'data-range="30d"' not in html


def test_the_widget_metadata_shows_the_range_in_use(client: FlaskClient) -> None:
    """The metadata line of a widget states the range that is displayed."""
    ingest(client)
    dashboard_id = create_dashboard(client, name="Operations")
    client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(title="Requests", time_range="30d"),
    )

    response = client.get(f"/dashboards/{dashboard_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # The range of the widget is inside a node the front end can replace with
    # the range the dashboard is showing.
    assert '<span data-role="range">30d</span>' in html


# ------------------------------------------------------------------ widgets


def test_create_widget_persists_filters_and_position(
    app: Flask, client: FlaskClient
) -> None:
    """A widget stores its filters and takes the next position."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(
            filters=json.dumps([{"field": "value", "op": ">", "value": 10}]),
        ),
    )
    assert response.status_code == 303
    assert f"/dashboards/{dashboard_id}" in response.headers["Location"]

    stored = widgets_of(app, dashboard_id)
    assert len(stored) == 1
    widget = stored[0]
    assert widget.title == "Requests"
    assert widget.chart_type == "line"
    assert widget.agg == "sum"
    assert widget.bucket_sec is None
    assert widget.time_range == "24h"
    assert widget.position == 0

    stored_config = json.loads(widget.filters)
    assert widget.group_by is None
    assert stored_config == [{"field": "value", "op": ">", "value": 10}]


def test_create_widget_stores_the_group_by_in_its_own_column(
    app: Flask, client: FlaskClient
) -> None:
    """The group by is a column of its own and the filters stay a bare array."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(
            group_by="host",
            filters=json.dumps([{"field": "tags.host", "op": "=", "value": "web-1"}]),
        ),
    )
    assert response.status_code == 303

    widget = widgets_of(app, dashboard_id)[0]
    assert widget.group_by == "host"
    # The filters column holds the bare array, exactly as the query API
    # expects it: no wrapper object, no group_by key.
    assert json.loads(widget.filters) == [
        {"field": "tags.host", "op": "=", "value": "web-1"}
    ]


def test_widget_with_group_by_round_trips_through_the_dashboard(
    app: Flask, client: FlaskClient
) -> None:
    """The group by survives the creation and shows up on the dashboard."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(title="Per host", group_by="host"),
    )

    detail = client.get(f"/dashboards/{dashboard_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)

    # The value is visible on the page...
    assert "by host" in html
    # ...and it reaches the front end, which sends it to the query API.
    assert '"group_by": "host"' in html

    stored = widgets_of(app, dashboard_id)[0]
    assert stored.group_by == "host"


def test_legacy_widget_document_still_exposes_its_group_by(
    app: Flask, client: FlaskClient
) -> None:
    """A widget written before the migration is still read correctly.

    The legacy document kept the group by inside the ``filters`` column; a
    database that was not migrated yet must keep working.
    """
    ingest(client)
    dashboard_id = create_dashboard(client)

    with app.app_context():
        db.session.add(
            Widget(
                dashboard_id=dashboard_id,
                metric_id=db.session.execute(
                    select(Metric).where(Metric.name == "requests")
                ).scalar_one().id,
                title="Legacy",
                chart_type="line",
                agg="sum",
                bucket_sec=None,
                time_range="24h",
                group_by=None,
                filters=json.dumps(
                    {
                        "group_by": "host",
                        "filters": [{"field": "value", "op": ">", "value": 1}],
                    }
                ),
                position=0,
                created_at_ms=NOW_MS,
            )
        )
        db.session.commit()

    detail = client.get(f"/dashboards/{dashboard_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "by host" in html
    assert '"group_by": "host"' in html


def test_second_widget_takes_the_next_position(
    app: Flask, client: FlaskClient
) -> None:
    """The position of a new widget follows the previous ones."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="First"))
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="Second"))

    stored = widgets_of(app, dashboard_id)
    assert [widget.position for widget in stored] == [0, 1]


def test_create_widget_with_invalid_aggregation_persists_nothing(
    app: Flask, client: FlaskClient
) -> None:
    """An aggregation the metric type does not allow is rejected."""
    ingest(client, name="status", metric_type="string", value="ok")
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(metric="status", agg="sum"),
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert_error_block(html, "INVALID_AGGREGATION_FOR_TYPE")
    assert "count" in html and "last" in html
    assert widgets_of(app, dashboard_id) == []


def test_create_widget_with_malformed_filters_persists_nothing(
    app: Flask, client: FlaskClient
) -> None:
    """A broken filters document is rejected with the parser message."""
    ingest(client)
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(filters="[{'field': 'value',}]"),
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert_error_block(html, "INVALID_FILTERS")
    assert "filters" in html
    assert widgets_of(app, dashboard_id) == []


def test_create_widget_with_unknown_metric_persists_nothing(
    app: Flask, client: FlaskClient
) -> None:
    """A widget cannot reference a metric that does not exist."""
    dashboard_id = create_dashboard(client)

    response = client.post(
        f"/dashboards/{dashboard_id}/widgets", data=widget_form(metric="ghost")
    )
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert_error_block(html, "METRIC_NOT_FOUND")
    assert widgets_of(app, dashboard_id) == []


def test_widget_edit_form_is_prefilled(client: FlaskClient) -> None:
    """The edit form shows the stored configuration."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(
        f"/dashboards/{dashboard_id}/widgets",
        data=widget_form(
            title="Latency",
            chart_type="bar",
            agg="avg",
            bucket="300",
            time_range="6h",
            group_by="host",
            filters=json.dumps([{"field": "tags.host", "op": "=", "value": "web-1"}]),
        ),
    )

    widget = widgets_of(client.application, dashboard_id)[0]
    response = client.get(f"/widgets/{widget.id}/edit")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="Latency"' in html
    assert 'value="300"' in html
    assert 'value="host" selected' in html


def test_update_widget_persists_the_new_configuration(
    app: Flask, client: FlaskClient
) -> None:
    """The edit form updates the widget."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="Before"))

    widget = widgets_of(app, dashboard_id)[0]
    response = client.post(
        f"/widgets/{widget.id}",
        data=widget_form(title="After", chart_type="stat", agg="max"),
    )
    assert response.status_code == 303

    stored = widgets_of(app, dashboard_id)[0]
    assert stored.title == "After"
    assert stored.chart_type == "stat"
    assert stored.agg == "max"


def test_delete_widget_removes_it(app: Flask, client: FlaskClient) -> None:
    """Deleting a widget removes it from the dashboard."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form())

    widget = widgets_of(app, dashboard_id)[0]
    response = client.post(f"/widgets/{widget.id}/delete")
    assert response.status_code == 303
    assert widgets_of(app, dashboard_id) == []


def test_delete_dashboard_removes_its_widgets(
    app: Flask, client: FlaskClient
) -> None:
    """Deleting a dashboard removes the widgets it holds."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form())

    response = client.post(f"/dashboards/{dashboard_id}/delete")
    assert response.status_code == 303
    assert "/dashboards" in response.headers["Location"]

    with app.app_context():
        assert db.session.execute(select(Dashboard)).scalars().all() == []
        assert db.session.execute(select(Widget)).scalars().all() == []


# ------------------------------------------------------------------- order


def test_reorder_persists_the_new_positions(
    app: Flask, client: FlaskClient
) -> None:
    """The drag & drop order is stored as the position of every widget."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="A"))
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="B"))

    stored = widgets_of(app, dashboard_id)
    ids = [widget.id for widget in stored]
    new_order = list(reversed(ids))

    response = client.post(
        f"/dashboards/{dashboard_id}/order", json={"widget_ids": new_order}
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["order"] == new_order

    positions = {widget.id: widget.position for widget in widgets_of(app, dashboard_id)}
    assert positions[new_order[0]] == 0
    assert positions[new_order[1]] == 1


def test_reorder_rejects_an_id_set_that_does_not_match(
    app: Flask, client: FlaskClient
) -> None:
    """An order that does not match the widgets of the dashboard is rejected."""
    ingest(client)
    dashboard_id = create_dashboard(client)
    client.post(f"/dashboards/{dashboard_id}/widgets", data=widget_form(title="A"))

    widget = widgets_of(app, dashboard_id)[0]
    response = client.post(
        f"/dashboards/{dashboard_id}/order", json={"widget_ids": [widget.id, 999]}
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"]["code"] == "INVALID_ORDER"
    assert payload["error"]["field"] == "widget_ids"
    assert payload["error"]["hint"]

    assert widgets_of(app, dashboard_id)[0].position == 0


# ----------------------------------------------------------------- metrics


def test_metrics_overview_lists_the_metrics(client: FlaskClient) -> None:
    """The metrics page shows the known metrics."""
    ingest(client, name="requests", unit="req")
    ingest(client, name="status", metric_type="string", value="ok")

    response = client.get("/metrics")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "requests" in html
    assert "counter" in html
    assert "req" in html
    assert "status" in html
    assert "Add widget" in html


def test_tags_endpoint_lists_the_tag_keys(client: FlaskClient) -> None:
    """The tag discovery endpoint answers with the keys and a sample."""
    ingest(client, tags={"host": "web-1", "region": "us"})
    ingest(client, tags={"host": "web-2", "region": "us"})

    response = client.get("/api/metrics/requests/tags")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["metric"] == "requests"
    assert payload["keys"] == ["host", "region"]
    assert sorted(payload["sample_values"]["host"]) == ["web-1", "web-2"]
    assert payload["sample_values"]["region"] == ["us"]


def test_tags_endpoint_of_an_unknown_metric(client: FlaskClient) -> None:
    """An unknown metric answers 404 with the shared error code."""
    response = client.get("/api/metrics/ghost/tags")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["error"]["code"] == "METRIC_NOT_FOUND"


# ----------------------------------------------------------- error contract


@pytest.mark.parametrize(
    "path",
    [
        "/dashboards/4242",
        "/widgets/4242/edit",
        "/metrics/nope",
        "/there-is-no-such-page",
    ],
)
def test_every_html_error_page_shows_code_message_and_hint(
    client: FlaskClient, path: str
) -> None:
    """Every HTML error page shows the code, the message and the hint."""
    response = client.get(path)
    assert response.status_code == 404
    html = response.get_data(as_text=True)

    assert 'class="error-code"' in html
    assert "data-error-field=\"message\"" in html
    assert "data-error-field=\"hint\"" in html
    assert "NOT_FOUND" in html or "DASHBOARD_NOT_FOUND" in html or "WIDGET_NOT_FOUND" in html
    # The hint must be visible, not hidden behind a tooltip.
    assert "href=" in html or "Back to the dashboards" in html


def test_the_api_prefix_answers_with_the_envelope(client: FlaskClient) -> None:
    """A 404 under the API prefix is JSON, even without the trailing slash.

    The prefix is what tells the error contract that the caller is an API
    client, so it must not depend on the exact spelling of the path.
    """
    for path in ("/api", "/api/does-not-exist"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.content_type.startswith("application/json"), path

        payload = response.get_json()
        assert payload["error"]["code"] == "NOT_FOUND"
        assert payload["error"]["message"]
        assert payload["error"]["hint"]


def test_html_error_page_of_a_wrong_method_shows_the_contract(
    client: FlaskClient,
) -> None:
    """A wrong method produces an explicit page, not a bare Werkzeug answer."""
    # The widget form is only served with GET, so a POST is a wrong method.
    response = client.post("/dashboards/1/widgets/new")
    assert response.status_code == 405
    html = response.get_data(as_text=True)

    assert "METHOD_NOT_ALLOWED" in html
    assert "data-error-field=\"message\"" in html
    assert "data-error-field=\"hint\"" in html
    # The protocol level hint of Werkzeug survives the translation.
    assert response.headers.get("Allow")


def test_catch_all_handler_answers_the_api_with_the_envelope(
    app: Flask, client: FlaskClient
) -> None:
    """An unexpected crash answers with the canonical envelope, no traceback."""

    @app.get("/api/boom")
    def _boom() -> Any:
        raise RuntimeError("the database is on fire")

    response = client.get("/api/boom")
    assert response.status_code == 500

    payload = response.get_json()
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"]
    assert payload["error"]["hint"]

    body = response.get_data(as_text=True)
    assert "the database is on fire" not in body
    assert "Traceback" not in body


def test_catch_all_handler_answers_the_ui_with_a_rendered_page(
    app: Flask, client: FlaskClient
) -> None:
    """An unexpected crash on an HTML route renders the error page."""

    @app.get("/boom")
    def _boom() -> Any:
        raise RuntimeError("the database is on fire")

    response = client.get("/boom")
    assert response.status_code == 500
    html = response.get_data(as_text=True)

    assert "INTERNAL_ERROR" in html
    assert "data-error-field=\"message\"" in html
    assert "data-error-field=\"hint\"" in html
    assert "the database is on fire" not in html
    assert "Traceback" not in html
