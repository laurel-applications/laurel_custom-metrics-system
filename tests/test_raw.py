"""Tests of the raw point service and JSON API.

The raw browser has to preserve every measurement exactly as it was ingested:
the value must follow the metric type, both timestamps must stay distinct, the
pagination must happen in SQL and the long poll must use a monotonic
watermark.  These tests exercise those guarantees through the public service
and the HTTP API.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from typing import Any, Iterable, Mapping

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.extensions import db
from app.models import DataPoint, Metric
from app.raw import service

#: The exact key set every error envelope must carry.
ENVELOPE_KEYS = {"code", "message", "field", "expected", "received", "hint"}

#: A fixed range used by the API tests.
FROM_MS = 1_700_000_000_000
TO_MS = 1_700_000_010_000


def assert_envelope(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the response is an error envelope and return the error object."""
    assert response.status_code == status, response.get_data(as_text=True)
    payload = response.get_json()
    assert isinstance(payload, dict)
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["hint"], str) and error["hint"]
    return error


def insert_points(
    app: Flask,
    name: str,
    metric_type: str,
    rows: Iterable[Mapping[str, Any]],
    unit: str | None = None,
) -> None:
    """Insert data points directly, the way the ingestion API would store them."""
    with app.app_context():
        session = db.session
        metric = session.execute(
            select(Metric).where(Metric.name == name)
        ).scalar_one_or_none()
        if metric is None:
            metric = Metric(name=name, type=metric_type, unit=unit, created_at_ms=0)
            session.add(metric)
            session.flush()

        for row in rows:
            value = row["value"]
            value_num: float | None = None
            value_str: str | None = None
            if metric_type == "string":
                value_str = str(value)
            elif metric_type == "bool":
                value_num = 1.0 if value else 0.0
                value_str = "true" if value else "false"
            else:
                value_num = float(value)

            session.add(
                DataPoint(
                    metric_id=metric.id,
                    ts_utc_ms=row["ts"],
                    value_num=value_num,
                    value_str=value_str,
                    tags=json.dumps(row.get("tags", {})),
                    received_at_ms=row.get("received_at", row["ts"]),
                )
            )
        session.commit()


def raw(client: FlaskClient, params: Mapping[str, Any]) -> Any:
    """Call the raw points endpoint with the given query string parameters."""
    return client.get("/api/raw", query_string=dict(params))


def wait_for_raw(client: FlaskClient, params: Mapping[str, Any]) -> Any:
    """Call the raw long poll endpoint with the given query string parameters."""
    return client.get("/api/raw/wait", query_string=dict(params))


# ------------------------------------------------------------------- listing


def test_raw_api_returns_a_paginated_descending_page(
    app: Flask, client: FlaskClient
) -> None:
    """The default page is newest first and carries the total count."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [
            {"ts": FROM_MS + 1000, "value": 20.0},
            {"ts": FROM_MS + 2000, "value": 21.0},
            {"ts": FROM_MS + 3000, "value": 22.0},
        ],
        unit="C",
    )

    response = raw(
        client,
        {
            "metric": "temperature",
            "from": FROM_MS,
            "to": TO_MS,
            "limit": 2,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["metric"] == {
        "id": 1,
        "name": "temperature",
        "type": "gauge",
        "unit": "C",
    }
    assert payload["from_ms"] == FROM_MS
    assert payload["to_ms"] == TO_MS
    assert payload["order"] == "desc"
    assert payload["total"] == 3
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert payload["latest_id"] == 3
    assert [point["id"] for point in payload["points"]] == [3, 2]
    assert payload["points"][0]["value"] == 22.0
    assert payload["points"][0]["tags"] == {}


def test_raw_api_accepts_ascending_order_and_an_offset(
    app: Flask, client: FlaskClient
) -> None:
    """Ascending order is available and the offset selects a later page."""
    insert_points(
        app,
        "requests",
        "counter",
        [
            {"ts": FROM_MS + 1000, "value": 1},
            {"ts": FROM_MS + 2000, "value": 2},
            {"ts": FROM_MS + 3000, "value": 3},
        ],
    )

    response = raw(
        client,
        {
            "metric": "requests",
            "from": FROM_MS,
            "to": TO_MS,
            "limit": 1,
            "offset": 1,
            "order": "asc",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["order"] == "asc"
    assert payload["total"] == 3
    assert payload["offset"] == 1
    assert [point["id"] for point in payload["points"]] == [2]


def test_raw_api_types_values_by_metric_type(
    app: Flask, client: FlaskClient
) -> None:
    """Numbers, strings and booleans keep their natural JSON types."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [{"ts": FROM_MS + 1000, "value": 24.5, "tags": {"sensor": "ambient"}}],
    )
    insert_points(
        app,
        "status",
        "string",
        [{"ts": FROM_MS + 1000, "value": "ready"}],
    )
    insert_points(
        app,
        "healthy",
        "bool",
        [{"ts": FROM_MS + 1000, "value": True}],
    )

    temperature = raw(
        client,
        {"metric": "temperature", "from": FROM_MS, "to": TO_MS},
    ).get_json()["points"][0]
    status = raw(
        client,
        {"metric": "status", "from": FROM_MS, "to": TO_MS},
    ).get_json()["points"][0]
    healthy = raw(
        client,
        {"metric": "healthy", "from": FROM_MS, "to": TO_MS},
    ).get_json()["points"][0]

    assert temperature["value"] == 24.5
    assert temperature["tags"] == {"sensor": "ambient"}
    assert status["value"] == "ready"
    assert healthy["value"] is True


