"""Tests of the query API, of the aggregation semantics and of the filters.

The aggregation helpers are pure functions, so most of the semantics are
verified without a database at all.  The HTTP level is verified through the
test client, against the same isolated SQLite database the ingestion tests
use.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.errors import ApiError
from app.extensions import db
from app.models import DataPoint, Metric
from app.query import service
from app.query.filters import FilterSpec, parse_filters

#: The exact key set every error envelope must carry.
ENVELOPE_KEYS = {"code", "message", "field", "expected", "received", "hint"}

#: A timestamp aligned to a 60 second bucket, so several points fall in the
#: same bucket without any alignment surprise.
BASE = 1_700_000_040_000

#: The timestamp of 2023-11-14T22:13:20Z, used by the ISO-8601 tests.
BASE_ISO = 1_700_000_000_000


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
                value_str = value
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
                    received_at_ms=0,
                )
            )
        session.commit()


def query(client: FlaskClient, params: Mapping[str, Any]) -> Any:
    """Call the query endpoint with the given query string parameters."""
    return client.get("/api/query", query_string=dict(params))


def post_query(client: FlaskClient, payload: Mapping[str, Any]) -> Any:
    """Call the query endpoint with a JSON body."""
    return client.post(
        "/api/query",
        data=json.dumps(payload),
        content_type="application/json",
    )


# --------------------------------------------------------------------------
# Pure aggregation helpers (no database, no Flask)
# --------------------------------------------------------------------------


def test_service_module_has_no_framework_dependency() -> None:
    """The aggregation module must stay free of Flask and of SQLAlchemy."""
    tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "flask" not in imported
    assert "sqlalchemy" not in imported
    assert "app" not in imported


def test_auto_bucket_sec_covers_every_band() -> None:
    """The automatic bucket ladder picks the smallest bucket that fits."""
    assert service.auto_bucket_sec(2 * 3600 * 1000) == 60
    assert service.auto_bucket_sec(2 * 3600 * 1000 + 1) == 300
    assert service.auto_bucket_sec(12 * 3600 * 1000) == 300
    assert service.auto_bucket_sec(12 * 3600 * 1000 + 1) == 900
    assert service.auto_bucket_sec(2 * 24 * 3600 * 1000) == 900
    assert service.auto_bucket_sec(2 * 24 * 3600 * 1000 + 1) == 3600
    assert service.auto_bucket_sec(14 * 24 * 3600 * 1000) == 3600
    assert service.auto_bucket_sec(14 * 24 * 3600 * 1000 + 1) == 21600
    assert service.auto_bucket_sec(90 * 24 * 3600 * 1000) == 21600
    assert service.auto_bucket_sec(90 * 24 * 3600 * 1000 + 1) == 86400


def test_bucket_start_is_aligned_to_the_epoch() -> None:
    """Buckets are aligned to the epoch, not to the start of the range."""
    assert service.bucket_start_ms(1_700_000_123_456, 300_000) == 1_700_000_100_000
    assert service.bucket_start_ms(1_700_000_100_000, 300_000) == 1_700_000_100_000


def test_service_aggregations_over_known_points() -> None:
    """Every aggregation computes the documented value."""
    points = [
        service.Point(id=1, ts_ms=1_700_000_000_000, value_num=10.0),
        service.Point(id=2, ts_ms=1_700_000_060_000, value_num=20.0),
        service.Point(id=3, ts_ms=1_700_000_120_000, value_num=30.0),
    ]

    assert service.aggregate(points, "sum", "counter") == 60.0
    assert service.aggregate(points, "avg", "counter") == 20.0
    assert service.aggregate(points, "min", "counter") == 10.0
    assert service.aggregate(points, "max", "counter") == 30.0
    assert service.aggregate(points, "count", "counter") == 3
    assert service.aggregate(points, "last", "counter") == 30.0


def test_service_last_breaks_ties_by_identifier() -> None:
    """Two points with the same timestamp are ordered by their identifier."""
    points = [
        service.Point(id=7, ts_ms=1_700_000_000_000, value_num=1.0),
        service.Point(id=9, ts_ms=1_700_000_000_000, value_num=2.0),
    ]
    assert service.aggregate(points, "last", "gauge") == 2.0


def test_service_aggregations_for_bool_and_string() -> None:
    """A bool metric aggregates over 1.0/0.0 and a string metric over text."""
    bool_points = [
        service.Point(id=1, ts_ms=1, value_num=1.0, value_str="true"),
        service.Point(id=2, ts_ms=2, value_num=0.0, value_str="false"),
        service.Point(id=3, ts_ms=3, value_num=1.0, value_str="true"),
    ]
    assert service.aggregate(bool_points, "avg", "bool") == pytest.approx(2 / 3)
    assert service.aggregate(bool_points, "sum", "bool") == 2.0
    assert service.aggregate(bool_points, "min", "bool") == 0.0
    assert service.aggregate(bool_points, "max", "bool") == 1.0
    assert service.aggregate(bool_points, "last", "bool") is True

    string_points = [
        service.Point(id=1, ts_ms=1, value_str="ok"),
        service.Point(id=2, ts_ms=2, value_str="ko"),
    ]
    assert service.aggregate(string_points, "count", "string") == 2
    assert service.aggregate(string_points, "last", "string") == "ko"


def test_service_build_series_groups_by_tag() -> None:
    """The grouping produces one series per tag value plus the null series."""
    points = [
        service.Point(id=1, ts_ms=0, value_num=1.0, tags={"host": "web"}),
        service.Point(id=2, ts_ms=0, value_num=2.0, tags={"host": "db"}),
        service.Point(id=3, ts_ms=0, value_num=3.0, tags={}),
    ]
    series = service.build_series(points, "sum", "counter", 60_000, "host")

    assert [serie["key"] for serie in series] == [None, "db", "web"]
    assert series[0]["points"] == [{"ts": 0, "value": 3.0, "count": 1}]
    assert series[1]["points"] == [{"ts": 0, "value": 2.0, "count": 1}]
    assert series[2]["points"] == [{"ts": 0, "value": 1.0, "count": 1}]


def test_service_build_series_without_points() -> None:
    """An empty result is a single empty series, not an error."""
    series = service.build_series([], "avg", "counter", 60_000)
    assert series == [{"key": None, "points": []}]


def test_service_valid_aggregations_per_type() -> None:
    """The matrix of valid aggregations is the one the API advertises."""
    assert service.valid_aggregations("counter") == (
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "last",
    )
    assert service.valid_aggregations("string") == ("count", "last")
    assert service.valid_aggregations("bool") == (
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "last",
    )


# --------------------------------------------------------------------------
# HTTP level
# --------------------------------------------------------------------------


def test_query_aggregations_over_a_known_metric(
    app: Flask, client: FlaskClient
) -> None:
    """Every aggregation answers with the documented value."""
    insert_points(
        app,
        "requests",
        "counter",
        [
            {"ts": BASE, "value": 10},
            {"ts": BASE + 1000, "value": 20},
            {"ts": BASE + 2000, "value": 30},
        ],
    )

    expected = {
        "sum": 60.0,
        "avg": 20.0,
        "min": 10.0,
        "max": 30.0,
        "count": 3,
        "last": 30.0,
    }
    for agg, value in expected.items():
        response = query(
            client,
            {
                "metric": "requests",
                "from": BASE,
                "to": BASE + 60_000,
                "agg": agg,
                "bucket": 60,
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = response.get_json()
        assert payload["agg"] == agg
        assert payload["point_count"] == 3
        assert len(payload["series"][0]["points"]) == 1
        assert payload["series"][0]["points"][0]["value"] == value
        assert payload["series"][0]["points"][0]["count"] == 3


def test_query_auto_bucket_is_returned(app: Flask, client: FlaskClient) -> None:
    """The chosen bucket size travels with the response."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    response = query(
        client, {"metric": "requests", "from": BASE, "to": BASE + 3600_000}
    )
    assert response.get_json()["bucket_sec"] == 60

    response = query(
        client, {"metric": "requests", "from": BASE, "to": BASE + 5 * 3600_000}
    )
    assert response.get_json()["bucket_sec"] == 300

    response = query(
        client, {"metric": "requests", "from": BASE, "to": BASE + 24 * 3600_000}
    )
    assert response.get_json()["bucket_sec"] == 900


