"""Tests of the ingestion API and of the error contract."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, insert, select

from app.extensions import db
from app.models import DataPoint, Metric

#: The exact key set every error envelope must carry.
ENVELOPE_KEYS = {"code", "message", "field", "expected", "received", "hint"}


def assert_envelope(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the response is an error envelope and return the error object."""
    assert response.status_code == status, response.get_data(as_text=True)
    payload = response.get_json()
    assert isinstance(payload, dict)
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == code
    # The contract promises an explicit message and an actionable hint.
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["hint"], str) and error["hint"]
    return error


def post_json(client: FlaskClient, payload: Any) -> Any:
    """Post a JSON payload to the ingestion endpoint."""
    return client.post(
        "/api/ingest",
        data=json.dumps(payload),
        content_type="application/json",
    )


def count_rows(model: Any) -> int:
    """Return the number of rows of ``model`` in the test database."""
    with db.session.get_bind().connect() as connection:
        return int(
            connection.execute(select(func.count()).select_from(model)).scalar_one()
        )


def test_happy_path_counter_ingest(app: Flask, client: FlaskClient) -> None:
    response = post_json(
        client,
        {
            "name": "requests",
            "type": "counter",
            "value": 42,
            "timestamp": 1700000000000,
            "tags": {"host": "web-1", "region": "us"},
            "unit": "req",
        },
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["status"] == "created"
    assert payload["metric"] == {
        "id": 1,
        "name": "requests",
        "type": "counter",
        "unit": "req",
    }
    assert payload["data_point"] == {
        "id": 1,
        "ts_utc_ms": 1700000000000,
        "value": 42,
        "tags": {"host": "web-1", "region": "us"},
    }

    with app.app_context():
        metric = db.session.execute(
            select(Metric).where(Metric.name == "requests")
        ).scalar_one()
        assert metric.type == "counter"
        assert metric.unit == "req"

        point = db.session.execute(select(DataPoint)).scalar_one()
        assert point.metric_id == metric.id
        assert point.ts_utc_ms == 1700000000000
        assert point.value_num == 42.0
        assert point.value_str is None
        assert json.loads(point.tags) == {"host": "web-1", "region": "us"}


def test_metric_is_auto_created_with_the_declared_type(
    app: Flask, client: FlaskClient
) -> None:
    response = post_json(
        client, {"name": "queue.depth", "type": "gauge", "value": 3.5}
    )

    assert response.status_code == 201
    with app.app_context():
        metric = db.session.execute(
            select(Metric).where(Metric.name == "queue.depth")
        ).scalar_one()
        assert metric.type == "gauge"
        assert metric.unit is None


def test_type_mismatch_returns_409(app: Flask, client: FlaskClient) -> None:
    assert post_json(
        client, {"name": "requests", "type": "counter", "value": 1}
    ).status_code == 201

    response = post_json(client, {"name": "requests", "type": "gauge", "value": 2})

    error = assert_envelope(response, 409, "METRIC_TYPE_MISMATCH")
    assert error["field"] == "type"
    assert error["expected"] == "counter"
    assert error["received"] == "gauge"
    assert "counter" in error["hint"]


def test_missing_type_on_a_new_metric(app: Flask, client: FlaskClient) -> None:
    response = post_json(client, {"name": "unknown", "value": 1})

    error = assert_envelope(response, 400, "MISSING_TYPE")
    assert error["field"] == "type"
    assert "declare its type" in error["hint"]

    with app.app_context():
        # The metric must not be created when the payload is rejected.
        assert count_rows(Metric) == 0


def test_existing_metric_inherits_its_type(app: Flask, client: FlaskClient) -> None:
    assert post_json(
        client, {"name": "requests", "type": "counter", "value": 1}
    ).status_code == 201

    response = post_json(client, {"name": "requests", "value": 7})

    assert response.status_code == 201
    assert response.get_json()["metric"]["type"] == "counter"


@pytest.mark.parametrize(
    ("metric_type", "value", "code"),
    [
        ("string", 42, "INVALID_VALUE"),
        ("bool", 1, "INVALID_VALUE"),
        ("counter", True, "INVALID_VALUE"),
        ("timer", -5, "INVALID_VALUE"),
        ("counter", float("nan"), "NON_FINITE_VALUE"),
        ("counter", float("inf"), "NON_FINITE_VALUE"),
        ("gauge", "12", "INVALID_VALUE"),
    ],
)
def test_invalid_value_per_type(
    app: Flask, client: FlaskClient, metric_type: str, value: Any, code: str
) -> None:
    response = post_json(
        client, {"name": f"m.{metric_type}", "type": metric_type, "value": value}
    )

    error = assert_envelope(response, 400, code)
    assert error["field"] == "value"

    with app.app_context():
        # A rejected payload must not leave a half created metric behind.
        assert count_rows(Metric) == 0
        assert count_rows(DataPoint) == 0


def test_string_value_is_stored_in_value_str(app: Flask, client: FlaskClient) -> None:
    response = post_json(
        client, {"name": "status", "type": "string", "value": "ok"}
    )

    assert response.status_code == 201
    assert response.get_json()["data_point"]["value"] == "ok"
    with app.app_context():
        point = db.session.execute(select(DataPoint)).scalar_one()
        assert point.value_str == "ok"
        assert point.value_num is None


def test_bool_value_is_stored_in_both_columns(app: Flask, client: FlaskClient) -> None:
    response = post_json(client, {"name": "healthy", "type": "bool", "value": True})

    assert response.status_code == 201
    assert response.get_json()["data_point"]["value"] is True
    with app.app_context():
        point = db.session.execute(select(DataPoint)).scalar_one()
        assert point.value_num == 1.0
        assert point.value_str == "true"


def test_invalid_metric_name(client: FlaskClient) -> None:
    response = post_json(
        client, {"name": "requests total!", "type": "counter", "value": 1}
    )

    error = assert_envelope(response, 400, "INVALID_METRIC_NAME")
    assert error["field"] == "name"
    assert "^[A-Za-z0-9]" in error["hint"]


def test_missing_metric_name(client: FlaskClient) -> None:
    response = post_json(client, {"type": "counter", "value": 1})

    error = assert_envelope(response, 400, "INVALID_METRIC_NAME")
    assert error["field"] == "name"


def test_unknown_metric_type(client: FlaskClient) -> None:
    response = post_json(client, {"name": "requests", "type": "histogram", "value": 1})

    error = assert_envelope(response, 400, "INVALID_METRIC_TYPE")
    assert error["field"] == "type"
    assert "counter" in error["expected"]
    assert error["received"] == "histogram"


def test_ambiguous_seconds_timestamp(client: FlaskClient) -> None:
    response = post_json(
        client, {"name": "requests", "type": "counter", "value": 1, "timestamp": 1700000000}
    )

    error = assert_envelope(response, 400, "AMBIGUOUS_TIMESTAMP")
    assert error["field"] == "timestamp"
    assert "epoch seconds" in error["hint"]


def test_invalid_timestamp_string(client: FlaskClient) -> None:
    response = post_json(
        client,
        {"name": "requests", "type": "counter", "value": 1, "timestamp": "not-a-date"},
    )

    error = assert_envelope(response, 400, "INVALID_TIMESTAMP")
    assert error["field"] == "timestamp"
    assert "Parser message" in error["hint"]


def test_iso_timestamp_is_accepted(app: Flask, client: FlaskClient) -> None:
    response = post_json(
        client,
        {
            "name": "requests",
            "type": "counter",
            "value": 1,
            "timestamp": "2023-11-14T22:13:20Z",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["data_point"]["ts_utc_ms"] == 1700000000000


def test_tags_with_a_nested_object(client: FlaskClient) -> None:
    response = post_json(
        client,
        {
            "name": "requests",
            "type": "counter",
            "value": 1,
            "tags": {"host": {"name": "web-1"}},
        },
    )

    error = assert_envelope(response, 400, "INVALID_TAGS")
    assert error["field"] == "tags.host"


def test_too_many_tags(client: FlaskClient) -> None:
    response = post_json(
        client,
        {
            "name": "requests",
            "type": "counter",
            "value": 1,
            "tags": {f"k{index}": "v" for index in range(33)},
        },
    )

    assert_envelope(response, 400, "TOO_MANY_TAGS")


def test_non_object_body(client: FlaskClient) -> None:
    response = client.post(
        "/api/ingest", data=json.dumps([1, 2, 3]), content_type="application/json"
    )

    error = assert_envelope(response, 400, "INVALID_BODY")
    assert "single JSON object" in error["hint"]


def test_malformed_json(client: FlaskClient) -> None:
    response = client.post(
        "/api/ingest", data="{not json", content_type="application/json"
    )

    error = assert_envelope(response, 400, "INVALID_JSON")
    assert error["received"]


def test_unsupported_media_type(client: FlaskClient) -> None:
    response = client.post(
        "/api/ingest", data="name=requests", content_type="application/x-www-form-urlencoded"
    )

    assert_envelope(response, 415, "UNSUPPORTED_MEDIA_TYPE")


def test_unknown_route_returns_the_envelope(client: FlaskClient) -> None:
    response = client.get("/api/does-not-exist")

    error = assert_envelope(response, 404, "NOT_FOUND")
    assert error["field"] is None


def test_wrong_method_returns_the_envelope(client: FlaskClient) -> None:
    response = client.get("/api/ingest")

    error = assert_envelope(response, 405, "METHOD_NOT_ALLOWED")
    assert "POST" in (error["expected"] or "")


def test_unexpected_exception_returns_the_internal_error_envelope(
    app: Flask, client: FlaskClient
) -> None:
    @app.get("/api/boom")
    def boom() -> Any:
        raise RuntimeError("a secret internal detail")

    response = client.get("/api/boom")

    error = assert_envelope(response, 500, "INTERNAL_ERROR")
    assert "server logs" in error["hint"]
    # The stack trace and the exception message never reach the client.
    body = response.get_data(as_text=True)
    assert "a secret internal detail" not in body
    assert "Traceback" not in body


def test_html_error_page_shows_the_same_information(client: FlaskClient) -> None:
    response = client.get("/not-a-page", headers={"Accept": "text/html"})

    assert response.status_code == 404
    body = response.get_data(as_text=True)
    assert "NOT_FOUND" in body
    assert "The requested resource does not exist." in body
    # Every field of the envelope is visible in the page.
    for label in ("Message", "Field", "Expected", "Received", "Hint"):
        assert label in body


def test_concurrent_ingestion_of_the_same_new_metric(app: Flask) -> None:
    """Two simultaneous ingests of the same new metric must not corrupt it.

    The metric creation is race safe: exactly one metric row survives and
    both data points are stored.
    """
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    lock = threading.Lock()

    def ingest() -> None:
        with app.test_client() as client:
            barrier.wait(timeout=10)
            response = post_json(
                client, {"name": "race.counter", "type": "counter", "value": 1}
            )
            with lock:
                statuses.append(response.status_code)

    threads = [threading.Thread(target=ingest) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [201, 201], statuses
    with app.app_context():
        assert count_rows(Metric) == 1
        assert count_rows(DataPoint) == 2


def test_metric_creation_conflict_is_resolved(app: Flask) -> None:
    """A metric created by another connection must not break the ingestion.

    The session reads the metric list before the other connection creates the
    metric, so the insert of this session collides with the committed row.
    The conflict has to be resolved by re-reading the winning row instead of
    surfacing an error.
    """
    from app.ingest.routes import _create_metric

    with app.app_context():
        session = db.session
        assert (
            session.execute(
                select(Metric).where(Metric.name == "race.gauge")
            ).scalar_one_or_none()
            is None
        )

        # Another connection wins the race and commits the metric.
        with db.engine.begin() as connection:
            connection.execute(
                insert(Metric).values(
                    name="race.gauge",
                    type="gauge",
                    unit=None,
                    created_at_ms=1,
                )
            )

        metric = _create_metric(session, "race.gauge", "gauge", None, 2)

        assert metric.id is not None
        assert metric.type == "gauge"
        rows = session.execute(
            select(Metric).where(Metric.name == "race.gauge")
        ).scalars().all()
        assert len(rows) == 1


def test_list_metrics(client: FlaskClient) -> None:
    assert post_json(
        client, {"name": "requests", "type": "counter", "value": 1}
    ).status_code == 201
    assert post_json(
        client, {"name": "requests", "type": "counter", "value": 2}
    ).status_code == 201
    assert post_json(
        client, {"name": "errors", "type": "counter", "value": 1}
    ).status_code == 201

    response = client.get("/api/metrics")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    names = [metric["name"] for metric in payload["metrics"]]
    assert names == ["errors", "requests"]
    by_name = {metric["name"]: metric for metric in payload["metrics"]}
    assert by_name["requests"]["data_point_count"] == 2
    assert by_name["errors"]["data_point_count"] == 1
    assert by_name["requests"]["type"] == "counter"


def test_list_metrics_filters_by_name(client: FlaskClient) -> None:
    assert post_json(
        client, {"name": "requests", "type": "counter", "value": 1}
    ).status_code == 201
    assert post_json(
        client, {"name": "errors", "type": "counter", "value": 1}
    ).status_code == 201

    response = client.get("/api/metrics?name=requests")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["metrics"][0]["name"] == "requests"
