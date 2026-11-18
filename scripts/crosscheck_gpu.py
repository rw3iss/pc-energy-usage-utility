#!/usr/bin/env python3
"""Cross-check the collector's GPU energy against nvidia-smi power readings.

Polls `nvidia-smi --query-gpu=power.draw` every second for N whole minutes (aligned to the collector's
minute boundaries), integrates the power, and compares it with the gpu.0 joules the collector wrote for
the same minutes. Run while the collector service is running.

    python3 scripts/crosscheck_gpu.py [--minutes 10] [--db /var/lib/pcenergy/energy.db]
"""

import argparse
import sqlite3
import subprocess
import time


def power_w() -> float:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits", "-i", "0"],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip().splitlines()[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--db", default="/var/lib/pcenergy/energy.db")
    args = ap.parse_args()

    t0 = (int(time.time()) // 60 + 1) * 60
    t1 = t0 + args.minutes * 60
    print(f"waiting {t0 - time.time():.0f} s for the next minute boundary, then polling {args.minutes} min")
    time.sleep(max(t0 - time.time(), 0))
    joules, samples = 0.0, 0
    prev_t, prev_w = time.time(), power_w()
    while prev_t < t1:
        time.sleep(max(1.0 - (time.time() - prev_t), 0))
        w = power_w()
        now = min(time.time(), t1)
        joules += (prev_w + w) / 2 * (now - prev_t)
        prev_t, prev_w = now, w
        samples += 1
    time.sleep(8)  # let the collector write the sample stamped t1
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    col = conn.execute(
        "SELECT COALESCE(SUM(joules), 0), COUNT(*) FROM energy WHERE source = 'gpu.0' AND ts > ? AND ts <= ?",
        (t0, t1),
    ).fetchone()
    diff = (col[0] - joules) / joules * 100 if joules else float("nan")
    print(f"window      {time.strftime('%Y-%m-%d %H:%M', time.localtime(t0))} + {args.minutes} min")
    print(f"nvidia-smi  {joules:10.0f} J  avg {joules / (t1 - t0):6.1f} W  ({samples} polls)")
    print(f"collector   {col[0]:10.0f} J  avg {col[0] / (t1 - t0):6.1f} W  ({col[1]} samples)")
    print(f"difference  {diff:+.2f} %")


if __name__ == "__main__":
    main()
