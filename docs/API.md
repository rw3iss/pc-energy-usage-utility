# pcenergy HTTP API (v1)

`pcenergy serve` starts a small **read-only** JSON API. It is meant for a desktop widget or a local web
page. A KDE plasmoid only needs `/live` and `/summary`.

```bash
pcenergy serve                       # http://127.0.0.1:8787/api/v1/
pcenergy serve --port 9000 --cors http://localhost:5173
```

- Binds to `127.0.0.1:8787` by default. A non-local `--host` prints a warning: there is no authentication.
- All routes are `GET`. Responses are JSON with `Cache-Control: no-store`.
- `--cors ORIGIN` adds `Access-Control-Allow-Origin: ORIGIN` (no CORS header without it).
- Each request reads the current settings, so a settings change applies at once.
- Energy is in kWh, power in W. Estimated parts (`rest`, `monitor`) are never stored: they are computed
  from the settings at request time. `total_is_estimate: true` means the total is the wall estimate
  (measured + rest, divided by the PSU efficiency).
- Times: `*_ts` fields are UTC unix seconds; `start` / `end` strings are ISO 8601 in local time.

Optional user service (off by default): `packaging/systemd/pcenergy-api.service`.

## Errors

Status 400, 404, 503 or 500 with a body:

```json
{"error": "devices must be one of ('all', 'cpu', 'gpu', 'rest', 'measured')", "code": "bad_devices"}
```

| Code | Status | When |
|---|---|---|
| `not_found` | 404 | Unknown route |
| `bad_period` | 400 | `period` is not `month`, `week` or `day` |
| `bad_date` | 400 | `date` is not `YYYY-MM-DD` |
| `bad_devices` | 400 | `devices` is not `all`, `cpu`, `gpu`, `rest` or `measured` |
| `bad_bucket` | 400 | `bucket` is not `hour`, `day` or `week` |
| `no_database` | 503 | The database file does not exist (collector not installed) |
| `internal` | 500 | Unexpected error (see the server log) |

## Common parameters

| Parameter | Values | Default |
|---|---|---|
| `period` | `month`, `week`, `day` | `month` |
| `date` | `YYYY-MM-DD`: any day inside the wanted period | today |
| `devices` | `all`, `cpu`, `gpu` (dGPU + iGPU), `rest` (estimate), `measured` (CPU + GPU) | setting `default_devices` |
| `bucket` (series) | `hour`, `day`, `week` | `hour` for a day, else `day` |

## `GET /api/v1/status`

Collector health (`ok`: last sample < 3 min old; `stale`; `none`), sources, database and recent events.

```json
{
  "health": "ok",
  "last_sample_ts": 1795055400,
  "last_sample_age_s": 0.0,
  "sources": [
    {
      "id": "cpu.core",
      "device": "cpu",
      "label": "Fake CPU (core)",
      "in_total": false,
      "first_seen": 1791599400
    },
    {
      "id": "cpu.package",
      "device": "cpu",
      "label": "Fake CPU",
      "in_total": true,
      "first_seen": 1791599400
    },
    {
      "id": "gpu.0",
      "device": "gpu",
      "label": "Fake GPU",
      "in_total": true,
      "first_seen": 1791599400
    }
  ],
  "db_path": "/var/lib/pcenergy/energy.db",
  "db_size_bytes": 7794688,
  "oldest_ts": 1791597600,
  "schema_version": 1,
  "events": [
    {
      "ts": 1793379600,
      "kind": "suspend",
      "detail": "7200s asleep"
    },
    {
      "ts": 1792792800,
      "kind": "boot",
      "detail": "uptime 52s"
    }
  ],
  "service": "inactive"
}
```

## `GET /api/v1/live?devices=all`

Current watts per device from the most recent sample, plus up to 10 minutes of history for a sparkline.
`watts` is empty and `total_w` is `null` when the last sample is older than 2 minutes.

```json
{
  "health": "ok",
  "sample_age_s": 0.0,
  "watts": {
    "cpu": 88.143,
    "gpu": 244.912,
    "igpu": 0.0,
    "rest": 60.0,
    "monitor": 0.0
  },
  "total_w": 436.728,
  "total_is_estimate": true,
  "history": [
    {
      "ts": 1795055340,
      "watts": {
        "cpu": 99.82,
        "gpu": 238.99,
        "igpu": 0.0,
        "rest": 60.0,
        "monitor": 0.0
      },
      "total_w": 443.123
    },
    {
      "ts": 1795055400,
      "watts": {
        "cpu": 88.143,
        "gpu": 244.912,
        "igpu": 0.0,
        "rest": 60.0,
        "monitor": 0.0
      },
      "total_w": 436.728
    }
  ]
}
```

