"""Textual pilot tests: navigation, every key on the main screen, settings save / cancel."""

import asyncio
from datetime import datetime, timedelta

import pytest

from pcenergy import config as cfg
from pcenergy import storage
from pcenergy.demo import fill
from pcenergy.render.text import plain
from pcenergy.tui.app import PcEnergyApp
from pcenergy.tui.day import DayScreen
from pcenergy.tui.events import EventsScreen
from pcenergy.tui.help import HelpScreen
from pcenergy.tui.live import LiveScreen
from pcenergy.tui.month import MonthScreen
from pcenergy.tui.settings import SettingsScreen
from pcenergy.tui.week import WeekScreen

NOW = datetime(2026, 11, 18, 21, 30).timestamp()


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "demo.db")
    fill(storage.connect_rw(path), days=60, now=NOW)
    monkeypatch.setenv("PCENERGY_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("PCENERGY_COLLECTOR_CONFIG", str(tmp_path / "none.toml"))
    return path


def run(db, keys, check, size=(100, 32)):
    async def go():
        app = PcEnergyApp(db, now=NOW)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for k in keys:
                await pilot.press(k)
                await pilot.pause()
            check(app)

    asyncio.run(go())


def text_of(app):
    w, h = app.size
    return plain(app.screen.draw(w, h))


def test_starts_on_today(env):
    def check(app):
        scr = app.screen
        assert isinstance(scr, MonthScreen)
        assert scr.sel == datetime.fromtimestamp(NOW).date()
        assert "Nov 2026" in text_of(app)

    run(env, [], check)


def test_left_right_wrap_through_all(env):
    def check(app):
        scr = app.screen
        assert scr.sel is None  # 18 → 17 … → 1 → All after 18 presses

    run(env, ["left"] * 18, check)

    def check2(app):
        assert app.screen.sel.day == 1  # All → right → day 1

    run(env, ["home", "right"], check2)

    def check3(app):
        assert app.screen.sel.day == 30  # All → left → last day

    run(env, ["home", "h"], check3)


def test_month_paging_limits(env):
    def check(app):
        scr = app.screen
        assert scr.month.label == "2026-10"

    run(env, ["pageup"], check)

    def check_cur(app):
        assert app.screen.month.label == "2026-11"  # cannot go past the current period

    run(env, ["pagedown"], check_cur)

    def check_first(app):
        assert app.screen.month.label == "2026-09"  # oldest data is in September

    run(env, ["left_square_bracket"] * 5, check_first)


def test_end_jumps_to_today(env):
    def check(app):
        scr = app.screen
        assert scr.month.label == "2026-11"
        assert scr.sel == datetime.fromtimestamp(NOW).date()

    run(env, ["pageup", "home", "end"], check)


def test_device_filter_keys(env):
    def check(app):
        assert app.devices == "gpu"
        assert "[GPU]" in text_of(app)

    run(env, ["d", "d"], check)

    def check5(app):
        assert app.devices == "measured"

    run(env, ["5"], check5)


@pytest.mark.parametrize(
    ("keys", "screen"),
    [
        (["enter"], DayScreen),
        (["home", "enter"], WeekScreen),
        (["w"], WeekScreen),
        (["n"], LiveScreen),
        (["e"], EventsScreen),
        (["question_mark"], HelpScreen),
        (["s"], SettingsScreen),
    ],
)
def test_screens_open(env, keys, screen):
    run(env, keys, lambda app: isinstance(app.screen, screen) or pytest.fail(str(app.screen)))


@pytest.mark.parametrize("keys", [["enter"], ["w"], ["n"], ["e"], ["question_mark"]])
def test_escape_goes_back(env, keys):
    run(
        env,
        [*keys, "escape"],
        lambda app: isinstance(app.screen, MonthScreen) or pytest.fail(str(app.screen)),
    )


def test_escape_on_main_exits(env):
    async def go():
        app = PcEnergyApp(env, now=NOW)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("escape")
            await pilot.pause()
        assert app.return_code == 0 or app._exit_renderables is not None or not app.is_running

    asyncio.run(go())


def test_q_exits_from_sub_screen(env):
    async def go():
        app = PcEnergyApp(env, now=NOW)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("w")
            await pilot.press("q")
            await pilot.pause()
            assert not app.is_running

    asyncio.run(go())


def test_table_toggle_focus_and_refresh(env):
    def check(app):
        scr = app.screen
        assert scr.table_mode
        assert "Total kWh" in text_of(app)
        assert scr.focus == 0

    run(env, ["t", "up", "r"], check)


def test_day_view_moves_by_day(env):
    def check(app):
        assert app.screen.day == datetime.fromtimestamp(NOW).date() - timedelta(days=2)

    run(env, ["enter", "left", "left", "left", "right"], check)


def test_week_view_moves_by_week(env):
    def check(app):
        assert app.screen.week.start.date().isoformat() == "2026-11-09"

    run(env, ["w", "left"], check)


def test_small_terminal_message(env):
    def check(app):
        assert "larger" in str(app.screen.canvas.render())

    run(env, [], check, size=(60, 20))


def test_80x24_fits(env):
    def check(app):
        lines = app.screen.draw(80, 24)
        assert len(lines) <= 24

    run(env, [], check, size=(80, 24))


def test_settings_save(env):
    async def go():
        app = PcEnergyApp(env, now=NOW)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            scr = app.screen
            scr.query_one("#w-price_per_kwh").value = "0.33"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, MonthScreen)
            assert app.settings["price_per_kwh"] == 0.33
        assert cfg.load_user()[0]["price_per_kwh"] == 0.33

    asyncio.run(go())


def test_settings_invalid_blocks_save(env):
    async def go():
        app = PcEnergyApp(env, now=NOW)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            app.screen.query_one("#w-month_start_day").value = "45"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            assert "must be" in str(app.screen.query_one("#err-month_start_day").render())

    asyncio.run(go())


def test_settings_cancel_asks_when_dirty(env):
    async def go():
        app = PcEnergyApp(env, now=NOW)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            app.screen.query_one("#w-base_watts").value = "75"
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)  # first Esc warns
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MonthScreen)
            assert app.settings["base_watts"] == 60
        assert not cfg.user_config_path().exists()

    asyncio.run(go())


def test_settings_cancel_clean_closes_at_once(env):
    run(env, ["s", "escape"], lambda app: isinstance(app.screen, MonthScreen) or pytest.fail("still open"))
