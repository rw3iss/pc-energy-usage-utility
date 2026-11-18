"""Command-line entry point: ``pcenergy [command] [options]``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from pcenergy import __version__
from pcenergy import config as cfg


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-d", "--devices", choices=cfg.DEVICE_FILTERS, help="device filter (default: setting)")
    p.add_argument(
        "--db", metavar="PATH", help="database file (default: $PCENERGY_DB or the collector setting)"
    )
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--no-color", action="store_true", help="no ANSI colors (also: NO_COLOR env)")
    p.add_argument("--ascii", action="store_true", help="ASCII graphs instead of Unicode blocks")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcenergy",
        description="Record and browse this PC's energy use. Without a command, opens the terminal UI.",
    )
    parser.add_argument("--version", action="version", version=f"pcenergy {__version__}")
    _common(parser)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("config", help="read or change settings")
    p.add_argument("action", nargs="?", default="show", choices=("show", "get", "set", "edit", "path"))
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")

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
