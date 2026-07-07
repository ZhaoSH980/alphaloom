"""反思闭环——Reflector + 经验库测试（AlphaLoom D3 Task 5）。

覆盖：
- 市场状态桶派生纯函数（ema 斜率 + atr → trend_up/trend_down/range，确定性）
- experience_store 按桶写入 + 按桶检索隔离（不同桶互不串味）
- ExperienceWrite 幂等（同一 (bucket, trade_key) 写两次不产生重复行）
- Reflector 四象限过程/结局分离打分——**好过程坏结局 → reasonable_but_wrong**
  （招牌卖点：被正确区分，不惩罚运气，与 Hindsight 分类学一致）
- 平仓 pnl 数据流：broker 暴露 closed_trades，Reflector 读 ctx.broker 最近平仓
- 记忆开/关（有无 ExperienceRetrieve）注入决策上下文的 signal 差异可测
- 画布连通自证：含 reflector 的图能编译，含 experience_retrieve→分析的图能编译
- 成本注解：Reflector/ExperienceRetrieve/ExperienceWrite 全 cost 0，不触发 llm 审计红线
"""
import json

import pytest
import alphaloom.nodes  # 触发全部内置节点注册
from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import NodeSpec, loads_loom
from alphaloom.graph.types import PinType
from alphaloom.memory.experience_store import (
    ExperienceStore,
    derive_regime_bucket,
)
from alphaloom.nodes.registry import create_instance, get_node_def
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.events import BarEvent


def _ctx(broker=None):
    return RunContext(clock=SimClock(), run_id="t", broker=broker)


_CANDLE = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}


# --------------------------------------------------------------------------- #
# 市场状态桶派生纯函数（ema 斜率 + atr → trend_up/trend_down/range）
# --------------------------------------------------------------------------- #

def test_regime_bucket_trend_up_when_ema_rising():
    """ema 明显上升（斜率 > 阈值）且波动足够 → trend_up。"""
    assert derive_regime_bucket(ema=105.0, ema_prev=100.0, atr=2.0) == "trend_up"


def test_regime_bucket_trend_down_when_ema_falling():
    """ema 明显下降 → trend_down。"""
    assert derive_regime_bucket(ema=95.0, ema_prev=100.0, atr=2.0) == "trend_down"


def test_regime_bucket_range_when_ema_flat():
    """ema 基本走平（斜率 < 阈值）→ range（震荡市），不论 atr。"""
    assert derive_regime_bucket(ema=100.05, ema_prev=100.0, atr=2.0) == "range"


def test_regime_bucket_range_when_data_missing():
    """ema/ema_prev/atr 缺失（未 warmup）→ 保守归 range（不冒进猜趋势）。"""
    assert derive_regime_bucket(ema=None, ema_prev=None, atr=None) == "range"
    assert derive_regime_bucket(ema=100.0, ema_prev=None, atr=1.0) == "range"


def test_regime_bucket_deterministic_pure_function():
    """纯函数确定性：同输入多次调用输出恒等（无随机、无状态、无 IO）。"""
    args = dict(ema=110.0, ema_prev=100.0, atr=3.0)
    outs = {derive_regime_bucket(**args) for _ in range(5)}
    assert outs == {"trend_up"}


# --------------------------------------------------------------------------- #
# 经验库：按桶写入 + 按桶检索隔离
# --------------------------------------------------------------------------- #

def test_experience_store_write_then_retrieve_by_bucket(tmp_path):
    store = ExperienceStore(tmp_path / "exp.sqlite")
    store.write(bucket="trend_up", trade_key="t1",
                config_summary="ema+atr long", outcome="reasonable_and_right",
                pnl=12.0, lesson="trend-following worked in trend_up")
    hits = store.retrieve(bucket="trend_up", top_k=5)
    assert len(hits) == 1
    assert hits[0]["bucket"] == "trend_up"
    assert hits[0]["outcome"] == "reasonable_and_right"
    assert "trend-following" in hits[0]["lesson"]


