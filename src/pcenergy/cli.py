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
    print("pcenergy: the terminal UI is not built yet; see `pcenergy --help`", file=sys.stderr)
    return 1


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
