# Laurel Metrics

A small, self-hosted custom metrics system: push primitive measurements over
HTTP, explore them through self-built widgets, without learning a query
language.

It is a single Flask monolith with server-rendered Jinja2 templates, one
SQLAlchemy 2.0 data layer and Alembic migrations. There is no build step, no
SPA, no client-side framework and no background worker.

---

## Table of contents

- [What this system is](#what-this-system-is)
- [What this system is NOT](#what-this-system-is-not)
- [Requirements](#requirements)
- [Local setup](#local-setup)
- [Configuration](#configuration)
- [Ingestion API](#ingestion-api)
- [Error contract](#error-contract)
- [Query API](#query-api)
- [Web UI](#web-ui)
- [Migrations](#migrations)
- [Testing](#testing)
- [Known limitations](#known-limitations)

---

## What this system is

- An **HTTP ingestion endpoint** that accepts one metric data point per
  request: `POST /api/ingest`.
- **Five metric types**: `counter`, `gauge`, `timer`, `string` and `bool`.
- **Tags** on every data point: a flat JSON object of key/value pairs used to
  segment the data.
- A **query API** that aggregates the points of a metric over a time range
  into a bucketed time series: `GET /api/query` (and the same thing with
  `POST /api/query` and a JSON body).
- A **web UI** with dashboards, a widget builder, drag & drop reordering and
  four chart renderings (line, bar, table and stat).
- **One error contract** for the API and for the UI: every failure carries a
  machine-readable code, the offending field, what was expected, what
  arrived and a hint.

Every timestamp is stored as **UTC epoch milliseconds** in a `BIGINT`
column, and every tag document is a JSON string in a `TEXT` column, so the
SQLite and the MariaDB engines behave identically.

## What this system is NOT

Read this before you deploy it:

- **No authentication and no authorization.** There is no login, no user, no
  session and no API key. Anything that can reach the port can ingest and
  read. Put it behind a reverse proxy if that matters.
- **No alerting.** There are no monitors, no thresholds, no notifications and
  no webhooks. The system answers questions you ask; it never raises an
  alarm on its own.
- **No rollups and no downsampling.** Every query reads the raw points of
  the range and aggregates them on the fly. There is no rollup table, no
  scheduler and no precomputed state.
- **No multi-tenancy.** There are no namespaces, no organizations, no teams
  and no per-client quota.
- **No log management, no tracing, no APM.** Only custom metric points.
- **No anomaly detection, no forecasting, no machine learning.**
- **No horizontal scaling.** One process, one database. The engine is
  scoped to the application context, so a multi-process deployment needs one
  application per process, not several workers sharing an engine.

## Requirements

| Requirement | Notes |
| --- | --- |
| Python **3.14** | The code uses the 3.11+ `datetime.fromisoformat` behaviour and modern typing syntax. |
| `pip` | The system Python has no pip; the virtualenv brings its own. |
| **MariaDB** *(optional)* | Only for production. Local development uses SQLite, which is the default. |
| A web server *(optional)* | For production, put the app behind `gunicorn` or `uwsgi`; the Flask development server is for local work only. |

The dependencies are pinned in `requirements.txt`:

```
flask==3.1.3
sqlalchemy==2.1.9
alembic==1.20.1
pymysql==1.2.4
pytest==9.2.0
```

`pymysql` is the driver used by the MariaDB URL.

## Local setup

Copy-pasteable, from a clean checkout:

```bash
# 1. Create the virtualenv with the system Python 3.14.
python3.14 -m venv .venv

# 2. Install the dependencies.
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 3. Create the schema (SQLite file in instance/metrics.db).
.venv/bin/alembic upgrade head

# 4. Run the application.
.venv/bin/flask --app app:create_app run --debug
```

The application is then on <http://127.0.0.1:5000>. The dashboard list is at
`/`, the metric overview at `/metrics`, and the health probe at `/health`.

Push a first data point and open the UI:

```bash
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "requests", "type": "counter", "value": 1, "tags": {"host": "web-1"}}'
```

## Configuration

The application reads **one** environment variable that matters:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///<repo>/instance/metrics.db` | The SQLAlchemy URL of the database. |
| `SECRET_KEY` | `dev-secret-key-change-me` | Flask session key. There is no authentication, so nothing depends on it yet; set it anyway. |

### SQLite (local development)

The default is a file in the `instance/` folder, which Flask creates on
startup:

```bash
export DATABASE_URL="sqlite:////absolute/path/to/instance/metrics.db"
```

SQLite gets two pragmas on every connection (`app/config.py`):
`journal_mode=WAL` and `foreign_keys=ON`. The `WAL` pragma is only ever sent
to SQLite; a MariaDB server would refuse it.

### MariaDB (production)

```bash
export DATABASE_URL="mysql+pymysql://user:password@host:3306/dbname"
```

Production uses **only** environment variables: there is no configuration
file, no `.env` loader and no secret baked into the image. The
`alembic/env.py` file reads the *same* `DATABASE_URL` variable, so the
migrations always run against the database the application uses:

```bash
DATABASE_URL="mysql+pymysql://user:password@host:3306/dbname" .venv/bin/alembic upgrade head
```

The MariaDB user needs `CREATE`, `ALTER`, `DROP`, `INDEX`, `INSERT`,
`UPDATE`, `DELETE` and `SELECT` on the target schema.

## Ingestion API

### `POST /api/ingest`

One JSON object per request, one data point per request.

```bash
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "requests", "type": "counter", "value": 42, "tags": {"host": "web-1"}}'
```

A successful ingestion answers `201 Created`:

```json
{
  "status": "created",
  "metric": {"id": 1, "name": "requests", "type": "counter", "unit": null},
  "data_point": {"id": 1, "ts_utc_ms": 1700000000000, "value": 42, "tags": {"host": "web-1"}}
}
```

### The fields of the payload

| Field | Required | Type | Rules |
| --- | --- | --- | --- |
| `name` | yes | string | 1 to 255 characters, matching `^[A-Za-z0-9][A-Za-z0-9._\-]*$`. Must start with a letter or a digit; dots, underscores and dashes are allowed afterwards. |
| `type` | on the **first** ingestion of a metric | string | One of `counter`, `gauge`, `timer`, `string`, `bool`. |
| `value` | yes | depends on the type | See the table below. |
| `timestamp` | no | integer or string | Epoch **milliseconds** or an ISO-8601 string. Defaults to the ingestion time. |
| `tags` | no | object | A flat object of non-empty string keys and scalar values. |
| `unit` | no | string | At most 32 characters. Stored on the metric, shown next to the value. |

### The metric types and their values

| Type | Accepted `value` | Stored as | Notes |
| --- | --- | --- | --- |
| `counter` | a finite number | numeric column | Booleans are rejected. |
| `gauge` | a finite number | numeric column | Booleans are rejected. |
| `timer` | a finite number `>= 0` | numeric column | A duration in milliseconds. A negative duration is rejected. |
| `string` | a string of at most 1024 characters | text column | |
| `bool` | `true` or `false` | numeric **and** text column | Stored as `1.0`/`0.0` and as `"true"`/`"false"`, so a numeric filter and a textual filter find the same point. |

`NaN` and `Infinity` are rejected for every numeric type.

### The auto-creation rule

A metric **does not exist** until its first ingestion. The first ingestion
creates it, and the type declared in that payload becomes the type of the
metric **forever**:

- The first ingestion of a new metric **must** declare `type`. Without it the
  answer is `400 MISSING_TYPE`.
- Every later ingestion may **omit** `type`; the stored type is inherited.
- If a later ingestion declares a **different** type, the answer is
  `409 METRIC_TYPE_MISMATCH`, and the envelope names the type the metric
  actually has (`expected`) and the one that was sent (`received`). Nothing
  is written.

### The tags

- At most **32** tags per data point.
- A tag key is a non-empty string of at most **64** characters.
- A tag value is a **JSON scalar**: a string, a number or a boolean. Nested
  objects and arrays are rejected — flatten them into separate tags.
- A numeric tag value must be finite.

### The timestamps

Two formats are accepted:

- **Epoch milliseconds** as an integer: `1700000000000`.
- **ISO-8601** as a string: `2023-11-14T22:13:20Z` (a `Z` suffix or an
  explicit offset). A timestamp without a timezone is read as UTC.

**The seconds-versus-milliseconds rule.** A numeric timestamp below
`100000000000` (1e11) is rejected with `400 AMBIGUOUS_TIMESTAMP`. 1e11
milliseconds is 1973-03-03, while epoch *seconds* only reach 1e11 in the
year 5138, so the threshold never rejects a realistic payload and always
catches the classic "I sent seconds" mistake. Multiply by 1000 and retry.

### Copy-pasteable examples

```bash
# A counter, with tags.
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "requests", "type": "counter", "value": 1, "tags": {"host": "web-1", "region": "us"}}'

# A gauge, with an explicit ISO-8601 timestamp and a unit.
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "cpu.load", "type": "gauge", "value": 0.73, "unit": "ratio", "timestamp": "2026-01-14T10:15:00Z"}'

# A timer (a duration in milliseconds) and an epoch millisecond timestamp.
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "http.latency", "type": "timer", "value": 128, "unit": "ms", "timestamp": 1768384500000}'

# A string metric.
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "deploy.status", "type": "string", "value": "ok", "tags": {"host": "web-1"}}'

# A bool metric.
curl -X POST http://127.0.0.1:5000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"name": "health.check", "type": "bool", "value": true, "tags": {"host": "web-1"}}'
```

### `GET /api/metrics`

Lists the known metrics with their data point count. Optional query
parameter: `name` (exact match).

```bash
curl http://127.0.0.1:5000/api/metrics
```

```json
{
  "metrics": [
    {"id": 1, "name": "requests", "type": "counter", "unit": null,
     "created_at_ms": 1700000000000, "data_point_count": 3}
  ],
  "count": 1
}
```

## Error contract

Every failure — a validation error, an unknown route, a wrong HTTP method, a
crash — answers with the **same envelope**:

```json
{
  "error": {
    "code": "METRIC_TYPE_MISMATCH",
    "message": "The metric already exists with the type 'counter', but the payload declares the type 'gauge'.",
    "field": "type",
    "expected": "counter",
    "received": "gauge",
    "hint": "The type of a metric is fixed when it is created; send the existing type 'counter' or omit the 'type' field to inherit it."
  }
}
```

- `code` is a stable, machine-readable identifier.
- `field` names the offending part of the request (`null` for a body-level
  error).
- `expected` and `received` describe the contract that was broken.
- `hint` tells the caller what to do next.

The API answers with that envelope as JSON. The web UI renders **exactly the
same information** as an HTML page (or as an error block inside the page the
user was on), with the code, the message, the field, the expected value, the
received value and the hint all **visible** — never hidden in a tooltip or a
collapsed element. A stack trace is never sent to a client; it stays in the
server log.

### Every error code

| Code | HTTP | When it happens |
| --- | --- | --- |
| `AMBIGUOUS_TIMESTAMP` | 400 | The numeric `timestamp` is below 1e11: it looks like epoch seconds, not milliseconds. |
| `BAD_REQUEST` | 400 | Werkzeug rejected the raw request before the application could parse it. |
| `DASHBOARD_NOT_FOUND` | 404 | No dashboard carries the requested id. |
| `HTTP_ERROR` | the HTTP status of the failure | Any other Werkzeug HTTP error (for example 413) that the application does not describe itself. |
| `INTERNAL_ERROR` | 500 | An unexpected crash. The traceback goes to the server log, never to the client. |
| `INVALID_AGGREGATION` | 400 | The `agg` parameter is not one of `sum`, `avg`, `min`, `max`, `count`, `last`. |
| `INVALID_AGGREGATION_FOR_TYPE` | 400 | The aggregation is known but not valid for the type of the metric (for example `sum` on a `string` metric). |
| `INVALID_BODY` | 400 | The request body is empty, or it is valid JSON but not a JSON object. |
| `INVALID_BUCKET` | 400 | The `bucket` is not `auto` and not a positive integer number of seconds. |
| `INVALID_CHART_TYPE` | 400 | The `chart_type` of a widget is not `line`, `bar`, `table` or `stat`. |
| `INVALID_FILTER_FIELD` | 400 | The `field` of a filter is not `value`, `ts` or `tags.<key>`. |
| `INVALID_FILTER_OPERATOR` | 400 | The `op` of a filter is not allowed for the kind of the field. |
| `INVALID_FILTERS` | 400 | The `filters` parameter is not a JSON array of filter objects. |
| `INVALID_FILTER_VALUE` | 400 | The `value` of a filter does not fit its operator (a `between` without two numbers, a text operator with a number, an invalid regular expression, ...). |
| `INVALID_GROUP_BY` | 400 | The `group_by` parameter of a query is present but empty or not a string. In the widget builder an empty value simply means "no grouping" and is not an error. |
| `INVALID_JSON` | 400 | The request body is not valid JSON. |
| `INVALID_METRIC_NAME` | 400 | The `name` is missing, not a string, empty, too long, or does not match the name pattern. |
| `INVALID_METRIC_TYPE` | 400 | The `type` is not a string, or it is not one of the five known types. |
| `INVALID_NAME` | 400 | The name of a dashboard or of a widget is missing, blank, not a string or too long. |
| `INVALID_ORDER` | 400 | The body of the reorder request does not carry `widget_ids`, or the ids do not match the widgets of the dashboard exactly. |
| `INVALID_TAGS` | 400 | The `tags` are not an object, a tag name is empty or too long, or a tag value is not a JSON scalar. |
| `INVALID_TIME_RANGE` | 400 | The `from` bound is not earlier than the `to` bound (query API), or the `time_range` of a widget is not one of `1h`, `6h`, `24h`, `7d`, `30d` (web UI). |
| `INVALID_TIMESTAMP` | 400 | The `timestamp` is a boolean, a fractional number, a negative number, or a string that is not an ISO-8601 date. |
| `INVALID_UNIT` | 400 | The `unit` is not a string or is longer than 32 characters. |
| `INVALID_VALUE` | 400 | The `value` is missing, or it does not match the type of the metric. |
| `METRIC_CONFLICT` | 409 | The metric could not be created because of a concurrent write. Retry. |
| `METRIC_NOT_FOUND` | 404 | No metric carries the requested name. |
| `METRIC_TYPE_MISMATCH` | 409 | The payload declares a type that contradicts the type of the existing metric. |
| `METHOD_NOT_ALLOWED` | 405 | The HTTP method is not allowed for that URL. The allowed methods are in the `expected` field and in the `Allow` header. |
| `MISSING_PARAMETER` | 400 | The `metric` parameter of a query is missing or is not a string. |
| `MISSING_TYPE` | 400 | The metric does not exist yet, so its type must be declared on the first ingestion. |
| `NON_FINITE_VALUE` | 400 | The `value` is `NaN` or `Infinity`. |
| `NOT_FOUND` | 404 | The requested URL does not exist. |
| `TOO_MANY_TAGS` | 400 | The data point carries more than 32 tags. |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | The request body was not sent as JSON. |
| `WIDGET_NOT_FOUND` | 404 | No widget carries the requested id. |

## Query API

### `GET /api/query` and `POST /api/query`

Both methods accept exactly the same parameters; `GET` reads them from the
query string and `POST` reads them from a JSON body. The `POST` form exists
so the UI can send complex filters without URL-encoding gymnastics.

| Parameter | Required | Default | Meaning |
| --- | --- | --- | --- |
| `metric` | yes | — | The name of the metric to query. |
| `from` | no | `to - 24h` | The start of the range, in epoch milliseconds or as an ISO-8601 string. |
| `to` | no | now | The end of the range, in epoch milliseconds or as an ISO-8601 string. |
| `agg` | no | `avg` | `sum`, `avg`, `min`, `max`, `count` or `last`. |
| `bucket` | no | `auto` | The bucket size **in seconds**, or `auto` to pick it from the range. |
| `group_by` | no | — | The name of a tag. The answer holds one series per tag value. |
| `filters` | no | `[]` | A JSON array of filter objects (see below). |

```bash
# The simplest query.
curl "http://127.0.0.1:5000/api/query?metric=requests"

# A grouped query with a filter, over an explicit range.
curl -X POST http://127.0.0.1:5000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"metric": "requests", "from": 1700000000000, "to": 1700003600000,
       "agg": "sum", "bucket": 60, "group_by": "host",
       "filters": [{"field": "value", "op": ">", "value": 10}]}'
```

### The response shape

```json
{
  "metric": {"id": 1, "name": "requests", "type": "counter", "unit": null},
  "agg": "avg",
  "bucket_sec": 60,
  "group_by": "host",
  "from_ms": 1700000000000,
  "to_ms": 1700003600000,
  "point_count": 42,
  "bucket_count": 60,
  "series": [
    {"key": "web-1", "points": [{"ts": 1700000000000, "value": 12.0, "count": 3}]},
    {"key": null, "points": [{"ts": 1700000000000, "value": 1.0, "count": 1}]}
  ]
}
```

- `series` always holds **at least one** series. An empty result is a
  legitimate answer, not an error.
- `key` is the tag value of the series, or `null` for the points that do not
  carry the tag. The series without a tag comes first, the others are
  sorted by their key.
- `points` holds the buckets that carry data, sorted by timestamp. A bucket
  with no point is simply absent.

### The automatic bucket ladder

With `bucket=auto` (the default), the bucket size is picked from the length
of the requested range. The first band that covers the range wins:

| Requested range | Bucket size |
| --- | --- |
| up to 2 hours | 60 seconds |
| up to 12 hours | 300 seconds (5 minutes) |
| up to 2 days | 900 seconds (15 minutes) |
| up to 14 days | 3600 seconds (1 hour) |
| up to 90 days | 21600 seconds (6 hours) |
| longer than 90 days | 86400 seconds (1 day) |

Buckets are aligned to the epoch, so two queries with the same bucket size
always agree on where a bucket starts.

### The aggregation per metric type

| Metric type | Valid aggregations |
| --- | --- |
| `counter` | `sum`, `avg`, `min`, `max`, `count`, `last` |
| `gauge` | `sum`, `avg`, `min`, `max`, `count`, `last` |
| `timer` | `sum`, `avg`, `min`, `max`, `count`, `last` |
| `string` | `count`, `last` |
| `bool` | `sum`, `avg`, `min`, `max`, `count`, `last` |

- `count` counts the points of the bucket, whatever the type.
- `last` returns the value of the most recent point of the bucket (ties are
  broken by the point id).
- For a `bool` metric, `sum` is the number of `true` points and `avg` is
  their fraction.
- An aggregation that is not valid for the type of the metric is rejected
  with `400 INVALID_AGGREGATION_FOR_TYPE`.

### The filter contract

A filter is a small, explicit document:

```json
{"field": "value", "op": ">", "value": 10}
```

| Field | Kind | Allowed operators |
| --- | --- | --- |
| `value` | the kind of the metric type | see below |
| `ts` | numeric (the timestamp) | `=`, `!=`, `>`, `>=`, `<`, `<=`, `between` |
| `tags.<key>` | textual (the tag value) | `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`, `regex` |

The kind of the `value` field follows the type of the metric:

| Metric type | Kind of `value` | Allowed operators |
| --- | --- | --- |
| `counter`, `gauge`, `timer` | numeric | `=`, `!=`, `>`, `>=`, `<`, `<=`, `between` |
| `string` | textual | `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`, `regex` |
| `bool` | boolean | `is_true`, `is_false` |

Rules:

- Filters are **ANDed**: a point is kept only when it satisfies all of them.
- `between` needs **exactly two numbers**, and the low bound must not be
  greater than the high bound.
- `is_true` and `is_false` ignore the value of the filter.
- A tag that is **not present** on a point never matches: the point simply
  carries no value to compare.
- A `regex` is a Python regular expression; an invalid one is rejected with
  `400 INVALID_FILTER_VALUE`.
- The `filters` parameter is a JSON **array**. A bare array is also accepted
  when reading a stored widget.

### `GET /api/metrics/<metric_name>/tags`

Lists the tag keys of a metric and a sample of their values. The widget
builder uses it to offer the tag keys as filter fields.

```bash
curl http://127.0.0.1:5000/api/metrics/requests/tags
```

```json
{"metric": "requests", "keys": ["host", "region"], "sample_values": {"host": ["web-1"], "region": ["us"]}}
```

The discovery reads at most the **1000 most recent** data points and samples
at most **10** values per key, so it stays cheap on a large metric.

## Web UI

The UI is server-rendered. Open <http://127.0.0.1:5000/> after starting the
app.

| Page | URL | What it does |
| --- | --- | --- |
| Dashboard list | `/` and `/dashboards` | Lists the dashboards and creates new ones. |
| Dashboard | `/dashboards/<id>` | Shows the widgets of a dashboard, in their order. |
| Widget builder | `/dashboards/<id>/widgets/new` and `/widgets/<id>/edit` | Creates or edits a widget. |
| Metric overview | `/metrics` | Lists the metrics with their type, unit and data point count. |

### Creating a dashboard

On `/dashboards`, use the **New dashboard** form on the right. The name is
the only thing it needs. The browser lands on the new dashboard.

### Creating a widget

On a dashboard, press **Add widget**. The builder asks for:

| Field | Meaning |
| --- | --- |
| **Title** | The name shown above the chart. |
| **Metric** | The metric to visualize. The list shows the type of every metric. |
| **Chart type** | `line`, `bar`, `table` or `stat`. |
| **Aggregation** | The aggregation to apply. |
| **Bucket** | `auto`, or a positive number of seconds. |
| **Time range** | `1h`, `6h`, `24h`, `7d` or `30d`. The range is resolved against the browser clock. |
| **Group by tag** | The tag key to group by, or no grouping. |
| **Filters** | The filters, one row per filter. |

### The operators that apply to which metric type

The builder is **type aware**: the type of the selected metric decides what
is offered.

- The **aggregation list** is narrowed to the aggregations the type accepts:
  a `string` metric only offers `count` and `last`; every other type offers
  the six aggregations.
- The **filter operators** follow the kind of the selected field:
  - the `value` field of a `counter`, `gauge` or `timer` offers
    `=`, `!=`, `>`, `>=`, `<`, `<=`, `between`;
  - the `value` field of a `string` metric offers `=`, `!=`, `contains`,
    `not_contains`, `starts_with`, `ends_with`, `regex`;
  - the `value` field of a `bool` metric offers `is_true` and `is_false`
    (the value input is disabled);
  - the `ts` field always offers the numeric operators;
  - a `tags.<key>` field always offers the textual operators.

The builder reuses the parsers of the query API, so **a widget can never
store a configuration the query API would reject**: an invalid aggregation
or a malformed filter is refused by the server with the explicit error
envelope, and the page shows the code, the message, the field, the expected
value, the received value and the hint.

### Drag & drop reordering

Every widget of a dashboard has a handle (the `⠿` glyph in its header).
Drag a widget onto another one and the new order is saved immediately; the
page confirms it with *"The new order was saved."* under the title. The order
survives a reload. The server refuses an order that does not contain
exactly one id per widget of the dashboard, so a widget can never be lost by
a reordering.

### Where the tag keys come from

The **Group by tag** list and the tag fields of the filters are built from
`GET /api/metrics/<metric_name>/tags`, which discovers the tag keys from the
most recent data points of the metric. A metric that has never received a
point with a tag offers no tag key.

## Migrations

The schema is managed by Alembic. The migrations run on SQLite and on
MariaDB with the very same code, and every one of them is reversible.

```bash
# Apply every pending migration.
.venv/bin/alembic upgrade head

# Create a migration from the difference between the models and the database.
.venv/bin/alembic revision --autogenerate -m "describe the change"

# Check that the models and the migrations agree (no output means "in sync").
.venv/bin/alembic check

# Undo the last migration.
.venv/bin/alembic downgrade -1
```

`alembic check` prints `No new upgrade operations detected.` when the models
and the migrations are in sync. That is the check to run before every
commit: an uncommitted change of a model shows up there as a pending
operation.

The revisions are:

| Revision | What it does |
| --- | --- |
| `0001_initial` | The `metrics`, `data_points`, `dashboards` and `widgets` tables. |
| `0002_widget_group_by` | Adds the `group_by` column to `widgets` and moves the value of the legacy configuration documents into it. |

### Moving a database from SQLite to MariaDB

1. Stop the application and make a copy of the SQLite file:
   `cp instance/metrics.db instance/metrics.db.bak`.
2. Create the empty schema on the server:
   ```bash
   DATABASE_URL="mysql+pymysql://user:password@host:3306/dbname" .venv/bin/alembic upgrade head
   ```
3. Copy the rows over. The schema is portable, so a plain dump and restore
   works; the timestamp columns are epoch milliseconds and the tags are JSON
   text, so no conversion is needed:
   ```bash
   sqlite3 instance/metrics.db .dump > dump.sql
   # Review the dump, then load it into MySQL/MariaDB.
   mysql -u user -p dbname < dump.sql
   ```
   The `sqlite_sequence` statements of the dump are MySQL-incompatible:
   remove them before loading.
4. Point the application at the new database:
   `export DATABASE_URL="mysql+pymysql://user:password@host:3306/dbname"`.
5. Start the application and check `/health`.

## Testing

```bash
.venv/bin/python -m pytest -q
```

The suite covers the ingestion validation, the error envelope, the
aggregation semantics, the filter language, the widget persistence, the drag &
drop ordering and the migrations (including the data migration of
`0002_widget_group_by` and its reversal). Every test runs against its own
throwaway SQLite database, so the suite never touches `instance/metrics.db`.

The JavaScript has no unit test framework; the front end is checked by
reading it, by `node --check` and by one Node script that exercises the
shipped `app/static/app.js` (skipped automatically when `node` is not
installed):

```bash
node tests/js/filter_value_test.js
```

## Known limitations

Honest list of what this system does **not** do:

- **No authentication, no authorization, no multi-tenancy.** Anything that
  reaches the port can read and write.
- **No alerting.** No monitors, no thresholds, no notifications.
- **No rollups, no downsampling, no background scheduler.** Every query
  reads and aggregates the raw points of the range. A large metric with a
  small bucket is expensive.
- **Tags are filtered and grouped in Python, not in SQL.** The tags live in
  a JSON document inside a `TEXT` column, because SQLite and MariaDB do not
  agree on JSON operators. Only the metric and the time range are pushed
  down to the database; the rows of the range are read and filtered in the
  application.
- **`string` metrics only support `count` and `last`.** A text cannot be
  added up.
- **It is a single-process monolith.** The engine is scoped to the
  application context, so there is no built-in horizontal scaling, no read
  replica and no cache layer.
- **The UI has NOT been visually verified in a browser.** The automated suite
  covers the rendered HTML and the API, but no human has opened the pages
  yet. See `docs/MANUAL_VERIFICATION.md` for the checklist to run.
- **The tag discovery is a sample.** The tag keys are read from the 1000 most
  recent points of a metric, so a tag that only appears in old points is not
  offered.
- **No rate limiting, no ingestion tokens.** Any client can push data.
