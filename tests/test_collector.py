import os
import stat

import pytest

from conftest import ScriptedSource
from pcenergy import storage
from pcenergy.collector import Collector
from pcenergy.sources.base import SourceError

RANGE = 65532.610987


def make(db, clock, *sources, interval=60):
    _, conn = db
    c = Collector(conn, sources, interval_s=interval, clock=clock)
    c.start()
    return c


def kinds(result):
    return [e[1] for e in result.events]


def test_first_sample_is_baseline(db, clock):
    src = ScriptedSource()
    src.value = 5000.0
    c = make(db, clock, src)
    r = c.sample()
    assert r.joules == {} and r.on_s == 0


def test_normal_delta_and_on_seconds(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 1000.0
    c.sample()
    clock.advance(60)
    src.value = 7000.0
    r = c.sample()
    assert r.joules == {"cpu.package": 6000.0}
    assert r.on_s == 60


def test_wrap(db, clock):
    src = ScriptedSource(max_range_j=RANGE)
    c = make(db, clock, src)
    src.value = RANGE - 1000
    c.sample()
    clock.advance(60)
    src.value = 2000.0
    r = c.sample()
    assert r.joules["cpu.package"] == pytest.approx(3000.0)
    assert "warning" not in kinds(r)


def test_driver_reload(db, clock):
    src = ScriptedSource(sid="gpu.0", device="gpu", reload_resets=True)
    c = make(db, clock, src)
    src.value = 900_000.0
    c.sample()
    clock.advance(60)
    src.value = 1200.0
    r = c.sample()
    assert r.joules == {"gpu.0": 1200.0}
    assert "reload" in kinds(r)


def test_counter_backwards_without_wrap_or_reload(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 9000.0
    c.sample()
    clock.advance(60)
    src.value = 100.0
    r = c.sample()
    assert r.joules == {}
    assert "warning" in kinds(r)


def test_new_boot_collector_started_with_boot(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 50_000.0
    c.sample()
    clock.reboot("boot-b", uptime=45)
    src.value = 2000.0  # 45 s at ~44 W
    r = c.sample()
    assert r.joules == {"cpu.package": 2000.0}
    assert r.on_s == 45
    assert "boot" in kinds(r)


def test_new_boot_implausible_counter(db, clock):
    src = ScriptedSource(max_power_w=100)
    c = make(db, clock, src)
    src.value = 1.0
    c.sample()
    clock.reboot("boot-b", uptime=30)
    src.value = 50_000.0  # more than 100 W × 30 s
    r = c.sample()
    assert r.joules == {}


def test_new_boot_late_start_is_a_baseline(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 1.0
    c.sample()
    clock.reboot("boot-b", uptime=7200)
    src.value = 30_000.0
    r = c.sample()
    assert r.joules == {} and r.on_s == 0
    assert any("late start" in e[2] for e in r.events)


def test_suspend_does_not_count_as_on(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 1000.0
    c.sample()
    clock.advance(awake=50, asleep=3600)
    src.value = 3000.0
    r = c.sample()
    assert r.on_s == 50
    assert r.joules == {"cpu.package": 2000.0}
    assert "suspend" in kinds(r)


def test_gap_counts_one_interval_and_drops_energy(db, clock):
    src = ScriptedSource(sid="gpu.0")
    c = make(db, clock, src)
    src.value = 1000.0
    c.sample()
    clock.advance(awake=1800)
    src.value = 200_000.0
    r = c.sample()
    assert r.on_s == 60
    assert r.joules == {}
    assert "gap" in kinds(r)
    # the next normal interval works again
    clock.advance(60)
    src.value = 206_000.0
    assert c.sample().joules == {"gpu.0": 6000.0}


def test_plausibility_guard(db, clock):
    src = ScriptedSource(max_power_w=100)
    c = make(db, clock, src)
    src.value = 0.0
    c.sample()
    clock.advance(60)
    src.value = 20_000.0  # 333 W, above 2 × 100 W
    r = c.sample()
    assert r.joules == {}
    assert "warning" in kinds(r)


def test_power_source_trapezoid(db, clock):
    src = ScriptedSource(sid="igpu.0", device="igpu", power=True)
    c = make(db, clock, src)
    src.value = 10.0
    c.sample()
    clock.advance(60)
    src.value = 20.0
    r = c.sample()
    assert r.joules["igpu.0"] == pytest.approx(900.0)  # (10+20)/2 × 60


def test_read_error_logged_once(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = SourceError("permission denied")
    r1 = c.sample()
    clock.advance(60)
    r2 = c.sample()
    assert "warning" in kinds(r1)
    assert "warning" not in kinds(r2)


def test_state_survives_restart(db, clock):
    src = ScriptedSource()
    c = make(db, clock, src)
    src.value = 1000.0
    c.sample()
    clock.advance(60)
    c2 = make(db, clock, src)  # service restarted within the same boot
    src.value = 4000.0
    assert c2.sample().joules == {"cpu.package": 3000.0}


def test_rows_and_hourly_rollup(db, clock):
    path, conn = db
    a = ScriptedSource()
    b = ScriptedSource(sid="cpu.core", in_total=False)
    c = make(db, clock, a, b)
    a.value = b.value = 0.0
    c.sample()
    for i in range(1, 4):
        clock.advance(60)
        a.value, b.value = 6000.0 * i, 3000.0 * i
        c.sample()
    hour = storage.hour_of(int(clock.now))
    rows = dict(conn.execute("SELECT source, joules FROM hourly WHERE hour = ?", (hour,)).fetchall())
    assert rows["cpu.package"] == pytest.approx(18000)
    assert rows["cpu.core"] == pytest.approx(9000)
    on = conn.execute("SELECT SUM(on_s) FROM hourly WHERE source = '_on'").fetchone()[0]
    assert on == 180
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 3
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


def test_hour_of_sample_at_boundary():
    assert storage.hour_of(3600) == 0
    assert storage.hour_of(3601) == 3600


def test_prune(db, clock):
    _path, conn = db
    storage.write_sample(conn, 1000, 60, {"x": 1.0}, {})
    storage.write_sample(conn, 100 * 86400, 60, {"x": 1.0}, {})
    assert storage.prune(conn, 90, 100 * 86400) == 1
    assert conn.execute("SELECT COUNT(*) FROM hourly WHERE source = 'x'").fetchone()[0] == 2


def test_readonly_connection(db):
    path, _ = db
    ro = storage.connect_ro(path)
    with pytest.raises(Exception, match="readonly"):
        ro.execute("INSERT INTO meta VALUES ('a', 'b')")
    assert storage.schema_version(ro) == storage.SCHEMA_VERSION
