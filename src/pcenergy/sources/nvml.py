"""NVIDIA GPU energy through NVML (the ``nvidia-ml-py`` package, imported as ``pynvml``).

Uses ``nvmlDeviceGetTotalEnergyConsumption`` (millijoules since the driver loaded, 64-bit, no wrap).
If a GPU does not support it, the source reports power instead and the collector integrates it.
The NVML handle stays open for the life of the process; no ``nvidia-smi`` process is ever started.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pcenergy.sources.base import Reading, SourceError

log = logging.getLogger(__name__)
DEFAULT_GPU_MAX_W = 600.0

_nvml: Any = None


def _load() -> Any:
    """Import and initialize NVML once. Returns the module or None if NVML is not available."""
    global _nvml
    if _nvml is None:
        try:
            import pynvml

            pynvml.nvmlInit()
            _nvml = pynvml
        except Exception as exc:  # ImportError, NVMLError_LibraryNotFound, driver not loaded
            log.debug("NVML not available: %s", exc)
            _nvml = False
    return _nvml or None


class NvmlSource:
    kind: ClassVar[str] = "nvml"
    device = "gpu"
    in_total = True
    reload_resets = True

    def __init__(self, index: int, handle: Any, label: str, max_power_w: float, use_counter: bool):
        self.index = index
        self.handle = handle
        self.id = f"gpu.{index}"
        self.label = label
        self.max_power_w = max_power_w
        self.use_counter = use_counter

    @classmethod
    def detect(cls) -> list[NvmlSource]:
        nv = _load()
        if nv is None:
            return []
        sources = []
        for i in range(nv.nvmlDeviceGetCount()):
            handle = nv.nvmlDeviceGetHandleByIndex(i)
            name = nv.nvmlDeviceGetName(handle)
            label = name.decode() if isinstance(name, bytes) else str(name)
            try:
                max_w = nv.nvmlDeviceGetPowerManagementLimitConstraints(handle)[1] / 1000
            except nv.NVMLError:
                max_w = DEFAULT_GPU_MAX_W
            try:
                nv.nvmlDeviceGetTotalEnergyConsumption(handle)
                use_counter = True
            except nv.NVMLError:
                use_counter = False
                log.info("gpu.%d: no energy counter; integrating power instead", i)
            sources.append(cls(i, handle, label, max(max_w, 50.0), use_counter))
        return sources

    def read(self) -> Reading:
        nv = _load()
        if nv is None:
            raise SourceError("NVML not available")
        try:
            if self.use_counter:
                return Reading(counter_j=nv.nvmlDeviceGetTotalEnergyConsumption(self.handle) / 1000)
            return Reading(power_w=nv.nvmlDeviceGetPowerUsage(self.handle) / 1000)
        except nv.NVMLError as exc:
            raise SourceError(f"{self.id}: {exc}") from exc
