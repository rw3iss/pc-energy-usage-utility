"""Full-screen day view: day summary, 24-hour graph and hour table."""

from __future__ import annotations

from datetime import date, timedelta

from pcenergy import periods
from pcenergy.render import report
from pcenergy.render.text import Line
from pcenergy.tui.widgets import BaseScreen, footer, health_segments, rule, screen_bindings


class DayScreen(BaseScreen):
    BINDINGS = screen_bindings("day")
    FOOTER_SCOPES = ("day", "global")

    def __init__(self, day: date) -> None:
        super().__init__()
        self.day = day
        self.scroll = 0
        self.reload()

    def reload(self) -> None:
        p = periods.day_period(self.day, self.pc.tz)
        self.sum = self.pc.summary(p)
        self.ser = self.pc.series(p, "hour")
        self.health, self.age = self.pc.health()
        self.redraw()

    def draw(self, width: int, height: int) -> list[Line]:
        pc = self.pc
        if self.sum is None:
            return [[("No database.", "err")]]
        head: Line = [
            (" pcenergy  ", "bold"),
            (f" {self.day:%A %d %B %Y} ", "bold"),
            (f"  devices: {pc.devices}   ", "dim"),
            *health_segments(self.health, self.age),
        ]
        out: list[Line] = [head, rule(width)]
        out += report.summary_lines(self.sum, "Day" if not self.sum.period.in_progress else "So far", width)
        out.append(rule(width, "24 hours"))
        rows = max((height - 8) // 2 - 3, 4)
        out += report.graph(self.ser, width, rows, pc.settings["day_graph_unit"], pc.ascii)
        out.append(rule(width, "hours"))
        table = report.bucket_table(self.ser, "Hour", pc.est.currency_symbol, pc.est.price_per_kwh)
        room = height - len(out) - 2
        out += [table[0], *table[1 + self.scroll :]][:room]
        while len(out) < height - 2:
            out.append([("", "")])
        return out + footer(width, self.FOOTER_SCOPES)

    def action_prev(self) -> None:
        self.day -= timedelta(days=1)
        self.scroll = 0
        self.reload()

    def action_next(self) -> None:
        if self.day >= self.pc.today():
            self.notify("This is today.")
            return
        self.day += timedelta(days=1)
        self.scroll = 0
        self.reload()

    def action_scroll_up(self) -> None:
        self.scroll = max(self.scroll - 1, 0)
        self.redraw()

    def action_scroll_down(self) -> None:
        self.scroll = min(self.scroll + 1, max(len(self.ser.buckets) - 1, 0))
        self.redraw()
