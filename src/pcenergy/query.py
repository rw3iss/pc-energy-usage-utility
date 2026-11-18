"""All read logic: periods, totals, estimates, series, live watts and collector status.

Pure functions over a read-only connection. Each returns plain dataclasses, which are also the JSON
shape of the CLI ``--json`` output and the HTTP API. The CLI, the TUI and the API only format them.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Any

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
