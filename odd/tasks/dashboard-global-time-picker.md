# Dashboard global time range picker (Datadog style)

## Objective

Add a dashboard-level time range control, modelled on Datadog's time picker, that
governs the time window of **every widget** of a dashboard when the charts are
rendered. The control lives on the dashboard page, not on the widget edit form.

## Problem

Each widget stores its own `time_range` and the front end turns it into the
`from`/`to` window of the query it sends to `POST /api/query`. A widget
configured with `30d` therefore scans and serializes a month of raw points on
every dashboard load. With several widgets on the same dashboard the server
repeats that work once per widget, which is what makes the gunicorn workers
run out of memory and get a SIGTERM.

The range is also invisible to the reader of the dashboard: the widget metadata
line states the range stored in the widget, not the range the chart actually
shows, so the two drift apart.

## Scope

- A time range control in the dashboard header that applies to all widgets.
- The selected range overrides the per-widget `time_range` at render time.
- The selection is shareable: it travels in the URL as the `range` query
  parameter (plus `from`/`to` for a custom window).
- The widget metadata line and the stat caption state the range that is
  actually displayed.
- The picker reaches a year: rolling 15m/1h/6h/24h/7d/30d/90d/1y, the previous
  calendar year and a custom `from`/`to` window. A wide window is safe because
  the server sizes the bucket from the span (`auto_bucket_sec`), so a year is
  returned as one point per day, not as a year of raw points.
- The custom window is edited with two `datetime-local` inputs read as the
  local time of Colombia.
- The last selection is remembered in `localStorage`, so opening the dashboard
  without a `range` in the URL restores it instead of resetting to the default.
- The widget form no longer offers a per-widget time range: the dashboard
  picker is the only thing that decides the window. The `time_range` column
  stays for compatibility; new widgets store `24h` and an edit preserves the
  stored value.

## Out of scope

- The `time_range` column is not removed from the model or the database. New
  widgets store the default `24h` and edits preserve the stored value, so no
  migration is needed.
- No auto-refresh / live mode. Continuous polling would re-introduce the load
  this change removes.
- No server-side change. The range travels in the query payload that the front
  end already sends, so `/api/query` needs no new parameter.

## Constraints

- No build step and no framework: the front end is plain JavaScript and
  vanilla CSS, matching the existing conventions of `app/static/app.js` and
  `app/static/style.css`.
- The dashboard page is the only page with widgets, so the control only has to
  work there. It must degrade to a no-op when the elements are absent.

## Tasks

- [x] T1 -- Add the picker markup to the dashboard header.
- [x] T2 -- Add the picker logic to `app/static/app.js`: read the range from the
      URL, apply it to every widget, re-render on change, keep the URL in sync.
- [x] T3 -- Style the picker in `app/static/style.css`.
- [x] T4 -- Verify the behaviour in the browser and against the production
      MariaDB.
- [x] T5 -- Extend the picker with rolling 30d/90d/1y ranges.
- [x] T6 -- Add the previous calendar year (Colombia local time) as an absolute
      window.
- [x] T7 -- Add the custom `from`/`to` window, read and written as epoch
      milliseconds in the URL.
- [x] T8 -- Persist the last selection in `localStorage`, so a reload without a
      `range` in the URL keeps it.
- [x] T9 -- Remove the redundant per-widget time range from the widget form; a
      new widget stores `24h` and an edit preserves the stored value.

## Authorized scope

- `app/templates/dashboard.html`
- `app/templates/metric_form.html`
- `app/static/app.js`
- `app/static/style.css`
- `app/web/routes.py` (the widget form no longer reads a time range)

No change to the query API, to the models, or to the database. The
`time_range` column stays with a default value.

## Acceptance criteria

1. The dashboard page shows a time range control in its header.
2. Choosing a range re-queries every widget of the dashboard with the new
   window, without reloading the page.
3. The window sent to the API is the chosen one, not the range stored in the
   widget.
4. The URL carries the chosen range, and opening that URL restores it.
5. The widget metadata line and the stat caption show the range in use.
6. The picker offers 30d, 90d, 1y, the previous year and a custom window.
7. A wide window is rendered from a span-sized bucket, so the point count stays
   bounded (a year is about 366 daily points).
8. A custom window requires a valid start earlier than its end, and both bounds
   survive in the URL.
9. A reload without a `range` in the URL restores the last selection saved in
   the browser.

## Verification

- `GET /dashboards/3` returns 200 and the control is present in the HTML.
- `GET /static/app.js` and `GET /static/style.css` return 200.
- Browser: choosing a range updates every chart and the URL.
- Browser: the query payload carries the chosen `from`/`to` window.
- Production MariaDB: the number of points scanned per widget drops with the
  window, which is the whole point of the change.

## Route

Delegated direct (one writer). Three files are touched, so the write is
delegated to a single bounded writer with a precise specification.

## Progress

Implementation and verification are described in the commit history.
