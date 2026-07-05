import math
import pytest
from hypothesis import given, strategies as st
import alphaloom.nodes  # 触发内置节点注册
from alphaloom.graph.model import NodeSpec
from alphaloom.graph.types import Stamped
from alphaloom.nodes.registry import create_instance, get_node_def
from alphaloom.runtime.context import SimClock, RunContext
from alphaloom.runtime.events import BarEvent
from alphaloom.brokers.paper import PaperBroker
from alphaloom.brokers.base import Order
from tests.fixtures.synth import gen_candles

def _ctx(broker=None):
    ctx = RunContext(clock=SimClock(), run_id="t")
    ctx.broker = broker
    return ctx

def _feed_ev(ctx, candle, bar_ms=60_000):
    ev = BarEvent(candle, bar_ms)
    ctx.clock.advance(ev.ts_close)
    ctx.current_event = ev
    return ev

# ---- CandleFeed ----
def test_candle_feed_stamps_close_time():
    ctx = _ctx()
    feed = create_instance(NodeSpec("f", "candle_feed", {"inst": "X", "bar": "1m"}))
    c = {"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    _feed_ev(ctx, c)
    out = feed.on_bar(ctx, {})
    assert isinstance(out["out"], Stamped)
    assert out["out"].as_of == 60_000 and out["out"].value == c

# ---- EMA 增量 == 批量（hypothesis 性质测试）----
def _batch_ema(closes, period):
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

@given(st.lists(st.floats(min_value=1, max_value=1000, allow_nan=False), min_size=2, max_size=200),
       st.integers(min_value=2, max_value=50))
def test_ema_incremental_matches_batch(closes, period):
    ctx = _ctx()
    ema = create_instance(NodeSpec("e", "ema", {"period": period}))
    last = None
    for i, c in enumerate(closes):
        candle = {"ts": i * 60_000, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        last = ema.on_bar(ctx, {"candle": candle})["value"]
    assert last == pytest.approx(_batch_ema(closes, period), rel=1e-9)

# ---- ATR 基本性质 ----
def test_atr_positive_and_warms_up():
    ctx = _ctx()
    atr = create_instance(NodeSpec("a", "atr", {"period": 3}))
    vals = []
    for c in gen_candles(20, seed=3):
        vals.append(atr.on_bar(ctx, {"candle": c})["value"])
    assert all(v is None for v in vals[:3]) and all(v > 0 for v in vals[3:])

# ---- CrossSignal ----
def test_cross_signal_long_and_short():
    ctx = _ctx()
    cross = create_instance(NodeSpec("c", "cross_signal", {"atr_mult": 2.0}))
    candle = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
    s1 = cross.on_bar(ctx, {"fast": 1.0, "slow": 2.0, "candle": candle, "atr": 1.0})
    assert s1["signal"]["side"] == "hold"          # 首 bar 只记状态
    s2 = cross.on_bar(ctx, {"fast": 3.0, "slow": 2.5, "candle": candle, "atr": 1.0})
    sig = s2["signal"]
    assert sig["side"] == "long" and sig["stop"] == pytest.approx(100 - 2.0 * 1.0)
    s3 = cross.on_bar(ctx, {"fast": 1.0, "slow": 2.0, "candle": candle, "atr": 1.0})
    assert s3["signal"]["side"] == "short"
    assert s3["signal"]["stop"] == pytest.approx(100 + 2.0 * 1.0)

# ---- ScenarioGate 突破状态机 ----
def test_scenario_gate_breakout_and_cooldown():
    ctx = _ctx()
    g = create_instance(NodeSpec("g", "scenario_gate",
                                 {"lookback": 3, "cooldown": 2, "atr_mult": 1.0}))
    def bar(i, hi, lo, close):
        return {"ts": i * 60_000, "open": close, "high": hi, "low": lo,
                "close": close, "volume": 1}
    sides = []
    seq = [bar(0, 10, 9, 9.5), bar(1, 10, 9, 9.6), bar(2, 10, 9, 9.4),
           bar(3, 12, 10, 11.5),   # close 11.5 > max(前3根 high)=10 → long
           bar(4, 13, 11, 12.5),   # cooldown
           bar(5, 14, 12, 13.5),   # cooldown
           bar(6, 15, 13, 14.5)]   # 可再触发
    for c in seq:
        sides.append(g.on_bar(ctx, {"candle": c, "atr": 0.5})["signal"]["side"])
    assert sides[3] == "long" and sides[4] == "hold" and sides[5] == "hold"
    assert sides[6] == "long"

# ---- PositionSizer ----
def test_position_sizer_risk_math():
    broker = PaperBroker(initial_cash=10_000.0)
    broker.on_bar({"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    ctx = _ctx(broker)
    sizer = create_instance(NodeSpec("s", "position_sizer", {"risk_pct": 0.02}))
    sig = {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "t"}
    candle = {"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1}
    out = sizer.on_bar(ctx, {"signal": sig, "candle": candle})["sized"]
    assert out["qty"] == pytest.approx(10_000 * 0.02 / 5.0)   # 风险额/止损距离
    hold = sizer.on_bar(ctx, {"signal": {"side": "hold", "qty": 0, "stop": None,
                                         "reason": ""}, "candle": candle})["sized"]
    assert hold["side"] == "hold" and hold["qty"] == 0

# ---- RiskGate ----
def test_risk_gate_stamps_and_blocks():
    ctx = _ctx()
    gate = create_instance(NodeSpec("r", "risk_gate", {"max_qty": 5.0, "require_stop": True}))
    ok = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 2.0, "stop": 95.0, "reason": "x"}})
    assert ok["stamped"]["risk"]["checked"] is True and ok["blocked"] is False
    no_stop = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 2.0, "stop": None, "reason": "x"}})
    assert no_stop["blocked"] is True and no_stop["stamped"]["side"] == "hold"
    assert any("stop" in c for c in no_stop["stamped"]["risk"]["checks"])
    too_big = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 99.0, "stop": 95.0, "reason": "x"}})
    assert too_big["blocked"] is True