def test_query_explicit_bucket_overrides_the_automatic_one(
    app: Flask, client: FlaskClient
) -> None:
    """An explicit bucket wins over the ladder."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": BASE, "value": 1}, {"ts": BASE + 1000, "value": 2}],
    )

    response = query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 3600_000,
            "bucket": 3600,
            "agg": "sum",
        },
    )
    payload = response.get_json()
    assert payload["bucket_sec"] == 3600
    assert len(payload["series"][0]["points"]) == 1
    assert payload["series"][0]["points"][0]["value"] == 3.0


def test_query_group_by_returns_one_series_per_tag(
    app: Flask, client: FlaskClient
) -> None:
    """The grouped answer holds one series per tag value and the null one."""
    insert_points(
        app,
        "requests",
        "counter",
        [
            {"ts": BASE, "value": 1, "tags": {"host": "web"}},
            {"ts": BASE, "value": 2, "tags": {"host": "db"}},
            {"ts": BASE, "value": 3, "tags": {}},
        ],
    )

    response = query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 60_000,
            "bucket": 60,
            "group_by": "host",
        },
    )
    payload = response.get_json()
    assert payload["group_by"] == "host"

    series = {serie["key"]: serie["points"] for serie in payload["series"]}
    assert set(series) == {None, "web", "db"}
    assert series["web"][0]["value"] == 1.0
    assert series["db"][0]["value"] == 2.0
    assert series[None][0]["value"] == 3.0


def test_query_filters_are_anded(app: Flask, client: FlaskClient) -> None:
    """Several filters narrow the result together."""
    insert_points(
        app,
        "requests",
        "counter",
        [
            {"ts": BASE, "value": 5, "tags": {"host": "web"}},
            {"ts": BASE + 1000, "value": 50, "tags": {"host": "web"}},
            {"ts": BASE + 2000, "value": 500, "tags": {"host": "db"}},
        ],
    )

    filters = [
        {"field": "value", "op": ">", "value": 10},
        {"field": "tags.host", "op": "=", "value": "web"},
    ]
    response = query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 60_000,
            "bucket": 60,
            "filters": json.dumps(filters),
        },
    )
    payload = response.get_json()
    assert payload["point_count"] == 1
    assert payload["series"][0]["points"][0]["value"] == 50.0


def test_query_with_a_numeric_filter(app: Flask, client: FlaskClient) -> None:
    """A numeric filter selects the points above the bound."""
    insert_points(
        app,
        "requests",
        "counter",
        [
            {"ts": BASE, "value": 5},
            {"ts": BASE + 1000, "value": 50},
            {"ts": BASE + 2000, "value": 500},
        ],
    )

    response = query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 60_000,
            "bucket": 60,
            "filters": json.dumps([{"field": "value", "op": ">", "value": 10}]),
        },
    )
    assert response.get_json()["point_count"] == 2


def test_query_with_a_string_metric(app: Flask, client: FlaskClient) -> None:
    """A string metric answers with count and last only."""
    insert_points(
        app,
        "status",
        "string",
        [
            {"ts": BASE, "value": "ok"},
            {"ts": BASE + 1000, "value": "ko"},
        ],
    )

    # The default aggregation is 'avg', which a string metric does not
    # accept, so the caller has to name a valid one.
    response = query(
        client,
        {
            "metric": "status",
            "from": BASE,
            "to": BASE + 60_000,
            "bucket": 60,
            "agg": "count",
        },
    )
    payload = response.get_json()
    assert payload["point_count"] == 2
    assert payload["series"][0]["points"][0]["value"] == 2

    error = assert_envelope(
        query(client, {"metric": "status", "from": BASE, "to": BASE + 60_000}),
        400,
        "INVALID_AGGREGATION_FOR_TYPE",
    )
    assert error["received"] == "avg"

    response = query(
        client,
        {
            "metric": "status",
            "from": BASE,
            "to": BASE + 60_000,
            "bucket": 60,
            "agg": "last",
        },
    )
    assert response.get_json()["series"][0]["points"][0]["value"] == "ko"


def test_query_with_a_bool_metric(app: Flask, client: FlaskClient) -> None:
    """The average of a bool metric is the fraction of true points."""
    insert_points(
        app,
        "healthy",
        "bool",
        [
            {"ts": BASE, "value": True},
            {"ts": BASE + 1000, "value": False},
            {"ts": BASE + 2000, "value": True},
            {"ts": BASE + 3000, "value": False},
        ],
    )

    response = query(
        client, {"metric": "healthy", "from": BASE, "to": BASE + 60_000, "bucket": 60}
    )
    payload = response.get_json()
    assert payload["series"][0]["points"][0]["value"] == pytest.approx(0.5)
    assert payload["series"][0]["points"][0]["count"] == 4


def test_query_without_points_is_not_an_error(
    app: Flask, client: FlaskClient
) -> None:
    """An empty answer is a 200 with an empty series."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    response = query(
        client, {"metric": "requests", "from": BASE + 10_000_000, "to": BASE + 20_000_000}
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["point_count"] == 0
    assert payload["series"] == [{"key": None, "points": []}]


