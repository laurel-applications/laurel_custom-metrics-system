# Colombia time and chart gaps

## Objective

Show every date and time in the Colombia timezone (GMT-5), and keep the time
slots that carry no measurement visible on the charts.

## Problem

Two separate defects of the same area, the reading of time.

**The timezone.** Every timestamp the user reads is rendered in UTC: the
widget tables, the chart axes, the raw data browser and the server rendered
timestamps. The reader of the dashboard is in Colombia, so the numbers do not
match the clock on the wall, and reading a chart means subtracting five hours
in the head.

**The gaps.** The query API only returns the buckets that carry data. The chart
builds its axis from the timestamps it receives, so a period without a
measurement does not appear at all: the axis compresses and the silence of the
metric becomes invisible. The reader cannot tell "the metric was quiet" from
"the metric was busy the whole time".

## Scope

- Render every date and time in `America/Bogota` (GMT-5, no daylight saving).
- Build the full time axis of the window on the chart, so a period without
  data shows as an empty stretch instead of disappearing.

## Out of scope

- The CSV export keeps writing UTC ISO-8601 timestamps. Its columns are named
  `ts_utc` and `received_at_utc`, so the contract they state is not changed.
- The stored timestamps stay UTC epoch milliseconds. Only the rendering
  changes.
- No live mode and no auto refresh.

## Constraints

- Colombia does not observe daylight saving time, so the offset from UTC is a
  constant of five hours. The front end applies that constant and the server
  uses `zoneinfo.ZoneInfo("America/Bogota")`.
- The API contract does not change: the gaps are filled on the front end, from
  the `from_ms`, `to_ms` and `bucket_sec` the response already carries.

## Tasks

- [x] T1 -- Render the timestamps of the server in the Colombia timezone.
- [x] T2 -- Render the timestamps of the front end in the Colombia timezone.
- [x] T3 -- Build the full time axis of the chart and draw the gaps.
- [x] T4 -- Update the test that asserted the UTC rendering.
- [x] T5 -- Add the tests of the new behaviour.

## Authorized scope

- `app/web/routes.py`
- `app/static/app.js`
- `app/static/raw.js`
- `tests/test_raw.py`

## Acceptance criteria

1. The tables, the chart axes and the server rendered timestamps show the
   Colombia time, and say so.
2. The datetime-local input of the raw data browser holds the Colombia time.
3. A period without data appears on the chart as an empty stretch, and the
   line breaks there instead of joining both sides.
4. The CSV export still writes UTC timestamps.
5. A window too large to be drawn is not built: the chart falls back to the
   timestamps the server returned instead of freezing the browser.

## Verification

- `pytest tests/ -q` covers the server side rendering.
- `node --check app/static/app.js` and `node --check app/static/raw.js` pass.
- A node test exercises the timezone and the axis against the shipped script.

## Route

Delegated direct (one writer), together with the confirmation modal. All three
touch `app/static/app.js`, so one bounded writer does all of them.
