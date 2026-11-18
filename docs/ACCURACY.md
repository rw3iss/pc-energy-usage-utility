# Accuracy

## What is measured and what is estimated

| Part | Source | Kind |
|---|---|---|
| CPU package | RAPL `package-0` energy counter (powercap) | measured |
| CPU cores | RAPL `core` counter: a detail, part of the package, never added to the total | measured |
| NVIDIA GPU | NVML `nvmlDeviceGetTotalEnergyConsumption` (mJ since the driver loaded) | measured |
| AMD GPU / iGPU (optional) | amdgpu hwmon `power1_average`/`power1_input`, integrated between samples | measured, coarse |
| Rest of system | `base_watts` × seconds on (board, RAM, drives, fans, USB) | **estimated** |
| Wall total | (CPU + GPU + rest) ÷ `psu_efficiency`, plus `monitor_watts` × seconds on | **estimated** |

The database stores only measured joules and "seconds on". The estimate is applied when data is read, so
changing `base_watts`, `psu_efficiency` or `monitor_watts` changes all history, old months included.

RAPL and NVML are model-based energy counters reported by the CPU and GPU firmware. They are good to a few
percent for the chip, but they do not include losses in the power supply, the voltage regulators on the
board, or anything outside the chip. That is why the "rest" and the PSU efficiency are estimates.

## Counter handling

- **RAPL wraps.** An AMD package counter has a range of about 65.5 kJ (`max_energy_range_uj`): about 7 minutes
  at 150 W. With a 60 s interval at most one wrap happens per sample, and the delta is
  `max_range − previous + current`. Ordinary wraps are not logged.
- **NVML** is a 64-bit counter that restarts when the driver reloads; a counter that goes down means a reload,
  and the delta is the new value (`reload` event).
- **Reboot:** the counters restart at boot. The first value after a boot is used only if the collector started
  with the boot (uptime ≤ 2 intervals + 60 s) and the value is plausible; otherwise the sample is a new
  baseline (`boot` event, "late start").
- **Suspend:** "seconds on" use the monotonic clock, which stops during suspend, so sleep time is never billed
  as "on" (`suspend` event).
- **Gaps:** more than 2 intervals of awake time without a sample (service stopped or stalled) count as one
  interval; the energy of that sample is dropped because the counters cannot be trusted over a long gap
  (`gap` event). Days with no samples show `·` in the graphs.
- **Plausibility guard:** a delta above 2 × the device's maximum power × elapsed time is dropped (`warning`).

## Cross-checks (2026-11-18, Ryzen 9 7900X, RTX 4070 Ti, Fedora 44)

Scripts: `scripts/crosscheck_gpu.py` and `scripts/crosscheck_cpu.py` (see [DEVELOPMENT.md](DEVELOPMENT.md)).

### GPU: collector vs `nvidia-smi` power, 10 minutes

`nvidia-smi --query-gpu=power.draw` polled every second, integrated (trapezoid), compared with the
collector's `gpu.0` joules for the same 10 whole minutes.

| | Energy | Average |
|---|---|---|
| nvidia-smi (583 polls) | 16 285 J | 27.1 W |
| collector (10 samples) | 16 271 J | 27.1 W |
| **difference** | **−0.09 %** | |

The GPU was near idle during this run (no GPU load test was run while other GPU work was active).

### CPU: collector vs `turbostat`, 12 minutes under load

One busy process per CPU (nice 19) for 12 whole minutes; `turbostat --show PkgWatt` over the same window.

| | Energy | Average |
|---|---|---|
| turbostat | 93 283 J | 129.6 W |
| collector (12 samples) | 93 280 J | 129.6 W |
| **difference** | **−0.00 %** | |

The package counter wrapped about 1.4 times in the window; there were no warnings and no dropped or
oversized deltas. turbostat reads the same RAPL counter, so this check confirms the wrap handling and the
delta and roll-up math, not RAPL's own accuracy.

### Collector cost

Over the first 15 minutes: 0.15 s of CPU time (about 0.02 %, mostly start-up) and 35.6 MB of memory
(`systemctl show -p CPUUsageNSec -p MemoryCurrent pcenergy-collector`).

### Not tested on hardware

Suspend and reboot handling were tested with a fake clock and scripted sources (`tests/test_collector.py`),
not with a real suspend or reboot.

## Calibrating the estimate

The measured parts are good; the wall total is only as good as `base_watts` and `psu_efficiency`.
To calibrate them with a plug-in power meter (or a metering smart plug):

1. Plug the PC (without monitors) into the meter.
2. Run `pcenergy now --watch` and leave the PC idle for a few minutes. Note the meter's watts **W_wall**,
   and the CPU and GPU watts **W_cpu**, **W_gpu** from pcenergy.
3. Under a steady load (a game, a render), note the same three numbers again.
4. With two points, solve `W_wall = (W_cpu + W_gpu + base) / eff` for `base` and `eff`. With one point, keep
   `psu_efficiency` from the PSU's 80 PLUS rating (Gold ≈ 0.90 at 50 % load, lower at light load) and set
   `base_watts = W_wall × eff − W_cpu − W_gpu`.
5. `pcenergy config set base_watts …` and `pcenergy config set psu_efficiency …`.

Monitors draw power from the wall directly: measure them separately and set `monitor_watts`.

A metering smart plug as a true wall source (and automatic calibration) is planned; the source interface
already supports adding it as a plugin.
