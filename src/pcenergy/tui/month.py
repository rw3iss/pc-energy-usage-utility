"""The main screen: month summary → month graph → day selector → selected day (or month table)."""

from __future__ import annotations

from datetime import date

from pcenergy import config as cfg
from pcenergy import periods
from pcenergy.render import report
from pcenergy.render.text import Line
from pcenergy.tui.widgets import BaseScreen, footer, health_segments, rule, screen_bindings
from pcenergy.units import fmt_cost, fmt_energy

PANELS = ("month", "selector", "day")
FILTER_NAMES = {"all": "All", "cpu": "CPU", "gpu": "GPU", "rest": "Rest", "measured": "Measured"}


def pad(lines: list[Line], height: int) -> list[Line]:
    return (lines + [[("", "")]] * height)[:height]


def mark(lines: list[Line], focused: bool) -> list[Line]:
    """Prefix each line with the focus bar (or a space)."""
    m = ("▌", "bold cpu") if focused else (" ", "")
    return [[m, *line] for line in lines]


class MonthScreen(BaseScreen):
    BINDINGS = screen_bindings("main")
    FOOTER_SCOPES = ("main", "global")

    def __init__(self) -> None:
        super().__init__()
        self.month = self.pc.current_month()
        self.sel: date | None = self.pc.today() if self.month.contains_date(self.pc.today()) else None
        self.focus = 1
        self.table_mode = False
        self.scroll = {"month": 0, "day": 0}
        self.reload()

    # ------------------------------------------------------------------ data

    def reload(self) -> None:
        pc = self.pc
        self.msum = pc.summary(self.month)
        self.mser = pc.series(self.month, "day")
        self.health, self.age = pc.health()
        self.refs = pc.month_refs()
        if self.sel is not None:
            dp = periods.day_period(self.sel, pc.tz)
            self.dsum, self.dser = pc.summary(dp), pc.series(dp, "hour")
        else:
            self.weeks = pc.series(self.month, "week")
        self.redraw()

    def on_mount(self) -> None:
        self.set_interval(self.pc.settings["refresh_s"], self._auto_refresh)

    def on_screen_resume(self) -> None:
        self.reload()

    def _auto_refresh(self) -> None:
        if self.month.start_ts <= self.pc.now() < self.month.end_ts:
            self.reload()

    # ------------------------------------------------------------------ drawing

    def draw(self, width: int, height: int) -> list[Line]:
        pc = self.pc
        c = width - 1
        if pc.conn is None or self.msum is None:
            return [
                [("pcenergy", "bold")],
                [(f"No database at {pc.db_path}.", "err")],
                [("Install the collector (sudo packaging/install.sh) or run `pcenergy doctor`.", "dim")],
                [("Esc: exit", "dim")],
            ]
        title = "Month so far" if self.msum.period.in_progress else "Month"
        sum_lines = report.summary_lines(self.msum, title, c)[:3]
        # fixed lines: header, rule, summary, rule, month legend/axis/labels, rule, day head (2) and
        # graph legend/axis/labels, footer (2)
        rows_total = max(height - (len(sum_lines) + 14), 6)
        rows1 = (rows_total + 1) // 2
        rows2 = rows_total - rows1

        out: list[Line] = [self._header(width), rule(width)]
        out += mark(sum_lines, False)
        out.append(rule(width, "month", self.focus == 0))

        if self.table_mode:
            table = report.bucket_table(self.mser, "Day", pc.est.currency_symbol, pc.est.price_per_kwh)
            body = [table[0], *table[1 + self.scroll["month"] :]]
            block = pad(body, rows1 + 3)
            out += mark(block[:-1], self.focus == 0)
            out += mark([self._selector_only(c)], self.focus == 1)
        else:
            days = self.month.days()
            idx = days.index(self.sel) if self.sel in days else None
            all_style = "sel" if self.sel is None else "bold"
            g = report.graph(
                self.mser,
                c,
                rows1,
                "kWh",
                pc.ascii,
                selected=idx,
                today=pc.today().isoformat(),
                prefix=("All", all_style),
            )
            graph_lines, selector = g[:-1], g[-1]
            out += mark(pad(graph_lines, rows1 + 2), self.focus == 0)
            out += mark([selector], self.focus == 1)

        lower_title = f"{self.sel:%a %d %b %Y}" if self.sel else "month by week"
        out.append(rule(width, lower_title, self.focus == 2))
        out += mark(pad(self._lower(c, rows2), rows2 + 5), self.focus == 2)
        out += footer(width, self.FOOTER_SCOPES)
        return out

    def _header(self, width: int) -> Line:
        p = self.month
        wide = width >= 110
        left: Line = [(" pcenergy  ", "bold"), (f"{p.start:%B %Y} " if wide else f"{p.start:%b %Y} ", "bold")]
        if wide:
            left.append((f"({p.range_text()})   ", "dim"))
        for key, name in FILTER_NAMES.items():
            label = name if wide or key != "measured" else "Meas"
            active = key == self.pc.devices
            left.append((f"[{label}]" if active else f" {label} ", "sel" if active else "dim"))
        right = health_segments(self.health, self.age, short=not wide)
        used = sum(len(t) for t, _ in left) + sum(len(t) for t, _ in right)
        return [*left, (" " * max(width - used - 1, 1), ""), *right]

    def _selector_only(self, width: int) -> Line:
        line: Line = [("All", "sel" if self.sel is None else "bold"), ("  ", "")]
        for d in self.month.days():
            style = "sel" if d == self.sel else ("today" if d == self.pc.today() else "dim")
            line += [(str(d.day), style), (" ", "")]
        return line

    def _lower(self, width: int, rows: int) -> list[Line]:
        pc = self.pc
        if self.sel is not None and self.dsum is not None:
            head = pad(report.summary_lines(self.dsum, f"{self.sel:%a %d %b}", width)[:2], 2)
            unit = pc.settings["day_graph_unit"]
            g = report.graph(self.dser, width, rows, unit, pc.ascii)
            return [*head, *g]
        table = report.bucket_table(self.weeks, "Week", pc.est.currency_symbol, pc.est.price_per_kwh)
        top = report.top_days(self.mser)
        top_line: Line = [(" Top days  ", "bold")]
        for b in top:
            top_line += [(f"{b.start[5:10]} ", "dim"), (f"{fmt_energy(b.total_kwh)}  ", "")]
        fee = self.msum.fixed_fee if self.msum else 0
        extra: list[Line] = [top_line]
        if fee:
            extra.append([(f" Fixed fee {fmt_cost(fee, pc.est.currency_symbol)} (not in graphs)", "cost")])
        body = [table[0], *table[1 + self.scroll["day"] :]]
        return body[: rows + 5 - len(extra)] + extra

    # ------------------------------------------------------------------ actions

    def _days(self) -> list[date]:
        return self.month.days()

    def action_prev_day(self) -> None:
        days = self._days()
        if self.sel is None:
            self.sel = days[-1]
        elif self.sel == days[0]:
            self.sel = None
        else:
            self.sel = days[days.index(self.sel) - 1]
        self.reload()

    def action_next_day(self) -> None:
        days = self._days()
        if self.sel is None:
            self.sel = days[0]
        elif self.sel == days[-1]:
            self.sel = None
        else:
            self.sel = days[days.index(self.sel) + 1]
        self.reload()

    def _scrollable(self) -> str | None:
        if self.focus == 0 and self.table_mode:
            return "month"
        if self.focus == 2 and self.sel is None:
            return "day"
        return None

    def action_focus_prev(self) -> None:
        key = self._scrollable()
        if key and self.scroll[key] > 0:
            self.scroll[key] -= 1
        else:
            self.focus = max(self.focus - 1, 0)
        self.redraw()

    def action_focus_next(self) -> None:
        key = self._scrollable()
        if key == "month":
            limit = sum(1 for b in self.mser.buckets if not b.future) - 1
        elif key == "day":
            limit = len(self.weeks.buckets) - 1
        if key and self.scroll[key] < limit:
            self.scroll[key] += 1
        else:
            self.focus = min(self.focus + 1, len(PANELS) - 1)
        self.redraw()

    def _go_month(self, step: int) -> None:
        labels = [r.label for r in self.refs]
        current = self.pc.current_month()
        target = periods.shift(self.month, step, self.pc.settings, self.pc.tz)
        if step > 0 and target.start_ts > current.start_ts:
            self.notify("This is the current period.")
            return
        if step < 0 and labels and target.label < labels[0]:
            self.notify("No older data.")
            return
        old_days = self._days()
        index = old_days.index(self.sel) if self.sel in old_days else None
        self.month = target
        new_days = self._days()
        self.sel = new_days[min(index, len(new_days) - 1)] if index is not None else None
        self.scroll = {"month": 0, "day": 0}
        self.reload()

    def action_prev_month(self) -> None:
        self._go_month(-1)

    def action_next_month(self) -> None:
        self._go_month(1)

    def action_select_all(self) -> None:
        self.sel = None
        self.reload()

    def action_select_today(self) -> None:
        self.month = self.pc.current_month()
        self.sel = self.pc.today()
        self.reload()

    def action_cycle_devices(self) -> None:
        order = cfg.DEVICE_FILTERS
        self.action_devices(order[(order.index(self.pc.devices) + 1) % len(order)])

    def action_devices(self, name: str) -> None:
        self.pc.devices = name
        self.reload()

    def action_open(self) -> None:
        if self.sel is not None:
            from pcenergy.tui.day import DayScreen

            self.app.push_screen(DayScreen(self.sel))
        else:
            from pcenergy.tui.week import WeekScreen

            self.app.push_screen(WeekScreen(self.month.start.date()))

    def action_week(self) -> None:
        from pcenergy.tui.week import WeekScreen

        self.app.push_screen(WeekScreen(self.sel or self.month.start.date()))

    def action_toggle_table(self) -> None:
        self.table_mode = not self.table_mode
        self.scroll["month"] = 0
        self.redraw()

    def action_settings(self) -> None:
        from pcenergy.tui.settings import SettingsScreen

        def saved(changed: bool | None) -> None:
            if changed:
                self.pc.load_settings()
                self.reload()

        self.app.push_screen(SettingsScreen(), saved)

    def action_live(self) -> None:
        from pcenergy.tui.live import LiveScreen

        self.app.push_screen(LiveScreen())

    def action_events(self) -> None:
        from pcenergy.tui.events import EventsScreen

        p = periods.day_period(self.sel, self.pc.tz) if self.sel else self.month
        self.app.push_screen(EventsScreen(p))

    def action_refresh(self) -> None:
        self.reload()
        self.notify("Refreshed", timeout=1)

    def action_back(self) -> None:
        self.app.exit()
