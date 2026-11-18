import json

import pytest

from pcenergy import storage
from pcenergy.cli import main
from pcenergy.demo import fill


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    path = str(tmp_path / "demo.db")
    fill(storage.connect_rw(path), days=40)
    monkeypatch.setenv("PCENERGY_DB", path)
    monkeypatch.setenv("PCENERGY_CONFIG", str(tmp_path / "c.toml"))
    monkeypatch.setenv("PCENERGY_COLLECTOR_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("COLUMNS", "100")
    return path


@pytest.mark.parametrize(
    "cmd",
    [
        ["month"],
        ["month", "-1"],
        ["week"],
        ["day", "-1"],
        ["now"],
        ["status"],
        ["month", "--ascii", "-d", "gpu"],
    ],
)
def test_reports_run(demo_db, capsys, cmd):
    assert main([*cmd, "--no-color"]) in (0, 1)
    out = capsys.readouterr().out
    assert out.strip()
    assert "\x1b[" not in out


def test_month_json(demo_db, capsys):
    assert main(["month", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["period"]["kind"] == "month"
    assert data["series"]["bucket"] == "day"
    assert data["summary"]["total_kwh"] > 0


def test_flags_before_or_after_command(demo_db, capsys):
    assert main(["--json", "day"]) == 0
    json.loads(capsys.readouterr().out)
    assert main(["day", "--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_export_csv_and_json(demo_db, capsys):
    assert main(["export", "--bucket", "day"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("start,cpu_kwh,gpu_kwh,rest_kwh,total_kwh")
    assert len(lines) == 31
    assert main(["export", "--json", "--bucket", "hour", "--from", "2026-01-01", "--to", "2026-01-01"]) == 0
    assert len(json.loads(capsys.readouterr().out)["buckets"]) in (23, 24, 25)


def test_bad_period_argument(demo_db):
    assert main(["day", "yesterday"]) == 2


def test_missing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PCENERGY_DB", str(tmp_path / "nope.db"))
    with pytest.raises(SystemExit, match="no database"):
        main(["month"])


def test_demo_data_refuses_real_db(monkeypatch, tmp_path):
    monkeypatch.delenv("PCENERGY_DB", raising=False)
    monkeypatch.setenv("PCENERGY_COLLECTOR_CONFIG", str(tmp_path / "none.toml"))
    assert main(["demo-data"]) == 2
