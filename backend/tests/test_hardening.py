# backend/tests/test_hardening.py
import json
import math
import pytest
import alphaloom.nodes  # noqa: F401
from alphaloom.api.serialize import sanitize
from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker
from alphaloom.graph.model import NodeSpec, loads_loom
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.types import Stamped
from alphaloom.nodes.registry import create_instance
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine, EngineDead
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.recorder import Recorder, from_json, to_json

def _bar(ts, px=100.0):
    return {"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1.0}

# ---- 15①: broker submit 防御 ----
def test_broker_rejects_nonpositive_qty():
    b = PaperBroker()
    assert b.submit(Order(side="buy", qty=0.0)) is False
    assert b.submit(Order(side="buy", qty=-1.0)) is False
    assert b._pending == []

# ---- 15②: halt 清空 pending ----
def test_halt_clears_pending():
    b = PaperBroker()
    b.on_bar(_bar(0))
    b.submit(Order(side="buy", qty=1.0))
    b.halt("test")
    b.on_bar(_bar(60_000))
    assert b.fills == [] and b.position().qty == 0.0

# ---- 15②: sanitize ----
def test_sanitize_inf_nan():
    d = {"pf": float("inf"), "x": [1.0, float("nan")], "n": {"y": float("-inf")}, "ok": 1.5}
    s = sanitize(d)
    assert s == {"pf": None, "x": [1.0, None], "n": {"y": None}, "ok": 1.5}
    json.dumps(s)  # 严格可序列化

# ---- 17①: risk_gate 恶意载荷 ----
@pytest.mark.parametrize("sig", [
    {"side": "long", "qty": float("nan"), "stop": 95.0, "reason": "x"},
    {"side": "long", "qty": 2.0, "stop": float("nan"), "reason": "x"},
    {"side": "long", "qty": -5.0, "stop": 95.0, "reason": "x"},
    {"qty": 1.0, "stop": 95.0, "reason": "no side key"},
])
def test_risk_gate_blocks_malformed(sig):
    gate = create_instance(NodeSpec("r", "risk_gate", {"max_qty": 10.0}))
    out = gate.on_bar(RunContext(clock=SimClock(), run_id="t"), {"signal": sig})
    assert out["blocked"] is True and out["stamped"]["side"] == "hold"

# ---- 17②: sizer 负 equity ----
def test_sizer_nonpositive_equity_holds():
    broker = PaperBroker(initial_cash=0.0)
    broker.on_bar(_bar(0))
    ctx = RunContext(clock=SimClock(), run_id="t"); ctx.broker = broker
    sizer = create_instance(NodeSpec("s", "position_sizer", {"risk_pct": 0.02}))
    out = sizer.on_bar(ctx, {"signal": {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "x"},
                             "candle": _bar(0)})
    assert out["sized"]["side"] == "hold"

# ---- 14①: 引擎毒化 ----
def test_engine_poisoned_after_crash():
    from tests.test_engine import _mk, _events  # 复用 te_* 图构造
    bp = {"id": "e", "name": "e", "nodes": [{"id": "bad", "type": "te_evil"}], "edges": []}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r"))
    with pytest.raises(Exception):
        eng.run(_events(1))
    assert eng._dead is True
    with pytest.raises(EngineDead):
        eng.step(_events(2)[1])

# ---- 14③: fetch 全序 ----
def test_recorder_fetch_execution_order(tmp_path):
    rec = Recorder(tmp_path / "r.sqlite")
    for nid in ["zeta", "alpha", "mid"]:   # 故意乱序字典序
        rec.record("r1", 0, 60_000, nid, {}, {})
    rows = rec.fetch("r1")
    assert [r["node_id"] for r in rows] == ["zeta", "alpha", "mid"]  # 按写入(rowid)序

# ---- 19: Stamped 解码 ----
def test_stamped_json_roundtrip():
    src = {"out": Stamped({"close": 1.5}, 60_000), "plain": 3.0}
    back = from_json(to_json(src))
    assert isinstance(back["out"], Stamped) and back["out"].as_of == 60_000
    assert back["out"].value == {"close": 1.5} and back["plain"] == 3.0

def test_stamped_decode_ambiguity_guard():
    txt = json.dumps({"x": {"__stamped__": 1, "value": 2, "extra": 3}})
    back = from_json(txt)
    assert back["x"] == {"__stamped__": 1, "value": 2, "extra": 3}  # 三键不还原

# ---- CLI 出口 sanitize（全赢 profit_factor=inf 场景）----
def test_cli_summary_sanitized(tmp_path, capsys):
    from alphaloom.cli import main
    from alphaloom.data.sqlite_source import SQLiteMarketData
    from tests.fixtures.synth import gen_candles
    from pathlib import Path
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    up = gen_candles(300, seed=11, trend=0.002)
    down = gen_candles(300, seed=12, trend=-0.002, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    repo = Path(__file__).resolve().parents[2]
    rc = main(["run", str(repo / "blueprints" / "breakout_scenario.loom"),
               "--db", str(tmp_path / "m.sqlite"), "--inst", "BTC-USDT-SWAP", "--bar", "1m"])
    out = json.loads(capsys.readouterr().out)   # 裸 Infinity 会让严格解析炸
    assert rc == 0
    pf = out["summary"]["profit_factor"]
    assert pf is None or isinstance(pf, (int, float))
    assert "Infinity" not in json.dumps(out)
