"""All read logic: periods, totals, estimates, series, live watts and collector status.

Pure functions over a read-only connection. Each returns plain dataclasses, which are also the JSON
shape of the CLI ``--json`` output and the HTTP API. The CLI, the TUI and the API only format them.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from pcenergy import periods as _p
from pcenergy import storage

HEALTH_OK_S = 180


def to_dict(obj: Any) -> Any:
    """Dataclass (or list of them) → JSON-ready dict."""
    if isinstance(obj, list):
        return [to_dict(o) for o in obj]
    return asdict(obj)


# ---------------------------------------------------------------------- status


@dataclass
class SourceInfo:
    id: str
    device: str
    label: str
    in_total: bool
    first_seen: int


@dataclass
class Event:
    ts: int
    kind: str
    detail: str


@dataclass
class Status:
    health: str  # "ok" | "stale" | "none"
    last_sample_ts: int | None
    last_sample_age_s: float | None
    sources: list[SourceInfo]
    db_path: str
    db_size_bytes: int
    oldest_ts: int | None
    schema_version: int
    events: list[Event] = field(default_factory=list)
    service: str = "unknown"


def health_of(age_s: float | None) -> str:
    if age_s is None:
        return "none"
    return "ok" if age_s < HEALTH_OK_S else "stale"


def sources(conn: sqlite3.Connection) -> list[SourceInfo]:
    rows = conn.execute("SELECT id, device, label, in_total, first_seen FROM sources ORDER BY id").fetchall()
    return [SourceInfo(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows]


def events(conn: sqlite3.Connection, start: int = 0, end: int | None = None, limit: int = 200) -> list[Event]:
    end = end if end is not None else 2**62
    rows = conn.execute(
        "SELECT ts, kind, detail FROM events WHERE ts >= ? AND ts < ? ORDER BY ts DESC LIMIT ?",
        (start, end, limit),
    ).fetchall()
    return [Event(*r) for r in rows]


def status(
    conn: sqlite3.Connection, db_path: str, now: float | None = None, service: str = "unknown"
) -> Status:
    now = time.time() if now is None else now
    last = conn.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
    oldest = conn.execute(f"SELECT MIN(hour) FROM hourly WHERE source = '{storage.ON_SOURCE}'").fetchone()[0]
    age = None if last is None else max(now - last, 0.0)
    try:
        size = os.path.getsize(db_path)
    except OSError:
        size = 0
    return Status(
        health=health_of(age),
        last_sample_ts=last,
        last_sample_age_s=age,
        sources=sources(conn),
        db_path=db_path,
        db_size_bytes=size,
        oldest_ts=oldest,
        schema_version=storage.schema_version(conn),
        events=events(conn, limit=10),
        service=service,
    )


# ---------------------------------------------------------------------- energy model

DEVICES = ("cpu", "gpu", "igpu", "rest", "monitor")
MEASURED = ("cpu", "gpu", "igpu")
ESTIMATED = ("rest", "monitor")
FILTERS: dict[str, tuple[str, ...]] = {
    "all": DEVICES,
    "cpu": ("cpu",),
    "gpu": ("gpu", "igpu"),
    "rest": ESTIMATED,
    "measured": MEASURED,
}


@dataclass(frozen=True)
class Estimate:
    """The estimate settings. Applied at query time, so a change applies to all history."""

    base_watts: float = 60.0
    psu_efficiency: float = 0.90
    monitor_watts: float = 0.0
    include_estimate: bool = True
    price_per_kwh: float = 0.0
    currency_symbol: str = "$"
    fixed_monthly_fee: float = 0.0

    @classmethod
    def from_settings(cls, s: dict) -> Estimate:
        return cls(**{k: s[k] for k in cls.__dataclass_fields__})


def filter_devices(devices: str, est: Estimate) -> tuple[str, ...]:
    if devices not in FILTERS:
        raise ValueError(f"unknown device filter {devices!r}")
    keep = FILTERS[devices]
    if not est.include_estimate:
        keep = tuple(d for d in keep if d not in ESTIMATED)
    return keep


@dataclass
class Totals:
    """Energy of one span (an hour, a day, a period) in joules, by device, before filtering."""

    joules: dict[str, float] = field(default_factory=lambda: dict.fromkeys(DEVICES, 0.0))
    details: dict[str, float] = field(default_factory=dict)  # sub-zones such as cpu.core
    on_s: float = 0.0
    has_data: bool = False

    def add(self, other: Totals) -> None:
        for k, v in other.joules.items():
            self.joules[k] += v
        for k, v in other.details.items():
            self.details[k] = self.details.get(k, 0.0) + v
        self.on_s += other.on_s
        self.has_data = self.has_data or other.has_data

    def kwh(self, device: str) -> float:
        return self.joules[device] / 3_600_000

    def total_j(self, keep: tuple[str, ...], est: Estimate) -> float:
        """Total for the filtered devices. With the estimate on, measured parts and the rest are
        divided by the PSU efficiency (wall energy); monitors are outside the PSU."""
        dc = sum(self.joules[d] for d in keep if d not in ("monitor",))
        if est.include_estimate:
            dc /= est.psu_efficiency
        return dc + (self.joules["monitor"] if "monitor" in keep else 0.0)


def _source_map(conn: sqlite3.Connection) -> dict[str, tuple[str, bool]]:
    return {r[0]: (r[1], bool(r[2])) for r in conn.execute("SELECT id, device, in_total FROM sources")}


def hourly_totals(conn: sqlite3.Connection, start_ts: int, end_ts: int, est: Estimate) -> dict[int, Totals]:
    """Totals per UTC hour in [start_ts, end_ts), with the estimate applied."""
    smap = _source_map(conn)
    out: dict[int, Totals] = {}
    rows = conn.execute(
        "SELECT hour, source, joules, on_s FROM hourly WHERE hour >= ? AND hour < ?",
        (start_ts - start_ts % 3600, end_ts),
    )
    for hour, source, joules, on_s in rows:
        t = out.setdefault(hour, Totals())
        t.has_data = True
        if source == storage.ON_SOURCE:
            t.on_s += on_s
            t.joules["rest"] += est.base_watts * on_s
            t.joules["monitor"] += est.monitor_watts * on_s
            continue
        device, in_total = smap.get(source, ("cpu", True))
        if in_total:
            t.joules[device if device in t.joules else "cpu"] += joules
        else:
            t.details[source] = t.details.get(source, 0.0) + joules
    return out


# ---------------------------------------------------------------------- summary / series


@dataclass
class PeriodInfo:
    kind: str
    label: str
    start: str
    end: str
    range_text: str
    in_progress: bool
    hours: float
    elapsed_hours: float

    @classmethod
    def of(cls, p: _p.Period, now: float) -> PeriodInfo:
        elapsed = min(max(now - p.start_ts, 0), p.end_ts - p.start_ts) / 3600
        return cls(
            p.kind,
            p.label,
            p.start.isoformat(),
            p.end.isoformat(),
            p.range_text(),
            p.start_ts <= now < p.end_ts,
            p.hours,
            elapsed,
        )


@dataclass
class Peak:
    start: str
    watts: float


@dataclass
class Projection:
    total_kwh: float
    cost: float


@dataclass
class Summary:
    period: PeriodInfo
    devices: str
    total_kwh: float
    total_is_estimate: bool
    measured_kwh: float
    estimated_kwh: float
    per_device_kwh: dict[str, float]
    details_kwh: dict[str, float]
    cost: float
    currency_symbol: str
    fixed_fee: float
    avg_w: float | None
    peak: Peak | None
    on_hours: float
    coverage_pct: float | None
    projection: Projection | None


def _projection(
    p: _p.Period, now: float, total_kwh: float, day_on: dict, tz, est: Estimate
) -> Projection | None:
    """Average per on-day so far × days left. Today counts as the elapsed fraction of a day."""
    if not (p.start_ts <= now < p.end_ts) or total_kwh <= 0:
        return None
    today = datetime.fromtimestamp(now, tz).date()
    on_days = 0.0
    for d, on_s in day_on.items():
        if on_s <= 0:
            continue
        if d == today:
            midnight = _p.day_period(d, tz).start_ts
            on_days += max(now - midnight, 1) / 86400
        else:
            on_days += 1
    if on_days <= 0:
        return None
    days_left = (p.end_ts - now) / 86400
    projected = total_kwh + total_kwh / on_days * days_left
    return Projection(projected, projected * est.price_per_kwh)


def summary(
    conn: sqlite3.Connection,
    period: _p.Period,
    devices: str,
    est: Estimate,
    now: float | None = None,
    tz=None,
) -> Summary:
    now = time.time() if now is None else now
    tz = tz or period.start.tzinfo
    keep = filter_devices(devices, est)
    hours = hourly_totals(conn, period.start_ts, period.end_ts, est)
    total = Totals()
    peak: Peak | None = None
    peak_j = -1.0
    day_on: dict = {}
    for hour, t in sorted(hours.items()):
        total.add(t)
        j = t.total_j(keep, est)
        if j > peak_j and t.on_s > 0:
            peak_j = j
            peak = Peak(datetime.fromtimestamp(hour, tz).isoformat(), j / t.on_s)
        d = datetime.fromtimestamp(hour, tz).date()
        day_on[d] = day_on.get(d, 0.0) + t.on_s
    total_kwh = total.total_j(keep, est) / 3_600_000
    measured = sum(total.kwh(d) for d in keep if d in MEASURED)
    estimated = sum(total.kwh(d) for d in keep if d in ESTIMATED)
    present = {d for d in DEVICES if total.joules[d] > 0} | {"cpu", "gpu"}
    per_device = {d: total.kwh(d) for d in keep if d in present or d in ESTIMATED}
    if est.monitor_watts == 0:
        per_device.pop("monitor", None)
    info = PeriodInfo.of(period, now)
    elapsed_s = info.elapsed_hours * 3600
    return Summary(
        period=info,
        devices=devices,
        total_kwh=total_kwh,
        total_is_estimate=est.include_estimate,
        measured_kwh=measured,
        estimated_kwh=estimated,
        per_device_kwh=per_device,
        details_kwh={k: v / 3_600_000 for k, v in sorted(total.details.items())},
        cost=total_kwh * est.price_per_kwh,
        currency_symbol=est.currency_symbol,
        fixed_fee=est.fixed_monthly_fee if period.kind == "month" else 0.0,
        avg_w=(total_kwh * 3_600_000 / total.on_s) if total.on_s > 0 else None,
        peak=peak,
        on_hours=total.on_s / 3600,
        coverage_pct=(100 * total.on_s / elapsed_s) if elapsed_s > 0 else None,
        projection=_projection(period, now, total_kwh, day_on, tz, est),
    )


@dataclass
class Bucket:
    start: str
    label: str
    per_device_kwh: dict[str, float]
    total_kwh: float
    on_s: float
    avg_w: float | None
    has_data: bool
    future: bool


@dataclass
class Series:
    period: PeriodInfo
    bucket: str
    devices: str
    buckets: list[Bucket]


def _bucket_spans(period: _p.Period, bucket: str, week_start: str, tz) -> list[tuple[int, int, str, str]]:
    """(start_ts, end_ts, iso start, label) per bucket."""
    spans = []
    if bucket == "hour":
        for h in _p.hour_starts(period):
            local = datetime.fromtimestamp(h, tz)
            spans.append((h, h + 3600, local.isoformat(), f"{local:%H}"))
    elif bucket == "day":
        for d in period.days():
            dp = _p.day_period(d, tz)
            spans.append((dp.start_ts, dp.end_ts, dp.start.isoformat(), str(d.day)))
    elif bucket == "week":
        d = period.start.date()
        while d < period.end.date():
            w = _p.week_period(d, week_start, tz)
            s, e = max(w.start_ts, period.start_ts), min(w.end_ts, period.end_ts)
            first = datetime.fromtimestamp(s, tz).date()
            last = datetime.fromtimestamp(e - 1, tz).date()
            spans.append((s, e, datetime.fromtimestamp(s, tz).isoformat(), f"{first:%b %d}–{last:%d}"))
            d = w.end.date()
    else:
        raise ValueError(f"unknown bucket {bucket!r}")
    return spans


def series(
    conn: sqlite3.Connection,
    period: _p.Period,
    bucket: str,
    devices: str,
    est: Estimate,
    week_start: str = "monday",
    now: float | None = None,
    tz=None,
) -> Series:
    now = time.time() if now is None else now
    tz = tz or period.start.tzinfo
    keep = filter_devices(devices, est)
    hours = hourly_totals(conn, period.start_ts, period.end_ts, est)
    out = []
    for s, e, iso, label in _bucket_spans(period, bucket, week_start, tz):
        t = Totals()
        for h in range(s - s % 3600, e, 3600):
            if h in hours:
                t.add(hours[h])
        total_j = t.total_j(keep, est)
        out.append(
            Bucket(
                start=iso,
                label=label,
                per_device_kwh={d: t.kwh(d) for d in keep},
                total_kwh=total_j / 3_600_000,
                on_s=t.on_s,
                avg_w=(total_j / t.on_s) if t.on_s > 0 else None,
                has_data=t.has_data,
                future=s > now,
            )
        )
    return Series(PeriodInfo.of(period, now), bucket, devices, out)


# ---------------------------------------------------------------------- live / months


@dataclass
class LivePoint:
    ts: int
    watts: dict[str, float]
    total_w: float


@dataclass
class Live:
    health: str
    sample_age_s: float | None
    watts: dict[str, float]
    total_w: float | None
    total_is_estimate: bool
    history: list[LivePoint]


def live(
    conn: sqlite3.Connection,
    devices: str,
    est: Estimate,
    window_s: int = 120,
    history_s: int = 600,
    now: float | None = None,
) -> Live:
    """Current watts per device from the most recent samples, plus a short history for sparklines."""
    now = time.time() if now is None else now
    keep = filter_devices(devices, est)
    smap = _source_map(conn)
    rows = conn.execute(
        "SELECT s.ts, s.on_s, e.source, e.joules FROM samples s LEFT JOIN energy e ON e.ts = s.ts "
        "WHERE s.ts >= ? ORDER BY s.ts",
        (int(now - max(history_s, window_s)),),
    ).fetchall()
    points: dict[int, tuple[float, dict[str, float]]] = {}
    for ts, on_s, source, joules in rows:
        on, per = points.setdefault(ts, (on_s, dict.fromkeys(DEVICES, 0.0)))
        if source is None or on <= 0:
            continue
        device, in_total = smap.get(source, ("cpu", True))
        if in_total:
            per[device] += joules / on
    history = []
    for ts, (on, per) in sorted(points.items()):
        if on <= 0:
            continue
        per["rest"] = est.base_watts
        per["monitor"] = est.monitor_watts
        w = {d: per[d] for d in keep}
        t = Totals(joules=dict(per))
        history.append(LivePoint(ts, w, t.total_j(keep, est)))
    last_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
    age = None if last_ts is None else max(now - last_ts, 0.0)
    current = history[-1] if history and now - history[-1].ts <= window_s else None
    return Live(
        health=health_of(age),
        sample_age_s=age,
        watts=current.watts if current else {},
        total_w=current.total_w if current else None,
        total_is_estimate=est.include_estimate,
        history=history,
    )


@dataclass
class MonthRef:
    label: str
    start: str
    end: str
    range_text: str


def months(conn: sqlite3.Connection, start_day: int, tz, now: float | None = None) -> list[MonthRef]:
    """Month periods from the first one with data up to the current one, oldest first."""
    now = time.time() if now is None else now
    first = conn.execute(f"SELECT MIN(hour) FROM hourly WHERE source = '{storage.ON_SOURCE}'").fetchone()[0]
    current = _p.month_period(datetime.fromtimestamp(now, tz).date(), start_day, tz)
    if first is None:
        return [
            MonthRef(current.label, current.start.isoformat(), current.end.isoformat(), current.range_text())
        ]
    p = _p.month_period(datetime.fromtimestamp(first, tz).date(), start_day, tz)
    out = []
    while p.start_ts <= current.start_ts:
        out.append(MonthRef(p.label, p.start.isoformat(), p.end.isoformat(), p.range_text()))
        p = _p.shift_month(p, 1, start_day, tz)
    return out
