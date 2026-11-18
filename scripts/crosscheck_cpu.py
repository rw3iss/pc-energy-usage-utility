#!/usr/bin/env python3
"""Cross-check the collector's CPU package energy against turbostat under a CPU load.

Runs a CPU load (one busy process per CPU, nice 19 so other work keeps priority) for N whole minutes,
measures package watts with turbostat over the same minutes, and compares with the cpu.package joules
the collector wrote. Long enough runs make the RAPL counter wrap; the script reports any warning
events in the window (a bad wrap would show as a dropped or huge delta). Needs root for turbostat.

    sudo python3 scripts/crosscheck_cpu.py [--minutes 12] [--db /var/lib/pcenergy/energy.db] [--no-load]
"""

import argparse
import multiprocessing as mp
import os
import re
import sqlite3
import subprocess
import time


def burn(stop_at: float) -> None:
    os.nice(19)
    x = 0
    while time.time() < stop_at:
        for _ in range(100_000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=12)
    ap.add_argument("--db", default="/var/lib/pcenergy/energy.db")
    ap.add_argument("--no-load", action="store_true")
    args = ap.parse_args()

    t0 = (int(time.time()) // 60 + 1) * 60
    t1 = t0 + args.minutes * 60
    print(f"waiting {t0 - time.time():.0f} s for the next minute boundary; window {args.minutes} min")
    time.sleep(max(t0 - time.time(), 0))
    procs = []
    if not args.no_load:
        procs = [mp.Process(target=burn, args=(t1,)) for _ in range(os.cpu_count() or 4)]
        for p in procs:
            p.start()
    ts = subprocess.run(
        [
            "turbostat",
            "--quiet",
            "--Summary",
            "--show",
            "PkgWatt",
            "--interval",
            str(t1 - t0),
            "--num_iterations",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    for p in procs:
        p.join()
    nums = [float(v) for v in re.findall(r"^\s*([\d.]+)\s*$", ts.stdout, re.M)]
    if not nums:
        raise SystemExit(f"could not read turbostat output:\n{ts.stdout}\n{ts.stderr}")
    turbo_j = nums[-1] * (t1 - t0)
    time.sleep(8)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    col_j, n, top = conn.execute(
        "SELECT COALESCE(SUM(joules), 0), COUNT(*), MAX(joules) FROM energy "
        "WHERE source = 'cpu.package' AND ts > ? AND ts <= ?",
        (t0, t1),
    ).fetchone()
    warnings = conn.execute(
        "SELECT ts, kind, detail FROM events WHERE ts > ? AND ts <= ? AND kind IN ('warning', 'gap')",
        (t0, t1),
    ).fetchall()
    max_range = 65532.610987
    print(f"window      {time.strftime('%Y-%m-%d %H:%M', time.localtime(t0))} + {args.minutes} min")
    print(f"turbostat   {turbo_j:10.0f} J  avg {nums[-1]:6.1f} W")
    print(f"collector   {col_j:10.0f} J  avg {col_j / (t1 - t0):6.1f} W  ({n} samples, max {top:.0f} J/min)")
    print(f"wraps       about {col_j / max_range:.1f} counter wraps in the window")
    print(f"difference  {(col_j - turbo_j) / turbo_j * 100:+.2f} %")
    print(f"warnings    {len(warnings)}" + "".join(f"\n  {w}" for w in warnings))


if __name__ == "__main__":
    main()
