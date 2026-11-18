"""Every TUI key binding, in one table. The screens' bindings, the footer and the help overlay all
read from here, so they can never disagree."""

from __future__ import annotations

from dataclasses import dataclass

from textual.binding import Binding


@dataclass(frozen=True)
class Key:
    keys: str  # Textual key names, comma-separated
    action: str  # action name on the screen (or app, for global keys)
    label: str  # how the key is shown
    help: str
    scope: str  # "global", "main", "day", "week", "live", "events", "settings"
    footer: str = ""  # short footer text; empty = not in the footer


KEYS: tuple[Key, ...] = (
    # main (month) screen
    Key("left,h", "prev_day", "←/→ h/l", "Previous / next day in the selector (wraps to All)", "main", "day"),
    Key("right,l", "next_day", "", "", "main"),
    Key("up,k", "focus_prev", "↑/↓ k/j", "Move focus between panels; scroll a table", "main"),
    Key("down,j", "focus_next", "", "", "main"),
    Key(
        "pageup,left_square_bracket",
        "prev_month",
        "PgUp/PgDn [/]",
        "Previous / next month period",
        "main",
        "month",
    ),
    Key("pagedown,right_square_bracket", "next_month", "", "", "main"),
    Key("home", "select_all", "Home", "Select All", "main"),
    Key("end", "select_today", "End", "Select today (and jump to the current month)", "main"),
    Key("d", "cycle_devices", "d / 1–5", "Device filter: all, cpu, gpu, rest, measured", "main", "devices"),
    Key("1", "devices('all')", "", "", "main"),
    Key("2", "devices('cpu')", "", "", "main"),
    Key("3", "devices('gpu')", "", "", "main"),
    Key("4", "devices('rest')", "", "", "main"),
    Key("5", "devices('measured')", "", "", "main"),
    Key("enter", "open", "Enter", "Open the selected day (on All: the week view)", "main", "open"),
    Key("w", "week", "w", "Week view: 7 day bars and a table; ←/→ move by week", "main", "week"),
    Key("t", "toggle_table", "t", "Month panel: graph ↔ table", "main", "table"),
    Key("s", "settings", "s", "Settings", "main", "settings"),
    Key("n", "live", "n", "Live view: watts per device, 10-minute sparkline", "main", "live"),
    Key("e", "events", "e", "Event log (boots, gaps, warnings) for the shown period", "main", "events"),
    Key("r", "refresh", "r", "Refresh now", "main"),
    # day / week screens
    Key("left,h", "prev", "←/→", "Previous / next day", "day", "day"),
    Key("right,l", "next", "", "", "day"),
    Key("left,h", "prev", "←/→", "Previous / next week", "week", "week"),
    Key("right,l", "next", "", "", "week"),
    Key("up,k", "scroll_up", "↑/↓", "Scroll the table", "day", "scroll"),
    Key("down,j", "scroll_down", "", "", "day"),
    Key("up,k", "scroll_up", "↑/↓", "Scroll the table", "events", "scroll"),
    Key("down,j", "scroll_down", "", "", "events"),
    # everywhere
    Key("question_mark", "help", "?", "Help: all keys", "global", "help"),
    Key("escape", "back", "Esc", "Close the panel / go back; on the main screen: exit", "global", "quit"),
    Key("q,ctrl+c", "quit", "q Ctrl+C", "Exit at once", "global"),
)


def bindings(scope: str) -> list[Binding]:
    out = []
    for k in KEYS:
        if k.scope != scope:
            continue
        priority = k.action == "quit"
        out.append(Binding(k.keys, k.action, k.footer or k.help, show=False, priority=priority))
    return out


def footer_text(scopes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Footer hints as (key label, short text), first key label of each group."""
    out = []
    for k in KEYS:
        if k.scope in scopes and k.footer and k.label:
            out.append((k.label.split()[0], k.footer))
    return out


def help_rows() -> list[tuple[str, str, str]]:
    """(scope, key label, help) for the help overlay."""
    return [(k.scope, k.label, k.help) for k in KEYS if k.label and k.help]
