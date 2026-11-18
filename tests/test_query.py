from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pcenergy import periods as P
from pcenergy import query, storage
from pcenergy.query import Estimate

NY = ZoneInfo("America/New_York")
EST = Estimate(base_watts=60, psu_efficiency=0.9, monitor_watts=0, price_per_kwh=0.2)


class Src:
    def __init__(self, sid, device, in_total=True):
        self.id, self.device, self.label, self.in_total = sid, device, sid, in_total


def ts(y, m, d, h=0, minute=0):
    return int(datetime(y, m, d, h, minute, tzinfo=NY).timestamp())


@pytest.fixture
def conn(tmp_path):
    c = storage.connect_rw(str(tmp_path / "e.db"))
    storage.register_sources(
        c, [Src("cpu.package", "cpu"), Src("cpu.core", "cpu", False), Src("gpu.0", "gpu")], 0
    )
    return c


def fill_hour(c, start, cpu_w, gpu_w, minutes=60):
    """Write one sample per minute at constant watts."""
    for i in range(1, minutes + 1):
        storage.write_sample(
            c,
            start + 60 * i,
            60,
            {"cpu.package": cpu_w * 60, "cpu.core": cpu_w * 30, "gpu.0": gpu_w * 60},
            {},
        )


def test_day_summary_math(conn):
    fill_hour(conn, ts(2026, 11, 10, 20), cpu_w=100, gpu_w=200)
    p = P.day_period(date(2026, 11, 10), NY)
    s = query.summary(conn, p, "all", EST, now=ts(2026, 11, 20))
    assert s.per_device_kwh["cpu"] == pytest.approx(0.1)
    assert s.per_device_kwh["gpu"] == pytest.approx(0.2)
    assert s.per_device_kwh["rest"] == pytest.approx(0.06)
    assert s.total_kwh == pytest.approx(0.36 / 0.9)
    assert s.measured_kwh == pytest.approx(0.3)
    assert s.estimated_kwh == pytest.approx(0.06)
    assert s.cost == pytest.approx(0.4 * 0.2)
    assert s.on_hours == pytest.approx(1)
    assert s.avg_w == pytest.approx(400)
    assert s.peak.watts == pytest.approx(400)
    assert s.details_kwh["cpu.core"] == pytest.approx(0.05)
    assert s.projection is None


def test_estimate_applies_at_query_time(conn):
    fill_hour(conn, ts(2026, 11, 10, 20), cpu_w=100, gpu_w=0)
    p = P.day_period(date(2026, 11, 10), NY)
    a = query.summary(conn, p, "all", EST).total_kwh
    b = query.summary(conn, p, "all", Estimate(base_watts=0, psu_efficiency=1.0)).total_kwh
    assert a == pytest.approx((0.1 + 0.06) / 0.9)
    assert b == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("devices", "expected"),
    [("cpu", 0.1 / 0.9), ("gpu", 0.2 / 0.9), ("rest", 0.06 / 0.9), ("measured", 0.3 / 0.9)],
)
def test_device_filters(conn, devices, expected):
    fill_hour(conn, ts(2026, 11, 10, 20), cpu_w=100, gpu_w=200)
    p = P.day_period(date(2026, 11, 10), NY)
    s = query.summary(conn, p, devices, EST)
    assert s.total_kwh == pytest.approx(expected)


def test_include_estimate_off(conn):
    fill_hour(conn, ts(2026, 11, 10, 20), cpu_w=100, gpu_w=200)
    p = P.day_period(date(2026, 11, 10), NY)
    s = query.summary(conn, p, "all", Estimate(include_estimate=False))
    assert s.total_kwh == pytest.approx(0.3)
    assert "rest" not in s.per_device_kwh


def test_monitor_watts_outside_psu(conn):
    fill_hour(conn, ts(2026, 11, 10, 20), cpu_w=0, gpu_w=0)
    p = P.day_period(date(2026, 11, 10), NY)
    s = query.summary(conn, p, "all", Estimate(base_watts=90, psu_efficiency=0.9, monitor_watts=30))
    assert s.total_kwh == pytest.approx(0.09 / 0.9 + 0.03)


