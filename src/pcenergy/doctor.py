"""``pcenergy doctor``: check every part and print the fix for each problem."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pcenergy import config as cfg
from pcenergy import query, storage, system
from pcenergy.sources import REGISTRY
from pcenergy.units import fmt_age

VENV_PYTHON = Path("/opt/pcenergy/venv/bin/python")


@dataclass
class Check:
    name: str
    level: str  # "ok" | "warn" | "fail"
    message: str
    fix: str = ""


def run_checks(db_path: str) -> list[Check]:
    checks: list[Check] = []
    ccfg, cwarn = cfg.load(cfg.COLLECTOR_SCHEMA, cfg.collector_config_path())
    checks.append(_sources_check(ccfg["sources"]))
    checks.extend(_rapl_checks())
    checks.append(_nvml_check())
    checks.append(_service_check())
    checks.extend(_db_checks(db_path))
    _, uwarn = cfg.load_user()
    for path_warnings, path in ((cwarn, cfg.collector_config_path()), (uwarn, cfg.user_config_path())):
        if path_warnings:
            checks.append(Check("settings", "warn", "; ".join(path_warnings), f"edit {path}"))
    if not (cwarn or uwarn):
        checks.append(Check("settings", "ok", "collector and user settings are valid"))
    return checks


def _sources_check(kinds: list[str]) -> Check:
    found = []
    for kind in kinds:
        try:
            found.extend(REGISTRY[kind].detect())
        except Exception as exc:
            return Check("sources", "fail", f"{kind}: detection failed: {exc}")
    if not found:
        return Check(
            "sources",
            "fail",
            f"no energy sources found (plugins: {', '.join(kinds)})",
            "check that /sys/class/powercap/intel-rapl:0 exists; for NVIDIA install the [nvidia] extra",
        )
    ids = ", ".join(s.id for s in found)
    return Check("sources", "ok", f"found {ids}")


def _rapl_checks() -> list[Check]:
    zone = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    if not zone.exists():
        return [Check("rapl", "warn", "no RAPL powercap zone; CPU energy will not be measured")]
    checks = []
    mode = stat.S_IMODE(zone.stat().st_mode)
    if mode & stat.S_IROTH:
        checks.append(
            Check(
                "rapl permissions",
                "warn",
                f"{zone} is world-readable (mode {mode:o}); this re-opens a power side channel",
                f"sudo chmod 0400 {zone} (pcenergy only needs root to read it)",
            )
        )
    else:
        checks.append(
            Check("rapl permissions", "ok", "energy counters are root-only (the collector runs as root)")
        )
    return checks


def _nvml_check() -> Check:
    if not Path("/proc/driver/nvidia").exists():
        return Check("nvml", "ok", "no NVIDIA driver loaded; GPU source not needed")
    if VENV_PYTHON.exists():
        ok = subprocess.run([str(VENV_PYTHON), "-c", "import pynvml"], capture_output=True).returncode == 0
        if not ok:
            return Check(
                "nvml",
                "fail",
                "NVIDIA driver found but the collector venv has no nvidia-ml-py",
                "sudo /opt/pcenergy/venv/bin/pip install nvidia-ml-py && "
                "sudo systemctl restart pcenergy-collector",
            )
        return Check("nvml", "ok", "NVIDIA driver and nvidia-ml-py found")
    try:
        import pynvml  # noqa: F401
    except ImportError:
        return Check("nvml", "warn", "nvidia-ml-py is not installed here", "uv tool install '.[nvidia]'")
    return Check("nvml", "ok", "NVIDIA driver and nvidia-ml-py found")


def _service_check() -> Check:
    state = system.service_state()
    enabled = system.service_enabled()
    if state == "active":
        level = "ok" if enabled == "enabled" else "warn"
        fix = "" if level == "ok" else f"sudo systemctl enable {system.SERVICE}"
        return Check("service", level, f"{system.SERVICE} is active ({enabled})", fix)
    fix = (
        "sudo packaging/install.sh"
        if state == "unknown" or enabled in ("unknown", "not-found", "")
        else f"sudo systemctl enable --now {system.SERVICE}; journalctl -u {system.SERVICE} -n 50"
    )
    return Check("service", "fail", f"{system.SERVICE} is {state} ({enabled})", fix)


def _db_checks(db_path: str) -> list[Check]:
    if not os.path.exists(db_path):
        return [Check("database", "fail", f"{db_path} does not exist", "start the collector service")]
    try:
        conn = storage.connect_ro(db_path)
        st = query.status(conn, db_path, now=time.time())
    except Exception as exc:
        return [
            Check("database", "fail", f"{db_path}: {exc}", f"ls -l {db_path}; it must be readable (0644)")
        ]
    checks = []
    if st.schema_version != storage.SCHEMA_VERSION:
        checks.append(
            Check(
                "database",
                "warn",
                f"schema v{st.schema_version}, this pcenergy expects v{storage.SCHEMA_VERSION}",
                "update the collector: sudo packaging/install.sh",
            )
        )
    else:
        checks.append(Check("database", "ok", f"{db_path} (schema v{st.schema_version})"))
    if st.health == "ok":
        checks.append(Check("samples", "ok", f"last sample {fmt_age(st.last_sample_age_s)}"))
    else:
        checks.append(
            Check(
                "samples",
                "fail" if st.health == "none" else "warn",
                f"last sample {fmt_age(st.last_sample_age_s)}",
                f"journalctl -u {system.SERVICE} -n 50",
            )
        )
    return checks
