#!/usr/bin/env python3
"""Save SVG screenshots of the TUI on demo data into docs/screenshots/ (no hardware, no real data).

uv run python scripts/screenshots.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
NOW = datetime(2026, 11, 18, 21, 30).timestamp()

SHOTS = [
    ("main", (120, 38), []),
    ("main-all", (120, 38), ["home"]),
    ("main-80x24", (80, 24), []),
    ("day", (120, 38), ["left", "enter"]),
    ("week", (120, 38), ["w"]),
    ("live", (100, 24), ["n"]),
    ("settings", (120, 40), ["s"]),
    ("help", (120, 40), ["question_mark"]),
]


async def shoot(db: str, name: str, size, keys) -> None:
    from pcenergy.tui.app import PcEnergyApp

    app = PcEnergyApp(db, now=NOW)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for k in keys:
            await pilot.press(k)
            await pilot.pause()
        await pilot.pause(0.3)
        app.save_screenshot(filename=f"{name}.svg", path=str(OUT))
    print(f"docs/screenshots/{name}.svg")


def main() -> None:
    tmp = tempfile.mkdtemp()
    os.environ["PCENERGY_CONFIG"] = str(Path(tmp) / "config.toml")
    os.environ["PCENERGY_COLLECTOR_CONFIG"] = str(Path(tmp) / "collector.toml")
    os.environ.setdefault("TZ", "America/New_York")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from pcenergy import storage
    from pcenergy.demo import fill

    db = str(Path(tmp) / "demo.db")
    fill(storage.connect_rw(db), days=75, now=NOW)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, keys in SHOTS:
        asyncio.run(shoot(db, name, size, keys))


if __name__ == "__main__":
    main()
