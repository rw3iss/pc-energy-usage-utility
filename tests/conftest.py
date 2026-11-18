from __future__ import annotations

from dataclasses import dataclass

import pytest

from pcenergy import storage
from pcenergy.sources.base import Reading, SourceError


@dataclass
class FakeClock:
    """Wall, monotonic and boot time under test control."""

    now: float = 1_800_000_000.0  # a whole minute, UTC
    mono_s: float = 1000.0
    boot: str = "boot-a"
    up: float = 1000.0

    def wall(self) -> float:
        return self.now

    def mono(self) -> float:
        return self.mono_s

    def boot_id(self) -> str:
        return self.boot

    def uptime(self) -> float:
        return self.up

    def advance(self, awake: float, asleep: float = 0.0) -> None:
        self.now += awake + asleep
        self.mono_s += awake
        self.up += awake + asleep

    def reboot(self, boot: str, uptime: float) -> None:
        self.boot = boot
        self.now += uptime
        self.mono_s = uptime
        self.up = uptime


class ScriptedSource:
    """A source whose next reading is set by the test."""

    kind = "scripted"

    def __init__(
        self,
        sid="cpu.package",
        device="cpu",
        in_total=True,
        max_power_w=200.0,
        max_range_j=None,
        reload_resets=False,
        power=False,
    ):
        self.id = sid
        self.device = device
        self.label = f"Test {sid}"
        self.in_total = in_total
        self.max_power_w = max_power_w
        self.max_range_j = max_range_j
        self.reload_resets = reload_resets
        self.power = power
        self.value: float | Exception = 0.0

    def read(self) -> Reading:
        if isinstance(self.value, Exception):
            raise self.value
        if self.power:
            return Reading(power_w=self.value)
        return Reading(counter_j=self.value, max_range_j=self.max_range_j)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "energy.db")
    conn = storage.connect_rw(path)
    yield path, conn
    conn.close()


__all__ = ["FakeClock", "ScriptedSource", "SourceError"]
