# Manual verification checklist

The automated suite covers the API, the rendered HTML and the migrations.
What it **cannot** cover is what the pages actually *look* like in a browser:
the charts, the drag & drop, the dynamic operator lists and the way an error
is presented to a human.

This checklist is what a human must run, in a browser, to confirm the parts
that were never visually verified. Work through it in order: every step
depends on the data pushed in step 0.

**Rules**

- Every step states the exact action and the exact **visible** result.
- A step that does not show what it describes is a **failure**: report it.
- Do not skip a step that fails; the following steps assume the previous ones
  passed.

---

## Step 0 — Prepare the data

In a terminal, with the application running on <http://127.0.0.1:5000>:

```bash
# A counter with two hosts and one point without any host tag.
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"requests","type":"counter","value":10,"tags":{"host":"web-1"}}'
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"requests","type":"counter","value":20,"tags":{"host":"web-2"}}'
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"requests","type":"counter","value":5}'

# A second counter, to make the filter visible.
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"requests_total","type":"counter","value":100,"tags":{"host":"web-1"}}'

# A string metric, a bool metric and a timer.
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"deploy_status","type":"string","value":"ok","tags":{"host":"web-1"}}'
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"health_check","type":"bool","value":true,"tags":{"host":"web-1"}}'
curl -s -X POST http://127.0.0.1:5000/api/ingest -H 'Content-Type: application/json' \
  -d '{"name":"http_latency","type":"timer","value":128,"unit":"ms"}'
```

**Expected visible result:** the terminal prints one JSON object per call,
each with `"status": "created"`. If a call answers `409 METRIC_TYPE_MISMATCH`,
the metric already exists with another type: start from a fresh database.

---

## Step 1 — The application boots and the dashboard list shows its empty state

**Action:** open <http://127.0.0.1:5000/> in the browser.

**Expected visible result:**