def test_malformed_tags_degrade_to_an_empty_object(
    app: Flask, client: FlaskClient
) -> None:
    """A broken tags document never breaks the raw response."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [{"ts": FROM_MS + 1000, "value": 24.5}],
    )
    with app.app_context():
        point = db.session.execute(select(DataPoint)).scalar_one()
        point.tags = "{not json"
        db.session.commit()

    response = raw(
        client,
        {"metric": "temperature", "from": FROM_MS, "to": TO_MS},
    )

    assert response.status_code == 200
    assert response.get_json()["points"][0]["tags"] == {}


def test_service_count_and_latest_id_use_the_metric_filters(
    app: Flask,
) -> None:
    """The count and latest id are scoped to the metric and the range."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [
            {"ts": FROM_MS + 1000, "value": 20.0},
            {"ts": FROM_MS + 2000, "value": 21.0},
            {"ts": TO_MS + 1000, "value": 22.0},
        ],
    )
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1500, "value": 1}],
    )

    with app.app_context():
        temperature = db.session.execute(
            select(Metric).where(Metric.name == "temperature")
        ).scalar_one()
        assert service.count_points(temperature.id, FROM_MS, TO_MS) == 2
        assert service.latest_id(temperature.id) == 3


# ---------------------------------------------------------------- long poll