def test_query_accepts_iso_timestamps(app: Flask, client: FlaskClient) -> None:
    """The bounds accept an ISO-8601 string as well as epoch milliseconds."""
    insert_points(app, "requests", "counter", [{"ts": BASE_ISO, "value": 1}])

    response = client.get(
        "/api/query",
        query_string={
            "metric": "requests",
            "from": "2023-11-14T22:13:20Z",
            "to": "2023-11-14T22:14:20Z",
            "bucket": 60,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["point_count"] == 1


def test_query_post_and_get_agree(app: Flask, client: FlaskClient) -> None:
    """Both methods share the same behaviour."""
    insert_points(
        app,
        "requests",
        "counter",
        [{"ts": BASE, "value": 4, "tags": {"host": "web"}}],
    )

    filters = [{"field": "tags.host", "op": "contains", "value": "we"}]
    posted = post_query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 60_000,
            "agg": "sum",
            "bucket": 60,
            "filters": filters,
        },
    )
    got = query(
        client,
        {
            "metric": "requests",
            "from": BASE,
            "to": BASE + 60_000,
            "agg": "sum",
            "bucket": 60,
            "filters": json.dumps(filters),
        },
    )
    assert posted.status_code == 200
    assert got.status_code == 200
    assert posted.get_json() == got.get_json()


