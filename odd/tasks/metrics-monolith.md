# Feature: laurel metrics monolith

## Objective

Build a Datadog-like custom metrics system as a single Flask monolith with server-rendered
Jinja2 templates. It must (1) accept ingestion of primitive-typed metric data over HTTP and
(2) let the user create widgets that visualize that data over a time range, with filters
appropriate to each data type. Errors must be explicit and diagnostic, never generic.

## Problem

There is no monitoring/metrics platform available for this project's data. The user needs a
place to push raw measurements and then explore them through self-built widgets without
learning a query language.

## Why this approach

- Flask monolith with Jinja templates keeps the whole system in one deployable unit with no
  build step, no SPA and no client-side framework to learn.
- SQLite for local development and MariaDB for production, selected through one
  `DATABASE_URL`, keeps the domain layer engine-agnostic (SQLAlchemy 2.0).
- Aggregation is computed on the fly with automatic bucket sizing, so there is no rollup
  scheduler, no backfill logic and no precomputed state to keep consistent.

## Scope

### In scope

- `POST /api/ingest` — one JSON metric point per request.
- Metric types: `counter`, `gauge`, `timer` (+ `string`, `bool` as primitive data points).
- Tags (JSON key/value) on each data point.
- `GET /api/query` — time series aggregation with automatic bucket sizing.
- Web UI: dashboards, widget builder, drag & drop reordering, charts via Chart.js.
- Filters by data type: numeric range/comparison, string operators, boolean, timestamp.
- Alembic migrations working on both SQLite and MariaDB.
- Explicit error envelope for every failure, JSON for the API and rendered HTML for the UI.
- Automated tests covering ingestion validation, query aggregation and the error contract.

### Out of scope

- Authentication, authorization, multi-tenancy, users, sessions.
- Alerting, monitors, notifications, webhooks.
- Anomaly detection, forecasting, machine learning.
- Log management, tracing, APM, infrastructure metrics collection.
- Pre-computed rollups, downsampling jobs, background schedulers.
- Public API keys, ingestion tokens, rate limiting per client.
- Multi-tenant namespaces, org/team management.
- Horizontal scaling, read replicas, caching layer.

## Constraints

- Single Flask application. No additional services or processes.
- Server-rendered HTML via Jinja2 templates. No SPA, no React/Vue build step.
- Python 3.14.6, virtualenv at `.venv` (system python has no pip).
- Local development: SQLite file. Production: MariaDB reached through `DATABASE_URL`.
- Timestamps stored as UTC epoch milliseconds in a `BIGINT` column on both engines, so no
  dialect-specific timezone or precision differences.
- Tags stored as JSON serialized into a `TEXT` column (SQLAlchemy `JSON` type), because
  MariaDB aliases `JSON` to `LONGTEXT` with different operator semantics.
- No authentication of any kind.
- Target test runner: `pytest` via `.venv/bin/python -m pytest`.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Metric semantics | counter, gauge, timer + tags | User selected. Gives real counter/timer semantics and per-tag segmentation. |
| Ingestion contract | JSON object, one point per request | User selected. Simplest contract to generate from any client. |
| Aggregation | On-the-fly, automatic bucket per range | User selected. No scheduler, no state, no backfill. |
| Widgets | line, bar, table, stat + drag & drop grid | User selected. |
| Metric creation | Auto-create on first ingest, type declared in payload | User selected. Type mismatch against an existing metric is an explicit error. |
| Database | SQLite locally, MariaDB in production | User selected. Production uses only environment variables against an existing instance. |
| Schema | Alembic migrations | User selected. Reproducible, reversible, required for SQLite → MariaDB migration without data loss. |
| RDD / native review | Off (global switch) | Verified via `gentle-ai review mode status`. Delivery follows ordinary repository policy. |

## Authorized scope

- Create the full application source tree, tests, migrations, docs and configuration files
  inside this repository.
- Branch: `feature/metrics-monolith` (branched from an empty `main`).
- No push, no pull request, no merge. Those remain the user's decisions.

## Acceptance criteria

1. `POST /api/ingest` with a valid counter point returns `201` and persists the point.
2. Ingesting the first point of a new metric creates the metric with the declared type.
3. Ingesting a point whose declared type differs from the existing metric type returns
   `409` with `code = METRIC_TYPE_MISMATCH` naming expected vs received type.
4. Any invalid payload returns `400` with a machine-readable `code`, the offending `field`,
   the `expected` shape and a human-readable `hint`. No raw stack trace, no bare `500`.
5. `GET /api/query` returns a series of `{ts, value}` points bucketed automatically from the
   requested range, with `sum`, `avg`, `min`, `max`, `count` and `last` aggregations.
6. Widgets can be created from the UI with metric, chart type, aggregation, bucket, time
   range and type-aware filters, and are persisted and rehydrated on dashboard reload.