## `GET /api/v1/summary?period=month&date=2026-11-18&devices=all`

Totals for one period. `projection` is set only for a period in progress (average per on-day so far × days
left). `fixed_fee` is the monthly fee (month periods only), not included in `cost`.

```json
{
  "period": {
    "kind": "month",
    "label": "2026-11",
    "start": "2026-11-01T00:00:00-04:00",
    "end": "2026-12-01T00:00:00-05:00",
    "range_text": "Nov 1 – Nov 30",
    "in_progress": true,
    "hours": 721.0,
    "elapsed_hours": 430.5
  },
  "devices": "all",
  "total_kwh": 75.376,
  "total_is_estimate": true,
  "measured_kwh": 48.728,
  "estimated_kwh": 19.11,
  "per_device_kwh": {
    "cpu": 21.372,
    "gpu": 27.357,
    "rest": 19.11
  },
  "details_kwh": {
    "cpu.core": 12.827
  },
  "cost": 11.306,
  "currency_symbol": "$",
  "fixed_fee": 0.0,
  "avg_w": 236.659,
  "peak": {
    "start": "2026-11-07T20:00:00-05:00",
    "watts": 462.26
  },
  "on_hours": 318.5,
  "coverage_pct": 73.984,
  "projection": {
    "total_kwh": 126.358,
    "cost": 18.954
  }
}
```

## `GET /api/v1/series?period=day&date=2026-11-17&bucket=hour`

Energy per bucket (hour, day or week) for a period. A DST day has 23 or 25 hour buckets. `future` buckets
are not reached yet; `has_data: false` means no samples (PC off or collector stopped).

Example (2 of 24 buckets shown):

```json
{
  "period": {
    "kind": "day",
    "label": "2026-11-17",
    "start": "2026-11-17T00:00:00-05:00",
    "end": "2026-11-18T00:00:00-05:00",
    "range_text": "Tue 17 Nov 2026",
    "in_progress": false,
    "hours": 24.0,
    "elapsed_hours": 24.0
  },
  "bucket": "hour",
  "devices": "all",
  "buckets": [
    {
      "start": "2026-11-17T20:00:00-05:00",
      "label": "20",
      "per_device_kwh": {
        "cpu": 0.1,
        "gpu": 0.249,
        "rest": 0.06
      },
      "total_kwh": 0.455,
      "on_s": 3600.0,
      "avg_w": 454.559,
      "has_data": true,
      "future": false
    },
    {
      "start": "2026-11-17T21:00:00-05:00",
      "label": "21",
      "per_device_kwh": {
        "cpu": 0.104,
        "gpu": 0.245,
        "rest": 0.06
      },
      "total_kwh": 0.454,
      "on_s": 3600.0,
      "avg_w": 453.602,
      "has_data": true,
      "future": false
    }
  ]
}
```

## `GET /api/v1/months`

Month periods with data, oldest first (the last one is the current period). Use it for paging limits.

```json
[
  {
    "label": "2026-10",
    "start": "2026-10-01T00:00:00-04:00",
    "end": "2026-11-01T00:00:00-04:00",
    "range_text": "Oct 1 – Oct 31"
  },
  {
    "label": "2026-11",
    "start": "2026-11-01T00:00:00-04:00",
    "end": "2026-12-01T00:00:00-05:00",
    "range_text": "Nov 1 – Nov 30"
  }
]
```

## `GET /api/v1/config`

The user settings (read-only; there is no write route in v1).

```json
{
  "month_start_day": 1,
  "price_per_kwh": 0.15,
  "currency_symbol": "$",
  "fixed_monthly_fee": 0.0,
  "base_watts": 60.0,
  "psu_efficiency": 0.9,
  "monitor_watts": 0.0,
  "include_estimate": true,
  "default_devices": "all",
  "week_start": "monday",
  "day_graph_unit": "W",
  "graph_style": "unicode",
  "color": true,
  "refresh_s": 60,
  "theme": "dark"
}
```
