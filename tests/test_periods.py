from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pcenergy import periods as P

NY = ZoneInfo("America/New_York")
SETTINGS = {"month_start_day": 1, "week_start": "monday"}


def ymd(p):
    return p.start.date(), p.end.date()


def test_calendar_month():
    p = P.month_period(date(2026, 11, 18), 1, NY)
    assert ymd(p) == (date(2026, 11, 1), date(2026, 12, 1))
    assert p.label == "2026-11"


def test_month_start_day_15():
    p = P.month_period(date(2026, 11, 18), 15, NY)
    assert ymd(p) == (date(2026, 11, 15), date(2026, 12, 15))
    assert p.label == "2026-11"
    before = P.month_period(date(2026, 11, 10), 15, NY)
    assert ymd(before) == (date(2026, 10, 15), date(2026, 11, 15))
    assert before.label == "2026-10"


@pytest.mark.parametrize(
    ("year", "month", "start", "end"),
    [
        (2026, 4, date(2026, 4, 30), date(2026, 5, 31)),  # 31 clamps to 30 Apr
        (2026, 2, date(2026, 2, 28), date(2026, 3, 31)),  # 31 clamps to 28 Feb
        (2028, 2, date(2028, 2, 29), date(2028, 3, 31)),  # leap year
        (2026, 12, date(2026, 12, 31), date(2027, 1, 31)),  # year boundary
    ],
)
def test_month_start_day_31_clamps(year, month, start, end):
    p = P.month_starting_in(year, month, 31, NY)
    assert ymd(p) == (start, end)


def test_day_in_clamped_gap_belongs_to_previous_period():
    # start day 31: 1 Mar 2026 is inside the period that starts 28 Feb
    p = P.month_period(date(2026, 3, 1), 31, NY)
    assert ymd(p) == (date(2026, 2, 28), date(2026, 3, 31))


def test_shift_month():
    p = P.month_starting_in(2026, 1, 15, NY)
    assert P.shift_month(p, -1, 15, NY).label == "2025-12"
    assert P.shift_month(p, 13, 15, NY).label == "2027-02"


@pytest.mark.parametrize(
    ("week_start", "expected"), [("monday", date(2026, 11, 16)), ("sunday", date(2026, 11, 15))]
)
def test_week_start(week_start, expected):
    p = P.week_period(date(2026, 11, 18), week_start, NY)  # a Wednesday
    assert p.start.date() == expected
    assert (p.end.date() - p.start.date()).days == 7


def test_week_start_on_the_start_day_itself():
    assert P.week_period(date(2026, 11, 15), "sunday", NY).start.date() == date(2026, 11, 15)
    assert P.week_period(date(2026, 11, 15), "monday", NY).start.date() == date(2026, 11, 9)


def test_dst_spring_forward_day_has_23_hours():
    p = P.day_period(date(2026, 3, 8), NY)
    assert p.hours == 23
    assert len(P.hour_starts(p)) == 23


def test_dst_fall_back_day_has_25_hours():
    p = P.day_period(date(2026, 11, 1), NY)
    assert p.hours == 25
    assert len(P.hour_starts(p)) == 25


def test_month_with_dst_change_hours():
    p = P.month_starting_in(2026, 11, 1, NY)
    assert p.hours == 30 * 24 + 1


def test_parse_period():
    today = date(2026, 11, 18)
    assert P.parse_period("month", None, SETTINGS, NY, today).label == "2026-11"
    assert P.parse_period("month", "-1", SETTINGS, NY, today).label == "2026-10"
    assert P.parse_period("month", "+1", SETTINGS, NY, today).label == "2026-12"
    assert P.parse_period("month", "2025-02", SETTINGS, NY, today).label == "2025-02"
    assert P.parse_period("day", "-1", SETTINGS, NY, today).label == "2026-11-17"
    assert P.parse_period("day", "2026-01-05", SETTINGS, NY, today).label == "2026-01-05"
    assert P.parse_period("week", "-1", SETTINGS, NY, today).start.date() == date(2026, 11, 9)
    with pytest.raises(ValueError):
        P.parse_period("month", "2026-13", SETTINGS, NY, today)
    with pytest.raises(ValueError):
        P.parse_period("day", "yesterday", SETTINGS, NY, today)


def test_contains_and_range_text():
    p = P.month_starting_in(2026, 11, 15, NY)
    assert p.contains(datetime(2026, 12, 14, 23, 59, tzinfo=NY))
    assert not p.contains(datetime(2026, 12, 15, 0, 0, tzinfo=NY))
    assert p.range_text() == "Nov 15 – Dec 14"
    assert len(p.days()) == 30
