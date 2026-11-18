"""Command-line entry point: ``pcenergy [command] [options]``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from pcenergy import __version__, query, storage, system
from pcenergy import config as cfg
from pcenergy.render.text import Styler, color_enabled
from pcenergy.sources import REGISTRY, detect_sources
from pcenergy.units import fmt_age, fmt_bytes


def _common(suppress: bool = False) -> argparse.ArgumentParser:
    """Flags accepted before or after the command. Sub-commands use SUPPRESS defaults so they do
    not overwrite a value given before the command."""
    d = {"default": argparse.SUPPRESS} if suppress else {}
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "-d", "--devices", choices=cfg.DEVICE_FILTERS, help="device filter (default: setting)", **d
    )
    p.add_argument(
        "--db", metavar="PATH", help="database file (default: $PCENERGY_DB or the collector setting)", **d
    )
    p.add_argument("--json", action="store_true", help="print JSON instead of text", **d)
    p.add_argument("--no-color", action="store_true", help="no ANSI colors (also: NO_COLOR env)", **d)
    p.add_argument("--ascii", action="store_true", help="ASCII graphs instead of Unicode blocks", **d)
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        parents=[_common()],
        prog="pcenergy",
        description="Record and browse this PC's energy use. Without a command, opens the terminal UI.",
    )
    parser.add_argument("--version", action="version", version=f"pcenergy {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    common = [_common(suppress=True)]

    p = sub.add_parser("config", parents=common, help="read or change settings")
    p.add_argument("action", nargs="?", default="show", choices=("show", "get", "set", "edit", "path"))
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")

    p = sub.add_parser(
        "collect", parents=common, help="run the collector loop (as root; normally the system service)"
    )
    p.add_argument("--once", action="store_true", help="read every source twice 1 s apart, print, exit")
    p.add_argument("--interval", type=int, metavar="S", help="seconds between samples (10–300)")
    p.add_argument(
        "--source",
        action="append",
        choices=sorted(REGISTRY),
        help="source plugin (repeatable; default: the collector config). 'fake' needs no hardware",
    )
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("month", parents=common, help="month summary, daily graph and day table")
    p.add_argument("period", nargs="?", help="YYYY-MM, -1 (previous), +1; default: current")
    p = sub.add_parser("week", parents=common, help="week summary, day graph and table")
    p.add_argument("period", nargs="?", help="YYYY-MM-DD (a day in the week) or -N; default: current")
    p = sub.add_parser("day", parents=common, help="day summary, 24-hour graph and hour table")
    p.add_argument("period", nargs="?", help="YYYY-MM-DD or -N; default: today")
    p = sub.add_parser("now", parents=common, help="live watts per device and the month so far")
    p.add_argument("--watch", action="store_true", help="refresh every 5 s (Ctrl+C to stop)")

    p = sub.add_parser("export", parents=common, help="export energy per hour or day (CSV or JSON)")
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="first day (default: 30 days ago)")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="last day, inclusive (default: today)")
    p.add_argument("--bucket", choices=("hour", "day"), default="day")
    p.add_argument("--csv", action="store_true", help="CSV output (default)")

    p = sub.add_parser("demo-data", parents=common, help="fill a database with realistic fake history")
    p.add_argument("--days", type=int, default=60)

    sub.add_parser("status", parents=common, help="collector health, sources, last sample, recent events")
    sub.add_parser(
        "doctor", parents=common, help="check sources, permissions, service and database; print fixes"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return _cmd_tui(args)
    handler = globals()[f"_cmd_{args.command.replace('-', '_')}"]
    try:
        return handler(args) or 0
    except KeyboardInterrupt:
        return 130
    except cfg.ConfigError as exc:
        print(f"pcenergy: {exc}", file=sys.stderr)
        return 2


def _cmd_tui(args: argparse.Namespace) -> int:
    from pcenergy.tui.app import run

    color = False if (args.no_color or os.environ.get("NO_COLOR")) else None
    return run(cfg.db_path(args.db), args.devices, bool(args.ascii), color)


# ---------------------------------------------------------------- config


def _cmd_config(args: argparse.Namespace) -> int:
    path = cfg.user_config_path()
    fields = cfg.schema_map(cfg.USER_SCHEMA)
    values, warnings = cfg.load_user()
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.action == "path":
        print(path)
        return 0
    if args.action == "show":
        if args.json:
            print(json.dumps(values, indent=2))
            return 0
        section = None
        for f in cfg.USER_SCHEMA:
            if f.section != section:
                section = f.section
                print(f"[{section}]")
            print(f"  {f.key:<18} = {values[f.key]!s:<10}  # {f.help} ({f.describe()})")
        print(f"\nfile: {path}{'' if path.exists() else ' (not created yet; defaults shown)'}")
        return 0
    if args.action in ("get", "set"):
        if not args.key:
            raise cfg.ConfigError(f"config {args.action} needs a KEY")
        if args.key not in fields:
            raise cfg.ConfigError(f"unknown setting {args.key!r}; see `pcenergy config`")
    if args.action == "get":
        value = values[args.key]
        print(json.dumps(value) if args.json else value)
        return 0
    if args.action == "set":
        if args.value is None:
            raise cfg.ConfigError("config set needs a VALUE")
        values[args.key] = fields[args.key].parse(args.value)
        cfg.save(cfg.USER_SCHEMA, values, path)
        print(f"{args.key} = {values[args.key]}")
        return 0
    # edit
    if not path.exists():
        cfg.save(cfg.USER_SCHEMA, values, path)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    subprocess.call([*shlex.split(editor), str(path)])
    _, warnings = cfg.load(cfg.USER_SCHEMA, Path(path))
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 1 if warnings else 0


# ---------------------------------------------------------------- helpers


def _styler(args: argparse.Namespace) -> Styler:
    settings, _ = cfg.load_user()
    return Styler(color_enabled(bool(args.no_color), settings["color"]))


def _open_ro(args: argparse.Namespace):
    path = cfg.db_path(args.db)
    try:
        return path, storage.connect_ro(path)
    except FileNotFoundError:
        raise SystemExit(
            f"pcenergy: no database at {path}. Is the collector installed? Run `pcenergy doctor`."
        ) from None


# ---------------------------------------------------------------- collector


def _cmd_collect(args: argparse.Namespace) -> int:
    import logging
    import threading

    from pcenergy.collector import Collector, install_signal_handlers, read_once

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ccfg, warnings = cfg.load(cfg.COLLECTOR_SCHEMA, cfg.collector_config_path())
    for w in warnings:
        logging.warning(w)
    interval = cfg.schema_map(cfg.COLLECTOR_SCHEMA)["interval_s"].parse(args.interval or ccfg["interval_s"])
    srcs = detect_sources(args.source or ccfg["sources"])

    if args.once:
        st = Styler(color_enabled(bool(args.no_color)))
        if not srcs:
            print("no sources found")
            return 1
        rc = 0
        for src, reading, watts in read_once(srcs):
            head = f"{src.id:<14} {src.label[:34]:<34}"
            if isinstance(reading, Exception):
                print(f"{head} {st.paint('error', 'err')}: {reading}")
                rc = 1
                continue
            raw = (
                f"counter {reading.counter_j:,.1f} J"
                if reading.counter_j is not None
                else f"power {reading.power_w:.1f} W"
            )
            extra = "" if src.in_total else st.paint("  (detail, not in total)", "dim")
            print(f"{head} {raw:<26} {st.paint(f'{watts:7.1f} W', src.device)}{extra}")
        return rc

    path = cfg.db_path(args.db)
    conn = storage.connect_rw(path)
    stop = threading.Event()
    install_signal_handlers(stop)
    logging.info("database %s, interval %ds", path, interval)
    Collector(conn, srcs, interval, ccfg["keep_minutes_days"]).run(stop)
    return 0


# ---------------------------------------------------------------- status / doctor


def _cmd_status(args: argparse.Namespace) -> int:
    path, conn = _open_ro(args)
    s = query.status(conn, path, service=system.service_state())
    if args.json:
        print(json.dumps(query.to_dict(s), indent=2))
        return 0
    st = _styler(args)
    color = {"ok": "ok", "stale": "warn", "none": "err"}[s.health]
    print(
        f"collector  {st.paint('● ' + s.health, color)}  service {s.service}  "
        f"last sample {fmt_age(s.last_sample_age_s)}"
    )
    print(f"database   {s.db_path}  {fmt_bytes(s.db_size_bytes)}  schema v{s.schema_version}")
    if s.oldest_ts:
        from datetime import datetime

        print(f"history    since {datetime.fromtimestamp(s.oldest_ts):%Y-%m-%d %H:%M}")
    print("sources")
    for src in s.sources:
        note = "" if src.in_total else st.paint("  (detail)", "dim")
        print(f"  {st.paint(src.id, src.device):<24} {src.label}{note}")
    if s.events:
        from datetime import datetime

        print("recent events")
        for e in s.events:
            print(f"  {datetime.fromtimestamp(e.ts):%Y-%m-%d %H:%M:%S}  {e.kind:<8} {e.detail}")
    return 0 if s.health == "ok" else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    from pcenergy.doctor import run_checks

    st = _styler(args)
    problems = 0
    for check in run_checks(cfg.db_path(args.db)):
        mark = {"ok": st.paint("✔", "ok"), "warn": st.paint("!", "warn"), "fail": st.paint("✖", "err")}
        print(f"{mark[check.level]} {check.name}: {check.message}")
        if check.fix and check.level != "ok":
            print(f"    fix: {check.fix}")
        problems += check.level == "fail"
    return 1 if problems else 0


# ---------------------------------------------------------------- reports


class _Ctx:
    """Settings, estimate, time zone and output options for one report command."""

    def __init__(self, args: argparse.Namespace):
        import shutil

        from pcenergy import periods

        self.settings, warnings = cfg.load_user()
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        self.est = query.Estimate.from_settings(self.settings)
        self.tz = periods.local_tz()
        self.devices = args.devices or self.settings["default_devices"]
        self.ascii = bool(args.ascii) or self.settings["graph_style"] == "ascii"
        self.st = Styler(color_enabled(bool(args.no_color), self.settings["color"]))
        self.width = max(min(shutil.get_terminal_size((100, 24)).columns, 160), 60)
        self.path, self.conn = _open_ro(args)

    def period(self, kind: str, arg: str | None):
        from pcenergy import periods

        try:
            return periods.parse_period(kind, arg, self.settings, self.tz)
        except ValueError as exc:
            raise cfg.ConfigError(str(exc)) from None

    def print(self, lines) -> None:
        print(self.st.lines(lines))


def _report(args: argparse.Namespace, kind: str) -> int:
    from datetime import datetime

    from pcenergy.render import report

    c = _Ctx(args)
    p = c.period(kind, args.period)
    bucket = "hour" if kind == "day" else "day"
    summ = query.summary(c.conn, p, c.devices, c.est, tz=c.tz)
    ser = query.series(c.conn, p, bucket, c.devices, c.est, c.settings["week_start"], tz=c.tz)
    if args.json:
        print(json.dumps({"summary": query.to_dict(summ), "series": query.to_dict(ser)}, indent=2))
        return 0
    title = {"month": f"{p.start:%B %Y}", "week": f"Week {p.label}", "day": f"{p.start:%a %d %b %Y}"}[kind]
    rng = "" if kind == "day" else f"({p.range_text()})  "
    c.print([[(f" {title} ", "bold"), (f"{rng}devices: {c.devices}", "dim")]])
    c.print(report.summary_lines(summ, "Total" if not summ.period.in_progress else "So far", c.width))
    print()
    unit = c.settings["day_graph_unit"] if kind == "day" else "kWh"
    today = datetime.now(c.tz).date().isoformat()
    c.print(report.graph(ser, c.width, 10 if kind != "day" else 8, unit, c.ascii, today=today))
    print()
    first = {"month": "Day", "week": "Day", "day": "Hour"}[kind]
    c.print(report.bucket_table(ser, first, c.est.currency_symbol, c.est.price_per_kwh))
    return 0


def _cmd_month(args: argparse.Namespace) -> int:
    return _report(args, "month")


def _cmd_week(args: argparse.Namespace) -> int:
    return _report(args, "week")


def _cmd_day(args: argparse.Namespace) -> int:
    return _report(args, "day")


def _cmd_now(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime

    from pcenergy import periods
    from pcenergy.render.text import DEVICE_NAMES, DEVICE_ORDER
    from pcenergy.units import fmt_energy, fmt_watts

    c = _Ctx(args)
    while True:
        lv = query.live(c.conn, c.devices, c.est)
        month = periods.month_period(datetime.now(c.tz).date(), c.settings["month_start_day"], c.tz)
        summ = query.summary(c.conn, month, c.devices, c.est, tz=c.tz)
        if args.json:
            print(json.dumps({"live": query.to_dict(lv), "month": query.to_dict(summ)}, indent=2))
            return 0
        out = []
        health = {"ok": "ok", "stale": "warn", "none": "err"}[lv.health]
        out.append(
            c.st.paint(f"● {lv.health}", health) + c.st.paint(f"  sample {fmt_age(lv.sample_age_s)}", "dim")
        )
        out.append(c.st.paint(f"{'':<10} {'now':>8}   month {month.label} so far", "dim"))
        for d in DEVICE_ORDER:
            if d not in summ.per_device_kwh:
                continue
            est_mark = d in ("rest", "monitor")
            w = lv.watts.get(d)
            now_txt = ("~" if est_mark and w is not None else "") + fmt_watts(w)
            out.append(
                f"{c.st.paint(f'{DEVICE_NAMES[d]:<10}', d)} {now_txt:>8}   "
                f"{fmt_energy(summ.per_device_kwh[d], est_mark)}"
            )
        total_now = ("~" if lv.total_is_estimate and lv.total_w else "") + fmt_watts(lv.total_w)
        out.append(
            c.st.paint(
                f"{'Total':<10} {total_now:>8}   {fmt_energy(summ.total_kwh, summ.total_is_estimate)}"
                f"  {c.est.currency_symbol}{summ.cost:.2f}",
                "bold",
            )
        )
        if args.watch:
            print("\x1b[H\x1b[2J" + "\n".join(out), flush=True)
            time.sleep(5)
            continue
        print("\n".join(out))
        return 0


def _cmd_export(args: argparse.Namespace) -> int:
    import csv
    from datetime import date, datetime, timedelta

    from pcenergy import periods

    c = _Ctx(args)
    today = datetime.now(c.tz).date()
    try:
        d_to = date.fromisoformat(args.date_to) if args.date_to else today
        d_from = date.fromisoformat(args.date_from) if args.date_from else d_to - timedelta(days=29)
    except ValueError as exc:
        raise cfg.ConfigError(f"bad date: {exc}") from None
    if d_from > d_to:
        raise cfg.ConfigError("--from is after --to")
    span = periods.Period(
        "range",
        periods.day_period(d_from, c.tz).start,
        periods.day_period(d_to, c.tz).end,
        f"{d_from}..{d_to}",
    )
    ser = query.series(c.conn, span, args.bucket, c.devices, c.est, c.settings["week_start"], tz=c.tz)
    if args.json:
        print(json.dumps(query.to_dict(ser), indent=2))
        return 0
    devices = list(ser.buckets[0].per_device_kwh) if ser.buckets else []
    w = csv.writer(sys.stdout)
    w.writerow(["start", *[f"{d}_kwh" for d in devices], "total_kwh", "cost", "on_hours"])
    for b in ser.buckets:
        if b.future:
            continue
        w.writerow(
            [
                b.start,
                *[f"{b.per_device_kwh[d]:.4f}" for d in devices],
                f"{b.total_kwh:.4f}",
                f"{b.total_kwh * c.est.price_per_kwh:.4f}",
                f"{b.on_s / 3600:.3f}",
            ]
        )
    return 0


def _cmd_demo_data(args: argparse.Namespace) -> int:
    from pcenergy import demo

    path = cfg.db_path(args.db)
    if path == cfg.DEFAULT_DB_PATH:
        raise cfg.ConfigError(
            "refusing to write demo data to the real database; pass --db or set PCENERGY_DB"
        )
    conn = storage.connect_rw(path)
    n = demo.fill(conn, args.days)
    print(f"wrote {n} samples ({args.days} days) to {path}")
    return 0
