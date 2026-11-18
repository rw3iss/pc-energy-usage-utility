"""The energy source interface shared by all source plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True)
class Reading:
    """One raw reading.

    Counter sources set ``counter_j`` (energy since some start point). Power sources set ``power_w``;
    the collector integrates power over time. ``max_range_j`` is set when the counter wraps.
    """

    counter_j: float | None = None
    power_w: float | None = None
    max_range_j: float | None = None


@runtime_checkable
class EnergySource(Protocol):
    kind: ClassVar[str]  # plugin name used in the collector config: "rapl", "nvml", "hwmon", "fake"
    id: str  # "cpu.package", "cpu.core", "gpu.0", "igpu.0"
    device: str  # "cpu" | "gpu" | "igpu"
    label: str  # human label, for example the CPU model
    in_total: bool  # False for sub-zones that are already part of another source
    max_power_w: float  # upper bound used by the plausibility guard
    reload_resets: bool  # True if the counter restarts when its driver reloads (NVML)

    @classmethod
    def detect(cls) -> list[EnergySource]: ...

    def read(self) -> Reading: ...


class SourceError(RuntimeError):
    """A source could not be read (permission, driver missing, device gone)."""
