# backend/tests/test_runs_store.py
import json
import queue
import threading
import time
import pytest
import alphaloom.nodes  # noqa: F401
from pathlib import Path
from alphaloom.api.runs_store import RunsStore
from alphaloom.api.service import RunService, BreakBridge
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import load_loom_file
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]

def _db(tmp_path, n=120):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(n, seed=5, trend=0.001))
    return tmp_path / "m.sqlite"

def _params(tmp_path, **kw):
    p = {"inst": "BTC-USDT-SWAP", "bar": "1m", "cash": 10_000.0,
         "fee_rate": 0.0005, "breakpoints": [], "playback_ms": 0}
    p.update(kw)
    return p

def test_store_crud(tmp_path):
    st = RunsStore(tmp_path / "runs.sqlite")
    st.create("r1", "bp", "{}", "{}", 123)
    st.set_status("r1", "completed", report_json='{"ok":1}', recording_path="x.sqlite")
    row = st.get("r1")
    assert row["status"] == "completed" and json.loads(row["report_json"]) == {"ok": 1}
    assert [r["run_id"] for r in st.list()] == ["r1"]
    assert st.get("nope") is None

def test_service_completes_run(tmp_path):
    db_path = _db(tmp_path)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    events = queue.Queue()
    run_id = svc.start(bp, _params(tmp_path), sink=events.put)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "completed"
    report = json.loads(row["report_json"])
    assert report["bars"] == 120 and "summary" in report
    types = set()
    while not events.empty():
        types.add(events.get()["type"])
    assert {"status", "bar", "done"} <= types

def test_service_marks_failed_on_crash(tmp_path):
    db_path = _db(tmp_path)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    import tests.test_engine  # noqa: F401  注册 te_evil
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    bp.nodes.append(type(bp.nodes[0])("bad", "te_evil", {}))
    run_id = svc.start(bp, _params(tmp_path), sink=lambda e: None)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "failed" and "future" in row["error"]

def test_break_bridge_pause_resume(tmp_path):
    db_path = _db(tmp_path, n=30)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    events = queue.Queue()
    run_id = svc.start(bp, _params(tmp_path, breakpoints=["risk"]), sink=events.put)
    ev = _wait_for(events, "paused", 15)
    assert ev["node_id"] == "risk" and "signal" in ev["inputs"]
    svc.command(run_id, "step")            # 放行一次，下一节点即停
    ev2 = _wait_for(events, "paused", 15)
    assert ev2["node_id"] != "risk" or ev2["ts"] != ev["ts"]
    svc.command(run_id, "resume")          # 之后每次 risk 命中仍会停 → 连续 resume 清完
    deadline = time.time() + 30
    while time.time() < deadline:
        if svc.store.get(run_id)["status"] == "completed":
            break
        svc.command(run_id, "resume")
        time.sleep(0.05)
    assert svc.store.get(run_id)["status"] == "completed"

def test_raising_sink_does_not_kill_run(tmp_path):
    db_path = _db(tmp_path, n=40)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    def evil_sink(event):
        raise RuntimeError("ws is gone")
    run_id = svc.start(bp, _params(tmp_path), sink=evil_sink)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "completed"          # 推送失败绝不杀回测
    assert run_id not in svc._bridges            # 无 bridge 泄漏

def _wait_for(q, typ, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ev = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if ev["type"] == typ:
            return ev
    pytest.fail(f"no {typ} event within {timeout}s")
