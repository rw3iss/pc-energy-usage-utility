"""The collector: reads every source once per interval, turns raw counters into energy deltas and
writes them. Runs as root (RAPL counters are root-only) under ``pcenergy-collector.service``.

Delta rules per source (see ``_counter_delta``):

* same boot, counter went up: ``cur - prev``
* same boot, counter went down, source wraps: ``max_range - prev + cur`` (one wrap per interval)
* same boot, counter went down, driver reload (NVML): ``cur``
* new boot: ``cur`` if the collector started with the boot and ``cur`` is plausible, else 0
* any delta above 2 × max power × elapsed is dropped (warning event)

"Seconds on" is the awake time between two samples (``CLOCK_MONOTONIC`` does not count suspend).
More than 2 intervals of awake time means the collector was not sampling (stopped or stalled):
only one interval is counted, its energy is dropped, and a ``gap`` event is written.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pcenergy import storage
from pcenergy.sources.base import EnergySource, SourceError
from pcenergy.storage import SourceState

log = logging.getLogger("pcenergy.collector")

CLOCK_STATE = "_clock"  # source_state row that keeps the last sample's boot id, monotonic time, wall time
SUSPEND_MIN_S = 30
PRUNE_EVERY_S = 86400


class SystemClock:
    def wall(self) -> float:
        return time.time()

    def mono(self) -> float:
        return time.monotonic()

    def boot_id(self) -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def uptime(self) -> float:
        return float(Path("/proc/uptime").read_text().split()[0])


@dataclass
class SampleResult:
    ts: int
    on_s: float
    joules: dict[str, float]
    events: list[tuple[int, str, str]] = field(default_factory=list)


class Collector:
    def __init__(
        self,
        conn,
        sources: Sequence[EnergySource],
        interval_s: int = 60,
        keep_minutes_days: int = 90,
        clock: SystemClock | None = None,
    ):
        self.conn = conn
        self.sources = list(sources)
        self.interval = interval_s
        self.keep_days = keep_minutes_days
        self.clock = clock or SystemClock()
        self.states = storage.load_states(conn)
        self._failing: set[str] = set()
        self._last_prune = 0.0

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        now = int(self.clock.wall())
        storage.register_sources(self.conn, self.sources, now)
        found = ", ".join(f"{s.id} ({s.label})" for s in self.sources) or "none"
        log.info("sources: %s", found)
        self.conn.execute("BEGIN")
        storage.add_event(self.conn, now, "start", f"interval {self.interval}s; sources: {found}")
        self.conn.execute("COMMIT")

    def stop(self) -> None:
        self.sample()
        self.conn.execute("BEGIN")
        storage.add_event(self.conn, int(self.clock.wall()), "stop", "")
        self.conn.execute("COMMIT")

    def run(self, stop: threading.Event) -> None:
        """Sample on whole multiples of the interval (wall clock aligned) until ``stop`` is set."""
        self.start()
        self.sample()  # baseline (or catch-up after a restart)
        while not stop.is_set():
            now = self.clock.wall()
            wait = (now // self.interval + 1) * self.interval - now
            if stop.wait(wait):
                break
            self.sample()
            if now - self._last_prune > PRUNE_EVERY_S:
                n = storage.prune(self.conn, self.keep_days, int(now))
                self._last_prune = now
                log.info("pruned %d per-minute rows older than %d days", n, self.keep_days)
        self.stop()

    # ------------------------------------------------------------------ sampling

    def sample(self) -> SampleResult:
        wall = self.clock.wall()
        ts = round(wall)
        mono = self.clock.mono()
        boot = self.clock.boot_id()
        events: list[tuple[int, str, str]] = []

        prev = self.states.get(CLOCK_STATE)
        new_boot = prev is not None and prev.boot_id != boot
        boot_fresh = False  # the collector started together with this boot
        gap = False
        if prev is None:
            on_s, elapsed = 0.0, 0.0
        elif new_boot:
            uptime = self.clock.uptime()
            boot_fresh = uptime <= 2 * self.interval + 60
            on_s = elapsed = uptime if boot_fresh else 0.0
            events.append((ts, "boot", f"uptime {uptime:.0f}s" + ("" if boot_fresh else "; late start")))
        else:
            awake = max(mono - prev.raw, 0.0)
            suspended = (wall - prev.ts) - awake
            if suspended > SUSPEND_MIN_S:
                events.append((ts, "suspend", f"{suspended:.0f}s asleep"))
            if awake <= 2 * self.interval:
                on_s = elapsed = awake
            else:
                gap = True
                on_s, elapsed = float(self.interval), awake
                events.append((ts, "gap", f"no samples for {awake:.0f}s awake; energy dropped"))

        joules: dict[str, float] = {}
        new_states: dict[str, SourceState] = {}
        for src in self.sources:
            try:
                reading = src.read()
            except SourceError as exc:
                if src.id not in self._failing:
                    self._failing.add(src.id)
                    events.append((ts, "warning", str(exc)))
                    log.warning("%s", exc)
                continue
            self._failing.discard(src.id)
            st = self.states.get(src.id)
            same_boot = st is not None and st.boot_id == boot

            if reading.counter_j is not None:
                raw = reading.counter_j
                if st is None or gap:
                    delta = 0.0
                elif not same_boot:
                    plausible = raw <= src.max_power_w * max(elapsed, 1.0)
                    delta = raw if (boot_fresh and plausible) else 0.0
                else:
                    delta, event = _counter_delta(src, st.raw, raw, reading.max_range_j)
                    if event:
                        events.append((ts, event[0], event[1]))
            else:
                raw = float(reading.power_w or 0.0)
                delta = (st.raw + raw) / 2 * on_s if (same_boot and not gap) else 0.0

            if delta > 2 * src.max_power_w * max(elapsed, 1.0):
                msg = f"{src.id}: implausible delta {delta:.0f} J over {elapsed:.0f}s dropped"
                events.append((ts, "warning", msg))
                log.warning(msg)
                delta = 0.0
            if delta > 0:
                joules[src.id] = delta
            new_states[src.id] = SourceState(boot, raw, ts)

        new_states[CLOCK_STATE] = SourceState(boot, mono, ts)
        storage.write_sample(self.conn, ts, on_s, joules, new_states, events)
        self.states.update(new_states)
        return SampleResult(ts, on_s, joules, events)


def _counter_delta(
    src: EnergySource, prev: float, cur: float, max_range: float | None
) -> tuple[float, tuple[str, str] | None]:
    if cur >= prev:
        return cur - prev, None
    if max_range:  # normal on RAPL: an AMD package counter wraps every few minutes under load
        return max_range - prev + cur, None
    if src.reload_resets:
        return cur, ("reload", f"{src.id}: counter restarted (driver reload)")
    return 0.0, ("warning", f"{src.id}: counter went backwards ({prev:.0f} → {cur:.0f} J)")


def install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, _frame):
        log.info("signal %d: stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def read_once(sources: Sequence[EnergySource], wait_s: float = 1.0, clock: Callable[[], float] = time.time):
    """Read every source twice, ``wait_s`` apart. Yields (source, reading or error, watts or None)."""
    first = {}
    t0 = clock()
    for s in sources:
        try:
            first[s.id] = s.read()
        except SourceError as exc:
            first[s.id] = exc
    time.sleep(wait_s)
    dt = clock() - t0
    for s in sources:
        a = first[s.id]
        if isinstance(a, SourceError):
            yield s, a, None
            continue
        try:
            b = s.read()
        except SourceError as exc:
            yield s, exc, None
            continue
        if b.counter_j is not None:
            d = b.counter_j - a.counter_j
            if d < 0 and b.max_range_j:
                d += b.max_range_j
            yield s, b, d / dt if dt > 0 else None
        else:
            yield s, b, b.power_w