def test_experience_store_retrieval_isolated_by_bucket(tmp_path):
    """桶隔离：写入 trend_up 的经验不出现在 range 桶的检索里。"""
    store = ExperienceStore(tmp_path / "exp.sqlite")
    store.write(bucket="trend_up", trade_key="a", config_summary="c",
                outcome="lucky", pnl=5.0, lesson="up lesson")
    store.write(bucket="range", trade_key="b", config_summary="c",
                outcome="bad_process", pnl=-3.0, lesson="range lesson")
    up = store.retrieve(bucket="trend_up", top_k=5)
    rng = store.retrieve(bucket="range", top_k=5)
    assert [h["lesson"] for h in up] == ["up lesson"]
    assert [h["lesson"] for h in rng] == ["range lesson"]


def test_experience_store_retrieve_empty_bucket_returns_nothing(tmp_path):
    store = ExperienceStore(tmp_path / "exp.sqlite")
    assert store.retrieve(bucket="trend_down", top_k=5) == []


def test_experience_store_write_is_idempotent(tmp_path):
    """幂等：同一 (bucket, trade_key) 写两次只留一行（同一笔平仓反思不重复入库）。"""
    store = ExperienceStore(tmp_path / "exp.sqlite")
    store.write(bucket="trend_up", trade_key="dup", config_summary="c",
                outcome="reasonable_and_right", pnl=10.0, lesson="L")
    store.write(bucket="trend_up", trade_key="dup", config_summary="c",
                outcome="reasonable_and_right", pnl=10.0, lesson="L")
    assert len(store.retrieve(bucket="trend_up", top_k=10)) == 1


def test_experience_store_persists_across_instances(tmp_path):
    """落盘持久化：同一 db 路径新开 store 能读到旧经验（离线演示复用）。"""
    path = tmp_path / "exp.sqlite"
    ExperienceStore(path).write(bucket="range", trade_key="p", config_summary="c",
                                outcome="lucky", pnl=1.0, lesson="persist")
    assert ExperienceStore(path).retrieve(bucket="range", top_k=5)[0]["lesson"] == "persist"


# --------------------------------------------------------------------------- #
# 平仓 pnl 数据流：broker 暴露 closed_trades（Reflector 的接缝）
# --------------------------------------------------------------------------- #

def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_broker_exposes_closed_trades_with_pnl():
    """平仓事件接缝：一笔往返平掉后，broker.closed_trades 追加带 pnl 的记录。"""
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0))
    b.on_bar(_bar(60_000, 10, 12, 10, 12))       # 建多 @10
    assert b.closed_trades == []                  # 还没平
    b.submit(Order(side="sell", qty=1.0))
    b.on_bar(_bar(120_000, 12, 13, 11, 13))       # 平多 @12 → pnl +2
    assert len(b.closed_trades) == 1
    ct = b.closed_trades[0]
    assert ct["pnl"] == pytest.approx(2.0)
    assert ct["ts"] == 120_000
    assert ct["entry_side"] == "long"


def test_broker_closed_trades_records_losing_trade():
    """亏损平仓也留痕（pnl < 0）。"""
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0))
    b.on_bar(_bar(60_000, 10, 10, 10, 10))        # 建多 @10
    b.submit(Order(side="sell", qty=1.0))
    b.on_bar(_bar(120_000, 8, 8, 7, 8))           # 平多 @8 → pnl -2
    assert b.closed_trades[-1]["pnl"] == pytest.approx(-2.0)


# --------------------------------------------------------------------------- #
# Reflector：四象限过程/结局分离打分（招牌卖点）
# --------------------------------------------------------------------------- #

def _reflector(params=None):
    return create_instance(NodeSpec("r", "reflector", params or {}))


def _closing_signal(side="long", confidence=0.9, rationale="ema uptrend + atr ok",
                    citations=None):
    """一个"好过程"信号：有 rationale、confidence 高、可选 citations。"""
    return {"side": side, "qty": 1.0, "stop": 95.0, "reason": rationale,
            "rationale": rationale, "confidence": confidence,
            "citations": citations if citations is not None else ["dca: risk"]}


def _broker_with_close(pnl, ts=120_000, entry_side="long"):
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.closed_trades.append({"ts": ts, "pnl": pnl, "entry_side": entry_side})
    return b


def test_reflector_cost_is_zero_deterministic():
    """Reflector 默认确定性四象限分类：cost 全 0 deterministic True，不触发 llm 红线。"""
    d = get_node_def("reflector")
    assert d.category == "reflection"
    assert d.cost.llm_calls_per_bar == 0
    assert d.cost.deterministic is True
    assert d.cost.latency_class == "fast"
    assert d.outputs["verdict"] == PinType.SERIES


