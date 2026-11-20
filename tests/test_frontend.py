"""Tests of the front end.

The front end is a plain script without a build step, so it is not unit
tested in the browser.  What *is* tested here is the behaviour that the
server depends on and that used to break silently:

* the filter value of a textual field stays a text, so the server never
  rejects a widget the user could not save;
* a widget never stays blank: the failure of the query is always rendered
  with the code, the message and the hint of the error envelope.

The JavaScript assertions run the *shipped* ``app/static/app.js`` with
``node``.  When node is not installed the JavaScript test is skipped, and the
static assertions (which only read the file) still run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

#: The root of the repository.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: The front end script.
APP_JS = REPO_ROOT / "app" / "static" / "app.js"

#: The raw data browser script.
RAW_JS = REPO_ROOT / "app" / "static" / "raw.js"

#: The dashboard template that renders the time picker.
DASHBOARD_HTML = REPO_ROOT / "app" / "templates" / "dashboard.html"

#: The JavaScript regression test of the filter values.
JS_TEST = REPO_ROOT / "tests" / "js" / "filter_value_test.js"

#: The JavaScript regression test of the dashboard time range.
JS_RANGE_TEST = REPO_ROOT / "tests" / "js" / "dashboard_range_test.js"

#: The JavaScript regression test of the local time and of the time axis.
JS_TIMEZONE_TEST = REPO_ROOT / "tests" / "js" / "timezone_axis_test.js"

#: The node executable, or None when node is not installed.
NODE = shutil.which("node")


def test_the_front_end_script_is_shipped() -> None:
    """The script the pages load exists and is not empty."""
    assert APP_JS.is_file()
    assert APP_JS.stat().st_size > 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_front_end_passes_the_javascript_syntax_check() -> None:
    """``node --check`` accepts the shipped script."""
    result = subprocess.run(
        [NODE, "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_textual_filter_value_stays_a_text() -> None:
    """A textual filter value is never turned into a number by the form.

    The bug this guards: a value that looked like a number was turned into
    one whatever the type of the field was, and the server then rejected the
    whole widget with ``INVALID_FILTER_VALUE``.
    """
    result = subprocess.run(
        [NODE, str(JS_TEST), str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_dashboard_range_overrides_the_range_of_the_widget() -> None:
    """The window sent to the query API is the range of the dashboard.

    The bug this guards: every widget built the window of its query from the
    ``time_range`` stored in the widget, so a widget configured with ``30d``
    scanned a month of raw points on every dashboard load and exhausted the
    workers.
    """
    result = subprocess.run(
        [NODE, str(JS_RANGE_TEST), str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_timestamps_are_local_and_the_axis_keeps_the_gaps() -> None:
    """The timestamps are local and the empty buckets stay on the axis.

    The two defects this guards: the timestamps were rendered in UTC, five
    hours ahead of the local time of Colombia, and the axis of a chart was
    built from the timestamps the server sent, so a period without a
    measurement collapsed the axis and the silence of the metric disappeared.
    """
    result = subprocess.run(
        [NODE, str(JS_TIMEZONE_TEST), str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_timestamps_of_the_front_end_are_rendered_in_the_local_time() -> None:
    """The front end renders the local time of Colombia, not UTC."""
    source = APP_JS.read_text(encoding="utf-8")

    assert "var TIMEZONE_OFFSET_MS = -5 * 60 * 60 * 1000;" in source
    assert "+ TIMEZONE_OFFSET_MS" in source
    assert '" GMT-5"' in source
    # The tooltip of a chart is rendered in the local time too: the default
    # title would be the raw epoch label.
    assert "function tooltipTitle(" in source
    assert "title: tooltipTitle" in source


def test_the_chart_keeps_the_empty_buckets_visible() -> None:
    """The axis of a chart holds every bucket of the window."""
    source = APP_JS.read_text(encoding="utf-8")

    assert "function buildTimeAxis(" in source
    assert "buildTimeAxis(data.from_ms, data.to_ms, data.bucket_sec)" in source
    # A period without data is a break in the line, never a joined segment.
    assert "spanGaps: false" in source
    assert "spanGaps: true" not in source


def test_the_front_end_queries_with_the_range_of_the_dashboard() -> None:
    """The payload of a widget is built from the range of the dashboard.

    The assertions look for the state the dashboard range lives in and for the
    window the payload is built with, so the range stored in the widget can
    never take the place of the one the user picked.
    """
    source = APP_JS.read_text(encoding="utf-8")

    assert "var activeRange = DEFAULT_RANGE;" in source
    assert "var span = RANGE_MS[activeRange] || RANGE_MS[DEFAULT_RANGE];" in source
    assert "RANGE_MS[config.time_range]" not in source
    # The range travels in the URL, so the view can be shared and restored.
    assert "function rangeFromUrl()" in source
    assert "function rangeToUrl(" in source
    assert "function initTimePicker()" in source


def test_the_time_picker_offers_long_and_custom_ranges() -> None:
    """The picker reaches a year, the previous year and a custom window.

    A year of data is safe: the server sizes the bucket from the span, so the
    chart receives one point per day, not a year of raw points.
    """
    source = APP_JS.read_text(encoding="utf-8")
    html = DASHBOARD_HTML.read_text(encoding="utf-8")

    assert '"1y": 365 * 24 * 60 * 60 * 1000' in source
    assert 'value: "90d"' in source
    assert 'value: "1y"' in source
    assert 'value: "prev-year"' in source
    assert 'value: "custom"' in source
    assert "function previousYearWindow(" in source
    assert "function resolveWindow(" in source

    assert 'data-range="90d"' in html
    assert 'data-range="1y"' in html
    assert 'data-range="prev-year"' in html
    assert 'data-range="custom"' in html
    assert 'data-role="custom-panel"' in html
    assert 'data-role="custom-apply"' in html


def test_the_selected_range_is_remembered_across_reloads() -> None:
    """The picker restores the last selection from the browser storage."""
    source = APP_JS.read_text(encoding="utf-8")

    assert "var STORAGE_KEY = \"laurel.dashboard.range\";" in source
    assert "function savedRange()" in source
    assert "function saveRange(" in source
    assert "function initialRange()" in source
    assert "window.localStorage.getItem(STORAGE_KEY)" in source
    assert "window.localStorage.setItem(STORAGE_KEY" in source


def test_a_failed_widget_query_never_leaves_a_blank_chart() -> None:
    """The front end renders the error of a failed query on the widget.

    The assertions look for the code of the envelope, the message and the
    hint, and for the guard that stops the renderer from dereferencing a
    missing body.
    """
    source = APP_JS.read_text(encoding="utf-8")

    # The error of the envelope is rendered on the widget itself.
    assert "function renderError(" in source
    assert 'envelope.code || "QUERY_FAILED"' in source
    assert "envelope.hint ||" in source

    # A successful answer that cannot be read is an error, not a blank
    # canvas: the placeholder is replaced by the error block.
    assert "if (!result.data) {" in source
    assert "renderError(" in source


def test_the_raw_data_script_is_shipped() -> None:
    """The raw browser script exists and is not empty."""
    assert RAW_JS.is_file()
    assert RAW_JS.stat().st_size > 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_raw_data_script_passes_the_javascript_syntax_check() -> None:
    """``node --check`` accepts the shipped raw browser script."""
    result = subprocess.run(
        [NODE, "--check", str(RAW_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_raw_data_script_uses_safe_recurring_updates() -> None:
    """The live browser never overlaps requests and never injects HTML."""
    source = RAW_JS.read_text(encoding="utf-8")

    assert "setTimeout(" in source
    assert "setInterval(" not in source
    assert '"/api/raw/wait?' in source
    assert "Accept: \"application/json\"" in source
    assert "result.data && result.data.error ? result.data.error : null" in source
    assert "document.createElement" in source
    assert ".textContent" in source
    assert ".innerHTML" not in source


def test_the_raw_data_script_uses_the_browser_clock() -> None:
    """Relative ranges are resolved against the browser, not the server."""
    source = RAW_JS.read_text(encoding="utf-8")

    assert "var now = Date.now();" in source
    assert '"5m": 5 * 60 * 1000' in source
    assert '"15m": 15 * 60 * 1000' in source


def test_the_widget_and_raw_ranges_share_the_new_short_ranges() -> None:
    """The widget and the raw picker agree on the five and fifteen minute ranges."""
    source = APP_JS.read_text(encoding="utf-8")

    assert '"5m": 5 * 60 * 1000' in source
    assert '"15m": 15 * 60 * 1000' in source
