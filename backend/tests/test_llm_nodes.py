import json
import pytest
import alphaloom.nodes  # 触发全部内置节点注册（审计遍历全 REGISTRY 前必须先 import）
from alphaloom.graph.model import NodeSpec
from alphaloom.nodes.registry import REGISTRY, create_instance, get_node_def
from alphaloom.runtime.context import SimClock, RunContext
from alphaloom.sandbox.audit import AuditLog


class _FakeLLM:
    """模拟 RecordingLLMClient.chat：返回 OpenAI 兼容响应，content 由 canned 提供。"""

    def __init__(self, content):
        self._content = content
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.2, **params):
        self.calls.append({"messages": messages, "temperature": temperature, **params})
        return {"choices": [{"message": {"content": self._content}}]}


def _ctx(llm=None):
    ctx = RunContext(clock=SimClock(), run_id="t")
    ctx.llm = llm
    ctx.audit = AuditLog()
    return ctx


_CANDLE = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}


def test_analyst_produces_signal_with_rationale_and_stop():
    # long + short 两向都覆盖：stop 方向随 side 翻转（close∓atr_mult*atr）
    llm = _FakeLLM(json.dumps(
        {"side": "long", "rationale": "uptrend intact", "confidence": 0.8}))
    ctx = _ctx(llm)
    node = create_instance(NodeSpec("a", "llm_analyst", {"persona": "trend", "atr_mult": 2.0}))
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]
    assert out["side"] == "long"
    assert out["rationale"] == "uptrend intact"
    assert out["confidence"] == pytest.approx(0.8)
    assert out["stop"] == pytest.approx(100 - 2.0 * 1.5)   # long: close - atr_mult*atr
    assert out["citations"] == []
    assert len(llm.calls) == 1 and llm.calls[0]["temperature"] == pytest.approx(0.2)
    assert ctx.audit.as_dicts()[0]["tool"] == "llm_chat"   # 审计留痕

    short_llm = _FakeLLM(json.dumps({"side": "short", "rationale": "down", "confidence": 0.6}))
    sctx = _ctx(short_llm)
    snode = create_instance(NodeSpec("a", "llm_analyst", {"persona": "bear", "atr_mult": 3.0}))
    sout = snode.on_bar(sctx, {"candle": _CANDLE, "atr": 2.0})["signal"]
    assert sout["side"] == "short"
    assert sout["stop"] == pytest.approx(100 + 3.0 * 2.0)   # short: close + atr_mult*atr


def test_analyst_bad_json_or_illegal_side_falls_back_to_hold():
    for content in ("this is not json at all",
                    json.dumps({"side": "buy", "rationale": "wrong word", "confidence": 0.9})):
        ctx = _ctx(_FakeLLM(content))
        node = create_instance(NodeSpec("a", "llm_analyst", {"persona": "x", "atr_mult": 2.0}))
        out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.0})["signal"]
        assert out["side"] == "hold"
        assert out["rationale"] == "parse failed"
        assert out["citations"] == []


def test_analyst_cost_annotation_is_honest():
    d = get_node_def("llm_analyst")
    assert d.category == "decision"
    assert d.cost.llm_calls_per_bar >= 1
    assert d.cost.deterministic is False
    assert d.cost.latency_class == "llm"


def test_analyst_without_llm_client_raises():
    ctx = _ctx(llm=None)
    node = create_instance(NodeSpec("a", "llm_analyst", {"persona": "x", "atr_mult": 2.0}))
    with pytest.raises(RuntimeError, match="no LLM client bound"):
        node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.0})


def test_llm_node_cost_audit_no_deterministic_true():
    """D1 Carryover 10：任何调用 LLM 的节点（llm_calls_per_bar>=1）禁止声明 deterministic=True。
    遍历全 REGISTRY（依赖顶部 `import alphaloom.nodes` 已触发全部注册）。"""
    offenders = [
        t for t, d in REGISTRY.items()
        if d.cost.llm_calls_per_bar >= 1 and d.cost.deterministic is True
    ]
    assert offenders == [], f"LLM nodes must not claim deterministic=True: {offenders}"