def test_reflector_good_process_bad_outcome_is_reasonable_but_wrong():
    """招牌案例：好过程（rationale+高 confidence）+ 坏结局（亏损）→ reasonable_but_wrong。

    与 Hindsight 分类学一致——过程健全、市场偏偏不配合，被正确区分为"合理但错"，
    不惩罚运气（不归 bad_process）。这是反思闭环的招牌卖点。
    """
    r = _reflector()
    broker = _broker_with_close(pnl=-8.0)
    out = r.on_bar(_ctx(broker), {"signal": _closing_signal(confidence=0.9)})
    v = out["verdict"]
    assert v is not None, "close happened this bar → reflector must emit a verdict"
    assert v["verdict"] == "reasonable_but_wrong", v


def test_reflector_good_process_good_outcome_is_reasonable_and_right():
    r = _reflector()
    broker = _broker_with_close(pnl=15.0)
    v = r.on_bar(_ctx(broker), {"signal": _closing_signal(confidence=0.9)})["verdict"]
    assert v["verdict"] == "reasonable_and_right", v


def test_reflector_bad_process_good_outcome_is_lucky():
    """坏过程（无 rationale + confidence 低）+ 好结局（盈利）→ lucky（运气，非本事）。"""
    r = _reflector()
    broker = _broker_with_close(pnl=15.0)
    bad = _closing_signal(confidence=0.1, rationale="", citations=[])
    v = r.on_bar(_ctx(broker), {"signal": bad})["verdict"]
    assert v["verdict"] == "lucky", v


def test_reflector_bad_process_bad_outcome_is_bad_process():
    r = _reflector()
    broker = _broker_with_close(pnl=-8.0)
    bad = _closing_signal(confidence=0.1, rationale="", citations=[])
    v = r.on_bar(_ctx(broker), {"signal": bad})["verdict"]
    assert v["verdict"] == "bad_process", v


def test_reflector_emits_nothing_when_no_close_this_bar():
    """无平仓事件（closed_trades 未增长）→ verdict 为 None（只在平仓那根 bar 反思）。"""
    r = _reflector()
    broker = PaperBroker(initial_cash=1000.0)   # closed_trades 空
    out = r.on_bar(_ctx(broker), {"signal": _closing_signal()})
    assert out["verdict"] is None


def test_reflector_consumes_each_close_once():
    """幂等消费：同一笔平仓只反思一次，下一根 bar 无新平仓 → 不再重复出 verdict。"""
    r = _reflector()
    broker = _broker_with_close(pnl=-8.0)
    first = r.on_bar(_ctx(broker), {"signal": _closing_signal()})["verdict"]
    assert first is not None
    # 同一 broker，无新平仓
    second = r.on_bar(_ctx(broker), {"signal": _closing_signal()})["verdict"]
    assert second is None


def test_reflector_drains_multiple_closes_in_one_bar_without_dropping():
    """一根 bar 内两笔平仓（反手后止损/多执行路径）→ 后续 bar 逐笔排空，一笔都不丢。

    评审 CONFIRMED bug 修复自证：旧实现只反思 closed[-1] 并跳游标到末尾会丢中间那笔。
    现在每根 bar 消费最旧未反思的一笔，两根 bar 反思出两条不同 verdict。
    """
    r = _reflector()
    broker = PaperBroker(initial_cash=1000.0)
    # 同一根 bar（同 ts）两笔平仓，同向 → 旧实现 trade_key 还会撞
    broker.closed_trades.append({"ts": 120_000, "pnl": -8.0, "entry_side": "long"})
    broker.closed_trades.append({"ts": 120_000, "pnl": 5.0, "entry_side": "long"})
    ctx = _ctx(broker)
    v1 = r.on_bar(ctx, {"signal": _closing_signal()})["verdict"]
    v2 = r.on_bar(ctx, {"signal": _closing_signal()})["verdict"]
    v3 = r.on_bar(ctx, {"signal": _closing_signal()})["verdict"]
    assert v1 is not None and v2 is not None, "both closes must be reflected"
    assert v3 is None, "no third close → drained"
    # 两笔 pnl 各自被反思到（最旧优先：先 -8 再 +5），不丢不合并
    assert v1["pnl"] == pytest.approx(-8.0) and v2["pnl"] == pytest.approx(5.0)
    # trade_key 唯一（含全局序号）→ 写库不被 UPSERT 合并
    assert v1["trade_key"] != v2["trade_key"]


