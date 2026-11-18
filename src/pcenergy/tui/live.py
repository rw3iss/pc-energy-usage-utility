"""Live view: current watts per device and a 10-minute sparkline. Refreshes every 5 s."""

from __future__ import annotations

from pcenergy import query
from pcenergy.render.bars import sparkline
from pcenergy.render.text import DEVICE_NAMES, DEVICE_ORDER, Line
from pcenergy.tui.widgets import BaseScreen, footer, health_segments, rule, screen_bindings
from pcenergy.units import fmt_cost, fmt_energy, fmt_watts

REFRESH_S = 5


class LiveScreen(BaseScreen):
    BINDINGS = screen_bindings("live")
    FOOTER_SCOPES = ("global",)

    def __init__(self) -> None:
        super().__init__()
        self.reload()

    def on_mount(self) -> None:
        self.set_interval(REFRESH_S, self.reload)

    def reload(self) -> None:
        pc = self.pc
        c = pc.conn
        self.live = query.live(c, pc.devices, pc.est, now=pc.now()) if c else None
        self.month = pc.summary(pc.current_month())
        self.redraw()

    def draw(self, width: int, height: int) -> list[Line]:
        pc = self.pc
        lv = self.live
        if lv is None or self.month is None:
            return [[("No database.", "err")]]
        out: list[Line] = [
            [
                (" pcenergy  ", "bold"),
                (" Live ", "bold"),
                (f"  devices: {pc.devices}   ", "dim"),
                *health_segments(lv.health, lv.sample_age_s),
            ],
            rule(width),
            [("", "")],
        ]
        spark_w = max(width - 50, 10)
        out.append([(f"  {'Device':<12}{'now':>9}   {'month so far':>14}   last 10 minutes", "dim")])
        for d in DEVICE_ORDER:
            if d not in self.month.per_device_kwh:
                continue
            est = d in ("rest", "monitor")
            w = lv.watts.get(d)
            hist = [p.watts.get(d, 0.0) for p in lv.history]
            out.append(
                [
                    (f"  {DEVICE_NAMES[d]:<12}", d),
                    (f"{('~' if est and w is not None else '') + fmt_watts(w):>9}", "bold"),
                    (f"   {fmt_energy(self.month.per_device_kwh[d]):>14}   ", ""),
                    *sparkline(hist, spark_w, d),
                ]
            )
        out.append([("", "")])
        total = lv.total_w
        out.append(
            [
                (f"  {'Total':<12}", "bold"),
                (f"{('~' if lv.total_is_estimate and total else '') + fmt_watts(total):>9}", "bold"),
                (f"   {fmt_energy(self.month.total_kwh, self.month.total_is_estimate):>14}   ", "bold"),
                *sparkline([p.total_w for p in lv.history], spark_w, "bold"),
            ]
        )
        out.append([(f"  {'':<12}{'':>9}   {fmt_cost(self.month.cost, pc.est.currency_symbol):>14}", "cost")])
        out.append([("", "")])
        out.append([("  ~ marks estimated values (rest of system, PSU loss). Refresh every 5 s.", "dim")])
        out = out[: height - 2]
        while len(out) < height - 2:
            out.append([("", "")])
        return out + footer(width, self.FOOTER_SCOPES)
