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

#: The JavaScript regression test of the filter values.
JS_TEST = REPO_ROOT / "tests" / "js" / "filter_value_test.js"

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