def test_reflector_multi_close_trade_keys_unique_in_store(tmp_path):
    """端到端：一根 bar 两笔同向平仓 → 经验库留两行（trade_key 唯一，不被 UPSERT 吞）。"""
    r = _reflector()
    broker = PaperBroker(initial_cash=1000.0)
    broker.closed_trades.append({"ts": 100, "pnl": -8.0, "entry_side": "long"})
    broker.closed_trades.append({"ts": 100, "pnl": -3.0, "entry_side": "long"})
    ctx = _ctx(broker)
    path = tmp_path / "exp.sqlite"
    w = create_instance(NodeSpec("w", "experience_write", {"db_path": str(path)}))
    # 每根 bar 排空一笔，把每条非空 verdict 都写库（含首笔）
    for _ in range(4):
        v = r.on_bar(ctx, {"signal": _closing_signal(), "ema": 100.0, "atr": 2.0})["verdict"]
        if v:
            w.on_bar(_ctx(), {"verdict": v})
    # 汇总所有桶的行数 == 2（两笔都落库，trade_key 唯一未被 UPSERT 合并）
    total = sum(len(ExperienceStore(path).retrieve(bucket=b, top_k=10))
                for b in ("trend_up", "trend_down", "range"))
    assert total == 2, "both closes must persist as distinct rows"


def test_reflector_verdict_carries_bucket_and_pnl():
    """verdict 载荷含市场状态桶 + pnl + config_summary（供 ExperienceWrite 落库）。

    桶由 ema 斜率派生——节点自身用 self.state 记住上一根 ema（不需要 ema_prev 引脚，
    否则真实蓝图连不通）。先喂一根建立 ema_prev=100，再喂 ema=105 平仓 → trend_up。
    """
    r = _reflector()
    broker = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    ctx = _ctx(broker)
    # bar 1：ema=100，无平仓 → 建立 ema_prev
    assert r.on_bar(ctx, {"signal": _closing_signal(), "ema": 100.0, "atr": 2.0})["verdict"] is None
    # bar 2：ema=105（上升），此时一笔亏损平仓 → trend_up + reasonable_but_wrong
    broker.closed_trades.append({"ts": 120_000, "pnl": -8.0, "entry_side": "long"})
    v = r.on_bar(ctx, {"signal": _closing_signal(), "ema": 105.0, "atr": 2.0})["verdict"]
    assert v["bucket"] == "trend_up"
    assert v["pnl"] == pytest.approx(-8.0)
    # 内容非空且可读（不只是 key 存在）：config_summary 含决策方向，lesson 含桶名
    assert v["config_summary"] and "side=long" in v["config_summary"]
    assert v["lesson"] and "trend_up" in v["lesson"]
    assert v["trade_key"]  # 唯一键非空，供落库


def test_reflector_without_broker_emits_nothing():
    """无 broker（ctx.broker None）→ 不抛异常，verdict None（防御）。"""
    r = _reflector()
    out = r.on_bar(_ctx(broker=None), {"signal": _closing_signal()})
    assert out["verdict"] is None


# --------------------------------------------------------------------------- #
# ExperienceWrite：由 Reflector verdict 驱动写库（幂等）
# --------------------------------------------------------------------------- #

def test_experience_write_cost_zero_deterministic():
    d = get_node_def("experience_write")
    assert d.category == "reflection"
    assert d.cost.llm_calls_per_bar == 0
    assert d.cost.deterministic is True


def test_experience_write_persists_verdict(tmp_path):
    """ExperienceWrite 收到 verdict → 写进经验库（按桶可检索）。"""
    path = tmp_path / "exp.sqlite"
    w = create_instance(NodeSpec("w", "experience_write", {"db_path": str(path)}))
    verdict = {"verdict": "reasonable_but_wrong", "bucket": "trend_up",
               "pnl": -8.0, "trade_key": "k1", "config_summary": "ema long",
               "lesson": "good process, bad luck"}
    w.on_bar(_ctx(), {"verdict": verdict})
    hits = ExperienceStore(path).retrieve(bucket="trend_up", top_k=5)
    assert len(hits) == 1 and hits[0]["outcome"] == "reasonable_but_wrong"


