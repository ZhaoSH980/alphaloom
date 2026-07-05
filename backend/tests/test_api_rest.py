# backend/tests/test_api_rest.py
import json
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
    up = gen_candles(200, seed=11, trend=0.002)
    down = gen_candles(200, seed=12, trend=-0.002, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    app = create_app(db_path=tmp_path / "demo.sqlite",
                     runs_db=tmp_path / "runs.sqlite",
                     record_dir=tmp_path,
                     blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "user_bp",
                     frontend_dist=tmp_path / "no_dist")
    return TestClient(app)

def test_nodes_endpoint_excludes_test_category(client):
    r = client.get("/api/nodes")
    assert r.status_code == 200
    types = {n["type"] for n in r.json()}
    assert "risk_gate" in types and "candle_feed" in types
    assert not any(t.startswith(("tc_", "te_", "t_", "tb_")) for t in types)
    rg = next(n for n in r.json() if n["type"] == "risk_gate")
    assert rg["outputs"]["stamped"] == "risk_stamped_signal" and rg["category"] == "risk"

def test_compile_endpoint_ok_and_errors(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/compile", json={"blueprint": loom, "bar": "1H"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["certificate"]["deterministic_ratio"] == 1.0
    bad = dict(loom, edges=[e for e in loom["edges"]
                            if e["to"] != "exec.signal"] + [{"from": "cross.signal",
                                                             "to": "exec.signal"}])
    r2 = client.post("/api/compile", json={"blueprint": bad})
    assert r2.json()["ok"] is False
    assert any(e["code"] == "TYPE_MISMATCH" for e in r2.json()["errors"])

def test_blueprints_list_get_save(client):
    lst = client.get("/api/blueprints").json()
    ids = {b["id"] for b in lst}
    assert {"ema_cross_v1", "breakout_scenario_v1"} <= ids
    one = client.get("/api/blueprints/ema_cross_v1")
    assert one.status_code == 200 and one.json()["id"] == "ema_cross_v1"
    custom = dict(one.json(), id="My Custom#1", name="c")
    r = client.post("/api/blueprints", json={"blueprint": custom})
    assert r.status_code == 200
    saved_id = r.json()["id"]
    assert saved_id == "mycustom1"          # server-slug 防注入
    assert client.get(f"/api/blueprints/{saved_id}").status_code == 200
    evil = dict(custom, id="../../etc/passwd")
    r2 = client.post("/api/blueprints", json={"blueprint": evil})
    assert r2.status_code in (200, 422)
    if r2.status_code == 200:
        assert "/" not in r2.json()["id"] and ".." not in r2.json()["id"]

def test_market_candles_window(client):
    r = client.get("/api/market/candles",
                   params={"inst": "BTC-USDT-SWAP", "bar": "1m", "limit": 50})
    rows = r.json()
    assert r.status_code == 200 and len(rows) == 50
    assert list(rows[0]) == ["ts", "open", "high", "low", "close", "volume"]

def test_run_lifecycle_and_trace(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"blueprint": loom, "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    import time
    for _ in range(100):
        row = client.get(f"/api/runs/{run_id}").json()
        if row["status"] != "running":
            break
        time.sleep(0.1)
    assert row["status"] == "completed"
    assert row["report"]["bars"] == 400
    assert "Infinity" not in json.dumps(row)
    lst = client.get("/api/runs").json()
    assert any(x["run_id"] == run_id for x in lst)
    tr = client.get(f"/api/runs/{run_id}/trace", params={"node_id": "risk", "limit": 5})
    assert tr.status_code == 200 and len(tr.json()) == 5
    assert tr.json()[0]["node_id"] == "risk" and "outputs" in tr.json()[0]

def test_run_compile_failure_422(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    bad = dict(loom, edges=[{"from": "cross.signal", "to": "exec.signal"}])
    r = client.post("/api/runs", json={"blueprint": bad, "inst": "BTC-USDT-SWAP", "bar": "1m"})
    assert r.status_code == 422
    assert any(e["code"] == "TYPE_MISMATCH" for e in r.json()["detail"]["errors"])

def test_unknown_run_404(client):
    assert client.get("/api/runs/nope").status_code == 404

def test_spa_fallback_no_path_traversal(tmp_path):
    # T3 审查 Critical-1 回归：编码穿越不得读出 dist 之外的文件
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path / "SECRET.txt").write_text("TOP-SECRET", encoding="utf-8")
    app = create_app(db_path=tmp_path / "d.sqlite", runs_db=tmp_path / "r.sqlite",
                     record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "ubp", frontend_dist=dist)
    c = TestClient(app)
    for evil in ["..%2FSECRET.txt", "%2e%2e%2fSECRET.txt", "..%2F..%2FSECRET.txt"]:
        r = c.get(f"/{evil}")
        assert "TOP-SECRET" not in r.text, evil
    assert "ok" in c.get("/anything/deep").text   # SPA fallback 正常路径不受影响

def test_run_window_bounds_rejected(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"blueprint": loom, "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "end_ms": 10**19})
    assert r.status_code == 422
