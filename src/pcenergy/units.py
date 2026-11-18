"""Number formatting shared by the CLI, the TUI and the renderer."""

from __future__ import annotations

J_PER_KWH = 3_600_000.0


def j_to_kwh(joules: float) -> float:
    return joules / J_PER_KWH


def fmt_energy(kwh: float, estimated: bool = False) -> str:
    """kWh with 2 decimals; Wh below 1 kWh. ``~`` prefix for estimated values."""
    prefix = "~" if estimated else ""
    if abs(kwh) < 1:
        return f"{prefix}{kwh * 1000:.0f} Wh"
    return f"{prefix}{kwh:.2f} kWh"


def fmt_kwh_short(kwh: float) -> str:
    """Compact form for tables and axis labels: ``3.1`` / ``0.42`` / ``12``."""
    if kwh >= 10:
        return f"{kwh:.0f}"
    if kwh >= 1:
        return f"{kwh:.1f}"
    return f"{kwh:.2f}"


def fmt_watts(w: float | None) -> str:
    return "–" if w is None else f"{w:.0f} W"


def fmt_cost(amount: float, symbol: str) -> str:
    return f"{symbol}{amount:.2f}"


def fmt_hours(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.1f} h" if h < 10 else f"{h:.0f} h"


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def fmt_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
