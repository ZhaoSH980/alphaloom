"""委员会消融实验测试（D4-T4）—— 护栏价值量化，全程确定性 fake transport，零配额。

三臂由**图变换**生成（消融 = 可编程的图手术，不是三份手写蓝图）：
- full：原蓝图原样
- no_risk_officer：参数手术——committee 加 skip_risk_officer=True 跳过 LLM 风控官（软护栏）
- no_rag：图手术——旁路 require_citations、移除 knowledge_retrieve 子链

硬护栏卖点锁定：RiskGate 是全宇宙唯一 risk_stamped_signal 产地，旁路它的图
**编译必 TYPE_MISMATCH**——消融能拆的只有 LLM 软护栏，类型系统硬护栏拆不掉。

guardrail_value 是计算值不是硬编码结论：本文件既有"风控官拦下暴跌前危险提案"
的正价值剧本，也有"风控官净拦盈利交易"的负价值反向剧本——正负都如实。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import alphaloom.nodes  # noqa: F401  触发全部内置节点注册
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.eval.ablation import (
    DEFAULT_ARMS,
    AblationReport,
    ArmResult,
    arm_blueprint,
    committee_ablation,
    graph_bypass,
)
from alphaloom.eval.scorecard import scorecard
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import PortRef, load_loom_file, loads_loom

_REPO = Path(__file__).resolve().parents[2]
_HEADLINE = _REPO / "blueprints" / "agent_committee.loom"
_EMA_CROSS = _REPO / "blueprints" / "ema_cross.loom"

_QUADRANTS = {"reasonable_and_right", "reasonable_but_wrong", "lucky", "bad_process"}


# --------------------------------------------------------------------------- #
# 确定性 fake LLM（纯本地 python 函数——无 socket、无网络、零配额；剧本可注入）
# --------------------------------------------------------------------------- #
def _wrap(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _try_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


class ScriptedCommitteeLLM:
    """按 system prompt 路由三角色的确定性 fake LLM。

    剧本由 strategist_fn(market)->dict 与 risk_fn(market, strategist)->dict 注入；
    主席固定"尊重 veto、否则跟随策略师"（与真实主席语义一致）。role_calls 计数
    供测试自证：所有请求都被本地路由（other==0），没有任何请求可能触网。
    """

    def __init__(self, strategist_fn, risk_fn):
        self.strategist_fn = strategist_fn
        self.risk_fn = risk_fn
        self.role_calls = {"strategist": 0, "risk": 0, "chair": 0, "other": 0}

    def chat(self, messages, tools=None, temperature=0.2, **params):
        system = next((str(m.get("content", "")) for m in messages
                       if m.get("role") == "system"), "").lower()
        user_objs = [o for o in (_try_json(m.get("content", "")) for m in messages
                                 if m.get("role") == "user") if isinstance(o, dict)]

        def _market():
            for o in user_objs:
                if isinstance(o.get("market"), dict):
                    return o["market"]
                if "close" in o:
                    return o
            return {}

        def _part(key):
            for o in user_objs:
                if isinstance(o.get(key), dict):
                    return o[key]
            return {}

        if "committee's strategist" in system:
            self.role_calls["strategist"] += 1
            return _wrap(json.dumps(self.strategist_fn(_market())))
        if "committee's risk officer" in system:
            self.role_calls["risk"] += 1
            return _wrap(json.dumps(self.risk_fn(_market(), _part("strategist"))))
        if "committee chair" in system:
            self.role_calls["chair"] += 1
            strat, risk = _part("strategist"), _part("risk_officer")
            if bool(risk.get("veto")):
                return _wrap(json.dumps({"side": "hold",
                                         "rationale": "chair defers to the veto",
                                         "confidence": 0.1}))
            side = strat.get("side", "hold")
            conf = float(strat.get("confidence", 0.5) or 0.5)
            return _wrap(json.dumps({"side": side,
                                     "rationale": f"chair follows the {side} thesis",
                                     "confidence": round(conf * 0.9, 3)}))
        self.role_calls["other"] += 1
        return _wrap(json.dumps({"side": "hold", "rationale": "unrecognized prompt",
                                 "confidence": 0.0}))


# --------------------------------------------------------------------------- #
# 剧本：策略师追动能；风控官 A（泡沫警惕）在价格过热区 veto；风控官 B（偏执）全 veto
# --------------------------------------------------------------------------- #
_HOT = 101.5   # 过热线：close 高于它 → 策略师看涨、泡沫警惕型风控官 veto


def momentum_strategist(market):
    close = float(market.get("close", 0.0) or 0.0)
    if close > _HOT:
        return {"side": "long", "rationale": "momentum: price extended above range",
                "confidence": 0.9}
    return {"side": "hold", "rationale": "no edge", "confidence": 0.4}


def bubble_wary_risk(market, strategist):
    close = float(market.get("close", 0.0) or 0.0)
    if strategist.get("side") in ("long", "short") and close > _HOT:
        return {"veto": True, "concern": "price parabolic above fair value - bubble risk",
                "confidence": 0.2}
    return {"veto": False, "concern": "volatility acceptable", "confidence": 0.7}


def paranoid_risk(market, strategist):
    if strategist.get("side") in ("long", "short"):
        return {"veto": True, "concern": "no trade is ever safe", "confidence": 0.1}
    return {"veto": False, "concern": "nothing to veto", "confidence": 0.5}


# --------------------------------------------------------------------------- #
# 合成行情（手工确定性，无 RNG）
# --------------------------------------------------------------------------- #
def _bar(i, o, c, spread=1.0):
    return {"ts": i * 60_000, "open": round(o, 6),
            "high": round(max(o, c) + spread, 6),
            "low": round(min(o, c) - spread, 6),
            "close": round(c, 6), "volume": 1.0}


def crash_candles():
    """20 根横盘 → 2 根冲上过热区（103）→ 9 根暴跌至 49 → 横盘。

    剧本：策略师在过热区看涨；泡沫警惕型风控官 veto → full 臂空仓躲过暴跌；
    no_risk_officer 臂进场吃暴跌（止损离场，亏损为实）。
    """
    closes = [100 + 0.2 * ((i % 3) - 1) for i in range(20)]      # 横盘
    closes += [103.0, 103.0]                                       # 过热区（>_HOT）
    closes += [103.0 - 6.0 * k for k in range(1, 10)]              # 暴跌 97→49
    closes += [49.0] * 15                                          # 跌后横盘
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(_bar(i, prev, c))
        prev = c
    return out


def rally_candles():
    """20 根横盘 → 26 根稳步上涨到 152：反向剧本——veto 全是误杀。"""
    closes = [100 + 0.2 * ((i % 3) - 1) for i in range(20)]
    closes += [102.0 + 2.0 * k for k in range(26)]                 # 102→152
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(_bar(i, prev, c))
        prev = c
    return out


def _source(tmp_path, candles):
    db = SQLiteMarketData(tmp_path / "market.sqlite")
    db.insert_candles("TEST", "1m", candles)
    return db


# --------------------------------------------------------------------------- #
# 测试蓝图（镜像 headline 结构，规模缩小；xp 库定向 tmp）
# --------------------------------------------------------------------------- #
def _bp(*, with_rag: bool, with_reflection: bool = False, xp_db: str | None = None):
    nodes = [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "TEST", "bar": "1m"}},
        {"id": "atr", "type": "atr", "params": {"period": 14}},
        {"id": "committee", "type": "committee", "params": {"atr_mult": 2.0}},
        {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.05}},
        {"id": "risk", "type": "risk_gate",
         "params": {"max_qty": 100_000.0, "require_stop": True}},
        {"id": "exec", "type": "execute_order", "params": {}},
    ]
    edges = [
        {"from": "feed.out", "to": "atr.candle"},
        {"from": "feed.out", "to": "committee.candle"},
        {"from": "atr.value", "to": "committee.atr"},
        {"from": "feed.out", "to": "sizer.candle"},
        {"from": "sizer.sized", "to": "risk.signal"},
        {"from": "risk.stamped", "to": "exec.signal"},
    ]
    if with_rag:
        nodes += [
            {"id": "kb", "type": "knowledge_retrieve",
             "params": {"query": "trend breakout risk", "top_k": 2}},
            {"id": "cite_gate", "type": "require_citations", "params": {}},
        ]
        edges += [
            {"from": "feed.out", "to": "kb.candle"},
            {"from": "kb.citations", "to": "cite_gate.citations"},
            {"from": "committee.signal", "to": "cite_gate.signal"},
            {"from": "cite_gate.signal", "to": "sizer.signal"},
        ]
        decision_out = "cite_gate.signal"
    else:
        edges.append({"from": "committee.signal", "to": "sizer.signal"})
        decision_out = "committee.signal"
    if with_reflection:
        nodes += [
            {"id": "reflector", "type": "reflector", "params": {}},
            {"id": "xp_write", "type": "experience_write",
             "params": {"db_path": xp_db}},
        ]
        edges += [
            {"from": decision_out, "to": "reflector.signal"},
            {"from": "atr.value", "to": "reflector.atr"},
            {"from": "reflector.verdict", "to": "xp_write.verdict"},
        ]
    return loads_loom(json.dumps({"id": "abl_test_v1", "name": "ablation test bp",
                                  "nodes": nodes, "edges": edges}))


# =========================================================================== #
# 图变换正确性
# =========================================================================== #
def test_default_arms_locked():
    assert DEFAULT_ARMS == ("full", "no_risk_officer", "no_rag")


def test_arm_full_is_faithful_copy():
    bp = _bp(with_rag=True)
    full = arm_blueprint(bp, "full")
    assert [(n.id, n.type, n.params) for n in full.nodes] \
        == [(n.id, n.type, n.params) for n in bp.nodes]
    assert full.edges == bp.edges
    # 深拷贝 params：改臂不污染原蓝图
    full.nodes[0].params["inst"] = "MUTATED"
    assert bp.nodes[0].params["inst"] == "TEST"


def test_arm_no_risk_officer_is_param_surgery_only():
    bp = _bp(with_rag=True)
    arm = arm_blueprint(bp, "no_risk_officer")
    committees = [n for n in arm.nodes if n.type == "committee"]
    assert len(committees) == 1
    assert committees[0].params.get("skip_risk_officer") is True
    # 原蓝图不被就地污染
    orig = [n for n in bp.nodes if n.type == "committee"][0]
    assert "skip_risk_officer" not in orig.params
    # 图结构不变（节点数/边完全一致——只动参数）
    assert len(arm.nodes) == len(bp.nodes)
    assert arm.edges == bp.edges
    compiled = compile_blueprint(arm)
    assert compiled.ok, [e.code for e in compiled.errors]


def test_arm_no_risk_officer_requires_committee():
    bp = load_loom_file(_EMA_CROSS)
    with pytest.raises(ValueError, match="committee"):
        arm_blueprint(bp, "no_risk_officer")


def test_arm_no_rag_surgery_on_headline_blueprint():
    """no_rag 臂在真实 headline 蓝图上：RAG 子链拆除、边安全重接、编译通过。"""
    bp = load_loom_file(_HEADLINE)
    arm = arm_blueprint(bp, "no_rag")
    types = {n.type for n in arm.nodes}
    assert "knowledge_retrieve" not in types
    assert "require_citations" not in types
    compiled = compile_blueprint(arm)
    assert compiled.ok, [e.code for e in compiled.errors]
    # 编译后计划里也没有 RAG 节点
    assert all(compiled.nodes[nid].type not in
               ("knowledge_retrieve", "require_citations") for nid in compiled.order)
    # 边重接：committee.signal 直连原 cite_gate 的两个下游（sizer + reflector）
    dsts = {(e.dst.node_id, e.dst.port) for e in arm.edges
            if e.src == PortRef("committee", "signal")}
    assert ("sizer", "signal") in dsts
    assert ("reflector", "signal") in dsts


def test_arm_no_rag_requires_rag_chain():
    bp = _bp(with_rag=False)
    with pytest.raises(ValueError, match="require_citations"):
        arm_blueprint(bp, "no_rag")


def test_unknown_arm_rejected():
    bp = _bp(with_rag=True)
    with pytest.raises(ValueError, match="unknown ablation arm"):
        arm_blueprint(bp, "no_hard_rail")
    src = SQLiteMarketData(":memory:")
    with pytest.raises(ValueError, match="unknown ablation arm"):
        committee_ablation(bp, src, inst="TEST", bar="1m",
                           llm=None, arms=("full", "bogus"))


# =========================================================================== #
# 硬护栏不可消融（卖点锁定）：旁路 risk_gate 的图编译必 TYPE_MISMATCH
# =========================================================================== #
def test_risk_gate_hard_rail_cannot_be_ablated_headline():
    bp = load_loom_file(_HEADLINE)
    mutilated = graph_bypass(bp, node_type="risk_gate",
                             in_port="signal", out_port="stamped")
    compiled = compile_blueprint(mutilated)
    assert not compiled.ok
    codes = [e.code for e in compiled.errors]
    assert "TYPE_MISMATCH" in codes
    # 编译器的修复提示把用户按回 RiskGate——硬护栏自带说明书
    hints = " ".join(e.fix_hint or "" for e in compiled.errors)
    assert "RiskGate" in hints


def test_risk_gate_hard_rail_cannot_be_ablated_test_bp():
    bp = _bp(with_rag=True)
    mutilated = graph_bypass(bp, node_type="risk_gate",
                             in_port="signal", out_port="stamped")
    compiled = compile_blueprint(mutilated)
    assert not compiled.ok
    assert any(e.code == "TYPE_MISMATCH" for e in compiled.errors)


def test_no_risk_gate_arm_is_not_offered():
    """消融臂清单里没有也不可能有"去 risk_gate"臂——类型系统不允许它存在。"""
    assert all("risk_gate" not in arm for arm in DEFAULT_ARMS)


# =========================================================================== #
# 三臂同窗跑通 + 报告形状 / JSON 安全
# =========================================================================== #
def test_three_arms_same_window_and_report_shape(tmp_path):
    src = _source(tmp_path, crash_candles())
    llm = ScriptedCommitteeLLM(momentum_strategist, bubble_wary_risk)
    rep = committee_ablation(_bp(with_rag=True), src, inst="TEST", bar="1m",
                             start_ms=0, end_ms=None, llm=llm)
    assert isinstance(rep, AblationReport)
    assert [a.arm for a in rep.arms] == list(DEFAULT_ARMS)
    # 同数据同窗口：三臂 bar 数一致
    assert len({a.bars for a in rep.arms}) == 1
    for a in rep.arms:
        assert isinstance(a, ArmResult)
        assert isinstance(a.summary, dict) and "net_pnl" in a.summary
        assert a.num_trades == a.summary["num_trades"]
        assert isinstance(a.verdict_counts, dict)
    d = rep.to_dict()
    json.dumps(d)                       # JSON 安全（inf/nan 已清洗）
    for arm_d in d["arms"]:
        assert set(arm_d) >= {"arm", "summary", "num_vetoes", "num_trades",
                              "verdict_counts"}
    assert d["guardrail_value"] is not None
    # 零配额自证：所有 LLM 请求都被本地剧本路由，没有一个走了未识别分支
    assert llm.role_calls["other"] == 0
    assert llm.role_calls["strategist"] > 0
    # no_risk_officer 臂主席照常出场（少的是风控官，不是整个委员会）
    assert llm.role_calls["chair"] > 0


def test_arms_subset_and_missing_guardrail(tmp_path):
    src = _source(tmp_path, crash_candles())
    llm = ScriptedCommitteeLLM(momentum_strategist, bubble_wary_risk)
    rep = committee_ablation(_bp(with_rag=False), src, inst="TEST", bar="1m",
                             llm=llm, arms=("full",))
    assert [a.arm for a in rep.arms] == ["full"]
    assert rep.guardrail_value is None          # 缺对照臂 → 不硬造护栏价值
    json.dumps(rep.to_dict())


# =========================================================================== #
# veto 计数 + 护栏价值剧本（正价值：风控官拦下暴跌前的危险提案）
# =========================================================================== #
def test_guardrail_scenario_full_avoids_crash_ablated_eats_it(tmp_path):
    src = _source(tmp_path, crash_candles())
    llm = ScriptedCommitteeLLM(momentum_strategist, bubble_wary_risk)
    bp = _bp(with_rag=False, with_reflection=True,
             xp_db=str(tmp_path / "xp.sqlite"))
    rep = committee_ablation(bp, src, inst="TEST", bar="1m", llm=llm,
                             arms=("full", "no_risk_officer"))
    by = {a.arm: a for a in rep.arms}
    full, ablated = by["full"], by["no_risk_officer"]

    # veto 计数：full 臂风控官在过热区必否决（>0）；无风控官臂没人可否决（=0）
    assert full.num_vetoes > 0
    assert ablated.num_vetoes == 0

    # full 臂被 veto 挡在场外 → 零交易零亏损；消融臂进场吃暴跌 → 真亏损
    assert full.num_trades == 0
    assert ablated.num_trades >= 1
    assert ablated.summary["net_pnl"] < 0
    assert full.summary["net_pnl"] > ablated.summary["net_pnl"]

    gv = rep.guardrail_value
    assert gv["net_pnl_delta"] == pytest.approx(
        full.summary["net_pnl"] - ablated.summary["net_pnl"])
    assert gv["net_pnl_delta"] > 0
    assert gv["guardrail_helped"] is True
    assert gv["num_vetoes_full"] == full.num_vetoes

    # 反思四象限：消融臂有平仓 → 有 verdict，标签合法；full 臂无平仓 → 空
    assert sum(ablated.verdict_counts.values()) >= 1
    assert set(ablated.verdict_counts) <= _QUADRANTS
    assert full.verdict_counts == {}


# =========================================================================== #
# 诚实性：guardrail_value 是算出来的，风控官净误杀盈利交易时必须如实为负
# =========================================================================== #
def test_guardrail_value_negative_when_risk_officer_blocks_winners(tmp_path):
    src = _source(tmp_path, rally_candles())
    llm = ScriptedCommitteeLLM(momentum_strategist, paranoid_risk)
    rep = committee_ablation(_bp(with_rag=False), src, inst="TEST", bar="1m",
                             llm=llm, arms=("full", "no_risk_officer"))
    by = {a.arm: a for a in rep.arms}
    assert by["full"].num_vetoes > 0            # 偏执风控官疯狂否决
    assert by["full"].num_trades == 0
    assert by["no_risk_officer"].summary["net_pnl"] > 0   # 被误杀的全是盈利交易
    gv = rep.guardrail_value
    assert gv["net_pnl_delta"] < 0              # 护栏价值为负——如实展示
    assert gv["guardrail_helped"] is False
    json.dumps(rep.to_dict())


# =========================================================================== #
# scorecard 集成：AblationReport.to_dict 塞进 scorecard(ablation=...) 不炸
# =========================================================================== #
def test_scorecard_accepts_ablation_report(tmp_path):
    src = _source(tmp_path, crash_candles())
    llm = ScriptedCommitteeLLM(momentum_strategist, bubble_wary_risk)
    rep = committee_ablation(_bp(with_rag=False), src, inst="TEST", bar="1m",
                             llm=llm, arms=("full", "no_risk_officer"))
    full = [a for a in rep.arms if a.arm == "full"][0]
    card = scorecard({"summary": full.summary}, ablation=rep.to_dict())
    d = card.to_dict()
    json.dumps(d)
    assert d["evidence_coverage"]["ablation"] is True
    assert d["ablation"]["guardrail_value"]["net_pnl_delta"] \
        == rep.guardrail_value["net_pnl_delta"]
