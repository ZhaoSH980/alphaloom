import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from alphaloom.api.app import create_app
from alphaloom.api.live import okx_candle_fetcher
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.llm.recording import RecordingLLMClient
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]


class FakeLiveFetcher:
    def __init__(self, candles, batch_size=None, delay_after_first=0.0):
        self.candles = list(candles)
        self.batch_size = batch_size
        self.delay_after_first = delay_after_first
        self.calls = 0

    def __call__(self, _inst, _bar, since_ts, _limit):
        import time

        self.calls += 1
        if self.calls > 1 and self.delay_after_first:
            time.sleep(self.delay_after_first)
        rows = [c for c in self.candles if since_ts is None or c["ts"] > since_ts]
        return rows if self.batch_size is None else rows[:self.batch_size]


class FlakyLiveFetcher:
    def __init__(self, candles):
        self.candles = list(candles)
        self.calls = 0

    def __call__(self, _inst, _bar, since_ts, _limit):
        self.calls += 1
        if self.calls == 2:
            raise TimeoutError("temporary OKX timeout")
        rows = [c for c in self.candles if since_ts is None or c["ts"] > since_ts]
        return rows[:1]


class FakeLLMTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return {"choices": [{"message": {"content": json.dumps({
            "market_state": "trend up",
            "current_gate": "RiskGate observed",
            "risk_reason": "risk output was inspected",
            "suggestion": "keep the blueprint unchanged",
            "confidence": 0.72,
        })}}]}


def _client(tmp_path, *, candles, batch_size=None, delay_after_first=0.0, fetcher=None):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    db.close()
    transport = FakeLLMTransport()
    llm = RecordingLLMClient(
        transport,
        tmp_path / f"llm_{uuid.uuid4().hex}.sqlite",
        model="test-live-model",
        offline=False,
    )
    app = create_app(
        db_path=tmp_path / "demo.sqlite",
        runs_db=tmp_path / "runs.sqlite",
        record_dir=tmp_path,
        blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "ubp",
        frontend_dist=tmp_path / "nd",
        llm_client=llm,
        live_fetcher=fetcher or FakeLiveFetcher(candles, batch_size=batch_size,
                                                delay_after_first=delay_after_first),
    )
    return TestClient(app), transport


def _loom():
    return json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))


def _collect(ws, typ, limit=100):
    for _ in range(limit):
        ev = ws.receive_json()
        if ev["type"] == typ:
            return ev
    raise AssertionError(f"no {typ} event")


def _collect_status(ws, status, limit=100):
    for _ in range(limit):
        ev = ws.receive_json()
        if ev["type"] == "status" and ev.get("status") == status:
            return ev
        if ev["type"] == "error":
            raise AssertionError(f"unexpected live error: {ev}")
    raise AssertionError(f"no status={status} event")


def test_live_session_streams_incremental_bars_and_records_llm_sidecar(tmp_path):
    candles = gen_candles(2, seed=99, trend=0.001, start_price=100)
    client, transport = _client(tmp_path, candles=candles)

    r = client.post("/api/live", json={
        "blueprint": _loom(),
        "inst": "BTC-USDT-SWAP",
        "bar": "1m",
        "poll_ms": 250,
        "max_bars": 2,
        "analysis": True,
        "ws_wait_ms": 300,
    })
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]

    with client.websocket_connect(f"/ws/live/{session_id}") as ws:
        first_bar = _collect(ws, "bar")
        analysis = _collect(ws, "analysis")
        done = _collect(ws, "done")

    assert first_bar["mode"] == "live"
    assert first_bar["idx"] == 0
    assert first_bar["candle"]["ts"] == candles[0]["ts"]
    assert analysis["prompt_hash"]
    assert analysis["model"] == "test-live-model"
    assert analysis["output"]["market_state"] == "trend up"
    assert done["report"]["bars"] == 2
    assert done["report"]["mode"] == "live"
    assert transport.calls

    rows = client.get(f"/api/live/{session_id}/analysis").json()
    assert len(rows) == 2
    assert rows[0]["prompt_hash"] == analysis["prompt_hash"]
    assert rows[0]["input_summary"]["blueprint"]["id"] == "ema_cross_v1"
    assert "risk_outputs" in rows[0]["input_summary"]

    stored = client.get(
        "/api/market/candles",
        params={"inst": "BTC-USDT-SWAP", "bar": "1m", "limit": 10},
    ).json()
    assert [row["ts"] for row in stored] == [c["ts"] for c in candles]