def test_query_response_shape(app: Flask, client: FlaskClient) -> None:
    """The answer holds exactly the documented keys."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    payload = query(
        client, {"metric": "requests", "from": BASE, "to": BASE + 60_000}
    ).get_json()

    assert list(payload) == [
        "metric",
        "agg",
        "bucket_sec",
        "group_by",
        "from_ms",
        "to_ms",
        "point_count",
        "bucket_count",
        "series",
    ]
    assert payload["metric"] == {
        "id": payload["metric"]["id"],
        "name": "requests",
        "type": "counter",
        "unit": None,
    }
    assert payload["agg"] == "avg"
    assert payload["group_by"] is None
    assert payload["from_ms"] == BASE
    assert payload["to_ms"] == BASE + 60_000
    assert payload["point_count"] == 1
    assert payload["bucket_count"] == 1
    assert list(payload["series"][0]) == ["key", "points"]
    assert list(payload["series"][0]["points"][0]) == ["ts", "value", "count"]


# --------------------------------------------------------------------------
# Error contract
# --------------------------------------------------------------------------


def test_query_without_a_metric_is_rejected(app: Flask, client: FlaskClient) -> None:
    """The metric name is required."""
    error = assert_envelope(query(client, {}), 400, "MISSING_PARAMETER")
    assert error["field"] == "metric"


def test_query_with_an_unknown_metric_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """An unknown metric is a 404."""
    error = assert_envelope(
        query(client, {"metric": "nope"}), 404, "METRIC_NOT_FOUND"
    )
    assert error["field"] == "metric"
    assert error["received"] == "nope"


def test_query_with_an_unknown_aggregation_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """An unknown aggregation lists the allowed ones."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(client, {"metric": "requests", "agg": "median"}),
        400,
        "INVALID_AGGREGATION",
    )
    assert error["expected"] == ["sum", "avg", "min", "max", "count", "last"]


