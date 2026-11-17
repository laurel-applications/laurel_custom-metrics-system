#!/usr/bin/env python3
"""Register a catalog of metrics in a Laurel Metrics deployment.

The catalog covers the five metric types, so the dashboard shows what the
system can store and how each type is queried. For every metric the script
pushes a short history of points and creates a widget on the target
dashboard.

It prints the equivalent ``curl`` command of every metric it sends, so the
same calls can be replayed by hand.

Usage::

    python scripts/seed_metrics.py --base-url https://metrics.andreslobaton.top
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timezone

from temperature_sender import (
    ApiError,
    create_dashboard,
    create_widget,
    dashboard_metrics,
    find_dashboard,
    post_json,
    query,
)

DEFAULT_BASE_URL = "https://metrics.andreslobaton.top"

#: One catalog entry per metric: the type, the unit and the tags it carries.
CATALOG: tuple[dict[str, object], ...] = (
    {
        "name": "temperature",
        "type": "gauge",
        "unit": "C",
        "chart_type": "line",
        "agg": "avg",
        "title": "Temperature",
        "tags": {"sensor": "ambient", "room": "office"},
        "values": lambda tick: round(22.0 + 3.0 * math.sin(tick / 30.0), 2),
    },
    {
        "name": "cpu.usage",
        "type": "gauge",
        "unit": "%",
        "chart_type": "line",
        "agg": "avg",
        "title": "CPU usage",
        "tags": {"host": "web-01"},
        "values": lambda tick: round(35.0 + 25.0 * math.sin(tick / 20.0), 2),
    },
    {
        "name": "memory.used",
        "type": "gauge",
        "unit": "MB",
        "chart_type": "bar",
        "agg": "avg",
        "title": "Memory used",
        "tags": {"host": "web-01"},
        "values": lambda tick: round(4200.0 + 900.0 * math.cos(tick / 25.0), 2),
    },
    {
        "name": "requests.total",
        "type": "counter",
        "unit": "req",
        "chart_type": "line",
        "agg": "sum",
        "title": "Requests",
        "tags": {"host": "web-01"},
        "values": lambda tick: 120 + tick * 7 + random.randint(0, 12),
    },
    {
        "name": "api.latency",
        "type": "timer",
        "unit": "ms",
        "chart_type": "line",
        "agg": "avg",
        "title": "API latency",
        "tags": {"route": "/api/query"},
        "values": lambda tick: round(random.uniform(18.0, 240.0), 2),
    },
    {
        "name": "service.up",
        "type": "bool",
        "unit": None,
        "chart_type": "table",
        "agg": "last",
        "title": "Service up",
        "tags": {"host": "web-01"},
        "values": lambda tick: True,
    },
    {
        "name": "deploy.status",
        "type": "string",
        "unit": None,
        "chart_type": "table",
        "agg": "last",
        "title": "Deploy status",
        "tags": {"env": "prod", "version": "1.4.2"},
        "values": lambda tick: "success",
    },
)


def curl_for(base_url: str, entry: dict[str, object], value: object) -> str:
    """Return the curl command that sends the same point."""
    payload: dict[str, object] = {
        "name": entry["name"],
        "type": entry["type"],
        "value": value,
        "tags": entry["tags"],
    }
    if entry.get("unit"):
        payload["unit"] = entry["unit"]
    return (
        f"curl -X POST {base_url}/api/ingest \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -d '{json.dumps(payload, ensure_ascii=False)}'"
    )


def send_history(
    base_url: str, entry: dict[str, object], points: int, spacing_ms: int
) -> None:
    """Push a short history of points for one metric."""
    values = entry["values"]
    now_ms = int(time.time() * 1000)

    for tick in range(points):
        value = values(tick) if callable(values) else values
        payload: dict[str, object] = {
            "name": entry["name"],
            "type": entry["type"],
            "value": value,
            "tags": entry["tags"],
            # The history is laid out backwards from now.
            "ts": now_ms - (points - 1 - tick) * spacing_ms,
        }
        if entry.get("unit"):
            payload["unit"] = entry["unit"]
        post_json(base_url, "/api/ingest", payload)


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        description="Register the metric catalog in a Laurel Metrics deployment."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dashboard", default="system")
    parser.add_argument(
        "--points", type=int, default=30, help="How many points per metric."
    )
    parser.add_argument(
        "--spacing",
        type=int,
        default=60,
        help="Seconds between the points of the history.",
    )
    parser.add_argument(
        "--time-range", default="24h", help="The time range of the new widgets."
    )
    parser.add_argument(
        "--show-curl",
        action="store_true",
        help="Print the curl command of every metric.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the seeder."""
    args = build_parser().parse_args(argv)
    base_url = args.base_url.rstrip("/")

    try:
        print(f"Target: {base_url}")

        dashboard_id = find_dashboard(base_url, args.dashboard)
        if dashboard_id is None:
            dashboard_id = create_dashboard(base_url, args.dashboard)
            print(f"Dashboard '{args.dashboard}' created with id {dashboard_id}")
        else:
            print(f"Dashboard '{args.dashboard}' found with id {dashboard_id}")

        existing = dashboard_metrics(base_url, dashboard_id)

        for entry in CATALOG:
            send_history(base_url, entry, args.points, args.spacing * 1000)
            print(f"Sent {args.points} points for '{entry['name']}' ({entry['type']})")

            if entry["name"] not in existing:
                create_widget(
                    base_url,
                    dashboard_id,
                    title=str(entry["title"]),
                    metric=str(entry["name"]),
                    chart_type=str(entry["chart_type"]),
                    agg=str(entry["agg"]),
                    time_range=args.time_range,
                )
                print(f"  widget '{entry['title']}' created")
            else:
                print(f"  widget for '{entry['name']}' already exists")

            if args.show_curl:
                values = entry["values"]
                sample = values(0) if callable(values) else values
                print(f"  {curl_for(base_url, entry, sample)}")

        print("\nRegistered metrics:")
        answer = query(base_url, "temperature", "avg")
        print(f"  temperature -> {json.dumps(answer)[:200]}")
        return 0
    except ApiError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
