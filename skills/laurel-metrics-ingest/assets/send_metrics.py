#!/usr/bin/env python3
"""Template for a consumer that pushes points to Laurel Metrics.

Copy it, set the metric type and the payload, and run it. It only uses the
standard library, so it needs no ``pip install``.

    python send_metrics.py --base-url https://metrics.andreslobaton.top
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://metrics.andreslobaton.top"


def send_point(
    base_url: str,
    *,
    name: str,
    metric_type: str,
    value: float | str | bool,
    unit: str | None = None,
    tags: dict[str, str] | None = None,
    timestamp_ms: int | None = None,
    timeout: float = 15.0,
) -> dict:
    """Send one metric point and return the decoded answer.

    Raises ``RuntimeError`` when the server rejects the point, with the
    machine code and the hint of the error envelope in the message.
    """
    payload: dict[str, object] = {"name": name, "type": metric_type, "value": value}
    if unit:
        payload["unit"] = unit
    if tags:
        payload["tags"] = tags
    if timestamp_ms is not None:
        payload["ts"] = timestamp_ms

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/ingest",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            envelope = json.loads(raw).get("error", {})
            raise RuntimeError(
                f"{error.code} {envelope.get('code')}: {envelope.get('message')}"
                f" (field={envelope.get('field')}, hint={envelope.get('hint')})"
            ) from None
        except ValueError:
            raise RuntimeError(f"{error.code}: {raw[:200]}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"could not reach the server: {error.reason}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a point every second.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--metric", default="temperature")
    parser.add_argument("--type", default="gauge", choices=("gauge", "counter", "timer"))
    parser.add_argument("--unit", default="C")
    parser.add_argument("--count", type=int, default=0, help="0 means run forever")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    sent = 0
    try:
        while not args.count or sent < args.count:
            answer = send_point(
                args.base_url,
                name=args.metric,
                metric_type=args.type,
                value=round(20.0 + 5.0 * (sent % 20) / 20.0, 2),
                unit=args.unit,
                tags={"sensor": "ambient"},
            )
            point = answer.get("data_point", {})
            print(f"{args.metric} = {point.get('value')} (id {point.get('id')})")
            sent += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
