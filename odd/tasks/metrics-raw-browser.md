# Feature — Raw Data Browser with a dynamic time picker

## Objective

Let a user open the raw data points ingested through the API for any metric,
read them in a table, and understand their temporality: a dynamic time range
picker in the style of Grafana or Datadog, pagination over the points, a
manual refresh, a CSV download, and a live long poll that brings the new
points in every two seconds.

## Problem

Today the only way to inspect what the ingestion API stored is the query API,
which answers aggregated buckets. The raw points, their exact timestamps, the
tags they carry and the moment they were received are invisible. A consumer
that needs to debug what it sent — or what the platform received — has to
guess.

The temporal dimension is also missing: the widget time range is a fixed
list resolved against the browser clock, so there is no quick range, no
absolute range, and no way to follow a metric while it is being written.

## Why

The ingestion API is public and the platform is deployed. When a consumer
reports "my temperature is not showing up", the first question is what the
server actually stored. Answering it must not require shell access to the
database.

## Scope

### In scope

- A page `GET /metrics/<metric_name>/raw` that lists the raw points of one
  metric in a table.
- A JSON API that serves the table with pagination and that holds a long
  poll open for the new points.
- A dynamic time range picker: quick relative ranges, a custom absolute
  range, and a refresh interval selector.
- Pagination over the points with a selectable page size.
- A manual refresh button and a CSV download of the current range.
- A link from every widget and from the metrics overview to the raw page of
  its metric.

### Out of scope

- No schema change. The composite index `(metric_id, ts_utc_ms)` already
  exists in `0001_initial_schema`.
- No new aggregation, no new filter operators, no change to the ingestion or
  the query contract.
- No authentication. The platform has none today.

## Constraints

- Read only over `data_points` and `metrics`. The feature never writes.
- The raw table must not load the whole window in memory: the pagination
  happens in SQL with `limit` and `offset`.
- The value shown in the table is decided by the metric type, because the
  point itself does not carry it. A boolean point stores both
  representations.
- The relative ranges are resolved against the browser clock, which is what
  the widget time range already does. The two must agree.
- `5m` and `15m` must be added to both the server whitelist
  (`validation.TIME_RANGES`) and the front end map (`RANGE_MS`), or the
  picker and the widget form disagree.
- Every failure answers the shared six key error envelope.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Feature identity | `metrics-raw-browser` | Reuses the same identity across the doc, the mirror and the commits. |
| Route | `master` | The user asked for the feature on the same master branch. |
| Delivery | Direct work-unit commits, no pull request | The user asked for direct commits on master. |
| TDD mode | Off — standard mode | No strict TDD configuration exists for this project. |
| Test runner | `.venv/bin/python -m pytest -q` | The runner the project already uses. |
| JSON endpoint | A new `app/raw` blueprint with `url_prefix="/api"` | The error envelope is only served as JSON under `/api/`. |
| Page and CSV | The `web` blueprint | The HTML routes live at the root of the site. |
| Shared logic | `app/raw/service.py` | The page, the CSV and the JSON all read the same points. |
| Table order | Newest first by default | A log style explorer reads the latest first. `order=asc` is available. |
| Live updates | Long poll on the point id | The auto increment id is monotonic, so no point is missed nor repeated. |
| Pagination limit | 500 points per page | A page larger than that stops being a table. |

## Authorized scope

- `app/raw/` (new package: service and routes).
- `app/web/routes.py` (two routes: the page and the CSV).
- `app/web/validation.py` (extend `TIME_RANGES`).
- `app/templates/` (`raw_data.html`, plus the links in `dashboard.html` and
  `metrics.html`, plus the script tag in `base.html`).
- `app/static/` (`raw.js`, plus the styles it needs in `style.css`, plus the
  new ranges in `app.js`).
- `tests/test_raw.py` (new) and `tests/test_frontend.py` (the new script).
- `README.md` and `docs/MANUAL_VERIFICATION.md`.

Nothing else may be modified.

## Tasks

- [x] **T1 — Raw service and JSON API.** `app/raw/service.py` with the
  point query, the count, the last id, the long poll wait and the CSV
  serialization; `app/raw/routes.py` with `GET /api/raw` and
  `GET /api/raw/wait`; the blueprint registered in `app/__init__.py`.
  *Route: delegated writer.*
  *Checks: `.venv/bin/python -m pytest -q tests/test_raw.py`.*
- [x] **T2 — Page, picker, pagination and live updates.** The page route,
  the CSV route, `raw_data.html`, `raw.js`, the styles, the links from the
  widgets and the metrics overview, and the new ranges.
  *Route: delegated writer.*
  *Checks: `.venv/bin/python -m pytest -q`.*
- [ ] **T3 — Documentation.** The README sections for the page, the two API
  endpoints and the new error codes; the manual verification steps.
  *Route: delegated writer.*
  *Checks: `.venv/bin/python -m pytest -q` and `.venv/bin/alembic check`.*

## Acceptance criteria

1. `GET /metrics/<metric>/raw` renders a table with one row per point and
   the columns: timestamp, value, tags, received at.
2. The value column shows the value of the metric type: the number for a
   numeric metric, the text for a string metric, `true` or `false` for a
   boolean one.
3. The table is paginated, the page size is selectable, and the total count
   of the points in the range is shown.
4. The time picker offers the quick ranges `5m`, `15m`, `1h`, `6h`, `24h`,
   `7d` and `30d`, and a custom absolute range with a start and an end.
5. Changing the range, the page size or the metric reloads the table.
6. The refresh button reloads the table on demand.
7. With a refresh interval of two seconds, the page holds a long poll open
   and the new points appear in the table without a manual reload.
8. The CSV download answers every point of the current range, not only the
   visible page.
9. An unknown metric answers `404 METRIC_NOT_FOUND`.
10. An invalid page, page size or order answers `400` with the shared
    envelope.
11. The existing suite stays green and `alembic check` reports no drift.

## Applicable checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/alembic check
```

## Forecast

Roughly 1250 authored changed lines. It exceeds the 400 line planning
heuristic because the feature is one vertical slice: an API without a page
serves nothing, and a page without the API cannot be built. The line count
is not a reason to split the behavior; the work is split into three
reviewable commits instead.

## Progress

| Task | Outcome | Evidence |
| --- | --- | --- |
| T1 | Implemented | Commit `648f4f0`. `.venv/bin/python -m pytest -q tests/test_raw.py` → `15 passed in 0.71s`; full suite → `134 passed in 13.40s`. |
| T2 | Implemented | `.venv/bin/python -m pytest -q tests/test_raw.py tests/test_frontend.py` → `33 passed in 1.65s`; full suite → `148 passed in 12.03s`. Commit id is recorded after the commit. |
| T3 | Pending | — |
