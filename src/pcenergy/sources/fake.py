"""Fake sources for development, tests and demo data. No hardware or root needed.

``pcenergy collect --source fake`` runs the collector against these; ``pcenergy demo-data`` uses
the same power model to fill a database with realistic history.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

from pcenergy.sources.base import Reading

FAKE_RAPL_RANGE_J = 65_532.610987  # same wrap range as a real AMD package counter


def _noise(ts: float, salt: int) -> float:
    """Deterministic noise in [0, 1) from the minute and a salt."""
    x = math.sin(int(ts // 60) * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


def fake_power(ts: float, day_kind: str | None = None) -> dict[str, float]:
    """Watts per fake source at unix time ``ts``: idle nights, work days, gaming evenings, AI jobs."""
    local = datetime.fromtimestamp(ts)
    h = local.hour + local.minute / 60
    kind = day_kind or ("ai" if local.toordinal() % 9 == 0 else "normal")
    n1, n2 = _noise(ts, 1), _noise(ts, 2)
    cpu, gpu = 28.0 + 6 * n1, 14.0 + 4 * n2  # idle
    if 9 <= h < 18:  # work: browser, editor, builds
        cpu, gpu = 45 + 40 * n1, 22 + 15 * n2
    if 19 <= h < 23.5:  # gaming evening
        cpu, gpu = 85 + 35 * n1, 210 + 70 * n2
    if kind == "ai" and (h >= 23 or h < 7):  # overnight training job
        cpu, gpu = 70 + 15 * n1, 270 + 15 * n2
    core = cpu * (0.55 + 0.1 * n2)
    return {"cpu.package": cpu, "cpu.core": core, "gpu.0": gpu}


class FakeSource:
    kind: ClassVar[str] = "fake"
    reload_resets = False

    def __init__(
        self,
        source_id: str,
        device: str,
        label: str,
        in_total: bool,
        max_power_w: float,
        max_range_j: float | None = None,
        power_fn: Callable[[float], dict[str, float]] = fake_power,
        clock: Callable[[], float] = time.time,
    ):
        self.id = source_id
        self.device = device
        self.label = label
        self.in_total = in_total
        self.max_power_w = max_power_w
        self.max_range_j = max_range_j
        self.power_fn = power_fn
        self.clock = clock
        self._counter = 0.0
        self._last: float | None = None

    @classmethod
    def detect(cls) -> list[FakeSource]:
        return [
            cls("cpu.package", "cpu", "Fake CPU", True, 230, FAKE_RAPL_RANGE_J),
            cls("cpu.core", "cpu", "Fake CPU (core)", False, 230, FAKE_RAPL_RANGE_J),
            cls("gpu.0", "gpu", "Fake GPU", True, 320),
        ]

    def read(self) -> Reading:
        now = self.clock()
        if self._last is not None:
            self._counter += self.power_fn(now)[self.id] * (now - self._last)
        self._last = now
        value = self._counter % self.max_range_j if self.max_range_j else self._counter
        return Reading(counter_j=value, max_range_j=self.max_range_j)
