# Laurel Metrics — ingestion API reference

Base URL of the reference deployment: `https://metrics.andreslobaton.top`

Every example below was executed against that deployment.

## Endpoint

```http
POST /api/ingest HTTP/1.1
Host: metrics.andreslobaton.top
Content-Type: application/json

{"name": "temperature", "type": "gauge", "value": 24.5, "unit": "C", "tags": {"sensor": "ambient"}}
```

One request carries exactly one point. There is no batch endpoint.

## Payload fields

| Field | Required | Type | Constraint |
| --- | --- | --- | --- |
| `name` | yes | string | `^[A-Za-z0-9][A-Za-z0-9._\-]*$`, 1–255 chars |
| `type` | only for a new metric | string | `counter`, `gauge`, `timer`, `string`, `bool` |
| `value` | yes | number / string / boolean | number for `counter`, `gauge`, `timer`; string for `string`; boolean for `bool` |
| `unit` | no | string | max 32 chars |
| `tags` | no | object | max 32 keys, key max 64 chars, values are strings |
| `ts` | no | string or number | ISO-8601 timestamp or epoch milliseconds; the server defaults to now |

The metric is created by the first point that carries its `type`. After that the type is
frozen: the server rejects a different one instead of silently converting the data.

## Success response

```http
HTTP/1.1 201 Created
```

```json
{
  "status": "created",
  "metric": {"id": 1, "name": "temperature", "type": "gauge", "unit": "C"},
  "data_point": {
    "id": 1,
    "ts_utc_ms": 1794891881993,
    "value": 24.12,
    "tags": {"sensor": "ambient"}
  }
}
```

`201` is returned for every accepted point, including the ones that reuse an existing metric.

## Error response

Every failure uses the same envelope, with the machine code first:

```json
{
  "error": {
    "code": "METRIC_TYPE_MISMATCH",
    "message": "The metric 'temperature' is a 'gauge'.",
    "field": "type",
    "expected": "gauge",
    "received": "counter",
    "hint": "..."
  }
}
```

| Code | HTTP | Cause |
| --- | --- | --- |
| `INVALID_METRIC_NAME` | 400 | The name is missing, empty or breaks the pattern |
| `INVALID_METRIC_TYPE` | 400 | The type is not one of the five known types |
| `MISSING_TYPE` | 400 | The metric is new and the point carries no type |
| `INVALID_VALUE` | 400 | The value does not fit the metric type |
| `NON_FINITE_VALUE` | 400 | The value is `NaN` or infinite |
| `INVALID_UNIT` | 400 | The unit is longer than 32 chars |
| `INVALID_TAGS` | 400 | The tags are not an object of strings |
| `TOO_MANY_TAGS` | 400 | More than 32 tags |
| `INVALID_TIMESTAMP` | 400 | The timestamp cannot be parsed |
| `AMBIGUOUS_TIMESTAMP` | 400 | The epoch value is between seconds and milliseconds |
| `INVALID_JSON` | 400 | The body is not a JSON object |
| `INVALID_BODY` | 400 | The body is empty |
| `METRIC_TYPE_MISMATCH` | 409 | The point declares a type the metric does not have |
| `METRIC_CONFLICT` | 409 | The metric could not be created concurrently |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | The `Content-Type` is not JSON |

## Reading the data back

```http
GET /api/query?metric=temperature&agg=avg
```

| Parameter | Meaning |
| --- | --- |
| `metric` | Required, the metric name |
| `agg` | `sum`, `avg`, `min`, `max`, `count`, `last` |
| `from` / `to` | ISO-8601 or epoch ms; `from` defaults to 24 hours before `to` |
| `group_by` | Tag key to split the series by |
| `filters` | JSON array of `{field, op, value}` |

```json
{
  "metric": {"id": 1, "name": "temperature", "type": "gauge", "unit": "C"},
  "agg": "avg",
  "bucket_sec": 900,
  "group_by": null,
  "from_ms": 1794805486302,
  "to_ms": 1794891886302,
  "point_count": 5,
  "bucket_count": 1,
  "series": [{"key": null, "points": [{"ts": 1794891600000, "value": 24.69, "count": 5}]}]
}
```

The known metrics are listed with `GET /api/metrics`, and the tags of one metric with
`GET /api/metrics/{name}/tags`.

## Dashboards and widgets

Both are created with form encoded bodies, not JSON, and both answer `303` with the
location of the new resource.

```bash
# Dashboard: the name is the only field it needs.
curl -X POST https://metrics.andreslobaton.top/dashboards \
  -d 'name=temperature' -i

# Widget: the metric must already exist, because the ingestion creates it.
curl -X POST https://metrics.andreslobaton.top/dashboards/1/widgets \
  -d 'title=Temperature' \
  -d 'metric=temperature' \
  -d 'chart_type=line' \
  -d 'agg=avg' \
  -d 'bucket=auto' \
  -d 'time_range=1h' \
  -d 'group_by=' \
  -d 'filters=[]' -i
```

| Widget field | Allowed values |
| --- | --- |
| `chart_type` | `line`, `bar`, `table`, `stat` |
| `agg` | `sum`, `avg`, `min`, `max`, `count`, `last`, filtered by the metric type |
| `bucket` | `auto` or a number of seconds |
| `time_range` | `1h`, `6h`, `24h`, `7d`, `30d` |
| `group_by` | A tag key, or empty for no grouping |
| `filters` | JSON array of `{field, op, value}` |

## Health endpoint

```http
GET /health
```

```json
{"status": "ok"}
```

The container image runs the same endpoint as its `HEALTHCHECK`.

## Operational notes

- The deployment has no authentication. Anything that can reach the URL can write points.
- The server is behind TLS, so the URL is `https://`.
- The timestamps are epoch milliseconds in UTC; the query API answers in the same unit.
- A rejected point is not stored: the response is the only trace of the failure.
