from pathlib import Path

import pytest

from pcenergy.sources.base import SourceError
from pcenergy.sources.fake import FakeSource
from pcenergy.sources.hwmon import HwmonPowerSource
from pcenergy.sources.rapl import RaplSource


def _zone(root: Path, name: str, zone_name: str, energy_uj: int, max_uj: int = 65532610987) -> Path:
    z = root / name
    z.mkdir()
    (z / "name").write_text(zone_name + "\n")
    (z / "energy_uj").write_text(f"{energy_uj}\n")
    (z / "max_energy_range_uj").write_text(f"{max_uj}\n")
    return z


def test_rapl_detect_and_read(tmp_path):
    _zone(tmp_path, "intel-rapl:0", "package-0", 12_000_000)
    _zone(tmp_path, "intel-rapl:0:0", "core", 5_000_000)
    (tmp_path / "intel-rapl-mmio:0").mkdir()  # duplicate interface, must be ignored
    srcs = RaplSource.detect(tmp_path)
    assert [s.id for s in srcs] == ["cpu.package", "cpu.core"]
    assert [s.in_total for s in srcs] == [True, False]
    r = srcs[0].read()
    assert r.counter_j == 12.0
    assert r.max_range_j == pytest.approx(65532.610987)


def test_rapl_permission_error(tmp_path):
    z = _zone(tmp_path, "intel-rapl:0", "package-0", 1)
    src = RaplSource.detect(tmp_path)[0]
    (z / "energy_uj").chmod(0)
    try:
        if Path(z / "energy_uj").stat().st_uid == 0:
            pytest.skip("running as root")
        with pytest.raises(SourceError, match="permission denied"):
            src.read()
    finally:
        (z / "energy_uj").chmod(0o644)


def test_rapl_missing_root(tmp_path):
    assert RaplSource.detect(tmp_path / "nope") == []


def test_hwmon_amdgpu_igpu(tmp_path):
    hw = tmp_path / "hwmon2"
    (hw / "device").mkdir(parents=True)
    (hw / "name").write_text("amdgpu\n")
    (hw / "power1_input").write_text("12500000\n")
    (hw / "device" / "mem_info_vram_total").write_text(str(512 << 20))
    other = tmp_path / "hwmon3"
    other.mkdir()
    (other / "name").write_text("k10temp\n")
    srcs = HwmonPowerSource.detect(tmp_path)
    assert [(s.id, s.device) for s in srcs] == [("igpu.0", "igpu")]
    assert srcs[0].read().power_w == 12.5


def test_fake_source_counts_and_wraps():
    t = [0.0]
    src = FakeSource(
        "cpu.package",
        "cpu",
        "x",
        True,
        300,
        max_range_j=1000,
        power_fn=lambda ts: {"cpu.package": 100.0},
        clock=lambda: t[0],
    )
    src.read()
    t[0] = 6.0
    assert src.read().counter_j == pytest.approx(600)
    t[0] = 12.0
    assert src.read().counter_j == pytest.approx(200)  # 1200 wrapped at 1000
