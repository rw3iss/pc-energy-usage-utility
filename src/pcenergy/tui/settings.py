"""Settings panel: a modal form built from the user settings schema (``config.USER_SCHEMA``).

Tab / Shift+Tab move, Ctrl+S or Save saves, Esc cancels (a second Esc discards unsaved changes).
Invalid values show an inline error and block saving. Collector settings are shown read-only.
"""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from pcenergy import config as cfg
from pcenergy.units import fmt_bytes

GROUP_TITLES = {"billing": "Billing", "estimate": "Estimate", "display": "Display"}
GROUP_NOTES = {"estimate": "Changes apply to all history: the database stores measured energy only."}


class SettingsScreen(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.initial = dict(self.app.settings)
        self.fields = cfg.USER_SCHEMA
        self._confirm_discard = False

    def compose(self):
        with VerticalScroll(id="settings-box"):
            yield Static("Settings  ·  Tab moves · Ctrl+S saves · Esc cancels", classes="group")
            section = None
            for f in self.fields:
                if f.section != section:
                    section = f.section
                    yield Static(GROUP_TITLES[section], classes="group")
                    if section in GROUP_NOTES:
                        yield Static(GROUP_NOTES[section], classes="help")
                with Horizontal(classes="row"):
                    yield Label(f.key)
                    yield self._widget(f)
                    yield Static(f"{f.help} ({f.describe()})", classes="help")
                yield Static("", id=f"err-{f.key}", classes="error")
            yield Static("Collector (read-only here)", classes="group")
            yield Static(self._collector_text(), classes="help")
            yield Static("", id="settings-status")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def _widget(self, f: cfg.Field):
        value = self.initial[f.key]
        if f.type is bool:
            return Switch(value=bool(value), id=f"w-{f.key}")
        if f.choices:
            return Select([(c, c) for c in f.choices], value=value, allow_blank=False, id=f"w-{f.key}")
        return Input(value=str(value), id=f"w-{f.key}")

    def _collector_text(self) -> str:
        info = self.app.collector_info()
        sources = ", ".join(info["sources"]) or "none yet"
        return (
            f"interval {info['interval_s']} s · per-minute rows kept {info['keep_minutes_days']} days · "
            f"hourly rows kept forever\nsources: {sources}\n"
            f"database: {info['db_path']} ({fmt_bytes(info['db_size'])})\n"
            f"Change these in {cfg.collector_config_path()} (sudoedit), then "
            "`sudo systemctl restart pcenergy-collector`."
        )

    # ------------------------------------------------------------------ values

    def _raw(self, f: cfg.Field):
        w = self.query_one(f"#w-{f.key}")
        return w.value

    def _collect(self) -> tuple[dict, dict[str, str]]:
        values, errors = {}, {}
        for f in self.fields:
            try:
                values[f.key] = f.parse(self._raw(f))
            except cfg.ConfigError as exc:
                errors[f.key] = str(exc)
        return values, errors

    def _dirty(self) -> bool:
        for f in self.fields:
            raw = self._raw(f)
            try:
                if f.parse(raw) != self.initial[f.key]:
                    return True
            except cfg.ConfigError:
                return True
        return False

    def _show_errors(self, errors: dict[str, str]) -> None:
        for f in self.fields:
            self.query_one(f"#err-{f.key}", Static).update(errors.get(f.key, ""))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._confirm_discard = False
        _, errors = self._collect()
        self._show_errors(errors)

    # ------------------------------------------------------------------ actions

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        values, errors = self._collect()
        self._show_errors(errors)
        if errors:
            self.query_one("#settings-status", Static).update("Fix the errors above to save.")
            return
        cfg.save(cfg.USER_SCHEMA, values, cfg.user_config_path())
        self.app.notify(f"Saved {cfg.user_config_path()}", timeout=2)
        self.dismiss(True)

    def action_cancel(self) -> None:
        if self._dirty() and not self._confirm_discard:
            self._confirm_discard = True
            self.query_one("#settings-status", Static).update(
                "Unsaved changes: press Esc again to discard, or Ctrl+S to save."
            )
            return
        self.dismiss(False)
