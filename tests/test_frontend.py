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

#: The JavaScript regression test of the filter values.
JS_TEST = REPO_ROOT / "tests" / "js" / "filter_value_test.js"

#: The JavaScript regression test of the dashboard time range.
JS_RANGE_TEST = REPO_ROOT / "tests" / "js" / "dashboard_range_test.js"

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
