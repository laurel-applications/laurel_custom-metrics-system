---
name: laurel-metrics-ingest
description: "Trigger: ingest metrics, push metric points, send telemetry, /api/ingest. Send typed metric points to a Laurel Metrics deployment."
license: Apache-2.0
metadata:
  author: "laurel-applications"
  version: "1.0"
---

# Laurel Metrics — Ingestion

## Activation Contract

Load this skill when a task sends, tests, or integrates metric points with a Laurel Metrics deployment (`POST {BASE_URL}/api/ingest`).

## Hard Rules

- Send **one point per request**. Never send an array.
- Send `Content-Type: application/json`. A wrong content type returns `415 UNSUPPORTED_MEDIA_TYPE`.
- Declare `type` on the **first** point of a metric. Later points inherit the stored type; a different one returns `409 METRIC_TYPE_MISMATCH`.
- Never invent a metric type. Use `counter` (monotonic total), `gauge` (a value that rises and falls), `timer` (a duration in ms), `string`, or `bool`.
- On `4xx`, read `error.code`, `error.field` and `error.hint`. Never retry a `4xx` payload unchanged.
- Take the base URL from the environment or the task. Default deployment: `https://metrics.andreslobaton.top`.

## Decision Gates

| Need | Type | Value shape |
| --- | --- | --- |
| Requests, bytes, errors | `counter` | number |
| CPU, memory, temperature | `gauge` | number |
| Latency, duration | `timer` | number (ms) |
| Version, status text | `string` | string |
| Feature flag | `bool` | boolean |

| Situation | Action |
| --- | --- |
| Metric already exists | Send the point; omit `type` |
| Metric is new | Send `type` on the first point |
| `409 METRIC_TYPE_MISMATCH` | Fix the payload type; never retry as is |
| `400 MISSING_TYPE` | The metric is new; add `type` |

## Execution Steps

1. Build the payload: `{"name", "type", "value", "unit"?, "tags"?, "ts"?}`.
2. `POST {BASE_URL}/api/ingest` with the JSON body.
3. Expect `201` with `{"status": "created", "metric": {...}, "data_point": {...}}`.
4. On `4xx`, surface `error.code` and `error.hint`; log `field`, `expected`, `received`.
5. Verify the point landed with `GET {BASE_URL}/api/query?metric={name}&agg=avg`.

## Output Contract

Return the endpoint, the metric name and type, the HTTP status and the `data_point.id`. On failure return `error.code`, `error.field` and `error.hint`.

## References

- `references/ingest-api.md` — full field contract, error table, query API, dashboard and widget creation.
- `assets/send_metrics.py` — dependency-free client template.
