"""Turn styled segments into ANSI text (CLI) or plain text. The TUI maps the same style names to
Rich styles, so CLI and TUI colors match.

A "line" is a list of ``(text, style)`` segments; ``style`` is a name from ``STYLES`` or "".
"""

from __future__ import annotations

import os
import sys

Segment = tuple[str, str]
Line = list[Segment]

# style name → (ANSI SGR, Rich style)
STYLES: dict[str, tuple[str, str]] = {
    "cpu": ("38;5;75", "#5fafff"),
    "gpu": ("38;5;78", "#5fd787"),
    "igpu": ("38;5;80", "#5fd7d7"),
    "rest": ("38;5;245", "#8a8a8a"),
    "monitor": ("38;5;140", "#af87d7"),
    "cost": ("38;5;221", "#ffd75f"),
    "ok": ("38;5;78", "#5fd787"),
    "warn": ("38;5;221", "#ffd75f"),
    "err": ("38;5;203", "#ff5f5f"),
    "dim": ("2", "dim"),
    "bold": ("1", "bold"),
    "sel": ("7", "reverse"),
    "today": ("4", "underline"),
    "missing": ("38;5;240", "#585858"),
    "hl": ("48;5;236", "on #303030"),  # background of the selected graph column
}

DEVICE_ORDER = ("cpu", "gpu", "igpu", "rest", "monitor")
DEVICE_NAMES = {"cpu": "CPU", "gpu": "GPU", "igpu": "iGPU", "rest": "~Rest", "monitor": "~Monitors"}


def color_enabled(no_color_flag: bool = False, setting: bool = True, stream=None) -> bool:
    stream = stream or sys.stdout
    if no_color_flag or not setting or os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Styler:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def paint(self, text: str, style: str = "") -> str:
        if not self.enabled or not style:
            return text
        codes = ";".join(STYLES[s][0] for s in style.split() if s in STYLES)
        return f"\x1b[{codes}m{text}\x1b[0m" if codes else text

    def line(self, segments: Line) -> str:
        return "".join(self.paint(t, s) for t, s in segments)

    def lines(self, lines: list[Line]) -> str:
        return "\n".join(self.line(seg) for seg in lines)


def plain(lines: list[Line]) -> str:
    return Styler(False).lines(lines)