def test_experience_write_none_verdict_is_noop(tmp_path):
    """verdict None（无平仓那根 bar）→ 不写库（no-op）。"""
    path = tmp_path / "exp.sqlite"
    w = create_instance(NodeSpec("w", "experience_write", {"db_path": str(path)}))
    w.on_bar(_ctx(), {"verdict": None})
    assert ExperienceStore(path).retrieve(bucket="trend_up", top_k=5) == []


def test_experience_write_idempotent_same_trade(tmp_path):
    """同一 verdict（同 trade_key）写两次 → 库里仍一行（幂等，配合 ExperienceStore）。"""
    path = tmp_path / "exp.sqlite"
    w = create_instance(NodeSpec("w", "experience_write", {"db_path": str(path)}))
    verdict = {"verdict": "lucky", "bucket": "range", "pnl": 3.0,
               "trade_key": "same", "config_summary": "c", "lesson": "L"}
    w.on_bar(_ctx(), {"verdict": verdict})
    w.on_bar(_ctx(), {"verdict": dict(verdict)})
    assert len(ExperienceStore(path).retrieve(bucket="range", top_k=5)) == 1


# --------------------------------------------------------------------------- #
# ExperienceRetrieve：按当前桶检索经验注入决策上下文
# --------------------------------------------------------------------------- #

def test_experience_retrieve_cost_zero_deterministic():
    d = get_node_def("experience_retrieve")
    assert d.category == "rag"
    assert d.cost.llm_calls_per_bar == 0
    assert d.cost.deterministic is True


def test_experience_retrieve_returns_lessons_for_current_bucket(tmp_path):
    """按当前桶（由 ema 斜率+atr 派生）检索经验，产 lessons 供下游注入决策上下文。

    节点自身记住上一根 ema（不需要 ema_prev 引脚）：先喂 ema=100 再喂 ema=105 → trend_up。
    """
    path = tmp_path / "exp.sqlite"
    ExperienceStore(path).write(bucket="trend_up", trade_key="k",
                                config_summary="c", outcome="reasonable_but_wrong",
                                pnl=-8.0, lesson="don't chase blowoff tops in trend_up")
    node = create_instance(NodeSpec("er", "experience_retrieve",
                                    {"db_path": str(path), "top_k": 3}))
    node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 100.0, "atr": 2.0})   # 建 ema_prev
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 105.0, "atr": 2.0})
    lessons = out["lessons"]
    assert isinstance(lessons, list) and len(lessons) == 1
    assert "blowoff" in lessons[0]


def test_experience_retrieve_empty_when_bucket_has_no_experience(tmp_path):
    path = tmp_path / "exp.sqlite"
    ExperienceStore(path)  # 建空库
    node = create_instance(NodeSpec("er", "experience_retrieve",
                                    {"db_path": str(path), "top_k": 3}))
    node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 100.0, "atr": 2.0})   # 建 ema_prev
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 95.0, "atr": 2.0})   # trend_down
    assert out["lessons"] == []


# --------------------------------------------------------------------------- #
# 记忆开/关：有无 ExperienceRetrieve 时 signal 上下文差异可测
# --------------------------------------------------------------------------- #

def _memory_inject_node_registered():
    """测试专用注入门：把 experience_retrieve 的 lessons 合流进 signal["experience"]。

    lessons pin 悬空（记忆关，画布未连 er）→ None → signal["experience"]==[]；
    lessons 非空（记忆开）→ signal["experience"] 带教训。这是"记忆改变决策上下文"的载体。
    幂等注册（多次调用只注册一次）。
    """
    from alphaloom.nodes.registry import REGISTRY, node as _nd
    if "mem_inject" in REGISTRY:
        return

    @_nd(type="mem_inject", category="test",
         inputs={"signal": PinType.SIGNAL, "lessons": PinType.SERIES},
         outputs={"signal": PinType.SIGNAL},
         optional_inputs={"lessons"})
    class _MemInject:
        def setup(self, params):
            pass

        def on_bar(self, ctx, inputs):
            sig = dict(inputs["signal"])
            lessons = inputs.get("lessons")
            sig["experience"] = list(lessons) if lessons else []
            return {"signal": sig}


