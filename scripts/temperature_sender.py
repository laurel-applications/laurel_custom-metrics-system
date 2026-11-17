#!/usr/bin/env python3
"""Send a temperature reading every second to a Laurel Metrics deployment.

The script only uses the Python standard library, so it runs anywhere Python
3.9 or newer is available, with no ``pip install`` step.

It also prepares the destination by using the HTTP API:

1. It creates the dashboard when it does not exist yet.
2. It creates a widget for the temperature metric when the dashboard has none.
3. It pushes one data point per interval.

Usage::

    python scripts/temperature_sender.py --base-url https://metrics.example.com

    # Send exactly 30 readings, five seconds apart.
    python scripts/temperature_sender.py --base-url https://metrics.example.com \
        --count 30 --interval 5
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "https://metrics.andreslobaton.top"
DEFAULT_METRIC = "temperature"
DEFAULT_DASHBOARD = "temperature"
DEFAULT_INTERVAL = 1.0
USER_AGENT = "laurel-temperature-sender/1.0"

#: The dashboard list renders one link per dashboard: ``/dashboards/1`` + name.
DASHBOARD_LINK = re.compile(r'href="/dashboards/(\d+)"[^>]*>\s*([^<]*?)\s*</a', re.S)

#: The widget grid stores the whole configuration in a ``data-config`` attribute.
WIDGET_CONFIG = re.compile(r"data-config='([^']*)'")


class ApiError(RuntimeError):
    """Raised when the server answers with an error."""


class _Stop(Exception):
    """Raised when the user interrupts the sender."""


def _request(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, str, str]:
    """Perform an HTTP request and return ``(status, body, final_url)``."""
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, body, response.geturl()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        return error.code, body, error.geturl()
    except urllib.error.URLError as error:
        raise ApiError(f"could not reach {url}: {error.reason}") from error


def _fail_for_status(status: int, body: str, url: str) -> None:
    """Turn an HTTP error into an :class:`ApiError` with the server envelope."""
    if status < 400:
        return

    code: object = status
    message = "the server rejected the request"
    try:
        payload = json.loads(body)
        envelope = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = envelope.get("code", code)
        message = envelope.get("message", message)
    except (ValueError, AttributeError):
        pass

    raise ApiError(f"HTTP {status} {code}: {message} ({url})")


def post_json(base_url: str, path: str, payload: dict) -> dict:
    """Send a JSON body to the API and return the decoded answer."""
    url = f"{base_url}{path}"
    body = json.dumps(payload).encode("utf-8")
    status, text, _ = _request(
        "POST", url, data=body, headers={"Content-Type": "application/json"}
    )
    _fail_for_status(status, text, url)
    return json.loads(text) if text.strip() else {}


def post_form(base_url: str, path: str, fields: dict[str, str]) -> str:
    """Send a form encoded body and return the URL the server redirected to."""
    url = f"{base_url}{path}"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    status, text, final_url = _request(
        "POST",
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    _fail_for_status(status, text, url)
    return final_url


def get_text(base_url: str, path: str) -> str:
    """Return the body of a GET request."""
    url = f"{base_url}{path}"
    status, text, _ = _request("GET", url)
    _fail_for_status(status, text, url)
    return text


def find_dashboard(base_url: str, name: str) -> int | None:
    """Return the id of the dashboard called ``name``, or ``None``."""
    page = get_text(base_url, "/dashboards")
    for dashboard_id, label in DASHBOARD_LINK.findall(page):
        if label.strip() == name:
            return int(dashboard_id)
    return None


def create_dashboard(base_url: str, name: str) -> int:
    """Create a dashboard through the API and return its id."""
    final_url = post_form(base_url, "/dashboards", {"name": name})
    match = re.search(r"/dashboards/(\d+)", final_url)
    if not match:
        raise ApiError(f"the dashboard was created but its id is unknown ({final_url})")
    return int(match.group(1))


def dashboard_metrics(base_url: str, dashboard_id: int) -> set[str]:
    """Return the metrics that already have a widget in the dashboard."""
    page = get_text(base_url, f"/dashboards/{dashboard_id}")
    metrics: set[str] = set()
    for raw_config in WIDGET_CONFIG.findall(page):
        # The attribute holds JSON with escaped quotes, so it is decoded first.
        try:
            config = json.loads(raw_config.replace("&#34;", '"'))
        except ValueError:
            continue
        name = config.get("metric")
        if isinstance(name, str):
            metrics.add(name)
    return metrics


def create_widget(
    base_url: str,
    dashboard_id: int,
    *,
    title: str,
    metric: str,
    chart_type: str,
    agg: str,
    time_range: str,
    bucket: str = "auto",
    group_by: str = "",
    filters: str = "[]",
) -> None:
    """Create a widget through the API."""
    post_form(
        base_url,
        f"/dashboards/{dashboard_id}/widgets",
        {
            "title": title,
            "metric": metric,
            "chart_type": chart_type,
            "agg": agg,
            "bucket": bucket,
            "time_range": time_range,
            "group_by": group_by,
            "filters": filters,
        },
    )


def query(base_url: str, metric: str, agg: str = "avg") -> dict:
    """Run a query against the API and return the decoded answer."""
    query_string = urllib.parse.urlencode({"metric": metric, "agg": agg})
    url = f"{base_url}/api/query?{query_string}"
    status, text, _ = _request("GET", url)
    _fail_for_status(status, text, url)
    return json.loads(text) if text.strip() else {}


def simulate_temperature(base: float, amplitude: float, period: float, tick: int) -> float:
    """Return a plausible ambient temperature for the given tick."""
    wave = base + amplitude * math.sin(tick / period * 2 * math.pi)
    return round(wave + random.uniform(-0.15, 0.15), 2)


def parse_tags(raw_tags: list[str]) -> dict[str, str]:
    """Turn ``key=value`` arguments into a tag dictionary."""
    tags: dict[str, str] = {}
    for raw in raw_tags:
        key, separator, value = raw.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"invalid tag '{raw}', expected key=value")
        tags[key.strip()] = value.strip()
    return tags


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        description="Send a temperature reading every second to Laurel Metrics."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--metric", default=DEFAULT_METRIC)
    parser.add_argument("--type", default="gauge", choices=("gauge", "counter", "timer"))
    parser.add_argument("--unit", default="C")
    parser.add_argument("--dashboard", default=DEFAULT_DASHBOARD)
    parser.add_argument("--title", default="Temperature")
    parser.add_argument("--chart-type", default="line", choices=("line", "bar", "table", "stat"))
    parser.add_argument("--agg", default="avg")
    parser.add_argument("--time-range", default="1h")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="How many readings to send; 0 means run until interrupted.",
    )
    parser.add_argument(
        "--value",
        type=float,
        default=None,
        help="Send a fixed value instead of the simulated temperature.",
    )
    parser.add_argument("--base-temp", type=float, default=24.0)
    parser.add_argument("--amplitude", type=float, default=3.5)
    parser.add_argument(
        "--period",
        type=float,
        default=60.0,
        help="Seconds of the simulated wave; the default is one minute.",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Tag attached to every point; can be repeated.",
    )
    parser.add_argument(
        "--no-widget",
        action="store_true",
        help="Do not create the widget, only send the points.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Query the metric once the sending loop finishes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the sender."""
    args = build_parser().parse_args(argv)

    base_url = args.base_url.rstrip("/")
    tags = parse_tags(args.tag) or {"sensor": "ambient"}

    def _interrupt(_signum: int, _frame: object) -> None:
        raise _Stop()

    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)

    try:
        print(f"Target: {base_url}")

        # The metric has to exist before a widget can reference it, so the
        # first reading is pushed before the dashboard is prepared.
        first_value = (
            args.value
            if args.value is not None
            else simulate_temperature(args.base_temp, args.amplitude, args.period, 0)
        )
        payload: dict[str, object] = {
            "name": args.metric,
            "type": args.type,
            "value": first_value,
            "tags": tags,
        }
        if args.unit:
            payload["unit"] = args.unit

        answer = post_json(base_url, "/api/ingest", payload)
        print(f"Metric '{args.metric}' ready ({answer})")

        dashboard_id = find_dashboard(base_url, args.dashboard)
        if dashboard_id is None:
            dashboard_id = create_dashboard(base_url, args.dashboard)
            print(f"Dashboard '{args.dashboard}' created with id {dashboard_id}")
        else:
            print(f"Dashboard '{args.dashboard}' found with id {dashboard_id}")

        if not args.no_widget:
            if args.metric in dashboard_metrics(base_url, dashboard_id):
                print(f"Widget for '{args.metric}' already exists")
            else:
                create_widget(
                    base_url,
                    dashboard_id,
                    title=args.title,
                    metric=args.metric,
                    chart_type=args.chart_type,
                    agg=args.agg,
                    time_range=args.time_range,
                )
                print(f"Widget '{args.title}' created")

        print(
            f"Sending every {args.interval:g}s"
            f"{'' if not args.count else f' ({args.count} readings)'}"
        )

        sent = 1
        tick = 1
        next_send = time.monotonic() + args.interval
        while not args.count or sent < args.count:
            time.sleep(max(0.0, next_send - time.monotonic()))
            next_send += args.interval

            value = (
                args.value
                if args.value is not None
                else simulate_temperature(
                    args.base_temp, args.amplitude, args.period, tick
                )
            )
            point = {
                "name": args.metric,
                "type": args.type,
                "value": value,
                "tags": tags,
            }
            if args.unit:
                point["unit"] = args.unit

            post_json(base_url, "/api/ingest", point)
            stamp = datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
            print(f"[{stamp}] {args.metric} = {value} {args.unit or ''}".rstrip())
            sent += 1
            tick += 1

        print(f"Done. {sent} points sent.")

        if args.verify:
            result = query(base_url, args.metric, args.agg)
            print(f"Query ({args.agg}): {json.dumps(result)[:600]}")

        return 0
    except _Stop:
        print("\nInterrupted.")
        return 130
    except ApiError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
