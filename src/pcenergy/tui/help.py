"""Help overlay: every key, read from the key table in ``pcenergy.keys``."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

from pcenergy import keys

SCOPE_TITLES = {
    "main": "Main screen",
    "day": "Day view",
    "week": "Week view",
    "events": "Event log",
    "global": "Everywhere",
}


class HelpScreen(ModalScreen):
    BINDINGS: ClassVar = [
        Binding("escape,question_mark", "dismiss", "Close", show=False),
        Binding("q,ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def compose(self):
        text = Text()
        text.append("pcenergy keys\n", "bold")
        scope = None
        for s, label, help_text in keys.help_rows():
            if s != scope:
                scope = s
                text.append(f"\n{SCOPE_TITLES.get(s, s)}\n", "bold")
            text.append(f"  {label:<16}", "bold")
            text.append(f"{help_text}\n")
        text.append("\nSettings panel: Tab / Shift+Tab move, Ctrl+S saves, Esc cancels.\n", "dim")
        text.append("~ marks estimated values. Press Esc or ? to close.", "dim")
        yield Static(text, id="help-box")

    def action_dismiss(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
