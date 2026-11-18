"""Shared TUI pieces: a canvas that draws styled lines, the base screen with global keys, and
header / footer helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text
from textual.screen import Screen
from textual.widget import Widget

from pcenergy import keys
from pcenergy.render.text import STYLES, Line
from pcenergy.units import fmt_age

if TYPE_CHECKING:
    from pcenergy.tui.app import PcEnergyApp

MIN_W, MIN_H = 80, 24
NO_COLOR_KEEP = {"sel", "today", "bold", "dim", "hl"}


def rich_style(style: str, color: bool) -> str:
    parts = []
    for name in style.split():
        if name not in STYLES or (not color and name not in NO_COLOR_KEEP):
            continue
        rs = STYLES[name][1]
        if not color and name == "hl":
            rs = "reverse"
        parts.append(rs)
    return " ".join(parts)


def to_rich(lines: list[Line], width: int, color: bool = True) -> Text:
    out = Text(no_wrap=True, overflow="crop")
    for i, line in enumerate(lines):
        row = Text(no_wrap=True, overflow="crop")
        for text, style in line:
            row.append(text, rich_style(style, color))
        row.truncate(width)
        out.append_text(row)
        if i < len(lines) - 1:
            out.append("\n")
    return out


class Canvas(Widget):
    """Draws whatever ``draw(width, height)`` returns. Re-draws on resize and on ``refresh()``."""

    DEFAULT_CSS = "Canvas { width: 1fr; height: 1fr; }"

    def __init__(self, draw: Callable[[int, int], list[Line]], color: Callable[[], bool], **kw):
        super().__init__(**kw)
        self._draw = draw
        self._color = color

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w < MIN_W or h < MIN_H:
            return to_rich(_too_small(w, h), w, self._color())
        return to_rich(self._draw(w, h)[:h], w, self._color())


def _too_small(w: int, h: int) -> list[Line]:
    return [
        [("Make the window larger", "bold")],
        [(f"pcenergy needs at least {MIN_W} × {MIN_H}; this window is {w} × {h}.", "dim")],
        [("Esc or q: exit", "dim")],
    ]


def rule(width: int, title: str = "", focused: bool = False) -> Line:
    if not title:
        return [("─" * width, "dim")]
    head = f"─ {title} "
    return [(head, "bold" if focused else "dim"), ("─" * max(width - len(head), 0), "dim")]


def footer(width: int, scopes: tuple[str, ...]) -> list[Line]:
    """Key hints that fit ``width``. Hints are dropped from the middle; help and quit always stay."""
    items = keys.footer_text(scopes)

    def size(its) -> int:
        return 1 + sum(len(k) + len(t) + 3 for k, t in its)

    while len(items) > 2 and size(items) > width:
        del items[-3]
    line: Line = [(" ", "")]
    for label, text in items:
        line += [(label, "bold"), (f" {text}  ", "dim")]
    return [rule(width), line]


def health_segments(health: str, age: float | None, short: bool = False) -> Line:
    color = {"ok": "ok", "stale": "warn", "none": "err"}[health]
    text = {"ok": "collector ok", "stale": "collector stale", "none": "no data"}[health]
    if short:
        text = {"ok": "ok", "stale": "stale", "none": "no data"}[health]
    return [("● ", color), (f"{text} · {fmt_age(age)}", "dim")]


class BaseScreen(Screen):
    """A full-screen view with one canvas and the global keys (help, back, quit)."""

    SCOPE = "global"
    FOOTER_SCOPES: tuple[str, ...] = ("global",)

    def __init__(self) -> None:
        super().__init__()
        self.canvas = Canvas(self.draw, lambda: bool(self.pc.settings["color"]))

    @property
    def pc(self) -> PcEnergyApp:
        return self.app  # type: ignore[return-value]

    def compose(self):
        yield self.canvas

    def draw(self, width: int, height: int) -> list[Line]:
        raise NotImplementedError

    def redraw(self) -> None:
        self.canvas.refresh()

    def action_help(self) -> None:
        from pcenergy.tui.help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()


def screen_bindings(scope: str):
    return keys.bindings(scope) + keys.bindings("global")
