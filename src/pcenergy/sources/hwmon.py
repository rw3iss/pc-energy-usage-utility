"""GPU power from amdgpu hwmon sensors (``power1_average`` or ``power1_input``, microwatts).

These report power, not energy: the collector integrates them between samples (trapezoid rule),
so the result is coarse. Off by default; enable with ``sources = ["rapl", "nvml", "hwmon"]``.
A GPU with less than 1 GiB of dedicated VRAM is treated as an integrated GPU (``igpu``).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import ClassVar

from pcenergy.sources.base import Reading, SourceError

HWMON = Path("/sys/class/hwmon")
IGPU_VRAM_LIMIT = 1 << 30


class HwmonPowerSource:
    kind: ClassVar[str] = "hwmon"
    in_total = True
    reload_resets = False

    def __init__(self, path: Path, source_id: str, device: str, label: str, max_power_w: float):
        self.path = path
        self.id = source_id
        self.device = device
        self.label = label
        self.max_power_w = max_power_w

    @classmethod
    def detect(cls, root: Path = HWMON) -> list[HwmonPowerSource]:
        if not root.is_dir():
            return []
        sources: list[HwmonPowerSource] = []
        counters = {"igpu": 0, "gpu": 0}
        for hw in sorted(root.iterdir(), key=lambda p: p.name):
            try:
                if (hw / "name").read_text().strip() != "amdgpu":
                    continue
            except OSError:
                continue
            sensor = next((hw / f for f in ("power1_average", "power1_input") if (hw / f).exists()), None)
            if sensor is None:
                continue
            try:
                vram = int((hw / "device" / "mem_info_vram_total").read_text())
            except (OSError, ValueError):
                vram = 0
            device = "igpu" if vram < IGPU_VRAM_LIMIT else "gpu"
            n = counters[device]
            counters[device] += 1
            sid = f"igpu.{n}" if device == "igpu" else f"gpu.amd{n}"
            label = "AMD integrated GPU" if device == "igpu" else "AMD GPU"
            max_w = 100.0 if device == "igpu" else 600.0
            with contextlib.suppress(OSError, ValueError):
                max_w = max(int((hw / "power1_cap_max").read_text()) / 1e6, 15.0)
            sources.append(cls(sensor, sid, device, label, max_w))
        return sources

    def read(self) -> Reading:
        try:
            return Reading(power_w=int(self.path.read_text().strip()) / 1e6)
        except (OSError, ValueError) as exc:
            raise SourceError(f"{self.path}: {exc}") from exc
