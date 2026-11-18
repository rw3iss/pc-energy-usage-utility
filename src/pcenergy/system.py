"""Small helpers that ask the OS about the collector service. Used by ``status`` and ``doctor``."""

from __future__ import annotations

import shutil
import subprocess

SERVICE = "pcenergy-collector.service"


def systemctl(*args: str) -> str:
    """Run ``systemctl`` and return its stdout ("" if systemctl is missing or fails to start)."""
    exe = shutil.which("systemctl")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def service_state(unit: str = SERVICE) -> str:
    """``active``, ``inactive``, ``failed``, ``activating`` … or ``unknown``."""
    return systemctl("is-active", unit) or "unknown"


def service_enabled(unit: str = SERVICE) -> str:
    return systemctl("is-enabled", unit) or "unknown"
