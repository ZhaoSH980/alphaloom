# backend/tests/test_api_ws.py
import json
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
import alphaloom.nodes  # noqa: F401
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]

@pytest.fixture()
def client(tmp_path):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(60, seed=5, trend=0.001))
    app = create_app(db_path=tmp_path / "demo.sqlite", runs_db=tmp_path / "runs.sqlite",
                     record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "ubp", frontend_dist=tmp_path / "nd")
    return TestClient(app)

def _loom():
    return json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))

def _collect_until(ws, typ, limit=500):
    for _ in range(limit):
        ev = ws.receive_json()
        if ev["type"] == typ:
            return ev
    pytest.fail(f"no {typ} in {limit} events")

def test_ws_streams_bars_and_done(client):
    r = client.post("/api/runs", json={"blueprint": _loom(), "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0, "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        first = ws.receive_json()
        assert first["type"] in ("status", "bar")
        done = _collect_until(ws, "done")
        assert done["report"]["bars"] == 60
        assert "Infinity" not in json.dumps(done)

def test_ws_breakpoint_pause_resume(client):
    r = client.post("/api/runs", json={"blueprint": _loom(), "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0,
                                       "breakpoints": ["risk"], "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        paused = _collect_until(ws, "paused")
        assert paused["node_id"] == "risk" and "signal" in paused["inputs"]
        ws.send_json({"cmd": "step"})
        paused2 = _collect_until(ws, "paused")
        assert (paused2["node_id"], paused2["ts"]) != (paused["node_id"], paused["ts"])
        ws.send_json({"cmd": "stop"})
        end = _collect_until(ws, "done")
        assert end["report"]["bars"] < 60

def test_ws_stop_halts_plain_streaming_run(client):
    r = client.post("/api/runs", json={"blueprint": _loom(), "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 5,
                                       "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        first_bar = _collect_until(ws, "bar")
        assert first_bar["idx"] >= 0
        ws.send_json({"cmd": "stop"})
        end = _collect_until(ws, "done")
        assert end["report"]["bars"] < 60

def test_ws_unknown_run_closes(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/runs/nope") as ws:
            ws.receive_json()
