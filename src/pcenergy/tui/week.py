"""Week view: week summary, 7 day bars and a day table. ←/→ move by week."""

from __future__ import annotations

from datetime import date

from pcenergy import periods
from pcenergy.render import report
from pcenergy.render.bars import legend, stacked_bars
from pcenergy.render.text import Line
from pcenergy.tui.widgets import BaseScreen, footer, health_segments, rule, screen_bindings


class WeekScreen(BaseScreen):
    BINDINGS = screen_bindings("week")
    FOOTER_SCOPES = ("week", "global")

    def __init__(self, day: date) -> None:
        super().__init__()
        self.week = periods.week_period(day, self.pc.settings["week_start"], self.pc.tz)
        self.reload()

    def reload(self) -> None:
        self.sum = self.pc.summary(self.week)
        self.ser = self.pc.series(self.week, "day")
        self.health, self.age = self.pc.health()
        self.redraw()

    def draw(self, width: int, height: int) -> list[Line]:
        pc = self.pc
        if self.sum is None or self.ser is None:
            return [[("No database.", "err")]]
        w = self.week
        head: Line = [
            (" pcenergy  ", "bold"),
            (f" Week {w.label} ", "bold"),
            (f"({w.range_text()})", "dim"),
            (f"  devices: {pc.devices}   ", "dim"),
            *health_segments(self.health, self.age),
        ]
        out: list[Line] = [head, rule(width)]
        out += report.summary_lines(self.sum, "So far" if self.sum.period.in_progress else "Week", width)
        out.append(rule(width, "days"))

        bars = report.series_bars(self.ser)
        today = pc.today()
        for b, d in zip(bars, w.days(), strict=True):
            b.label = f"{d:%a} {d.day}"
            b.today = d == today
        devices = list(self.ser.buckets[0].per_device_kwh) if self.ser.buckets else []
        out.append([("  kWh   ", "dim"), *legend(devices, pc.ascii)])
        rows = max(height - 25, 4)
        out += stacked_bars(bars, width, rows, pc.ascii)
        out.append(rule(width, "table"))
        out += report.bucket_table(self.ser, "Day", pc.est.currency_symbol, pc.est.price_per_kwh)
        out = out[: height - 2]
        while len(out) < height - 2:
            out.append([("", "")])
        return out + footer(width, self.FOOTER_SCOPES)

    def _move(self, step: int) -> None:
        target = periods.shift(self.week, step, self.pc.settings, self.pc.tz)
        if step > 0 and target.start.date() > self.pc.today():
            self.notify("This is the current week.")
            return
        self.week = target
        self.reload()

    def action_prev(self) -> None:
        self._move(-1)

    def action_next(self) -> None:
        self._move(1)
