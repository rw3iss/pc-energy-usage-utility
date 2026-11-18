"""Event log for a period: collector starts and stops, boots, suspends, gaps and warnings."""

from __future__ import annotations

from datetime import datetime

from pcenergy import periods, query
from pcenergy.render.text import Line
from pcenergy.tui.widgets import BaseScreen, footer, rule, screen_bindings

KIND_STYLE = {
    "warning": "warn",
    "gap": "warn",
    "boot": "cpu",
    "suspend": "igpu",
    "start": "ok",
    "stop": "dim",
    "reload": "gpu",
}


class EventsScreen(BaseScreen):
    BINDINGS = screen_bindings("events")
    FOOTER_SCOPES = ("events", "global")

    def __init__(self, period: periods.Period) -> None:
        super().__init__()
        self.period = period
        self.scroll = 0
        c = self.pc.conn
        self.events = query.events(c, period.start_ts, period.end_ts, limit=5000) if c else []

    def draw(self, width: int, height: int) -> list[Line]:
        p = self.period
        out: list[Line] = [
            [
                (" pcenergy  ", "bold"),
                (" Events ", "bold"),
                (f"{p.range_text()}  ({len(self.events)})", "dim"),
            ],
            rule(width),
        ]
        if not self.events:
            out.append([("  No events in this period.", "dim")])
        for e in self.events[self.scroll :]:
            when = datetime.fromtimestamp(e.ts, self.pc.tz)
            out.append(
                [
                    (f"  {when:%Y-%m-%d %H:%M:%S}  ", "dim"),
                    (f"{e.kind:<8} ", KIND_STYLE.get(e.kind, "")),
                    (e.detail, ""),
                ]
            )
        out = out[: height - 2]
        while len(out) < height - 2:
            out.append([("", "")])
        return out + footer(width, self.FOOTER_SCOPES)

    def action_scroll_up(self) -> None:
        self.scroll = max(self.scroll - 1, 0)
        self.redraw()

    def action_scroll_down(self) -> None:
        self.scroll = min(self.scroll + 1, max(len(self.events) - 1, 0))
        self.redraw()
