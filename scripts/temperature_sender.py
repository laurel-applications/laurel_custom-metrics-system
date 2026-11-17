#!/usr name of the repository. The current branch is `master` and it MUST stay `master` (the user asked for the feature on the same master branch). Do NOT create another branch, do NOT merge, do NOT push.

## Skills to load before work

Read these exact files BEFORE any task-specific work:

- `/home/lobaton/.config/opencode/skills/work-unit-commits/SKILL.md`
- `/home/lobaton/.config/opencode/skills/cognitive-doc-design/SKILL.md`

## Read first

Read `odd/tasks/metrics-raw-browser.md` in full. It is the source of truth for the objective, scope, decisions, tasks, acceptance criteria and checks. Follow it exactly.

## TDD mode

TDD mode is OFF (standard mode). Write the behavior and its tests together, then run the checks. Do not attempt a strict RED-first cycle.

## Test runner

`.venv/bin/python -m pytest -q` (from the repository root).

## What you must build

Three work units, each ending in its own commit with a Conventional Commit message.

### T1 — Raw service and JSON API

New package `app/raw/`:

- `app/raw/service.py`
  - `RawPoint` dataclass: `id`, `ts_ms`, `received_at_ms`, `value` (typed by metric type: number for counter/gauge/timer, the text for string, a real boolean for bool), `tags` (decoded dict).
  - `list_points(metric, from_ms, to_ms, limit, offset, order)` → `(list[RawPoint], total)`. The pagination MUST happen in SQL with `.limit()` / `.offset()`; never materialize the whole window. Order by `ts_utc_ms` then `id`, `desc` by default, `asc` when asked.
  - `count_points(metric_id, from_ms, to_ms)` → int, with the same filters.
  - `latest_id(metric_id)` → int | None.
  - `wait_for_points(metric_id, since_id, from_ms, to_ms, timeout_s)` → `(list[RawPoint], waited_ms)`. It holds the request open until a point with `id > since_id` exists inside the range, polling in short sleeps (≈100 ms) until the timeout elapses. The id is the watermark because the auto increment id is monotonic, so no point is missed nor repeated.
  - `to_csv(points)` → CSV text. Header: `id,ts_utc,received_at_utc,value,tags`. Timestamps as ISO-8601 UTC. Tags as compact JSON.
  - The value typing by metric type: the point does NOT carry the type, so the service must be given the `Metric` row. A boolean point stores BOTH `value_num` (1.0/0.0) and `value_str` ("true"/"false") — expose a real boolean.

- `app/raw/routes.py`
  - `bp = Blueprint("raw", __name__, url_prefix="/api")`
  - `GET /api/raw` — paginated points.
    Query: `metric` (required), `from` (optional), `to` (optional), `limit` (optional, default 50, max 500), `offset` (optional, default 0), `order` (optional, `desc` default, `asc` allowed).
    Response 200:
    ```json
    {"metric": {"id": 1, "name": "temperature", "type": "gauge", "unit": "C"},
     "from_ms": 1794805726546, "to_ms": 1794892126546,
     "order": "desc", "total": 68, "limit": 50, "offset": 0, "latest_id": 68,
     "points": [{"id": 68, "ts_ms": 1794892126546, "received_at_ms": 1794892126546, "value": 24.5, "tags": {"sensor": "ambient"}}]}
    ```
  - `GET /api/raw/wait` — the long poll.
    Query: `metric` (required), `since_id` (optional, default 0), `from`, `to`, `timeout` (optional seconds, default 2, max 10).
    Response 200: `{"metric": {...}, "latest_id": 68, "waited_ms": 2000, "points": [...]}` with an empty `points` list when nothing arrived before the timeout.

- Register the blueprint in `app/__init__.py` (`BLUEPRINTS` tuple) so the app serves it.

- `tests/test_raw.py` — a new file with the tests of this work unit.

Error contract, reuse the existing machinery:
- Unknown metric → `404 METRIC_NOT_FOUND`.
- `from >= to` → `400 INVALID_TIME_RANGE` (the code already exists in `app/query/routes.py`).
- Negative `offset` → `400 INVALID_PAGE`.
- `limit` below 1 or above 500 → `400 INVALID_PAGE_SIZE`.
- `order` outside `asc`/`desc` → `400 INVALID_ORDER`.
- `timeout` above 10 → `400 INVALID_TIMEOUT`.
Every error MUST carry all six envelope keys (`code`, `message`, `field`, `expected`, `received`, `hint`) with a non-empty hint.