def test_risk_gate_is_sole_stamper():
    d = get_node_def("risk_gate")
    from alphaloom.graph.types import PinType
    from alphaloom.nodes.registry import REGISTRY
    stampers = [t for t, dd in REGISTRY.items()
                if PinType.RISK_STAMPED_SIGNAL in dd.outputs.values()
                and dd.category != "test"]
    assert stampers == ["risk_gate"]

# ---- ExecuteOrder ----
def test_execute_order_delta_and_reversal():
    broker = PaperBroker(initial_cash=10_000.0, fee_rate=0.0)
    broker.on_bar({"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    ctx = _ctx(broker)
    ex = create_instance(NodeSpec("x", "execute_order", {}))
    stamped = {"side": "long", "qty": 2.0, "stop": 95.0, "reason": "t",
               "risk": {"checked": True, "blocked": False, "checks": []}}
    assert ex.on_bar(ctx, {"signal": stamped})["submitted"] is True
    broker.on_bar({"ts": 60_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    assert broker.position().qty == 2.0
    rev = dict(stamped, side="short", qty=1.0, stop=105.0)   # 反手信号带正确方向的止损
    ex.on_bar(ctx, {"signal": rev})
    broker.on_bar({"ts": 120_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    assert broker.position().qty == -1.0          # 2.0 多 → 1.0 空，一次性下 3.0 卖单
    hold = dict(stamped, side="hold")
    assert ex.on_bar(ctx, {"signal": hold})["submitted"] is False

# ---- KillSwitch ----
def test_kill_switch_halts_broker():
    broker = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    ctx = _ctx(broker)
    ks = create_instance(NodeSpec("k", "kill_switch", {"max_drawdown_pct": 0.10}))
    bars = [
        {"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"ts": 60_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"ts": 120_000, "open": 100, "high": 100, "low": 70, "close": 70, "volume": 1},
    ]
    broker.on_bar(bars[0])
    out = ks.on_bar(ctx, {"candle": bars[0]})        # 建立 peak=1000
    assert out["halted"] is False
    broker.submit(Order(side="buy", qty=5.0))
    broker.on_bar(bars[1])                           # 成交 5 @100，equity 仍 1000
    assert ks.on_bar(ctx, {"candle": bars[1]})["halted"] is False
    broker.on_bar(bars[2])                           # close 70 → equity 850，回撤 15%
    out = ks.on_bar(ctx, {"candle": bars[2]})
    assert out["halted"] is True and broker.halted is True
    assert "drawdown" in broker.summary()["halt_reason"]
