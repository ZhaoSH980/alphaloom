"""Committee 节点测试（AlphaLoom D3 Task 3）。

角色链（锁定）：策略师 → 风控官 → 主席，三次 LLM 调用，结构化 JSON 交接
（下游角色的 user prompt 里含上游角色的 JSON）。风控官 veto → 主席终案 side=hold。
输出 signal 附加 committee_trace:[strategist_json, risk_json, chair_json]。
"""
import json
import pytest
import alphaloom.nodes  # 触发全部内置节点注册
from alphaloom.graph.model import NodeSpec
from alphaloom.nodes.registry import create_instance, get_node_def
from alphaloom.runtime.context import SimClock, RunContext
from alphaloom.sandbox.audit import AuditLog


class _SequencedLLM:
    """按调用序返回不同 canned content：第 1/2/3 次分别是策略师/风控官/主席。

    每次 chat 记录收到的 messages（含上游 JSON 的交接自证）。
    """

    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.2, **params):
        idx = len(self.calls)
        self.calls.append({"messages": messages, "temperature": temperature, **params})
        content = self._contents[min(idx, len(self._contents) - 1)]
        return {"choices": [{"message": {"content": content}}]}


def _ctx(llm=None):
    ctx = RunContext(clock=SimClock(), run_id="t")
    ctx.llm = llm
    ctx.audit = AuditLog()
    return ctx


_CANDLE = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}

# 三份 canned JSON：策略师提案 long；风控官不 veto；主席合成 long。
_STRAT = {"side": "long", "rationale": "breakout above range", "confidence": 0.8}
_RISK = {"veto": False, "concern": "watch spread", "confidence": 0.7}
_CHAIR = {"side": "long", "rationale": "committee agrees", "confidence": 0.75}


def _committee(spec_params=None):
    return create_instance(NodeSpec("c", "committee", spec_params or {"atr_mult": 2.0}))


def test_committee_three_roles_sequential_with_structured_handoff():
    """三角色顺序调用：3 次 chat；风控官 prompt 含策略师 JSON；主席 prompt 含两份 JSON。"""
    llm = _SequencedLLM([json.dumps(_STRAT), json.dumps(_RISK), json.dumps(_CHAIR)])
    ctx = _ctx(llm)
    node = _committee()
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]

    # 恰好三次 LLM 调用
    assert len(llm.calls) == 3

    # 风控官（第 2 次）的 user prompt 里含策略师提案 —— 结构化交接自证
    risk_user = "".join(
        m["content"] for m in llm.calls[1]["messages"] if m["role"] == "user")
    assert "breakout above range" in risk_user  # 策略师 rationale 传递到风控官

    # 主席（第 3 次）的 user prompt 里同时含策略师 + 风控官两份 JSON
    chair_user = "".join(
        m["content"] for m in llm.calls[2]["messages"] if m["role"] == "user")
    assert "breakout above range" in chair_user  # 策略师
    assert "watch spread" in chair_user          # 风控官 concern

    # 终案是主席合成结果
    assert out["side"] == "long"
    assert out["rationale"] == "committee agrees"

    # 每角色一次 audit.record
    tools = [d["tool"] for d in ctx.audit.as_dicts()]
    assert tools == ["committee:strategist", "committee:risk", "committee:chair"]


def test_committee_risk_veto_forces_hold():
    """风控官 veto → 终案必须 side=hold（主席尊重否决），即便主席自己想 long。"""
    veto_risk = {"veto": True, "concern": "regime too choppy", "confidence": 0.0}
    llm = _SequencedLLM(
        [json.dumps(_STRAT), json.dumps(veto_risk), json.dumps(_CHAIR)])
    ctx = _ctx(llm)
    node = _committee()
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]
    assert out["side"] == "hold"
    # veto 后 stop 无意义
    assert out["stop"] is None


def test_committee_cost_annotation_is_three_calls():
    d = get_node_def("committee")
    assert d.category == "decision"
    assert d.cost.llm_calls_per_bar == 3
    assert d.cost.deterministic is False
    assert d.cost.latency_class == "llm"


def test_committee_output_carries_trace_of_three_roles():
    """输出 signal 含 committee_trace：三角色 JSON 列表（供前端展示）。"""
    llm = _SequencedLLM([json.dumps(_STRAT), json.dumps(_RISK), json.dumps(_CHAIR)])
    ctx = _ctx(llm)
    node = _committee()
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]
    trace = out["committee_trace"]
    assert isinstance(trace, list) and len(trace) == 3
    assert trace[0]["side"] == "long"          # 策略师
    assert trace[1]["veto"] is False           # 风控官
    assert trace[2]["side"] == "long"          # 主席


def test_committee_bad_json_any_role_falls_back_to_hold():
    """任一角色坏 JSON → 整体回退 hold，rationale 说明哪个角色 parse 失败。"""
    # 风控官返回非 JSON
    llm = _SequencedLLM(
        [json.dumps(_STRAT), "not json at all", json.dumps(_CHAIR)])
    ctx = _ctx(llm)
    node = _committee()
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]
    assert out["side"] == "hold"
    assert "risk" in out["rationale"].lower()  # 指明是风控官角色 parse 失败


def test_committee_without_llm_client_raises():
    ctx = _ctx(llm=None)
    node = _committee()
    with pytest.raises(RuntimeError, match="no LLM client bound"):
        node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.0})


# --------------------------------------------------------------------------- #
# skip_risk_officer 消融参数（D4-T4）：跳过 LLM 风控官"软护栏"，两次调用两条 trace
# --------------------------------------------------------------------------- #
def test_committee_skip_risk_officer_two_calls_and_two_role_trace():
    """消融臂：策略师 → 主席，两次 LLM 调用；trace 两项且无 veto 键；
    主席 user prompt 只含策略师 JSON（无 risk_officer 键）。"""
    llm = _SequencedLLM([json.dumps(_STRAT), json.dumps(_CHAIR)])
    ctx = _ctx(llm)
    node = _committee({"atr_mult": 2.0, "skip_risk_officer": True})
    out = node.on_bar(ctx, {"candle": _CANDLE, "atr": 1.5})["signal"]

    assert len(llm.calls) == 2
    chair_user = "".join(
        m["content"] for m in llm.calls[1]["messages"] if m["role"] == "user")
    assert "breakout above range" in chair_user     # 策略师 JSON 交接不变
    assert "risk_officer" not in chair_user         # 风控官整个角色不存在

    assert out["side"] == "long"
    trace = out["committee_trace"]
    assert len(trace) == 2                          # 策略师 + 主席，无风控官
    assert all("veto" not in t for t in trace)

    tools = [d["tool"] for d in ctx.audit.as_dicts()]
    assert tools == ["committee:strategist", "committee:chair"]


def test_committee_skip_risk_officer_registered_param_defaults_off():
    d = get_node_def("committee")
    assert d.params.get("skip_risk_officer") is bool
    # 默认不跳过：不传参数时三角色三调用（上方既有测试已锁），此处锁 setup 默认值
    node = _committee({"atr_mult": 2.0})
    assert node.skip_risk_officer is False
