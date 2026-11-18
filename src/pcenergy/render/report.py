"""Summary lines, tables and graphs built from query results. Shared by the CLI and the TUI."""

from __future__ import annotations

from datetime import datetime

from pcenergy.query import Bucket, Series, Summary
from pcenergy.render.bars import Bar, legend, stacked_bars
from pcenergy.render.text import DEVICE_NAMES, DEVICE_ORDER, Line
from pcenergy.units import fmt_cost, fmt_energy, fmt_kwh_short, fmt_watts

ESTIMATED = ("rest", "monitor")


def _sep() -> tuple[str, str]:
    return (" │ ", "dim")


def summary_lines(s: Summary, title: str, width: int = 200) -> list[Line]:
    """Totals, per-device split, averages, projection and on-time, packed into as few lines as fit
    in ``width`` (two lines on a wide terminal)."""
    est = s.total_is_estimate
    total: Line = [
        (f"{title}  ", "bold"),
        (fmt_energy(s.total_kwh, est), "bold"),
        (" wall" if est else " measured", "dim"),
        ("  ", ""),
        (fmt_cost(s.cost, s.currency_symbol), "cost"),
    ]
    devices: Line = []
    for d in DEVICE_ORDER:
        if d in s.per_device_kwh:
            devices += [(f"{DEVICE_NAMES[d]} ", d), (f"{fmt_energy(s.per_device_kwh[d])}  ", "")]
    if devices:
        devices[-1] = (devices[-1][0].rstrip(), "")
    avg: Line = [(f"avg {fmt_watts(s.avg_w)}", "")]
    if s.peak:
        when = datetime.fromisoformat(s.peak.start)
        stamp = f"{when:%H:%M}" if s.period.kind == "day" else f"{when:%b %d %H:%M}"
        avg.append((f"  peak {stamp} ({fmt_watts(s.peak.watts)})", "dim"))
    groups = [total, avg, devices]
    second: list[Line] = []
    if s.projection:
        second.append(
            [
                ("Projected  ", "dim"),
                (fmt_energy(s.projection.total_kwh, est), ""),
                ("  ", ""),
                (fmt_cost(s.projection.cost, s.currency_symbol), "cost"),
            ]
        )
    cov = f" ({s.coverage_pct:.0f} %)" if s.coverage_pct is not None else ""
    second.append([(f"on {s.on_hours:.0f} h of {s.period.elapsed_hours:.0f} h{cov}", "dim")])
    if s.fixed_fee:
        second.append([(f"+ {fmt_cost(s.fixed_fee, s.currency_symbol)} fixed fee", "cost")])
    if s.details_kwh:
        detail = "  ".join(f"{k} {fmt_energy(v)}" for k, v in s.details_kwh.items())
        second.append([(f"detail: {detail}", "dim")])
    return pack([g for g in groups + second if g], width)


def _len(line: Line) -> int:
    return sum(len(t) for t, _ in line)


def pack(groups: list[Line], width: int) -> list[Line]:
    """Join groups with " │ " separators, starting a new line when the next group would not fit."""
    lines: list[Line] = []
    for g in groups:
        if lines and _len(lines[-1]) + 3 + _len(g) <= width:
            lines[-1] = [*lines[-1], _sep(), *g]
        else:
            lines.append(list(g))
    return lines


def series_bars(series: Series, unit: str = "kWh", selected: int | None = None, today: str | None = None):
    """Bars for a series. ``unit`` "W" plots average watts (day graphs), else kWh."""
    bars = []
    for i, b in enumerate(series.buckets):
        if unit == "W":
            scale = 3_600_000 / b.on_s if b.on_s > 0 else 0.0
            values = {d: v * scale for d, v in b.per_device_kwh.items()}
        else:
            values = dict(b.per_device_kwh)
        bars.append(
            Bar(
                label=b.label,
                values=values,
                missing=not b.has_data,
                future=b.future,
                selected=selected == i,
                today=today is not None and b.start.startswith(today),
            )
        )
    return bars


def graph(
    series: Series,
    width: int,
    height: int,
    unit: str = "kWh",
    ascii_mode: bool = False,
    selected: int | None = None,
    today: str | None = None,
    prefix: tuple[str, str] | None = None,
) -> list[Line]:
    devices = [d for d in DEVICE_ORDER if any(d in b.per_device_kwh for b in series.buckets)]
    head: Line = [(f"  {unit}", "dim"), (" " * max(width - 12 - 12 * len(devices), 1), "")]
    head += legend(devices, ascii_mode)
    bars = series_bars(series, unit, selected, today)
    return [head, *stacked_bars(bars, width, height, ascii_mode, prefix=prefix)]


def table(header: list[str], rows: list[list[tuple[str, str]]], right: set[int]) -> list[Line]:
    widths = [len(h) for h in header]
    for row in rows:
        for i, (text, _) in enumerate(row):
            widths[i] = max(widths[i], len(text))

    def fmt(i: int, text: str) -> str:
        return text.rjust(widths[i]) if i in right else text.ljust(widths[i])

    out: list[Line] = [[(" " + "  ".join(fmt(i, h) for i, h in enumerate(header)), "bold")]]
    for row in rows:
        line: Line = [(" ", "")]
        for i, (text, style) in enumerate(row):
            line.append((fmt(i, text), style))
            if i < len(row) - 1:
                line.append(("  ", ""))
        out.append(line)
    return out


def bucket_table(series: Series, first_col: str, currency: str, price: float, limit_future: bool = True):
    devices = [d for d in DEVICE_ORDER if any(d in b.per_device_kwh for b in series.buckets)]
    header = [first_col, *[DEVICE_NAMES[d] for d in devices], "Total kWh", "Cost", "Avg W", "On h"]
    rows = []
    for b in series.buckets:
        if limit_future and b.future:
            continue
        rows.append(_bucket_row(b, devices, currency, price, series.bucket))
    return table(header, rows, right=set(range(1, len(header))))


def _bucket_row(b: Bucket, devices: list[str], currency: str, price: float, kind: str):
    start = datetime.fromisoformat(b.start)
    if kind == "hour":
        first = f"{start:%H:%M}"
    elif kind == "day":
        first = f"{start:%a %d %b}"
    else:
        first = b.label
    if not b.has_data:
        return [
            (first, "dim"),
            *[("·", "missing") for _ in devices],
            ("·", "missing"),
            ("", ""),
            ("", ""),
            ("", ""),
        ]
    cells = [(first, "")]
    cells += [(fmt_kwh_short(b.per_device_kwh[d]), d) for d in devices]
    cells += [
        (fmt_kwh_short(b.total_kwh), "bold"),
        (fmt_cost(b.total_kwh * price, currency), "cost"),
        (f"{b.avg_w:.0f}" if b.avg_w else "–", ""),
        (f"{b.on_s / 3600:.1f}", "dim"),
    ]
    return cells


def top_days(series: Series, n: int = 5) -> list[Bucket]:
    return sorted((b for b in series.buckets if b.has_data), key=lambda b: b.total_kwh, reverse=True)[:n]
