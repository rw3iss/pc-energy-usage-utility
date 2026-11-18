import pytest

from pcenergy import config as cfg
from pcenergy.cli import main


def test_defaults_are_valid():
    for schema in (cfg.USER_SCHEMA, cfg.COLLECTOR_SCHEMA):
        assert cfg.validate(schema, cfg.defaults(schema)) == cfg.defaults(schema)


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("month_start_day", "15", 15),
        ("price_per_kwh", "0.21", 0.21),
        ("include_estimate", "off", False),
        ("color", "yes", True),
        ("week_start", "sunday", "sunday"),
    ],
)
def test_parse_cli_strings(key, raw, expected):
    assert cfg.schema_map(cfg.USER_SCHEMA)[key].parse(raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("month_start_day", "0"),
        ("month_start_day", "32"),
        ("month_start_day", "1.5"),
        ("psu_efficiency", "1.2"),
        ("week_start", "friday"),
        ("color", "maybe"),
        ("refresh_s", "x"),
    ],
)
def test_parse_rejects_bad_values(key, raw):
    with pytest.raises(cfg.ConfigError):
        cfg.schema_map(cfg.USER_SCHEMA)[key].parse(raw)


def test_list_field():
    field = cfg.schema_map(cfg.COLLECTOR_SCHEMA)["sources"]
    assert field.parse("rapl, hwmon") == ["rapl", "hwmon"]
    with pytest.raises(cfg.ConfigError):
        field.parse("rapl,bogus")


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    values = cfg.defaults(cfg.USER_SCHEMA) | {"month_start_day": 15, "currency_symbol": '€"', "color": False}
    cfg.save(cfg.USER_SCHEMA, values, path)
    loaded, warnings = cfg.load(cfg.USER_SCHEMA, path)
    assert warnings == []
    assert loaded == values
    assert "[billing]" in path.read_text()


def test_load_bad_values_fall_back_with_warning(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[billing]\nmonth_start_day = 40\nnope = 1\n[display]\ntheme = "light"\n')
    loaded, warnings = cfg.load(cfg.USER_SCHEMA, path)
    assert loaded["month_start_day"] == 1
    assert loaded["theme"] == "light"
    assert len(warnings) == 2


def test_load_broken_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is = = not toml")
    loaded, warnings = cfg.load(cfg.USER_SCHEMA, path)
    assert loaded == cfg.defaults(cfg.USER_SCHEMA)
    assert warnings


def test_db_path_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("PCENERGY_COLLECTOR_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.delenv("PCENERGY_DB", raising=False)
    assert cfg.db_path() == cfg.DEFAULT_DB_PATH
    monkeypatch.setenv("PCENERGY_DB", "/tmp/x.db")
    assert cfg.db_path() == "/tmp/x.db"
    assert cfg.db_path("/tmp/y.db") == "/tmp/y.db"


def test_cli_config_set_get(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PCENERGY_CONFIG", str(tmp_path / "c.toml"))
    assert main(["config", "set", "price_per_kwh", "0.3"]) == 0
    capsys.readouterr()
    assert main(["config", "get", "price_per_kwh"]) == 0
    assert capsys.readouterr().out.strip() == "0.3"
    assert main(["config", "set", "price_per_kwh", "-1"]) == 2
    assert main(["config", "get", "nope"]) == 2
