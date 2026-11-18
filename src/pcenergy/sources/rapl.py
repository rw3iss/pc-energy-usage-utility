"""CPU energy from RAPL counters exposed through the Linux powercap framework (Intel and AMD).

The ``energy_uj`` files are root-only on current kernels (a side-channel fix), so only the collector,
which runs as root, can read them. Do not change their permissions.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import ClassVar

from pcenergy.sources.base import Reading, SourceError

POWERCAP = Path("/sys/class/powercap")
DEFAULT_CPU_MAX_W = 400.0


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                name = line.split(":", 1)[1].strip()
                return re.sub(r"\s+\d+-Core Processor$", "", name)
    except OSError:
        pass
    return "CPU"


class RaplSource:
    kind: ClassVar[str] = "rapl"
    device = "cpu"
    reload_resets = False

    def __init__(self, zone: Path, source_id: str, label: str, in_total: bool, max_power_w: float):
        self.zone = zone
        self.id = source_id
        self.label = label
        self.in_total = in_total
        self.max_power_w = max_power_w
        self._max_range_j = _read_int(zone / "max_energy_range_uj") / 1e6

    @classmethod
    def detect(cls, root: Path = POWERCAP) -> list[RaplSource]:
        """One source per package zone (``intel-rapl:N``) and per sub-zone (``intel-rapl:N:M``).

        Sub-zones (cores, uncore, dram) are details: ``in_total`` is False so they are never added
        to the CPU total. A ``psys`` zone (whole platform) is also kept out of the total.
        """
        if not root.is_dir():
            return []
        model = cpu_model()
        sources: list[RaplSource] = []
        zones = sorted(
            (z for z in root.iterdir() if re.fullmatch(r"intel-rapl:\d+(:\d+)?", z.name)),
            key=lambda z: [int(n) for n in z.name.split(":")[1:]],
        )
        for zone in zones:
            try:
                name = (zone / "name").read_text().strip()
                _read_int(zone / "max_energy_range_uj")
            except (OSError, SourceError):
                continue
            parts = [int(n) for n in zone.name.split(":")[1:]]
            pkg = parts[0]
            suffix = "" if pkg == 0 else str(pkg)
            if len(parts) == 1 and name.startswith("package"):
                sid, label, in_total = f"cpu.package{suffix}", model, True
            elif len(parts) == 1:  # psys or another top-level zone
                sid, label, in_total = f"cpu.{name}{suffix}", f"{model} ({name})", False
            else:
                sid, label, in_total = f"cpu.{name}{suffix}", f"{model} ({name})", False
            max_w = DEFAULT_CPU_MAX_W
            with contextlib.suppress(OSError, SourceError):
                max_w = max(_read_int(zone / "constraint_0_max_power_uw") / 1e6, 50.0)
            sources.append(cls(zone, sid, label, in_total, max_w))
        return sources

    def read(self) -> Reading:
        try:
            uj = _read_int(self.zone / "energy_uj")
        except PermissionError as exc:
            raise SourceError(
                f"{self.zone}/energy_uj: permission denied (run the collector as root)"
            ) from exc
        return Reading(counter_j=uj / 1e6, max_range_j=self._max_range_j)


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except PermissionError:
        raise
    except (OSError, ValueError) as exc:
        raise SourceError(f"{path}: {exc}") from exc
