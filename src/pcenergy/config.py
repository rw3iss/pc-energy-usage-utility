"""Settings schema, validation, and TOML load/save.

One schema per file: every key has a type, a range or choice list, a default and a help line.
The CLI ``config`` command, the TUI settings panel and the collector all use this module.

User settings live in ``~/.config/pcenergy/config.toml`` (``PCENERGY_CONFIG`` overrides).
Collector settings live in ``/etc/pcenergy/collector.toml`` (``PCENERGY_COLLECTOR_CONFIG`` overrides).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "/var/lib/pcenergy/energy.db"
DEVICE_FILTERS = ("all", "cpu", "gpu", "rest", "measured")
SOURCE_KINDS = ("rapl", "nvml", "hwmon")


class ConfigError(ValueError):
    """A setting value failed validation."""


@dataclass(frozen=True)
class Field:
    key: str
    section: str
    type: type  # int, float, str, bool or list
    default: Any
    help: str
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None

    def parse(self, value: Any) -> Any:
        """Convert ``value`` (a TOML value or a CLI string) to the field type and validate it."""
        try:
            if self.type is bool:
                if isinstance(value, bool):
                    v = value
                elif str(value).strip().lower() in ("1", "true", "on", "yes"):
                    v = True
                elif str(value).strip().lower() in ("0", "false", "off", "no"):
                    v = False
                else:
                    raise ValueError
            elif self.type is int:
                if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                    raise ValueError
                v = int(str(value).strip()) if isinstance(value, str) else int(value)
            elif self.type is float:
                if isinstance(value, bool):
                    raise ValueError
                v = float(value)
            elif self.type is list:
                if isinstance(value, str):
                    v = [p.strip() for p in value.split(",") if p.strip()]
                elif isinstance(value, list):
                    v = [str(p) for p in value]
                else:
                    raise ValueError
            else:
                v = str(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{self.key}: expected {self.type.__name__}, got {value!r}") from None
        if self.min is not None and v < self.min:
            raise ConfigError(f"{self.key}: must be ≥ {_num(self.min)}")
        if self.max is not None and v > self.max:
            raise ConfigError(f"{self.key}: must be ≤ {_num(self.max)}")
        if self.choices is not None:
            items = v if isinstance(v, list) else [v]
            bad = [i for i in items if i not in self.choices]
            if bad:
                raise ConfigError(f"{self.key}: {', '.join(bad)} not one of {', '.join(self.choices)}")
        return v

    def describe(self) -> str:
        """Allowed values, for help text."""
        if self.choices:
            return " | ".join(self.choices)
        if self.min is not None or self.max is not None:
            lo = _num(self.min) if self.min is not None else ""
            hi = _num(self.max) if self.max is not None else ""
            return f"{lo}–{hi}"
        return self.type.__name__


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


USER_SCHEMA: tuple[Field, ...] = (
    # Billing
    Field("month_start_day", "billing", int, 1, "Day of the month a billing period starts (1–31)", 1, 31),
    Field("price_per_kwh", "billing", float, 0.15, "Electricity price per kWh", 0, 100),
    Field("currency_symbol", "billing", str, "$", "Currency symbol shown with costs"),
    Field("fixed_monthly_fee", "billing", float, 0.0, "Fixed fee per month (shown separately)", 0, 100000),
    # Estimate
    Field(
        "base_watts",
        "estimate",
        float,
        60.0,
        "Estimated draw of the rest of the system (board, RAM, drives, fans)",
        0,
        2000,
    ),
    Field(
        "psu_efficiency",
        "estimate",
        float,
        0.90,
        "Power supply efficiency used for the wall estimate",
        0.5,
        1.0,
    ),
    Field(
        "monitor_watts", "estimate", float, 0.0, "Monitor draw while the PC is on (0 = not counted)", 0, 2000
    ),
    Field(
        "include_estimate",
        "estimate",
        bool,
        True,
        "On: totals show the wall estimate. Off: measured parts only",
    ),
    # Display
    Field("default_devices", "display", str, "all", "Device filter at start", choices=DEVICE_FILTERS),
    Field("week_start", "display", str, "monday", "First day of the week", choices=("monday", "sunday")),
    Field("day_graph_unit", "display", str, "W", "Unit of the 24-hour graph", choices=("W", "kWh")),
    Field("graph_style", "display", str, "unicode", "Graph characters", choices=("unicode", "ascii")),
    Field("color", "display", bool, True, "Use colors"),
    Field("refresh_s", "display", int, 60, "TUI auto refresh interval in seconds", 5, 3600),
    Field("theme", "display", str, "dark", "TUI theme", choices=("dark", "light")),
)

COLLECTOR_SCHEMA: tuple[Field, ...] = (
    Field("interval_s", "collector", int, 60, "Seconds between samples", 10, 300),
    Field(
        "keep_minutes_days",
        "collector",
        int,
        90,
        "Days to keep per-minute rows (hourly rows are kept forever)",
        1,
        3650,
    ),
    Field("sources", "collector", list, ["rapl", "nvml"], "Source plugins to use", choices=SOURCE_KINDS),
    Field("db_path", "collector", str, DEFAULT_DB_PATH, "Database file"),
)


def schema_map(schema: tuple[Field, ...]) -> dict[str, Field]:
    return {f.key: f for f in schema}


def defaults(schema: tuple[Field, ...]) -> dict[str, Any]:
    return {f.key: (list(f.default) if isinstance(f.default, list) else f.default) for f in schema}


def user_config_path() -> Path:
    if env := os.environ.get("PCENERGY_CONFIG"):
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pcenergy" / "config.toml"


def collector_config_path() -> Path:
    return Path(os.environ.get("PCENERGY_COLLECTOR_CONFIG", "/etc/pcenergy/collector.toml"))


def db_path(explicit: str | None = None) -> str:
    """Database path: ``--db`` flag, then ``PCENERGY_DB``, then the collector config, then the default."""
    if explicit:
        return explicit
    if env := os.environ.get("PCENERGY_DB"):
        return env
    try:
        return load(COLLECTOR_SCHEMA, collector_config_path())[0]["db_path"]
    except OSError:
        return DEFAULT_DB_PATH


def load(schema: tuple[Field, ...], path: Path) -> tuple[dict[str, Any], list[str]]:
    """Load settings from ``path`` (missing file = defaults).

    Returns the settings and a list of warnings. Invalid or unknown keys never stop the program:
    an invalid value falls back to its default and adds a warning.
    """
    values = defaults(schema)
    warnings: list[str] = []
    if not path.exists():
        return values, warnings
    with path.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            return values, [f"{path}: {exc}; using defaults"]
    fields = schema_map(schema)
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    for key, value in flat.items():
        field = fields.get(key)
        if field is None:
            warnings.append(f"{path}: unknown key {key!r} ignored")
            continue
        try:
            values[key] = field.parse(value)
        except ConfigError as exc:
            warnings.append(f"{path}: {exc}; using default {field.default!r}")
    return values, warnings


def load_user() -> tuple[dict[str, Any], list[str]]:
    return load(USER_SCHEMA, user_config_path())


def validate(schema: tuple[Field, ...], values: dict[str, Any]) -> dict[str, Any]:
    """Validate a full dict of values. Raises ConfigError on the first bad value."""
    fields = schema_map(schema)
    out = {}
    for key, value in values.items():
        if key not in fields:
            raise ConfigError(f"unknown setting {key!r}")
        out[key] = fields[key].parse(value)
    return out


def save(schema: tuple[Field, ...], values: dict[str, Any], path: Path) -> None:
    """Write all settings as TOML, grouped by section, with help comments. Atomic replace."""
    clean = validate(schema, {**defaults(schema), **values})
    lines = ["# pcenergy settings. Edit with `pcenergy config set KEY VALUE` or the TUI settings panel.", ""]
    section = None
    for field in schema:
        if field.section != section:
            if section is not None:
                lines.append("")
            section = field.section
            lines.append(f"[{section}]")
        lines.append(f"# {field.help} ({field.describe()})")
        lines.append(f"{field.key} = {_toml_value(clean[field.key])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