def _run_memory_bp(bp_json, bars=2):
    _memory_inject_node_registered()
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    instances = {n.id: create_instance(n) for n in bp.nodes}
    eng = Engine(compiled, instances, RunContext(clock=SimClock(), run_id="mem"))
    out_sigs = []
    eng.after_node = (lambda nid, outs:
                      out_sigs.append(outs["signal"].value) if nid == "inject" else None)
    # ema 稳步上升的 candle 序列 → trend_up 桶
    for i in range(bars):
        px = 100 + i * 3
        eng.step(BarEvent({"ts": i * 60_000, "open": px, "high": px + 1,
                           "low": px - 1, "close": px, "volume": 1}, 60_000))
    return out_sigs


def test_memory_on_vs_off_signal_context_differs(tmp_path):
    """记忆开关差异自证（引擎级）：同一决策链在画布上接 ExperienceRetrieve（记忆开）
    vs 不接（记忆关），下游 signal 携带的 experience 上下文实测出差异。

    这是"记忆改变决策上下文"的画布连通证据——不是节点单测，而是引擎真跑，signal 真被改写。
    """
    path = tmp_path / "exp.sqlite"
    ExperienceStore(path).write(bucket="trend_up", trade_key="k",
                                config_summary="c", outcome="reasonable_but_wrong",
                                pnl=-8.0, lesson="lesson from trend_up")

    # 记忆开：feed→(ema,atr,sig,er)→inject，er.lessons 连进 inject
    on_bp = {
        "id": "on", "name": "on",
        "nodes": [
            {"id": "feed", "type": "candle_feed"},
            {"id": "ema", "type": "ema", "params": {"period": 2}},
            {"id": "atr", "type": "atr", "params": {"period": 2}},
            {"id": "sig", "type": "scenario_gate"},
            {"id": "er", "type": "experience_retrieve",
             "params": {"db_path": str(path), "top_k": 3}},
            {"id": "inject", "type": "mem_inject"},
        ],
        "edges": [
            {"from": "feed.out", "to": "ema.candle"},
            {"from": "feed.out", "to": "atr.candle"},
            {"from": "feed.out", "to": "sig.candle"},
            {"from": "atr.value", "to": "sig.atr"},
            {"from": "feed.out", "to": "er.candle"},
            {"from": "ema.value", "to": "er.ema"},
            {"from": "atr.value", "to": "er.atr"},
            {"from": "sig.signal", "to": "inject.signal"},
            {"from": "er.lessons", "to": "inject.lessons"},
        ],
    }
    # 记忆关：同链但删掉 er，inject.lessons pin 悬空
    off_bp = {
        "id": "off", "name": "off",
        "nodes": [
            {"id": "feed", "type": "candle_feed"},
            {"id": "atr", "type": "atr", "params": {"period": 2}},
            {"id": "sig", "type": "scenario_gate"},
            {"id": "inject", "type": "mem_inject"},
        ],
        "edges": [
            {"from": "feed.out", "to": "atr.candle"},
            {"from": "feed.out", "to": "sig.candle"},
            {"from": "atr.value", "to": "sig.atr"},
            {"from": "sig.signal", "to": "inject.signal"},
        ],
    }

    on_sigs = _run_memory_bp(on_bp, bars=3)
    off_sigs = _run_memory_bp(off_bp, bars=3)

    # 记忆开：末根 bar 处于 trend_up → experience 非空且含库里教训
    assert any(s["experience"] for s in on_sigs), on_sigs
    assert any("lesson from trend_up" in s["experience"] for s in on_sigs)
    # 记忆关：experience 恒空（pin 悬空）
    assert all(s["experience"] == [] for s in off_sigs)
    # 差异可测：开与关的 experience 上下文不同
    assert [s["experience"] for s in on_sigs] != [s["experience"] for s in off_sigs]


# --------------------------------------------------------------------------- #
# 画布连通自证：含 reflector 的图能编译（反思数据流在真实蓝图连通）
# --------------------------------------------------------------------------- #

