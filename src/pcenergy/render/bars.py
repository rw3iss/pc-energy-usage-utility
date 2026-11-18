"""Stacked bar graphs and sparklines from Unicode blocks (or ASCII), as styled lines.

Output is a list of lines of ``(text, style)`` segments (see ``render.text``); the CLI turns them into
ANSI text and the TUI into Rich text, so both show the same graph.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from pcenergy.render.text import DEVICE_NAMES, DEVICE_ORDER, Line

EIGHTHS = " ▁▂▃▄▅▆▇█"
SPARK = "▁▂▃▄▅▆▇█"
ASCII_FULL = {"cpu": "#", "gpu": "%", "igpu": "+", "rest": ":", "monitor": "="}
LEGEND_MARK = {"cpu": "■", "gpu": "■", "igpu": "■", "rest": "░", "monitor": "░"}


@dataclass
class Bar:
    label: str
    values: dict[str, float] = field(default_factory=dict)  # device → value, stacked in DEVICE_ORDER
    missing: bool = False  # no data for a past bucket: drawn as "·"
    future: bool = False  # not reached yet: drawn empty
    selected: bool = False
    today: bool = False

    @property
    def total(self) -> float:
        return sum(self.values.values())


def nice_step(max_value: float, max_ticks: int) -> float:
    """A 1/2/5 × 10^n step so that ``max_value`` needs at most ``max_ticks`` ticks."""
    if max_value <= 0:
        return 1.0
    raw = max_value / max(max_ticks, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _fmt_tick(v: float) -> str:
    if v >= 100 or v == int(v):
        return f"{v:.0f}"
    if v >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}" if v >= 1 else f"{v:.2f}"


def stacked_bars(
    bars: list[Bar],
    width: int,
    height: int,
    ascii_mode: bool = False,
    tick_fmt: Callable[[float], str] = _fmt_tick,
    show_labels: bool = True,
    prefix: tuple[str, str] | None = None,
) -> list[Line]:
    """Render ``bars`` into ``width`` × (``height`` + 2) cells: plot rows, axis row, label row.

    ``prefix`` is a (text, style) drawn left of the labels, under the y axis (the TUI's "All").
    """
    top_value = max((b.total for b in bars), default=0.0)
    max_ticks = max(1, min(5, height // 2))
    step = nice_step(top_value, max_ticks)
    ticks = max(1, math.ceil(top_value / step)) if top_value > 0 else 1
    rows_per_tick = max(1, height // ticks)
    rows = rows_per_tick * ticks
    top = step * ticks
    unit = top / rows

    axis_w = max(len(tick_fmt(top)), 2 if prefix is None else 3) + 2  # "400 ┤"
    plot_w = max(width - axis_w, len(bars))
    n = max(len(bars), 1)
    slot = min(max(plot_w // n, 1), 8)
    bar_w = max(slot - 1, 1) if slot >= 3 else 1
    plot_w = slot * n

    lines: list[Line] = []
    for r in range(rows - 1, -1, -1):
        lo, hi = r * unit, (r + 1) * unit
        label_here = (r + 1) % rows_per_tick == 0
        axis = f"{tick_fmt(hi):>{axis_w - 2}} ┤" if label_here else " " * (axis_w - 2) + " │"
        line: Line = [(axis, "dim")]
        for b in bars:
            line.extend(_cell(b, lo, hi, unit, r, bar_w, ascii_mode))
            gap = slot - bar_w
            if gap:
                line.append((" " * gap, "hl" if b.selected else ""))
        lines.append(line)
    lines.append([(" " * (axis_w - 2) + " └" + "─" * plot_w, "dim")])
    if show_labels:
        lines.append(_label_row(bars, axis_w, slot, prefix))
    return lines


def _cell(b: Bar, lo: float, hi: float, unit: float, row: int, bar_w: int, ascii_mode: bool) -> Line:
    sel = " hl" if b.selected else ""
    if b.missing and not b.future:
        return [(("·" if row == 0 else " ").ljust(bar_w), "missing" + sel)]
    total = b.total
    if total <= lo or b.future:
        return [(" " * bar_w, sel.strip())]
    stack = [(d, b.values[d]) for d in DEVICE_ORDER if b.values.get(d, 0) > 0]
    if total >= hi:
        mid = (lo + hi) / 2
        device = _device_at(stack, mid)
        ch = ASCII_FULL.get(device, "#") if ascii_mode else "█"
    else:
        device = _device_at(stack, total - unit * 1e-6)
        frac = (total - lo) / unit
        if ascii_mode:
            ch = ASCII_FULL.get(device, "#") if frac >= 0.5 else "."
        else:
            ch = EIGHTHS[max(1, min(8, round(frac * 8)))]
    return [(ch * bar_w, device + sel)]


def _device_at(stack: list[tuple[str, float]], value: float) -> str:
    acc = 0.0
    for device, v in stack:
        acc += v
        if value < acc:
            return device
    return stack[-1][0] if stack else "cpu"


def _label_row(bars: list[Bar], axis_w: int, slot: int, prefix: tuple[str, str] | None) -> Line:
    """Labels under the bars. The selected and today's labels always show; others show every N
    bars and only where they do not touch another label."""
    longest = max((len(b.label) for b in bars), default=1)
    every = max(1, math.ceil((longest + 1) / slot))
    size = slot * len(bars)
    chars, styles, used = [" "] * size, [""] * size, [False] * size
    forced = [i for i, b in enumerate(bars) if b.selected or b.today]
    regular = [i for i in range(len(bars)) if i % every == 0 and i not in forced]
    for i in forced + regular:
        b = bars[i]
        start, end = i * slot, i * slot + len(b.label)
        if end > size or any(used[max(start - 1, 0) : min(end + 1, size)]):
            continue
        style = "sel" if b.selected else ("today" if b.today else "dim")
        for k, ch in enumerate(b.label):
            chars[start + k], styles[start + k], used[start + k] = ch, style, True
    if prefix:
        line: Line = [(" ", ""), prefix, (" " * max(axis_w - 1 - len(prefix[0]), 0), "")]
    else:
        line = [(" " * axis_w, "")]
    for ch, st in zip(chars, styles, strict=True):
        if line and line[-1][1] == st:
            line[-1] = (line[-1][0] + ch, st)
        else:
            line.append((ch, st))
    return line


def legend(devices: list[str], ascii_mode: bool = False) -> Line:
    line: Line = []
    for d in devices:
        mark = ASCII_FULL[d] if ascii_mode else LEGEND_MARK[d]
        line.append((mark, d))
        line.append((f" {DEVICE_NAMES[d]}  ", "dim"))
    return line


def sparkline(values: list[float], width: int, style: str = "") -> Line:
    """The last ``width`` values as a one-line sparkline."""
    vals = values[-width:]
    if not vals:
        return [(" " * width, "")]
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    text = "".join(SPARK[min(7, int((v - lo) / span * 7.999))] for v in vals)
    return [(text.rjust(width), style)]
