"""``pcenergy demo-data``: fill a database with realistic fake history for UI work and screenshots.

The history has idle nights (PC off), work days, gaming evenings, overnight AI jobs, a reboot and a
suspend. It uses the fake source power model, so it needs no hardware.
"""

from __future__ import annotations

import random
import sqlite3
import time
from datetime import datetime

from pcenergy import storage
from pcenergy.collector import CLOCK_STATE
from pcenergy.sources.fake import FakeSource, fake_power


def fill(conn: sqlite3.Connection, days: int, now: float | None = None, seed: int = 7) -> int:
    """Write ``days`` of per-minute history ending at ``now``. Returns the number of samples."""
    now = time.time() if now is None else now
    rng = random.Random(seed)
    end = int(now // 60 * 60)
    start = end - days * 86400
    sources = FakeSource.detect()
    storage.register_sources(conn, sources, start)

    off_nights = {d for d in range(days) if rng.random() < 0.7}  # PC switched off overnight
    ai_nights = {d for d in range(days) if d not in off_nights and rng.random() < 0.5}
    reboot_day = days // 3
    suspend_day = days // 2

    samples, energy, events = [], [], [(start, "start", "demo data")]
    hourly: dict[tuple[int, str], list[float]] = {}
    for ts in range(start + 60, end + 1, 60):
        local = datetime.fromtimestamp(ts)
        day_index = (ts - start) // 86400
        h = local.hour + local.minute / 60
        kind = "ai" if day_index in ai_nights else "normal"
        if day_index in off_nights and (1.5 <= h < 8.5):
            continue  # PC off
        if day_index == suspend_day and 13 <= h < 15:
            if h == 13 and local.minute == 0:
                events.append((ts, "suspend", "7200s asleep"))
            continue
        if day_index == reboot_day and local.hour == 18 and local.minute == 0:
            events.append((ts, "boot", "uptime 52s"))
        watts = fake_power(ts, kind)
        samples.append((ts, 60.0))
        hour = storage.hour_of(ts)
        hourly.setdefault((hour, storage.ON_SOURCE), [0.0, 0.0])[1] += 60.0
        for sid, w in watts.items():
            j = w * 60
            energy.append((ts, sid, j))
            hourly.setdefault((hour, sid), [0.0, 0.0])[0] += j

    conn.execute("BEGIN")
    conn.executemany("INSERT OR REPLACE INTO samples (ts, on_s) VALUES (?, ?)", samples)
    conn.executemany("INSERT OR REPLACE INTO energy (ts, source, joules) VALUES (?, ?, ?)", energy)
    conn.executemany(
        "INSERT OR REPLACE INTO hourly (hour, source, joules, on_s) VALUES (?, ?, ?, ?)",
        [(h, s, v[0], v[1]) for (h, s), v in hourly.items()],
    )
    conn.executemany("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", events)
    conn.execute(
        "INSERT OR REPLACE INTO source_state (source, boot_id, raw, ts) VALUES (?, 'demo', 0, ?)",
        (CLOCK_STATE, end),
    )
    conn.execute("COMMIT")
    return len(samples)