def test_reflector_graph_compiles_wiring_pnl_via_broker():
    """含 reflector 的图能编译：signal 从上游、pnl 从 ctx.broker（不占引脚）。

    证明反思闭环不是纸面功能——reflector 在画布上接得到平仓 pnl（读 ctx.broker），
    只需 signal + 可选 ema/ema_prev/atr 引脚。编译通过即数据流连通。
    """
    bp_json = {
        "id": "reflect", "name": "reflect",
        "nodes": [
            {"id": "data", "type": "candle_feed"},
            {"id": "ema", "type": "ema", "params": {"period": 5}},
            {"id": "atr", "type": "atr", "params": {"period": 5}},
            {"id": "sig", "type": "scenario_gate"},
            {"id": "risk", "type": "risk_gate"},
            {"id": "exec", "type": "execute_order"},
            {"id": "reflector", "type": "reflector"},
            {"id": "writer", "type": "experience_write"},
        ],
        "edges": [
            {"from": "data.out", "to": "ema.candle"},
            {"from": "data.out", "to": "atr.candle"},
            {"from": "data.out", "to": "sig.candle"},
            {"from": "atr.value", "to": "sig.atr"},
            {"from": "sig.signal", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "exec.signal"},
            {"from": "sig.signal", "to": "reflector.signal"},
            {"from": "ema.value", "to": "reflector.ema"},
            {"from": "atr.value", "to": "reflector.atr"},
            {"from": "reflector.verdict", "to": "writer.verdict"},
        ],
    }
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    # reflector 排在 sig 之后（读其 signal），writer 排在 reflector 之后
    order = compiled.order
    assert order.index("reflector") > order.index("sig")
    assert order.index("writer") > order.index("reflector")


def test_experience_retrieve_graph_compiles():
    """含 experience_retrieve 的记忆注入图能编译（按桶检索注入决策上下文的连通性）。"""
    bp_json = {
        "id": "mem", "name": "mem",
        "nodes": [
            {"id": "data", "type": "candle_feed"},
            {"id": "ema", "type": "ema", "params": {"period": 5}},
            {"id": "atr", "type": "atr", "params": {"period": 5}},
            {"id": "er", "type": "experience_retrieve"},
        ],
        "edges": [
            {"from": "data.out", "to": "ema.candle"},
            {"from": "data.out", "to": "atr.candle"},
            {"from": "data.out", "to": "er.candle"},
            {"from": "ema.value", "to": "er.ema"},
            {"from": "atr.value", "to": "er.atr"},
        ],
    }
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]


def test_reflector_end_to_end_via_engine():
    """引擎级端到端：跑几根 bar，人为触发一笔平仓，reflector 在平仓那根产 verdict。

    最硬的连通证明——不是编译过就算，而是引擎真跑一遍，reflector 真接到 broker 平仓 pnl。
    """
    # 我们手动操纵 broker 制造平仓（scenario_gate 恒 hold 于此数据，不实际下单）
    broker = PaperBroker(initial_cash=1000.0, fee_rate=0.0)

    bp_json = {
        "id": "e2e", "name": "e2e",
        "nodes": [
            {"id": "data", "type": "candle_feed"},
            {"id": "ema", "type": "ema", "params": {"period": 5}},
            {"id": "atr", "type": "atr", "params": {"period": 5}},
            {"id": "sig", "type": "scenario_gate"},
            {"id": "reflector", "type": "reflector"},
        ],
        "edges": [
            {"from": "data.out", "to": "ema.candle"},
            {"from": "data.out", "to": "atr.candle"},
            {"from": "data.out", "to": "sig.candle"},
            {"from": "atr.value", "to": "sig.atr"},
            {"from": "sig.signal", "to": "reflector.signal"},
            {"from": "ema.value", "to": "reflector.ema"},
            {"from": "atr.value", "to": "reflector.atr"},
        ],
    }
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    instances = {n.id: create_instance(n) for n in bp.nodes}
    ctx = RunContext(clock=SimClock(), run_id="e2e", broker=broker)
    eng = Engine(compiled, instances, ctx)

    verdicts = []
    eng.after_node = (lambda nid, outs:
                      verdicts.append(outs["verdict"].value) if nid == "reflector" else None)

    # bar 0,1：无平仓
    eng.step(BarEvent(_bar(0, 100, 101, 99, 100), 60_000))
    eng.step(BarEvent(_bar(60_000, 100, 101, 99, 100), 60_000))
    # 制造一笔平仓恰在 bar 2 之前（broker.closed_trades 增长）
    broker.closed_trades.append({"ts": 120_000, "pnl": -5.0, "entry_side": "long"})
    eng.step(BarEvent(_bar(120_000, 100, 101, 99, 100), 60_000))

    # 前两根无平仓 → None；第三根平仓 → 非空 verdict
    assert verdicts[0] is None and verdicts[1] is None
    assert verdicts[2] is not None
    # scenario_gate 产 bare signal（无 rationale/confidence）→ 过程判坏；pnl<0 结局坏
    # → bad_process（非 membership 恒真断言，锁死确切分类走通引擎）
    assert verdicts[2]["verdict"] == "bad_process", verdicts[2]