### T2 — Page, picker, pagination and live updates

- `app/web/routes.py`
  - `GET /metrics/<metric_name>/raw` — the page. Accepts `range` (quick relative range), `from`, `to`, `page`, `size`. It renders the picker with the current selection AND the first page of rows server-side, so the page works before the JavaScript runs.
  - `GET /metrics/<metric_name>/raw.csv` — the download. Same range parameters as the page, no pagination. It answers EVERY point of the range with `Content-Type: text/csv` and a `Content-Disposition` attachment. If the range holds more than 10000 points, answer `400 EXPORT_TOO_LARGE` with the envelope.
- `app/templates/raw_data.html` — the page. It extends `base.html` and imports `error_block` from `_error_block.html`.
- `app/static/raw.js` — the behaviour. Register it in `app/templates/base.html` with `url_for('static', filename='raw.js')`; otherwise it never loads.
- `app/static/style.css` — the styles the new page needs, reusing the existing class vocabulary and CSS variables. Do NOT introduce new colours.
- `app/web/validation.py` — extend `TIME_RANGES` with `5m` and `15m` (keep the existing ones).
- `app/static/app.js` — extend the `RANGE_MS` map with `5m` and `15m`, so the front end and the server agree.
- Links: every widget in `app/templates/dashboard.html` gets a link to the raw page of its metric, and every row of `app/templates/metrics.html` gets one too.

The picker must offer:
- Quick relative ranges: `5m`, `15m`, `1h`, `6h`, `24h`, `7d`, `30d`.
- A custom absolute range with a start and an end (`datetime-local` inputs).
- A refresh interval selector: `off`, `2s`, `5s`, `10s`, `30s`, `1m`.
- The current selection is reflected in the controls.

The table must show the columns: timestamp of the measurement, value, tags, received at, and the id.

Behaviour:
- Changing the range, the page size or the metric reloads the table.
- A refresh button reloads the table on demand.
- With a refresh interval of two seconds, the page holds the long poll open and the new points appear in the table WITHOUT a manual reload. Use a `setTimeout` recursion, NOT `setInterval`, so two requests never overlap. The default refresh interval is `2s`.
- The relative ranges are resolved against the BROWSER clock, exactly like the widget time range already does.
- A live indicator shows the last update time and how many new points arrived.

### T3 — Documentation

- `README.md`: the new page in the web UI section, the two new API endpoints, the new error codes in the error table, and the new time ranges where the ranges are listed.
- `docs/MANUAL_VERIFICATION.md`: the manual steps of the new page (open it, change the range, paginate, refresh, watch the live updates, download the CSV).

## Conventions you MUST follow

This codebase has strict conventions. Match them exactly.

**Python**
- `from __future__ import annotations` at the top of every module.
- Module docstring explaining WHY, not what. Every function has a docstring; private helpers start with an imperative verb ("Return …").
- Type hints everywhere. Constants are UPPER_SNAKE at the module top with a `#:` comment.
- Private helpers are `_snake_case` module functions.
- Never `abort()`. Never a bare `raise ValueError` in a route. Raise `ApiError(code, message, field=..., expected=..., received=..., hint=...)`.
- The JSON endpoints MUST live under the `/api` path so the shared error handler answers JSON instead of HTML.
- The relative ranges resolve against the browser clock, like the existing widget time range.

**Templates**
- `{% extends "base.html" %}` + `{% from "_error_block.html" import error_block %}`; override only `title` and `content`.
- Structure: `section.panel` → `div.panel-head` (`h1.page-title`, `p.page-subtitle`, `div.panel-actions`) → `error_block` → content → `div.empty-state` for the empty case.
- Timestamps via `{{ value | ts }}`. Errors via `{{ error.expected | jvalue }}`.
- Hooks for JavaScript use `data-role="..."`. JSON to JS via `data-config='{{ ... | tojson }}'`.
- The CSS classes that already exist and must be reused: `.panel`, `.panel-head`, `.page-title`, `.page-subtitle`, `.panel-actions`, `.split`, `.split-main`, `.split-side`, `.muted`, `.table`, `.table th`, `.table td`, `.table .num`, `.form`, `.form-grid`, `.field`, `.field-label`, `.field-hint`, `.actions`, `.button`, `.button-ghost`, `.button-danger`, `.button-small`, `.error-block`, `.error-code`, `.error-fields`, `.empty-state`, `.widget-error`, `.grid-status`, `.grid-status.is-error`, `.code-block`.

