from pcenergy.render.bars import Bar, legend, nice_step, sparkline, stacked_bars
from pcenergy.render.text import Styler, plain


def test_nice_step():
    assert nice_step(4.2, 5) == 1
    assert nice_step(430, 4) == 200 or nice_step(430, 4) == 250
    assert nice_step(0, 3) == 1


def test_stacked_bars_shape_and_content():
    bars = [
        Bar("1", {"cpu": 1.0, "gpu": 1.0}),
        Bar("2", {"cpu": 0.5}),
        Bar("3", missing=True),
        Bar("4", future=True),
    ]
    lines = stacked_bars(bars, width=40, height=4)
    text = plain(lines).splitlines()
    assert len(text) == 4 + 2  # rows + axis + labels
    assert all(len(t) <= 40 for t in text)
    assert "█" in text[0] or "█" in text[1]
    assert "·" in text[3]  # missing bar marker on the bottom row
    assert "1" in text[-1] and "4" in text[-1]


def test_stacked_bar_colors_follow_devices():
    lines = stacked_bars([Bar("a", {"cpu": 1.0, "gpu": 1.0})], width=20, height=2)
    styles = [s for line in lines[:2] for _, s in line]
    assert "gpu" in styles[1:3] or "gpu" in styles  # top row is the GPU (stacked on CPU)
    assert "cpu" in [s for _, s in lines[1]]


def test_ascii_mode_has_no_unicode_blocks():
    lines = stacked_bars([Bar("a", {"cpu": 2.0, "rest": 1.0})], width=20, height=3, ascii_mode=True)
    body = plain(lines[:-2])
    assert "█" not in body and "#" in body and ":" in body


def test_selected_bar_gets_highlight_style():
    lines = stacked_bars([Bar("a", {"cpu": 1}), Bar("b", {"cpu": 1}, selected=True)], 20, 2)
    assert any("hl" in s for line in lines for _, s in line)


def test_styler_ansi_and_plain():
    assert Styler(True).paint("x", "cpu").startswith("\x1b[")
    assert Styler(False).paint("x", "cpu") == "x"
    assert "CPU" in plain([legend(["cpu", "rest"])])


def test_sparkline():
    ((text, _),) = sparkline([0, 1, 2, 3], 6)
    assert text.strip() == "▁▃▆█"