def test_query_with_an_aggregation_invalid_for_the_type_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """A string metric does not accept an average."""
    insert_points(app, "status", "string", [{"ts": BASE, "value": "ok"}])

    error = assert_envelope(
        query(client, {"metric": "status", "agg": "avg"}),
        400,
        "INVALID_AGGREGATION_FOR_TYPE",
    )
    assert error["expected"] == ["count", "last"]
    assert error["received"] == "avg"


def test_query_with_an_invalid_range_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """A range that ends before it starts is refused."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(client, {"metric": "requests", "from": BASE + 1000, "to": BASE}),
        400,
        "INVALID_TIME_RANGE",
    )
    assert error["expected"] == "from must be earlier than to"


def test_query_with_an_invalid_timestamp_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """A bound that is neither epoch milliseconds nor ISO-8601 is refused."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    assert_envelope(
        query(client, {"metric": "requests", "from": "yesterday"}),
        400,
        "INVALID_TIMESTAMP",
    )


def test_query_with_an_invalid_bucket_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """The bucket size must be a positive integer."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(client, {"metric": "requests", "bucket": 0}), 400, "INVALID_BUCKET"
    )
    assert error["expected"] == "a positive integer number of seconds"

    assert_envelope(
        query(client, {"metric": "requests", "bucket": "soon"}), 400, "INVALID_BUCKET"
    )


def test_query_with_broken_filters_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """Filters that are not a JSON array are refused."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    assert_envelope(
        query(client, {"metric": "requests", "filters": "not json"}),
        400,
        "INVALID_FILTERS",
    )
    assert_envelope(
        query(client, {"metric": "requests", "filters": '{"field": "value"}'}),
        400,
        "INVALID_FILTERS",
    )


def test_query_with_an_unknown_filter_field_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """An unknown filter field lists the allowed forms."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(
            client,
            {
                "metric": "requests",
                "filters": json.dumps(
                    [{"field": "colour", "op": "=", "value": "red"}]
                ),
            },
        ),
        400,
        "INVALID_FILTER_FIELD",
    )
    assert "tags.<key>" in error["expected"]


def test_query_with_an_unknown_filter_operator_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """An operator that does not fit the field is refused."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(
            client,
            {
                "metric": "requests",
                "filters": json.dumps(
                    [{"field": "value", "op": "contains", "value": 1}]
                ),
            },
        ),
        400,
        "INVALID_FILTER_OPERATOR",
    )
    assert "between" in error["expected"]


def test_query_with_an_invalid_filter_value_is_rejected(
    app: Flask, client: FlaskClient
) -> None:
    """A value that does not fit the operator is refused with the reason."""
    insert_points(app, "requests", "counter", [{"ts": BASE, "value": 1}])

    error = assert_envelope(
        query(
            client,
            {
                "metric": "requests",
                "filters": json.dumps(
                    [{"field": "value", "op": "between", "value": [1]}]
                ),
            },
        ),
        400,
        "INVALID_FILTER_VALUE",
    )
    assert "two numbers" in error["received"]

    error = assert_envelope(
        query(
            client,
            {
                "metric": "requests",
                "filters": json.dumps(
                    [{"field": "tags.host", "op": "regex", "value": "["}]
                ),
            },
        ),
        400,
        "INVALID_FILTER_VALUE",
    )
    assert "regular expression" in error["received"]


def test_query_on_an_unknown_api_route_uses_the_envelope(
    app: Flask, client: FlaskClient
) -> None:
    """The 404 of an unknown API route keeps the canonical envelope."""
    error = assert_envelope(client.get("/api/nope"), 404, "NOT_FOUND")
    assert set(error) == ENVELOPE_KEYS


def test_query_with_a_wrong_method_uses_the_envelope(
    app: Flask, client: FlaskClient
) -> None:
    """The 405 of a wrong method keeps the canonical envelope."""
    error = assert_envelope(client.delete("/api/query"), 405, "METHOD_NOT_ALLOWED")
    assert set(error) == ENVELOPE_KEYS


