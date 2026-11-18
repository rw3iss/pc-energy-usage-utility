"""SQLite storage: schema, migrations and writes. The collector is the only writer.

All timestamps are UTC unix seconds. The database stores measured joules and "seconds on" only;
estimates are computed at query time so a settings change applies to all history.

Journal mode is DELETE (not WAL): WAL readers need write access to the ``-shm`` file, and the
readers here are unprivileged users with read-only access.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta      (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources   (id TEXT PRIMARY KEY, device TEXT, label TEXT, in_total INTEGER,
                                      first_seen INTEGER);
CREATE TABLE IF NOT EXISTS samples   (ts INTEGER PRIMARY KEY, on_s REAL);
CREATE TABLE IF NOT EXISTS energy    (ts INTEGER, source TEXT, joules REAL, PRIMARY KEY (ts, source));
CREATE TABLE IF NOT EXISTS hourly    (hour INTEGER, source TEXT, joules REAL, on_s REAL,
                                      PRIMARY KEY (hour, source));
CREATE TABLE IF NOT EXISTS events    (ts INTEGER, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS source_state (source TEXT PRIMARY KEY, boot_id TEXT, raw REAL, ts INTEGER);
"""

# Each migration takes the database from version N-1 to N. Append new ones; never edit old ones.
MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1}

ON_SOURCE = "_on"  # pseudo source in ``hourly`` that carries the hour's on-seconds


@dataclass(frozen=True)
class SourceState:
    boot_id: str
    raw: float
    ts: int


def connect_rw(path: str) -> sqlite3.Connection:
    """Open (and create or migrate) the database for writing. Creates the parent dir 0755, file 0644."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn)
    if new:
        os.chmod(path, 0o644)
    return conn


def connect_ro(path: str) -> sqlite3.Connection:
    """Open the database read-only. Raises ``FileNotFoundError`` if it does not exist."""
    if not Path(path).exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    for version in sorted(v for v in MIGRATIONS if v > current):
        conn.execute("BEGIN")
        for statement in MIGRATIONS[version].split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(version),))
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('created', ?)", (str(int(time.time())),))
        conn.execute("COMMIT")


def register_sources(conn: sqlite3.Connection, sources: Iterable, now: int) -> None:
    """Insert new sources and refresh labels of known ones."""
    conn.execute("BEGIN")
    for s in sources:
        conn.execute(
            "INSERT INTO sources (id, device, label, in_total, first_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET device = excluded.device, label = excluded.label, "
            "in_total = excluded.in_total",
            (s.id, s.device, s.label, int(s.in_total), now),
        )
    conn.execute("COMMIT")


def load_states(conn: sqlite3.Connection) -> dict[str, SourceState]:
    rows = conn.execute("SELECT source, boot_id, raw, ts FROM source_state").fetchall()
    return {r[0]: SourceState(r[1], r[2], r[3]) for r in rows}


def hour_of(ts: int) -> int:
    """The UTC hour a sample belongs to. A sample stamped at the end of its interval (for example
    01:00:00) belongs to the hour before (00:00)."""
    return (ts - 1) // 3600 * 3600


def write_sample(
    conn: sqlite3.Connection,
    ts: int,
    on_s: float,
    joules: Mapping[str, float],
    states: Mapping[str, SourceState],
    events: Iterable[tuple[int, str, str]] = (),
) -> None:
    """Write one sample, its per-source joules, the hourly roll-up, new states and events in one
    transaction."""
    hour = hour_of(ts)
    conn.execute("BEGIN")
    try:
        if on_s > 0 or joules:
            conn.execute(
                "INSERT INTO samples (ts, on_s) VALUES (?, ?) "
                "ON CONFLICT(ts) DO UPDATE SET on_s = samples.on_s + excluded.on_s",
                (ts, on_s),
            )
            for source, j in joules.items():
                conn.execute(
                    "INSERT INTO energy (ts, source, joules) VALUES (?, ?, ?) "
                    "ON CONFLICT(ts, source) DO UPDATE SET joules = energy.joules + excluded.joules",
                    (ts, source, j),
                )
                _hourly_add(conn, hour, source, j, 0.0)
            _hourly_add(conn, hour, ON_SOURCE, 0.0, on_s)
        for source, st in states.items():
            conn.execute(
                "INSERT OR REPLACE INTO source_state (source, boot_id, raw, ts) VALUES (?, ?, ?, ?)",
                (source, st.boot_id, st.raw, st.ts),
            )
        for event in events:
            conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", event)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _hourly_add(conn: sqlite3.Connection, hour: int, source: str, joules: float, on_s: float) -> None:
    conn.execute(
        "INSERT INTO hourly (hour, source, joules, on_s) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(hour, source) DO UPDATE SET joules = hourly.joules + excluded.joules, "
        "on_s = hourly.on_s + excluded.on_s",
        (hour, source, joules, on_s),
    )


def add_event(conn: sqlite3.Connection, ts: int, kind: str, detail: str = "") -> None:
    conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (ts, kind, detail))


def prune(conn: sqlite3.Connection, keep_minutes_days: int, now: int) -> int:
    """Delete per-minute rows older than ``keep_minutes_days``. Hourly rows are kept forever.
    Events are kept one year. Returns the number of deleted sample rows."""
    cutoff = now - keep_minutes_days * 86400
    conn.execute("BEGIN")
    n = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
    conn.execute("DELETE FROM energy WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM events WHERE ts < ?", (now - 366 * 86400,))
    conn.execute("COMMIT")
    return n