def test_series_by_day_and_hour(conn):
    fill_hour(conn, ts(2026, 11, 2, 9), cpu_w=50, gpu_w=50)
    fill_hour(conn, ts(2026, 11, 3, 21), cpu_w=100, gpu_w=100)
    month = P.month_starting_in(2026, 11, 1, NY)
    s = query.series(conn, month, "day", "measured", EST, now=ts(2026, 11, 3, 23))
    assert len(s.buckets) == 30
    assert [b.label for b in s.buckets[:3]] == ["1", "2", "3"]
    assert s.buckets[1].total_kwh == pytest.approx(0.1 / 0.9)
    assert s.buckets[2].total_kwh == pytest.approx(0.2 / 0.9)
    assert not s.buckets[0].has_data
    assert s.buckets[5].future
    day = query.series(conn, P.day_period(date(2026, 11, 3), NY), "hour", "all", EST)
    assert len(day.buckets) == 24
    assert day.buckets[21].avg_w == pytest.approx((200 + 60) / 0.9)


def test_series_dst_day_has_25_hour_buckets(conn):
    s = query.series(conn, P.day_period(date(2026, 11, 1), NY), "hour", "all", EST)
    assert len(s.buckets) == 25
    assert [b.label for b in s.buckets[:4]] == ["00", "01", "01", "02"]


def test_series_by_week_clips_to_period(conn):
    month = P.month_starting_in(2026, 11, 1, NY)
    weeks = query.series(conn, month, "week", "all", EST).buckets
    assert weeks[0].start.startswith("2026-11-01")  # first (partial) week clipped to the 1st
    assert len(weeks) == 6


def test_projection(conn):
    for day in (1, 2):
        fill_hour(conn, ts(2026, 11, day, 12), cpu_w=1000, gpu_w=0)  # 1 kWh per day
    month = P.month_starting_in(2026, 11, 1, NY)
    now = ts(2026, 11, 3, 0)
    s = query.summary(conn, month, "cpu", Estimate(include_estimate=False, price_per_kwh=0.1), now=now)
    assert s.total_kwh == pytest.approx(2.0)
    assert s.projection.total_kwh == pytest.approx(2.0 + 1.0 * 28)
    assert s.projection.cost == pytest.approx(3.0)
    assert s.period.in_progress


def test_live(conn):
    now = ts(2026, 11, 10, 20, 30)
    for i in range(5):
        storage.write_sample(conn, now - 240 + 60 * i, 60, {"cpu.package": 60 * 80, "gpu.0": 60 * 150}, {})
    lv = query.live(conn, "all", EST, now=now + 10)
    assert lv.health == "ok"
    assert lv.watts["cpu"] == pytest.approx(80)
    assert lv.watts["rest"] == 60
    assert lv.total_w == pytest.approx((80 + 150 + 60) / 0.9)
    assert len(lv.history) == 5
    stale = query.live(conn, "all", EST, now=now + 3600)
    assert stale.health == "stale" and stale.total_w is None


def test_months(conn):
    fill_hour(conn, ts(2026, 9, 20, 12), 10, 10, minutes=2)
    refs = query.months(conn, 1, NY, now=ts(2026, 11, 18))
    assert [r.label for r in refs] == ["2026-09", "2026-10", "2026-11"]


def test_status(conn, tmp_path):
    storage.write_sample(conn, 1000, 60, {"cpu.package": 1.0}, {}, [(1000, "start", "x")])
    s = query.status(conn, str(tmp_path / "e.db"), now=1060)
    assert s.health == "ok"
    assert s.events[0].kind == "start"
    assert {x.id for x in s.sources} == {"cpu.package", "cpu.core", "gpu.0"}
    assert query.to_dict(s)["last_sample_ts"] == 1000