# --------------------------------------------------------------------------
# Filters as pure functions
# --------------------------------------------------------------------------


def test_every_numeric_operator_is_evaluated() -> None:
    """Every numeric operator compares the way it is documented."""
    def matches(op: str, value: Any) -> bool:
        spec = parse_filters([{"field": "value", "op": op, "value": value}], "counter")[0]
        return spec.matches(
            ts_ms=1000, value_num=10.0, value_str=None, tags={}
        )

    assert matches("=", 10) is True
    assert matches("=", 11) is False
    assert matches("!=", 11) is True
    assert matches(">", 9) is True
    assert matches(">", 10) is False
    assert matches(">=", 10) is True
    assert matches("<", 11) is True
    assert matches("<=", 10) is True
    assert matches("between", [5, 15]) is True
    assert matches("between", [11, 20]) is False
    assert matches("between", [10, 10]) is True


def test_every_string_operator_is_evaluated() -> None:
    """Every string operator compares the way it is documented."""
    def matches(op: str, value: Any) -> bool:
        spec = parse_filters([{"field": "value", "op": op, "value": value}], "string")[0]
        return spec.matches(
            ts_ms=1000, value_num=None, value_str="web-server", tags={}
        )

    assert matches("=", "web-server") is True
    assert matches("!=", "db") is True
    assert matches("contains", "serv") is True
    assert matches("not_contains", "db") is True
    assert matches("starts_with", "web") is True
    assert matches("ends_with", "server") is True
    assert matches("regex", r"^web-\w+$") is True
    assert matches("regex", r"^db") is False


def test_boolean_operators_are_evaluated() -> None:
    """``is_true`` and ``is_false`` ignore the filter value."""
    is_true = parse_filters([{"field": "value", "op": "is_true"}], "bool")[0]
    is_false = parse_filters([{"field": "value", "op": "is_false"}], "bool")[0]

    assert (
        is_true.matches(ts_ms=1, value_num=1.0, value_str="true", tags={}) is True
    )
    assert (
        is_true.matches(ts_ms=1, value_num=0.0, value_str="false", tags={}) is False
    )
    assert (
        is_false.matches(ts_ms=1, value_num=0.0, value_str="false", tags={}) is True
    )


def test_tag_filters_are_evaluated() -> None:
    """A tag filter compares the tag as a string and misses absent tags."""
    spec = parse_filters(
        [{"field": "tags.host", "op": "contains", "value": "web"}], "counter"
    )[0]
    assert (
        spec.matches(ts_ms=1, value_num=1.0, value_str=None, tags={"host": "web-1"})
        is True
    )
    # A point without the tag never matches.
    assert spec.matches(ts_ms=1, value_num=1.0, value_str=None, tags={}) is False


def test_timestamp_filter_is_evaluated() -> None:
    """The timestamp is a numeric field."""
    spec = parse_filters(
        [{"field": "ts", "op": "between", "value": [1000, 2000]}], "counter"
    )[0]
    assert spec.matches(ts_ms=1500, value_num=1.0, value_str=None, tags={}) is True
    assert spec.matches(ts_ms=2500, value_num=1.0, value_str=None, tags={}) is False


def test_parse_filters_rejects_an_unknown_field() -> None:
    """An unknown field is refused with the allowed forms."""
    with pytest.raises(ApiError) as info:
        parse_filters([{"field": "colour", "op": "=", "value": "red"}], "counter")
    assert info.value.code == "INVALID_FILTER_FIELD"


def test_parse_filters_rejects_an_invalid_regex() -> None:
    """A broken regular expression carries the parser message."""
    with pytest.raises(ApiError) as info:
        parse_filters([{"field": "tags.host", "op": "regex", "value": "["}], "counter")
    assert info.value.code == "INVALID_FILTER_VALUE"
    assert "regular expression" in str(info.value.received)


def test_filter_spec_is_immutable() -> None:
    """A parsed filter cannot be mutated behind the back of the caller."""
    spec = FilterSpec(field="value", op="=", value=1, kind="numeric")
    with pytest.raises(Exception):
        spec.value = 2  # type: ignore[misc]