# 测试专用「好过程」信号源：带 rationale + 高 confidence + citations（模拟 LLM 分析师产出），
# 使 reasonable_and_right / reasonable_but_wrong 在编译图里可达（评审 gap 修复）。
from alphaloom.nodes.registry import node as _reflect_node  # noqa: E402


@_reflect_node(type="tr_sound_sig", category="test",
               inputs={"candle": PinType.CANDLE},
               outputs={"signal": PinType.SIGNAL})
class _SoundSignalSource:
    def setup(self, params):
        pass

    def on_bar(self, ctx, inputs):
        close = float(inputs["candle"]["close"])
        return {"signal": {"side": "long", "qty": 1.0, "stop": close - 1.0,
                           "reason": "ema uptrend confirmed",
                           "rationale": "ema uptrend confirmed by breakout",
                           "confidence": 0.9, "citations": ["pa: H2 long"]}}


def test_reflector_reasonable_but_wrong_reachable_through_engine():
    """招牌自证（引擎级）：把「好过程」信号（rationale+高 confidence+citations）接进 reflector，
    平仓亏损 → 编译图里真跑出 reasonable_but_wrong（不是手喂单测，是画布连通的确切分类）。

    这补上评审发现的 gap——旧测试只有 scenario_gate 的 bare signal，过程恒判坏，
    reasonable_but_wrong 在引擎里结构上不可达；现在可达且被锁死。
    """
    broker = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    bp_json = {
        "id": "rbw", "name": "rbw",
        "nodes": [
            {"id": "data", "type": "candle_feed"},
            {"id": "ema", "type": "ema", "params": {"period": 5}},
            {"id": "atr", "type": "atr", "params": {"period": 5}},
            {"id": "sig", "type": "tr_sound_sig"},
            {"id": "reflector", "type": "reflector"},
        ],
        "edges": [
            {"from": "data.out", "to": "ema.candle"},
            {"from": "data.out", "to": "atr.candle"},
            {"from": "data.out", "to": "sig.candle"},
            {"from": "sig.signal", "to": "reflector.signal"},
            {"from": "ema.value", "to": "reflector.ema"},
            {"from": "atr.value", "to": "reflector.atr"},
        ],
    }
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    instances = {n.id: create_instance(n) for n in bp.nodes}
    ctx = RunContext(clock=SimClock(), run_id="rbw", broker=broker)
    eng = Engine(compiled, instances, ctx)
    verdicts = []
    eng.after_node = (lambda nid, outs:
                      verdicts.append(outs["verdict"].value) if nid == "reflector" else None)
    eng.step(BarEvent(_bar(0, 100, 101, 99, 100), 60_000))
    # 好过程信号 + 亏损平仓 → reasonable_but_wrong
    broker.closed_trades.append({"ts": 60_000, "pnl": -12.0, "entry_side": "long"})
    eng.step(BarEvent(_bar(60_000, 100, 101, 99, 100), 60_000))
    assert verdicts[1] is not None
    assert verdicts[1]["verdict"] == "reasonable_but_wrong", verdicts[1]
    # 招牌语义自证：好过程坏结局被区分，lesson 明确「过程没错，别过度纠偏」
    assert "process was fine" in verdicts[1]["lesson"] or \
           "well-reasoned" in verdicts[1]["lesson"], verdicts[1]["lesson"]


# --------------------------------------------------------------------------- #
# 成本审计红线：新增反思节点全部不触发 llm 审计红线（自动覆盖已在 test_llm_nodes）
# --------------------------------------------------------------------------- #

def test_new_reflection_nodes_not_flagged_as_llm_offenders():
    """反思/记忆节点全 deterministic True cost 0 → 不出现在 llm 审计红线名单里。"""
    from alphaloom.nodes.registry import REGISTRY
    offenders = [
        t for t, d in REGISTRY.items()
        if d.cost.llm_calls_per_bar >= 1 and d.cost.deterministic is True
    ]
    assert offenders == []
    for t in ("reflector", "experience_retrieve", "experience_write"):
        assert REGISTRY[t].cost.deterministic is True
        assert REGISTRY[t].cost.llm_calls_per_bar == 0