def test_long_poll_returns_only_points_after_the_watermark(
    app: Flask, client: FlaskClient
) -> None:
    """The watermark excludes every point the caller already has."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [
            {"ts": FROM_MS + 1000, "value": 20.0},
            {"ts": FROM_MS + 2000, "value": 21.0},
        ],
    )

    response = wait_for_raw(
        client,
        {
            "metric": "temperature",
            "from": FROM_MS,
            "to": TO_MS,
            "since_id": 1,
            "timeout": 0.1,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["latest_id"] == 2
    assert payload["waited_ms"] >= 0
    assert [point["id"] for point in payload["points"]] == [2]


def test_long_poll_returns_an_empty_list_after_the_timeout(
    app: Flask, client: FlaskClient
) -> None:
    """A quiet metric produces an empty, successful long poll answer."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [{"ts": FROM_MS + 1000, "value": 20.0}],
    )

    response = wait_for_raw(
        client,
        {
            "metric": "temperature",
            "from": FROM_MS,
            "to": TO_MS,
            "since_id": 1,
            "timeout": 0,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["points"] == []
    assert payload["waited_ms"] >= 0


def test_long_poll_observes_a_point_committed_while_it_waits(
    app: Flask,
) -> None:
    """A concurrent insertion wakes the long poll before its timeout."""
    insert_points(
        app,
        "temperature",
        "gauge",
        [{"ts": FROM_MS + 1000, "value": 20.0}],
    )
    result: dict[str, Any] = {}

    def poll() -> None:
        """Run the long poll in another request context."""
        with app.test_client() as polling_client:
            response = wait_for_raw(
                polling_client,
                {
                    "metric": "temperature",
                    "from": FROM_MS,
                    "to": TO_MS,
                    "since_id": 1,
                    "timeout": 2,
                },
            )
            result["status"] = response.status_code
            result["payload"] = response.get_json()

    thread = threading.Thread(target=poll)
    thread.start()
    time.sleep(0.15)
    insert_points(
        app,
        "temperature",
        "gauge",
        [{"ts": FROM_MS + 2000, "value": 21.0}],
    )
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result["status"] == 200
    assert [point["id"] for point in result["payload"]["points"]] == [2]


# ---------------------------------------------------------------------- CSV


def test_to_csv_serializes_timestamps_values_and_compact_tags() -> None:
    """The CSV keeps the point order and writes UTC ISO-8601 timestamps."""
    points = [
        service.RawPoint(
            id=7,
            ts_ms=1_700_000_000_000,
            received_at_ms=1_700_000_001_500,
            value=24.5,
            tags={"sensor": "ambient"},
        ),
        service.RawPoint(
            id=8,
            ts_ms=1_700_000_002_000,
            received_at_ms=1_700_000_003_500,
            value=True,
            tags={},
        ),
    ]

    text = service.to_csv(points)
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0] == ["id", "ts_utc", "received_at_utc", "value", "tags"]
    assert rows[1] == [
        "7",
        "2023-11-14T22:13:20.000Z",
        "2023-11-14T22:13:21.500Z",
        "24.5",
        '{"sensor":"ambient"}',
    ]
    assert rows[2][3] == "true"
    assert rows[2][4] == "{}"


# ------------------------------------------------------------ error contract


def test_raw_api_rejects_an_unknown_metric(client: FlaskClient) -> None:
    """An unknown metric answers 404 with the shared envelope."""
    error = assert_envelope(raw(client, {"metric": "ghost"}), 404, "METRIC_NOT_FOUND")
    assert error["field"] == "metric"
    assert error["received"] == "ghost"


def test_raw_api_rejects_a_reversed_time_range(
    app: Flask, client: FlaskClient
) -> None:
    """A range that ends before it starts is refused."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1000, "value": 1}],
    )
    error = assert_envelope(
        raw(client, {"metric": "requests", "from": TO_MS, "to": FROM_MS}),
        400,
        "INVALID_TIME_RANGE",
    )
    assert error["field"] == "from"
    assert error["hint"]


def test_raw_api_rejects_a_negative_offset(
    app: Flask, client: FlaskClient
) -> None:
    """A negative page offset is refused."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1000, "value": 1}],
    )
    error = assert_envelope(
        raw(client, {"metric": "requests", "offset": -1}),
        400,
        "INVALID_PAGE",
    )
    assert error["field"] == "offset"
    assert error["hint"]


def test_raw_api_rejects_an_out_of_bounds_page_size(
    app: Flask, client: FlaskClient
) -> None:
    """The page size must stay between one and five hundred points."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1000, "value": 1}],
    )
    for limit in (0, 501):
        error = assert_envelope(
            raw(client, {"metric": "requests", "limit": limit}),
            400,
            "INVALID_PAGE_SIZE",
        )
        assert error["field"] == "limit"
        assert error["hint"]


def test_raw_api_rejects_an_unknown_order(
    app: Flask, client: FlaskClient
) -> None:
    """Only ascending and descending orders are accepted."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1000, "value": 1}],
    )
    error = assert_envelope(
        raw(client, {"metric": "requests", "order": "sideways"}),
        400,
        "INVALID_ORDER",
    )
    assert error["field"] == "order"
    assert error["hint"]


def test_raw_api_rejects_a_timeout_above_ten_seconds(
    app: Flask, client: FlaskClient
) -> None:
    """The long poll cannot hold a request open for more than ten seconds."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": FROM_MS + 1000, "value": 1}],
    )
    error = assert_envelope(
        wait_for_raw(client, {"metric": "requests", "timeout": 11}),
        400,
        "INVALID_TIMEOUT",
    )
    assert error["field"] == "timeout"
    assert error["hint"]
