# Development

## Setup

```bash
uv sync --all-extras          # venv with textual, nvidia-ml-py, pytest, ruff
uv run pcenergy --help
```

Checks (all must pass before a commit):

```bash
uv run ruff check && uv run ruff format --check && uv run pytest
```

## Working without root or hardware

Every command takes `--db PATH` or the `PCENERGY_DB` environment variable, so development never touches the
real database. `demo-data` refuses to write to the default path.

```bash
export PCENERGY_DB=/tmp/pce.db
uv run pcenergy demo-data --days 60          # idle nights, work days, gaming evenings, AI jobs, a reboot, a suspend
uv run pcenergy                              # TUI
uv run pcenergy month --json | jq .summary
uv run pcenergy collect --source fake --interval 10   # live collector loop on fake sources
uv run pcenergy serve                        # API on the same database
```

Real sources without the service:

```bash
uv run pcenergy collect --once                                  # NVML works as a user; RAPL says "permission denied"
sudo .venv/bin/pcenergy collect --once --source rapl --source nvml --source hwmon
```

Settings for experiments: `PCENERGY_CONFIG=/tmp/c.toml`, `PCENERGY_COLLECTOR_CONFIG=/tmp/collector.toml`.

## Layout

| Module | Role |
|---|---|
| `sources/` | Source plugins behind one interface (`base.EnergySource`): `rapl`, `nvml`, `hwmon`, `fake`. Register a new one in `sources/__init__.py:REGISTRY` |
| `collector.py` | Sampling loop and the counter delta rules (wrap, reboot, driver reload, suspend, gap, plausibility) |
| `storage.py` | Schema, migrations (`MIGRATIONS`, append only), writes, retention. The only writer |
| `periods.py` | All calendar math: billing months, weeks, DST-aware days |
| `query.py` | All read logic; returns dataclasses that are also the JSON shape. No SQL outside storage and query |
| `render/` | Graphs (`bars.py`), styled text (`text.py`), report blocks (`report.py`), shared by CLI and TUI |
| `cli.py` | Command-line entry point |
| `tui/` | Textual app: one screen per view; most drawing is a `Canvas` of styled lines |
| `keys.py` | Every TUI key in one table (bindings, footer and help read it) |
| `api.py` | Read-only HTTP API (stdlib) |
| `config.py` | Settings schema (type, range, default, help) and TOML load/save |

## Tests

- `test_collector.py`: every delta rule with a fake clock and scripted sources.
- `test_periods.py`: month start day and clamping, leap years, week start, DST 23/25-hour days.
- `test_query.py`: estimate math, device filters, series, projection, live.
- `test_render.py`, `test_cli.py`, `test_api.py`: output, flags, routes and errors on a temp database.
- `test_tui.py`: Textual pilot tests for navigation, every main-screen key and the settings form.

## Cross-checks on real hardware

With the collector service running:

```bash
python3 scripts/crosscheck_gpu.py --minutes 10        # collector GPU joules vs integrated nvidia-smi power
sudo python3 scripts/crosscheck_cpu.py --minutes 12   # collector CPU joules vs turbostat, under a CPU load
```

Results are recorded in [ACCURACY.md](ACCURACY.md).

## Screenshots

```bash
uv run python scripts/screenshots.py      # SVGs of the TUI on demo data into docs/screenshots/
```
