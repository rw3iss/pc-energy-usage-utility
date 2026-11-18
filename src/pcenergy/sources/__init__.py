"""Source plugins. Add a new source by writing a class that follows ``EnergySource`` and
registering it in ``REGISTRY``; nothing else needs to change."""

from __future__ import annotations

import logging

from pcenergy.sources.base import EnergySource, Reading, SourceError
from pcenergy.sources.fake import FakeSource
from pcenergy.sources.hwmon import HwmonPowerSource
from pcenergy.sources.nvml import NvmlSource
from pcenergy.sources.rapl import RaplSource

__all__ = ["REGISTRY", "EnergySource", "Reading", "SourceError", "detect_sources"]

log = logging.getLogger(__name__)

REGISTRY: dict[str, type] = {
    "rapl": RaplSource,
    "nvml": NvmlSource,
    "hwmon": HwmonPowerSource,
    "fake": FakeSource,
}


def detect_sources(kinds: list[str]) -> list[EnergySource]:
    """Detect every source of the given plugin kinds, in order. Unknown kinds are skipped."""
    found: list[EnergySource] = []
    for kind in kinds:
        cls = REGISTRY.get(kind)
        if cls is None:
            log.warning("unknown source kind %r skipped", kind)
            continue
        try:
            found.extend(cls.detect())
        except Exception as exc:
            log.warning("%s: detection failed: %s", kind, exc)
    return found
