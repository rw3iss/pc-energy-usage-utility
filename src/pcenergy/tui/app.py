"""The Textual app: settings, database access and the screen stack."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from textual.app import App

from pcenergy import config as cfg
from pcenergy import periods, query, storage


class PcEnergyApp(App):
    CSS_PATH = str(Path(__file__).with_name("app.tcss"))
    TITLE = "pcenergy"
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        db_path: str,
        devices: str | None = None,
        ascii_mode: bool = False,
        color: bool | None = None,
        now: float | None = None,
    ):
        super().__init__()
        self.db_path = db_path
        self._fixed_now = now  # tests and screenshots pin the clock
        self.ascii_override = ascii_mode
        self.color_override = color
        self.tz = periods.local_tz()
        self.load_settings()
        self.devices = devices or self.settings["default_devices"]
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ settings / time

    def load_settings(self) -> None:
        self.settings, self.settings_warnings = cfg.load_user()
        if self.color_override is not None:
            self.settings["color"] = self.color_override
        self.est = query.Estimate.from_settings(self.settings)
        self.theme = "textual-light" if self.settings["theme"] == "light" else "textual-dark"

    @property
    def ascii(self) -> bool:
        return self.ascii_override or self.settings["graph_style"] == "ascii"

    def now(self) -> float:
        return self._fixed_now if self._fixed_now is not None else time.time()

    def today(self) -> date:
        return datetime.fromtimestamp(self.now(), self.tz).date()

    def current_month(self) -> periods.Period:
        return periods.month_period(self.today(), self.settings["month_start_day"], self.tz)

    # ------------------------------------------------------------------ data

    @property
    def conn(self) -> sqlite3.Connection | None:
        if self._conn is None:
            try:
                self._conn = storage.connect_ro(self.db_path)
            except (FileNotFoundError, sqlite3.Error):
                return None
        return self._conn

    def summary(self, p: periods.Period) -> query.Summary | None:
        c = self.conn
        return query.summary(c, p, self.devices, self.est, now=self.now(), tz=self.tz) if c else None

    def series(self, p: periods.Period, bucket: str) -> query.Series | None:
        c = self.conn
        if c is None:
            return None
        return query.series(
            c, p, bucket, self.devices, self.est, self.settings["week_start"], now=self.now(), tz=self.tz
        )

    def health(self) -> tuple[str, float | None]:
        c = self.conn
        if c is None:
            return "none", None
        last = c.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        age = None if last is None else max(self.now() - last, 0.0)
        return query.health_of(age), age

    def month_refs(self) -> list[query.MonthRef]:
        c = self.conn
        return query.months(c, self.settings["month_start_day"], self.tz, now=self.now()) if c else []

    def collector_info(self) -> dict:
        ccfg, _ = cfg.load(cfg.COLLECTOR_SCHEMA, cfg.collector_config_path())
        info = {
            "interval_s": ccfg["interval_s"],
            "keep_minutes_days": ccfg["keep_minutes_days"],
            "db_path": self.db_path,
            "sources": [],
            "db_size": 0,
        }
        c = self.conn
        if c is not None:
            st = query.status(c, self.db_path, now=self.now())
            info["sources"] = [f"{s.id} ({s.label})" for s in st.sources]
            info["db_size"] = st.db_size_bytes
        return info

    # ------------------------------------------------------------------ lifecycle

    def on_mount(self) -> None:
        from pcenergy.tui.month import MonthScreen

        self.push_screen(MonthScreen())
        for w in self.settings_warnings:
            self.notify(w, severity="warning")


def run(db_path: str, devices: str | None = None, ascii_mode: bool = False, color: bool | None = None) -> int:
    PcEnergyApp(db_path, devices, ascii_mode, color).run()
    return 0
