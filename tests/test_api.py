import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import pytest

from pcenergy import storage
from pcenergy.api import Api, make_handler
from pcenergy.demo import fill

NOW = datetime(2026, 11, 18, 21, 30).timestamp()


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = str(tmp_path / "demo.db")
    fill(storage.connect_rw(path), days=40, now=NOW)
    monkeypatch.setenv("PCENERGY_CONFIG", str(tmp_path / "c.toml"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Api(path, now=lambda: NOW), "http://x.test"))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(base, path):
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def test_status(server):
    code, headers, body = get(server, "/api/v1/status")
    assert code == 200
    assert body["health"] == "ok"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Access-Control-Allow-Origin"] == "http://x.test"


def test_live(server):
    code, _, body = get(server, "/api/v1/live?devices=all")
    assert code == 200 and body["total_w"] > 0 and "cpu" in body["watts"]


def test_summary_and_series(server):
    code, _, s = get(server, "/api/v1/summary?period=month&date=2026-11-10&devices=gpu")
    assert code == 200 and s["period"]["label"] == "2026-11" and s["devices"] == "gpu"
    code, _, ser = get(server, "/api/v1/series?period=day&date=2026-11-10&bucket=hour")
    assert code == 200 and len(ser["buckets"]) == 24
    code, _, ser = get(server, "/api/v1/series?period=month")
    assert ser["bucket"] == "day" and len(ser["buckets"]) == 30


def test_months_and_config(server):
    code, _, months = get(server, "/api/v1/months")
    assert code == 200 and months[-1]["label"] == "2026-11"
    code, _, conf = get(server, "/api/v1/config")
    assert code == 200 and conf["month_start_day"] == 1


@pytest.mark.parametrize(
    ("path", "code", "err"),
    [
        ("/api/v1/nope", 404, "not_found"),
        ("/other", 404, "not_found"),
        ("/api/v1/summary?devices=bogus", 400, "bad_devices"),
        ("/api/v1/summary?period=year", 400, "bad_period"),
        ("/api/v1/summary?date=11/10/2026", 400, "bad_date"),
        ("/api/v1/series?bucket=minute", 400, "bad_bucket"),
    ],
)
def test_errors(server, path, code, err):
    status, _, body = get(server, path)
    assert status == code and body["code"] == err and body["error"]


def test_missing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PCENERGY_CONFIG", str(tmp_path / "c.toml"))
    from pcenergy.api import ApiError

    with pytest.raises(ApiError) as e:
        Api(str(tmp_path / "none.db")).handle("/api/v1/status", {})
    assert e.value.code == "no_database"