7. Filters support numeric comparison and `between`, string equality / contains / prefix /
   regex, boolean true-false and timestamp before-after.
8. Dashboards reorder widgets by drag & drop and the new order survives a reload.
9. The full test suite passes on SQLite, and the application boots against a MariaDB
   `DATABASE_URL` with the same Alembic migrations.
10. Every failure path in the UI renders an explicit message naming the field and the cause.

## Tasks

### T1 — Scaffold, configuration and data model

- `app/__init__.py` with `create_app()` application factory.
- Configuration reads `DATABASE_URL`; defaults to a local SQLite file when unset.
- SQLAlchemy 2.0 models: `Metric`, `DataPoint`, `Dashboard`, `Widget`.
- `ts_utc_ms` as `BIGINT`; tags as JSON-serialized `TEXT`; indexes on `(metric_id, ts_utc_ms)`.
- Alembic initialized with an initial migration that applies cleanly on SQLite and MariaDB.
- Unit selection `WAL` only for SQLite, never for MariaDB.
- Acceptance: app imports, `alembic upgrade head` succeeds on SQLite, models are creatable.

### T2 — Ingestion API and explicit error contract

- `POST /api/ingest` accepting `{name, type, value, timestamp?, tags?, unit?}`.
- Strict per-type validation of `value`, `timestamp`, `tags`.
- Auto-create metric on first ingest; explicit `METRIC_TYPE_MISMATCH` afterwards.
- Central error module producing one envelope shape for API and UI.
- Acceptance: criteria 1, 2, 3, 4.

### T3 — Query API and aggregation

- `GET /api/query` with `metric`, `from`, `to`, `agg`, `bucket`, `group_by`.
- Automatic bucket sizing from the requested range.
- Support `sum`, `avg`, `min`, `max`, `count`, `last`.
- `group_by` on a tag returns one series per tag value.
- Acceptance: criterion 5.

### T4 — Web UI: dashboards, widget builder, drag & drop

- Jinja templates: base, dashboard list, dashboard detail, widget form, error page.
- Widget builder: metric picker, chart type, aggregation, bucket, time range, filters.
- Filters rendered according to the selected metric's data type.
- Chart.js rendering for line and bar; table and stat renderings.
- Drag & drop reordering with the new order persisted.
- Vendored JS libraries (no CDN dependency at runtime).
- Acceptance: criteria 6, 7, 8.

### T5 — Tests, documentation and error hardening

- pytest suite covering ingestion validation, error envelope, aggregation and widget
  persistence.
- README with setup, configuration, ingestion examples and API reference.
- Hardening pass: every unhandled route and invalid input produces an explicit message.
- Acceptance: criterion 9, 10.

## Progress

| Task | Route | Status | Evidence |
| --- | --- | --- | --- |
| T1 | delegated writer | done | `1ff66da` — app factory, models, Alembic initial migration; `alembic upgrade head` + `alembic check` clean on SQLite, `--sql` renders valid MariaDB DDL |
| T2 | delegated writer | done | 33 pytest tests pass; curl smoke: 201 created / 409 METRIC_TYPE_MISMATCH / 400 INVALID_VALUE / 200 metrics list |
| T3 | delegated writer | done | 75 pytest tests pass (42 new); curl smoke: auto bucket, explicit bucket, `group_by=host`, filters, string/bool metrics, 4 error envelopes verified |
| T4 | delegated writer | done | 98 pytest tests pass (23 new); curl smoke: dashboard/widget CRUD, explicit 400 for an invalid aggregation and for malformed filters, drag & drop order persisted, tag discovery endpoint |
| T5 | delegated writer | pending | — |

## Verification

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m alembic upgrade head`
- Manual smoke test of the ingestion and query endpoints via `curl`.

## Notes and rationale for accepted changes

- Timestamp representation was chosen as epoch milliseconds because SQLite and MariaDB
  `DATETIME` disagree on timezone handling and sub-second precision, and the same column
  must behave identically on both engines.
- Tags are serialized into `TEXT` rather than using MariaDB's `JSON` alias, because the
  alias carries no operator guarantees and would make the SQLite path diverge.
- The `widgets` table has no `group_by` column, and the schema must not change in this
  task, so the whole configuration of a widget lives in the single JSON document of the
  `filters` column: `{"group_by": "host", "filters": [...]}`.  Every filter object of the
  array is stored verbatim, exactly as the filter contract describes it, and a bare JSON
  array is still accepted when reading.  Adding a real `group_by` column (with its
  Alembic migration) is the natural follow-up.
- The widget builder reuses the parsers of the query API (`parse_agg`, `parse_bucket`,
  `parse_group_by` and `app.query.filters.parse_filters`), so a widget can never store a
  configuration the query API would reject.
