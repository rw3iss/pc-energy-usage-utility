"""Calendar periods in local time: billing months, weeks and days.

* A month period starts on ``month_start_day`` (1–31). A start day past the end of a month clamps to
  the month's last day (31 → 30 Apr, 28/29 Feb). The label is the month the period starts in.
* A week starts on ``week_start`` (monday or sunday).
* A day is local midnight to midnight, so DST days have 23 or 25 hours.

All period math lives here. Periods carry aware local datetimes; ``start_ts``/``end_ts`` are UTC
unix seconds for queries.
"""

from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_tz() -> tzinfo:
    """The system time zone as a DST-aware zone (TZ env, then /etc/localtime)."""
    if tz := os.environ.get("TZ"):
        try:
            return ZoneInfo(tz.lstrip(":"))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    link = Path("/etc/localtime")
    try:
        target = str(link.resolve())
        if "zoneinfo/" in target:
            return ZoneInfo(target.split("zoneinfo/", 1)[1])
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    try:
        with link.open("rb") as fh:
            return ZoneInfo.from_file(fh, key="localtime")
    except OSError:
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class Period:
    kind: str  # "month" | "week" | "day"
    start: datetime  # aware, local, inclusive
    end: datetime  # aware, local, exclusive
    label: str

    @property
    def start_ts(self) -> int:
        return int(self.start.timestamp())

    @property
    def end_ts(self) -> int:
        return int(self.end.timestamp())

    @property
    def hours(self) -> float:
        return (self.end_ts - self.start_ts) / 3600

    def contains(self, moment: datetime) -> bool:
        return self.start_ts <= moment.timestamp() < self.end_ts

    def contains_date(self, d: date) -> bool:
        return self.start.date() <= d < self.end.date()

    def days(self) -> list[date]:
        out, d = [], self.start.date()
        while d < self.end.date():
            out.append(d)
            d += timedelta(days=1)
        return out

    def range_text(self) -> str:
        last = (self.end - timedelta(seconds=1)).date()
        if self.kind == "day":
            return f"{self.start:%a %d %b %Y}"
        return f"{self.start:%b} {self.start.day} – {last:%b} {last.day}"


def _midnight(d: date, tz: tzinfo) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=tz)


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    i = year * 12 + (month - 1) + n
    return i // 12, i % 12 + 1


def month_starting_in(year: int, month: int, start_day: int, tz: tzinfo) -> Period:
    """The billing period that starts in ``year``-``month``."""
    s = _clamped(year, month, start_day)
    ny, nm = _add_months(year, month, 1)
    e = _clamped(ny, nm, start_day)
    return Period("month", _midnight(s, tz), _midnight(e, tz), f"{year:04d}-{month:02d}")


def month_period(d: date, start_day: int, tz: tzinfo) -> Period:
    """The billing period that contains day ``d``."""
    p = month_starting_in(d.year, d.month, start_day, tz)
    if d < p.start.date():
        y, m = _add_months(d.year, d.month, -1)
        p = month_starting_in(y, m, start_day, tz)
    return p


def shift_month(p: Period, n: int, start_day: int, tz: tzinfo) -> Period:
    y, m = int(p.label[:4]), int(p.label[5:7])
    y, m = _add_months(y, m, n)
    return month_starting_in(y, m, start_day, tz)


def week_period(d: date, week_start: str, tz: tzinfo) -> Period:
    offset = d.weekday() if week_start == "monday" else (d.weekday() + 1) % 7
    s = d - timedelta(days=offset)
    e = s + timedelta(days=7)
    iso = s.isocalendar()
    return Period("week", _midnight(s, tz), _midnight(e, tz), f"{iso.year}-W{iso.week:02d}")


def day_period(d: date, tz: tzinfo) -> Period:
    return Period("day", _midnight(d, tz), _midnight(d + timedelta(days=1), tz), d.isoformat())


def period_of(kind: str, d: date, settings: dict, tz: tzinfo) -> Period:
    if kind == "month":
        return month_period(d, settings["month_start_day"], tz)
    if kind == "week":
        return week_period(d, settings["week_start"], tz)
    if kind == "day":
        return day_period(d, tz)
    raise ValueError(f"unknown period kind {kind!r}")


def shift(p: Period, n: int, settings: dict, tz: tzinfo) -> Period:
    """The period ``n`` steps before (n < 0) or after (n > 0) ``p``."""
    if p.kind == "month":
        return shift_month(p, n, settings["month_start_day"], tz)
    step = 7 if p.kind == "week" else 1
    return period_of(p.kind, p.start.date() + timedelta(days=step * n), settings, tz)


def parse_period(kind: str, arg: str | None, settings: dict, tz: tzinfo, today: date | None = None) -> Period:
    """CLI period argument: none (current), ``-N``/``+N`` (relative), ``YYYY-MM`` (month) or
    ``YYYY-MM-DD`` (the period containing that day)."""
    today = today or datetime.now(tz).date()
    current = period_of(kind, today, settings, tz)
    if not arg:
        return current
    if re.fullmatch(r"[+-]\d+", arg):
        return shift(current, int(arg), settings, tz)
    if kind == "month" and re.fullmatch(r"\d{4}-\d{2}", arg):
        y, m = int(arg[:4]), int(arg[5:])
        if not 1 <= m <= 12:
            raise ValueError(f"bad month {arg!r}")
        return month_starting_in(y, m, settings["month_start_day"], tz)
    try:
        return period_of(kind, date.fromisoformat(arg), settings, tz)
    except ValueError:
        raise ValueError(f"bad {kind} {arg!r}: use YYYY-MM-DD, -N or +N") from None


def hour_starts(p: Period) -> list[int]:
    """UTC unix hour starts inside ``p`` (23 or 25 on DST days)."""
    return list(range(p.start_ts - p.start_ts % 3600, p.end_ts, 3600))