def test_live_session_can_stop_from_websocket(tmp_path):
    candles = gen_candles(5, seed=100, trend=0.001, start_price=100)
    client, _transport = _client(tmp_path, candles=candles, batch_size=1,
                                 delay_after_first=0.2)
    r = client.post("/api/live", json={
        "blueprint": _loom(),
        "inst": "BTC-USDT-SWAP",
        "bar": "1m",
        "poll_ms": 250,
        "analysis": False,
        "ws_wait_ms": 300,
    })
    session_id = r.json()["session_id"]

    with client.websocket_connect(f"/ws/live/{session_id}") as ws:
        _collect(ws, "bar")
        ws.send_json({"cmd": "stop"})
        done = _collect(ws, "done")

    assert done["report"]["bars"] < 5


def test_live_session_retries_transient_fetch_errors(tmp_path):
    candles = gen_candles(2, seed=101, trend=0.001, start_price=100)
    fetcher = FlakyLiveFetcher(candles)
    client, _transport = _client(tmp_path, candles=candles, fetcher=fetcher)
    r = client.post("/api/live", json={
        "blueprint": _loom(),
        "inst": "BTC-USDT-SWAP",
        "bar": "1m",
        "poll_ms": 250,
        "max_bars": 2,
        "analysis": False,
        "ws_wait_ms": 0,
    })
    session_id = r.json()["session_id"]

    with client.websocket_connect(f"/ws/live/{session_id}") as ws:
        _collect(ws, "bar")
        retrying = _collect_status(ws, "retrying")
        done = _collect(ws, "done")

    assert retrying["attempt"] == 1
    assert "temporary OKX timeout" in retrying["message"]
    assert done["report"]["bars"] == 2
    assert fetcher.calls >= 3


def test_live_session_can_stop_from_rest_endpoint(tmp_path):
    candles = gen_candles(5, seed=102, trend=0.001, start_price=100)
    client, _transport = _client(tmp_path, candles=candles, batch_size=1,
                                 delay_after_first=0.2)
    r = client.post("/api/live", json={
        "blueprint": _loom(),
        "inst": "BTC-USDT-SWAP",
        "bar": "1m",
        "poll_ms": 250,
        "analysis": False,
        "ws_wait_ms": 300,
    })
    session_id = r.json()["session_id"]

    with client.websocket_connect(f"/ws/live/{session_id}") as ws:
        _collect(ws, "bar")
        stop = client.post(f"/api/live/{session_id}/stop")
        done = _collect(ws, "done")

    assert stop.status_code == 200
    assert stop.json()["status"] == "stopping"
    assert done["report"]["bars"] < 5


def test_okx_fetcher_sends_json_headers(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps({
                "data": [["1000", "1", "2", "0.5", "1.5", "42"]]
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("alphaloom.api.live.urlopen", fake_urlopen)

    rows = okx_candle_fetcher("SOL-USDT-SWAP", "1m", since_ts=None, limit=1)

    assert rows == [{"ts": 1000, "open": 1.0, "high": 2.0, "low": 0.5,
                     "close": 1.5, "volume": 42.0}]
    assert captured["request"].get_header("User-agent").startswith("AlphaLoom/")
    assert captured["request"].get_header("Accept") == "application/json"
    assert captured["timeout"] == 10
