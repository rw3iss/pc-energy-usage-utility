"""``pcenergy serve``: a small read-only JSON API over HTTP (stdlib only).

Bound to 127.0.0.1:8787 by default. Every route returns the same dataclasses as the query layer.
Each request opens its own read-only database connection and reads the current settings, so a
settings change applies at once.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from pcenergy import config as cfg
from pcenergy import periods, query, storage, system

log = logging.getLogger("pcenergy.api")
PREFIX = "/api/v1"
DISPLAY_KEYS = [f.key for f in cfg.USER_SCHEMA]


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Api:
    """Route handlers, independent of HTTP so tests can call them directly."""

    def __init__(self, db_path: str, now=time.time):
        self.db_path = db_path
        self.now = now

    def _conn(self) -> sqlite3.Connection:
        try:
            return storage.connect_ro(self.db_path)
        except FileNotFoundError:
            raise ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE, "no_database", f"no database at {self.db_path}"
            ) from None

    @staticmethod
    def _settings() -> dict:
        return cfg.load_user()[0]

    @staticmethod
    def _one(params: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
        values = params.get(name)
        return values[0] if values else default

    def _devices(self, params, settings) -> str:
        devices = self._one(params, "devices", settings["default_devices"])
        if devices not in cfg.DEVICE_FILTERS:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "bad_devices", f"devices must be one of {cfg.DEVICE_FILTERS}"
            )
        return devices

    def _period(self, params, settings, tz) -> periods.Period:
        kind = self._one(params, "period", "month")
        if kind not in ("month", "week", "day"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad_period", "period must be month, week or day")
        raw = self._one(params, "date")
        try:
            d = date.fromisoformat(raw) if raw else datetime.fromtimestamp(self.now(), tz).date()
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad_date", "date must be YYYY-MM-DD") from None
        return periods.period_of(kind, d, settings, tz)

    def handle(self, path: str, params: dict[str, list[str]]) -> Any:
        if not path.startswith(PREFIX + "/"):
            raise ApiError(HTTPStatus.NOT_FOUND, "not_found", f"unknown route {path}")
        route = path[len(PREFIX) + 1 :].rstrip("/")
        settings = self._settings()
        tz = periods.local_tz()
        est = query.Estimate.from_settings(settings)
        if route == "config":
            return {k: settings[k] for k in DISPLAY_KEYS}
        if route not in ("status", "live", "summary", "series", "months"):
            raise ApiError(HTTPStatus.NOT_FOUND, "not_found", f"unknown route {path}")
        conn = self._conn()
        try:
            now = self.now()
            if route == "status":
                return query.status(conn, self.db_path, now=now, service=system.service_state())
            if route == "live":
                return query.live(conn, self._devices(params, settings), est, now=now)
            if route == "months":
                return query.months(conn, settings["month_start_day"], tz, now=now)
            p = self._period(params, settings, tz)
            devices = self._devices(params, settings)
            if route == "summary":
                return query.summary(conn, p, devices, est, now=now, tz=tz)
            bucket = self._one(params, "bucket", "hour" if p.kind == "day" else "day")
            if bucket not in ("hour", "day", "week"):
                raise ApiError(HTTPStatus.BAD_REQUEST, "bad_bucket", "bucket must be hour, day or week")
            return query.series(conn, p, bucket, devices, est, settings["week_start"], now=now, tz=tz)
        finally:
            conn.close()


def make_handler(api: Api, cors_origin: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pcenergy"

        def do_GET(self) -> None:
            url = urlparse(self.path)
            try:
                result = api.handle(url.path, parse_qs(url.query))
                body = query.to_dict(result) if not isinstance(result, dict) else result
                self._send(HTTPStatus.OK, body)
            except ApiError as exc:
                self._send(exc.status, {"error": exc.message, "code": exc.code})
            except Exception as exc:
                log.exception("request failed")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "code": "internal"})

        def do_OPTIONS(self) -> None:
            self._send(HTTPStatus.NO_CONTENT, None)

        def _send(self, status: HTTPStatus, body: Any) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s %s", self.address_string(), fmt % args)

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8787, cors_origin: str | None = None) -> int:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {host}: the API has no authentication", file=sys.stderr)
    server = ThreadingHTTPServer((host, port), make_handler(Api(db_path), cors_origin))
    print(f"pcenergy API on http://{host}:{port}{PREFIX}/  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