- The page title reads **Dashboards**.
- The main panel shows the heading **"There is no dashboard yet"**.
- Below it, a numbered list of three steps, and a grey code block that ends
  with:
  `curl -X POST http://127.0.0.1:5000/api/ingest \`
  `-H 'Content-Type: application/json' \`
  `-d '{"name": "requests", "type": "counter", "value": 1, "tags": {"host": "web-1"}}'`
- On the right, the **New dashboard** form with a single **Name** input and a
  **Create dashboard** button.
- The top bar shows **Laurel Metrics**, with the links **Dashboards** and
  **Metrics**.

---

## Step 2 — Creating a dashboard

**Action:** type `Operations` in the **Name** field of the **New dashboard**
form and press **Create dashboard**.

**Expected visible result:**

- The browser lands on `/dashboards/1` (the URL ends with the id of the new
  dashboard).
- The heading is **Operations**.
- The subtitle reads `Created <date> UTC · 0 widgets`.
- The panel shows the empty state **"This dashboard has no widget yet"** with
  a **Add widget** button.

---

## Step 3 — Creating a widget for a counter metric and seeing the line chart

**Action:** press **Add widget**, then fill the form:

- **Title**: `Requests per host`
- **Metric**: `requests (counter)`
- **Chart type**: `line`
- **Aggregation**: `sum`
- **Bucket**: `auto`
- **Time range**: `24h`
- **Group by tag**: `No grouping`

and press **Add the widget**.

**Expected visible result:**

- The browser returns to the dashboard.
- A widget card titled **Requests per host** appears, with the subtitle
  `requests · sum · 24h · line`.
- Inside the card, a **line chart is drawn** (an axis with a time scale on the
  left and a value scale at the bottom, with a line through the points). The
  placeholder text *"Loading the data…"* is **gone**.
- The **Edit** and **Delete** buttons are in the widget header, on the right.

---

## Step 4 — The four chart types

**Action:** on the widget of step 3, press **Edit**, change one field at a
time, press **Save the widget** after each change, and look at the card.

**Expected visible result:**

| Chart type | What must be visible |
| --- | --- |
| `line` | A **line** chart: connected points, no bars. |
| `bar` | A **bar** chart: vertical bars, one per bucket. |
| `table` | A **table** with the columns `Series`, `Bucket`, `Value`, `Points` and at least one row. |
| `stat` | A **single large number** (the aggregated value), with the caption `requests (sum) over 24h` above it. |

After the last change, put the chart type back to `line` and save again.

---

## Step 5 — Grouping by a tag key

**Action:** press **Edit** on the widget, set **Group by tag** to `host`, and
press **Save the widget**.

**Expected visible result:**

- The widget subtitle now ends with `· by host`.
- The chart shows **one line per tag value**: a line for `web-1` and a line
  for `web-2`.
- A **legend** appears in the chart with the entries `web-1`, `web-2` **and
  `no tag`**. The `no tag` series is the point that was ingested without a
  `host` tag; it is drawn because the third point of step 0 has no tag.
- If the legend shows only one entry, the third point of step 0 was not
  ingested: push it again and reload the page.

---

## Step 6 — The filters

For every filter below: press **Edit**, press **Add filter**, choose the
field, the operator and the value, then **Save the widget** and compare the
chart with the one of the previous step.

**Expected visible result:** the series changes after each save, and the
**Filters (JSON)** textarea always holds the filter that was built, for
example:

```json
[{"field": "value", "op": ">", "value": 10}]
```

| # | Metric | Field | Operator | Value | What must change |
| --- | --- | --- | --- | --- | --- |
| 6.1 | `requests` (counter) | `value` | `>` | `10` | The `web-1` line stays, the `web-2` line (20) and the `no tag` line (5) disappear. |
| 6.2 | `requests` (counter) | `value` | `between` | `1, 30` | Both tagged lines are back, the `no tag` line (5) is back too: every value is between 1 and 30. |
| 6.3 | `deploy_status` (string) | `value` | `contains` | `ok` | The chart shows the string metric; the value `ok` matches. Change the value to `ko` and the series becomes empty. |
| 6.4 | `deploy_status` (string) | `value` | `regex` | `^o` | The series is still there. Change the regular expression to `^z` and the series becomes empty. |
| 6.5 | `health_check` (bool) | `value` | `is_true` | *(the input is disabled)* | The chart shows the bool metric with the value `true`. |

**Also check:** a filter on a tag. Choose the field `tags.host` and the
operator `=` with the value `web-1`. Only the `web-1` series must survive.

**Remove the filter** (press **Remove** on the row, then **Save the
widget**) and check that the chart is the complete one again.

---

## Step 7 — The operator list follows the metric type

**Action:** press **Edit**, then change the **Metric** field, and watch the
**Aggregation** list and the operator list of a filter row.

**Expected visible result:**

- With `requests` (a **counter**) selected, the **Aggregation** list offers
  the six entries `sum`, `avg`, `min`, `max`, `count`, `last`.
- With `deploy_status` (a **string**) selected, the **Aggregation** list
  offers **only** `count` and `last`.
- With `health_check` (a **bool**) selected, the **Aggregation** list offers
  the six entries again.
- Add a filter row and select the field `value`: with a counter the operator
  list is numeric (`=`, `!=`, `>`, `>=`, `<`, `<=`, `between`); with the
  string metric it becomes textual (`=`, `!=`, `contains`, `not_contains`,
  `starts_with`, `ends_with`, `regex`); with the bool metric it becomes
  `is_true` and `is_false`, and the value input is **greyed out**.
- The **Group by tag** list changes with the metric as well: it offers the
  tag keys of the selected metric (`host` for `requests`, and nothing for
  `http_latency`, which has no tag).

Put the metric back to `requests` before continuing.

---

## Step 8 — Drag & drop reordering, and the order survives a reload

**Action:** create two more widgets on the dashboard (**Add widget**, with
the titles `B second` and `C third`, metric `requests`, chart type `stat`).
Then drag the widget **C third** onto the widget **Requests per host**, so
that **C third** becomes the first card.

**Expected visible result:**

- While dragging, the card being dragged follows the pointer.
- When the card is dropped, a line under the dashboard title reads
  **"The new order was saved."**
- The cards are now in the new order: **C third**, **Requests per host**,
  **B second**.
- **Reload the page** (F5). The cards keep the **same order**. The order was
  persisted, not only rearranged in the browser.

---

## Step 9 — A widget with an invalid aggregation shows the explicit error

**Action:** press **Add widget**, and fill the form with:

- **Title**: `Broken widget`
- **Metric**: `deploy_status (string)`
- **Chart type**: `line`
- **Aggregation**: `sum`
- **Bucket**: `auto`
- **Time range**: `24h`

and press **Add the widget**.

**Expected visible result:**

- The widget is **not** created, and the browser stays on the form.
- A **red error block** is visible at the top of the form, with the title
  *"The widget could not be saved"*, and the following six entries, all
  **visible on the page** (not in a tooltip):
  - **Code**: `INVALID_AGGREGATION_FOR_TYPE`
  - **Message**: `The aggregation 'sum' is not valid for a 'string' metric.`
  - **Field**: `agg`
  - **Expected**: `["count", "last"]`
  - **Received**: `sum`
  - **Hint**: `A 'string' metric only supports: count, last.`
- The values you typed are still in the form.

---

## Step 10 — A failing query shows the error on the widget, not a blank canvas

**Action:** with the dashboard open, make one of its widgets fail on purpose.

The cleanest way is to make the metric the widget points at disappear, from a
terminal. Renaming it keeps the data points and the widget rows intact and
only breaks the name the widget asks for:

```bash
.venv/bin/python - <<'EOF'
import sqlite3
connection = sqlite3.connect("instance/metrics.db")
connection.execute("UPDATE metrics SET name = 'requests_gone' WHERE name = 'requests'")
connection.commit()
connection.close()
EOF
```

then reload the dashboard in the browser.

**Expected visible result:**

- The widget card shows a **red error box** with the code
  **`METRIC_NOT_FOUND`**, the message
  *"There is no metric named 'requests'."* and the hint
  *"List the known metrics with GET /api/metrics."*
- The card is **not** empty and the placeholder *"Loading the data…"* is
  **not** left on the screen. A blank canvas is a failure.
- The other widgets of the dashboard are unaffected.

**Restore the data** afterwards, so the following steps work:

```bash
.venv/bin/python - <<'EOF'
import sqlite3
connection = sqlite3.connect("instance/metrics.db")
connection.execute("UPDATE metrics SET name = 'requests' WHERE name = 'requests_gone'")
connection.commit()
connection.close()
EOF
```

The three points of step 0 are still stored: only the name of the metric
was changed back. Reload the dashboard and check that the widget shows its
chart again.

**Second check — the network is down.** Stop the application (Ctrl-C in the
terminal) and reload the dashboard.

**Expected visible result:** every widget shows a red error box with a
message that says the query could not be sent. No widget stays on
*"Loading the data…"*. Restart the application afterwards.

---

## Step 11 — The metrics overview page

**Action:** click **Metrics** in the top bar.

**Expected visible result:**

- The heading is **Metrics**.
- A table with the columns `Metric`, `Type`, `Unit`, `Data points`, `Created`
  and `Add widget`.
- One row per metric of step 0: `requests` (`counter`), `requests_total`
  (`counter`), `deploy_status` (`string`), `health_check` (`bool`),
  `http_latency` (`timer`, unit `ms`).
- The **Data points** column shows the number of points of each metric.
- The **Add widget** button of a row opens the widget builder with that
  metric already selected.

---

## Step 12 — Deleting a widget and a dashboard

**Action:** press **Delete** on one widget of the dashboard. A confirmation
dialog appears for the dashboard, but not for a widget.

**Expected visible result:**

- The browser returns to the dashboard and the widget is **gone**.
- The remaining widgets keep their order.
- The dashboard subtitle shows one widget less.

**Action:** press **Delete dashboard** on the dashboard. A confirmation dialog
appears: *"Delete this dashboard and all its widgets?"*. Confirm it.

**Expected visible result:**

- The browser lands on `/dashboards`, and the deleted dashboard is **not** in
  the table any more.
- Its widgets are gone with it: open **Metrics**, pick another metric and
  press **Add widget** to check that the deleted widgets are not in the way.

---

## Step 13 — An unknown page shows the error page

**Action:** open <http://127.0.0.1:5000/there-is-no-such-page>.

**Expected visible result:**

- The page shows the heading **"Something went wrong"** and the subtitle
  **HTTP 404**.
- The red error block shows the code **`NOT_FOUND`**, the message *"The
  requested resource does not exist."* and the hint *"Check the URL path; the
  routes that exist are listed in the API reference."*
- A **Back to the dashboards** button is visible.

---

## Result

Fill this in when the checklist is done:

| Step | Result | Comment |
| --- | --- | --- |
| 0 — data | ☐ pass ☐ fail | |
| 1 — boot and empty state | ☐ pass ☐ fail | |
| 2 — create a dashboard | ☐ pass ☐ fail | |
| 3 — widget and line chart | ☐ pass ☐ fail | |
| 4 — the four chart types | ☐ pass ☐ fail | |
| 5 — group by a tag | ☐ pass ☐ fail | |
| 6 — the filters | ☐ pass ☐ fail | |
| 7 — type aware operators | ☐ pass ☐ fail | |
| 8 — drag & drop and reload | ☐ pass ☐ fail | |
| 9 — invalid aggregation | ☐ pass ☐ fail | |
| 10 — failing query on a widget | ☐ pass ☐ fail | |
| 11 — metrics overview | ☐ pass ☐ fail | |
| 12 — delete a widget and a dashboard | ☐ pass ☐ fail | |
| 13 — unknown page | ☐ pass ☐ fail | |