**JavaScript** (`app/static/raw.js`)
- A separate file, one IIFE, `"use strict"`, ES5 only (`var`, `function`), no modules, no build step.
- Build the DOM with `document.createElement` and `textContent`. NEVER `innerHTML`.
- `fetch` with `Accept: application/json`; unwrap the envelope as `result.data.error`.
- Every failure renders a visible error block with the code and the fields. Never leave a placeholder.
- Guard the globals: `if (typeof Chart === "undefined")`.
- Timestamps formatted as `YYYY-MM-DD HH:MM:SS UTC`.

**Tests**
- One file per concern. Only the `app` and `client` fixtures from `tests/conftest.py`.
- Create data points with a local `insert_points(...)` helper modeled on the one in `tests/test_query.py` (creates the `Metric` if missing and inserts `DataPoint` rows with `ts_utc_ms`, `value_num`, `value_str`, `tags` as `json.dumps`, `received_at_ms`).
- Assert on the rendered HTML for the UI (`response.get_data(as_text=True)`) and on the envelope for the API. The envelope assertion helper pattern is in `tests/test_query.py`: the payload has exactly `{"error"}` and the error has exactly the six keys.
- Every new failure path gets a test that asserts the code, the message and the hint are visible.

## Gotchas that will bite you

1. There is NO existing pagination, LIMIT/OFFSET or CSV code in this repository. You are inventing both patterns.
2. The metric type is NOT on the point. You must join `Metric` to decide how to render the value. A boolean point stores BOTH representations.
3. `tags` is a JSON string in a TEXT column. Malformed JSON must degrade to `{}`, never crash.
4. `received_at_ms` is the ingestion time and `ts_utc_ms` is the measurement time. A point can arrive long after its timestamp. Show both.
5. The ordering of the table is by `ts_utc_ms` then `id`.
6. Epoch seconds are silently wrong, not rejected. The picker must send epoch MILLISECONDS or ISO-8601.
7. `5m` and `15m` do not exist yet in either `validation.TIME_RANGES` or the front end `RANGE_MS` map. Add both to both, or they disagree.
8. There are NO timers in `app/static/` today. The long poll is the first recurring timer. A `setTimeout` recursion avoids overlapping requests.
9. `base.html` is the only place scripts are loaded. A new script tag must be added there, otherwise it never runs.
10. `app/templates/dashboard.html` renders each widget with `data-config='{{ widget.config | tojson }}'`; `_widget_rows` already exposes `metric_name` for the link.
10. An empty result is NOT an error. Render an empty table, never an error block.
11. `tests/conftest.py` builds the schema with `Base.metadata.create_all`, not Alembic. This feature needs NO migration. Confirm with `.venv/bin/alembic check` → `No new upgrade operations detected.`

## Verification you must run and report

Run each command and report the ACTUAL observed output:

```bash
.venv/bin/python -m pytest -q
.venv/bin/alembic check
```

If a command fails, fix the cause and rerun. Do not report success without the observed output.

## Commits

Three commits, one per work unit, Conventional Commit messages, no AI attribution and no `Co-Authored-By` trailer:

1. `feat(raw): add the raw points API with pagination and long polling`
2. `feat(web): add the raw data page with a dynamic time picker`
3. `docs: document the raw data browser`

Stage ONLY the files of that work unit. Never use `git add -A` blindly; stage explicit paths. Do NOT push.

After each work unit, update `odd/tasks/metrics-raw-browser.md`: check the task box and record the commit id and the observed check output in the Progress table.

## Final report

Return ONE message with:
1. The files created and modified, grouped by work unit.
2. The three commit ids with their subjects.
3. The exact observed output of `.venv/bin/python -m pytest -q` and `.venv/bin/alembic check`.
3. The endpoint contracts you implemented (the query parameters and the response shape), so the parent can verify them.
4. Anything you could not finish, and why.