"""Copilot 后端测试（AlphaLoom D3 Task 6）。

招牌演示核心：text_to_blueprint 把自然语言变蓝图 → 编译 → 失败则把 CompileError
（含 fix_hint）喂回 LLM 让它自修复重试 ≤3 次。这是 "Agent 在可验证环境自修复" 的
自证：编译器报错真进重试提示，坏图（绕风控 TYPE_MISMATCH）→ CompileError → fix_hint
进提示 → 修正图 → ok。

mock llm 技巧：_SequencedLLM 按调用序返回不同 loom JSON（第一次坏、第二次好），
验证自修复循环。此外有一个用**真** compile_blueprint 的集成测试（不是 mock 假
compile_fn 就完事）。
"""
import json

import pytest

import alphaloom.nodes  # 触发全部内置节点注册（REGISTRY 满）
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import loads_loom
from alphaloom.nodes.registry import REGISTRY
from alphaloom.copilot import blueprint as bp_mod
from alphaloom.copilot import layout as layout_mod
from alphaloom.copilot import prompts as prompts_mod


# --------------------------------------------------------------------------- #
# mock LLM：按调用序返回预置 content，并记录每次收到的 messages（用于断言 fix_hint
# 是否进了重试提示）。content 直接是 loom JSON 字符串（system prompt 要求 JSON-only）。
# --------------------------------------------------------------------------- #
class _SequencedLLM:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.2, **params):
        idx = len(self.calls)
        self.calls.append({"messages": messages, "temperature": temperature, **params})
        content = self._contents[min(idx, len(self._contents) - 1)]
        return {"choices": [{"message": {"content": content}}]}


# 一个合法蓝图：feed → ema×2 → cross → sizer → risk_gate → execute（下单过风控）。
_GOOD_LOOM = {
    "id": "gen_v1",
    "name": "generated",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
        {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
        {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
        {"id": "atr", "type": "atr", "params": {"period": 14}},
        {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
        {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
        {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": True}},
        {"id": "exec", "type": "execute_order", "params": {}},
    ],
    "edges": [
        {"from": "feed.out", "to": "ema_fast.candle"},
        {"from": "feed.out", "to": "ema_slow.candle"},
        {"from": "feed.out", "to": "atr.candle"},
        {"from": "ema_fast.value", "to": "cross.fast"},
        {"from": "ema_slow.value", "to": "cross.slow"},
        {"from": "feed.out", "to": "cross.candle"},
        {"from": "atr.value", "to": "cross.atr"},
        {"from": "cross.signal", "to": "sizer.signal"},
        {"from": "feed.out", "to": "sizer.candle"},
        {"from": "sizer.sized", "to": "risk.signal"},
        {"from": "risk.stamped", "to": "exec.signal"},
    ],
    "meta": {},
}

# 坏蓝图：sizer.sized(signal) 直连 exec.signal(risk_stamped_signal) —— 绕过 RiskGate。
# 编译器必报 TYPE_MISMATCH（招牌卖点：即便 LLM 也造不出绕风控的图）。
_BAD_BYPASS_LOOM = {
    "id": "gen_v1",
    "name": "generated",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
        {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
        {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
        {"id": "atr", "type": "atr", "params": {"period": 14}},
        {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
        {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
        {"id": "exec", "type": "execute_order", "params": {}},
    ],
    "edges": [
        {"from": "feed.out", "to": "ema_fast.candle"},
        {"from": "feed.out", "to": "ema_slow.candle"},
        {"from": "feed.out", "to": "atr.candle"},
        {"from": "ema_fast.value", "to": "cross.fast"},
        {"from": "ema_slow.value", "to": "cross.slow"},
        {"from": "feed.out", "to": "cross.candle"},
        {"from": "atr.value", "to": "cross.atr"},
        {"from": "cross.signal", "to": "sizer.signal"},
        {"from": "feed.out", "to": "sizer.candle"},
        # 绕风控：signal 直连 risk_stamped_signal 输入端 → TYPE_MISMATCH
        {"from": "sizer.sized", "to": "exec.signal"},
    ],
    "meta": {},
}


def _real_compile_fn(loom_dict):
    """真实 compile_fn：loom dict → BlueprintSpec → compile_blueprint（集成用）。"""
    return compile_blueprint(loads_loom(json.dumps(loom_dict)))


# --------------------------------------------------------------------------- #
# 系统提示词：含全 NodeDef 目录 + loom schema + 风控硬约束
# --------------------------------------------------------------------------- #
def test_system_prompt_contains_full_node_catalog():
    """系统提示词动态含全 REGISTRY 节点目录（类型 + 引脚 + 类型标注）。"""
    prompt = prompts_mod.build_system_prompt(REGISTRY)
    # 每个注册的节点类型都出现在提示词里（动态生成，不是硬编码子集）
    for node_type in REGISTRY:
        assert node_type in prompt, f"node type {node_type!r} missing from catalog"
    # 引脚类型信息也在（下游 LLM 才知道怎么连线）
    assert "risk_stamped_signal" in prompt
    assert "signal" in prompt


def test_system_prompt_states_risk_gate_hard_constraint():
    """系统提示词含 "下单必须过 RiskGate" 硬约束（招牌卖点写进提示）。"""
    prompt = prompts_mod.build_system_prompt(REGISTRY)
    low = prompt.lower()
    assert "risk_gate" in low or "riskgate" in low
    assert "risk_stamped_signal" in low
    # 明确说明 execute_order 只吃 risk_stamped_signal（唯一 RiskGate 产出）
    assert "execute_order" in prompt


def test_system_prompt_documents_loom_schema():
    """提示词说明 .loom JSON schema（nodes/edges/from/to）。"""
    prompt = prompts_mod.build_system_prompt(REGISTRY)
    for token in ("nodes", "edges", "from", "to"):
        assert token in prompt


# --------------------------------------------------------------------------- #
# text_to_blueprint：NL → 合法 loom
# --------------------------------------------------------------------------- #
def test_text_to_blueprint_produces_compilable_loom():
    """一次到位：LLM 返回合法 loom → compile ok → 返回 {loom, notes}。"""
    llm = _SequencedLLM([json.dumps(_GOOD_LOOM)])
    result = bp_mod.text_to_blueprint(
        "EMA cross trend follow that routes orders through the risk gate",
        REGISTRY, llm, compile_fn=_real_compile_fn)
    assert "loom" in result and "notes" in result
    # 返回的 loom 真能编译过（用真 compile_blueprint 复核）
    compiled = _real_compile_fn(result["loom"])
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    # 只调了一次 LLM（首图即合法）
    assert len(llm.calls) == 1


# --------------------------------------------------------------------------- #
# 编译错误自修复（核心自证）——用真 compile_blueprint
# --------------------------------------------------------------------------- #
def test_compile_error_self_repair_loop_with_real_compiler():
    """招牌自证：首图绕风控（TYPE_MISMATCH）→ 编译器拦 → fix_hint 进重试提示 →
    第二图修正 → 最终 ok。用**真** compile_blueprint，不是假 compile_fn。"""
    llm = _SequencedLLM([json.dumps(_BAD_BYPASS_LOOM), json.dumps(_GOOD_LOOM)])
    result = bp_mod.text_to_blueprint(
        "trade EMA crosses", REGISTRY, llm, compile_fn=_real_compile_fn, max_retries=3)

    # 最终产出的 loom 编译通过（自修复成功）
    compiled = _real_compile_fn(result["loom"])
    assert compiled.ok, [e.to_dict() for e in compiled.errors]

    # 恰好两次 LLM 调用：首次坏图、第二次修正图（≤3 重试）
    assert len(llm.calls) == 2

    # 关键自证：断言只针对**追加的编译反馈那条 user 消息**，不是整串 messages。
    # （system prompt 本就含 TYPE_MISMATCH/RiskGate/risk_stamped_signal 字样，对整串断言
    #  会永远为真、即便反馈路径被删也绿——那样测不出任何东西。M1 修复。）
    feedback_msgs = [
        m["content"] for m in llm.calls[1]["messages"]
        if m["role"] == "user" and "failed to compile" in m["content"]]
    assert feedback_msgs, "compiler feedback was not fed back into the second LLM call"
    feedback = feedback_msgs[-1]
    # 第二次调用真带了首次编译错误的 code + fix_hint —— 编译器报错真喂回 LLM
    assert "TYPE_MISMATCH" in feedback
    assert "RiskGate" in feedback or "risk_stamped_signal" in feedback

    # notes 记录了自修复轨迹（重试次数可见，provenance）
    assert result["notes"]


def test_compile_error_bypass_is_actually_rejected_by_real_compiler():
    """底座自证：绕风控的坏图确实被真 compile_blueprint 判 TYPE_MISMATCH（不是靠 mock）。"""
    compiled = _real_compile_fn(_BAD_BYPASS_LOOM)
    assert not compiled.ok
    codes = [e.code for e in compiled.errors]
    assert "TYPE_MISMATCH" in codes
    # 且 fix_hint 明确指向走 RiskGate（这就是喂回 LLM 的修复线索）
    hints = " ".join(e.fix_hint or "" for e in compiled.errors)
    assert "RiskGate" in hints or "risk_stamped_signal" in hints


def test_text_to_blueprint_gives_up_after_max_retries():
    """始终坏图 → 达到 max_retries 后放弃并抛错（不无限循环）；错误含最后编译报告。"""
    llm = _SequencedLLM([json.dumps(_BAD_BYPASS_LOOM)])  # 永远返回坏图
    with pytest.raises(bp_mod.BlueprintGenerationError) as ei:
        bp_mod.text_to_blueprint(
            "trade", REGISTRY, llm, compile_fn=_real_compile_fn, max_retries=3)
    # 最多 3 次尝试（不是无限）
    assert len(llm.calls) == 3
    assert "TYPE_MISMATCH" in str(ei.value)


# --------------------------------------------------------------------------- #
# 自动布局：每节点有 position，无重叠，进 meta.positions
# --------------------------------------------------------------------------- #
def test_layout_assigns_position_to_every_node_no_overlap():
    """按拓扑 order 分层分列，每节点一个唯一 position（无两节点重叠）。"""
    compiled = _real_compile_fn(_GOOD_LOOM)
    assert compiled.ok
    positions = layout_mod.layout(_GOOD_LOOM, compiled.order)
    node_ids = {n["id"] for n in _GOOD_LOOM["nodes"]}
    # 每个节点都有 position
    assert set(positions) == node_ids
    # 无重叠：所有 (x, y) 两两不同
    coords = [(p["x"], p["y"]) for p in positions.values()]
    assert len(coords) == len(set(coords)), "positions overlap"
    # 每个 position 是 {x, y} 数值
    for p in positions.values():
        assert isinstance(p["x"], (int, float)) and isinstance(p["y"], (int, float))


def test_layout_places_upstream_left_of_downstream():
    """分层布局：拓扑靠前的节点列号（x）不大于其下游（feed 在 exec 左侧）。"""
    compiled = _real_compile_fn(_GOOD_LOOM)
    positions = layout_mod.layout(_GOOD_LOOM, compiled.order)
    assert positions["feed"]["x"] < positions["exec"]["x"]
    assert positions["feed"]["x"] < positions["risk"]["x"]


def test_text_to_blueprint_writes_positions_into_meta():
    """text_to_blueprint 返回的 loom 经自动布局后 position 进 meta.positions（前端 loomToFlow 读它）。"""
    llm = _SequencedLLM([json.dumps(_GOOD_LOOM)])
    result = bp_mod.text_to_blueprint(
        "ema cross", REGISTRY, llm, compile_fn=_real_compile_fn)
    positions = result["loom"]["meta"]["positions"]
    node_ids = {n["id"] for n in result["loom"]["nodes"]}
    assert set(positions) == node_ids
    for p in positions.values():
        assert "x" in p and "y" in p


# --------------------------------------------------------------------------- #
# explain：自然语言解释图在干什么
# --------------------------------------------------------------------------- #
def test_explain_returns_non_empty_narrative():
    llm = _SequencedLLM(["This blueprint follows EMA crosses and gates every order through risk."])
    text = bp_mod.explain(_GOOD_LOOM, llm)
    assert isinstance(text, str) and text.strip()
    # 图内容进了 LLM 提示（不是空手解释）
    sent = json.dumps(llm.calls[0]["messages"])
    assert "cross_signal" in sent or "risk_gate" in sent


def test_explain_falls_back_to_non_empty_on_empty_llm_content():
    """LLM 空返回时 explain 仍产非空叙述（契约 "非空" 兜底，m1 修复）。"""
    for empty in ("", "   \n\t "):
        llm = _SequencedLLM([empty])
        text = bp_mod.explain(_GOOD_LOOM, llm)
        assert isinstance(text, str) and text.strip()


# --------------------------------------------------------------------------- #
# optimize：读回测报告 → 提出图变异 + diff
# --------------------------------------------------------------------------- #
def test_optimize_produces_diff_from_report():
    """optimize 读 report → LLM 产变异 loom → 返回 {loom, diff, notes}，diff 高亮改动。"""
    # 变异图：把 atr period 从 14 改成 20（一个节点 param 改动）
    mutated = json.loads(json.dumps(_GOOD_LOOM))
    for n in mutated["nodes"]:
        if n["id"] == "atr":
            n["params"]["period"] = 20
    llm = _SequencedLLM([json.dumps(mutated)])
    report = {"summary": {"num_trades": 3, "win_rate": 0.33, "total_return": -0.05}}
    result = bp_mod.optimize(_GOOD_LOOM, report, llm, compile_fn=_real_compile_fn)
    assert "loom" in result and "diff" in result and "notes" in result
    # 变异图仍能编译
    assert _real_compile_fn(result["loom"]).ok
    # diff 标出改动的节点（atr）
    diff = result["diff"]
    changed_ids = {c["id"] for c in diff.get("changed", [])}
    assert "atr" in changed_ids
    # report 进了 LLM 提示（optimize 真读了回测结果）
    sent = json.dumps(llm.calls[0]["messages"])
    assert "win_rate" in sent or "num_trades" in sent


# --------------------------------------------------------------------------- #
# 接缝：copilot 层自带 default_compile_fn（loom dict → loads_loom → compile_blueprint）
# 真实调用无需手拼 wrapper（Task 8 api 层可直接 text_to_blueprint(nl, defs, llm)）。
# --------------------------------------------------------------------------- #
def test_default_compile_fn_uses_real_compiler():
    """copilot 层自带的 default_compile_fn 对合法/非法图给出正确 ok 判定（用真编译器）。"""
    good = bp_mod.default_compile_fn(_GOOD_LOOM)
    assert good.ok and good.order
    bad = bp_mod.default_compile_fn(_BAD_BYPASS_LOOM)
    assert not bad.ok
    assert "TYPE_MISMATCH" in [e.code for e in bad.errors]


def test_default_compile_fn_wraps_malformed_json_as_compile_error():
    """畸形结构（缺 nodes）→ default_compile_fn 包成 not-ok 结果（喂回自修复循环，不炸）。"""
    result = bp_mod.default_compile_fn({"id": "x"})  # 无 nodes/edges
    assert not result.ok
    assert result.errors


def test_text_to_blueprint_works_without_explicit_compile_fn():
    """不传 compile_fn 时走 default_compile_fn（真编译器）——Task 8 api 层零拼装可用。"""
    llm = _SequencedLLM([json.dumps(_GOOD_LOOM)])
    result = bp_mod.text_to_blueprint("ema cross with risk gate", REGISTRY, llm)
    assert _real_compile_fn(result["loom"]).ok
    assert result["loom"]["meta"]["positions"]


def test_optimize_diff_detects_added_and_removed_nodes():
    """diff 检测新增/删除节点（前端 diff 预览的数据来源）。"""
    # 变异图：删掉 atr、加一个 rsi
    mutated = json.loads(json.dumps(_GOOD_LOOM))
    mutated["nodes"] = [n for n in mutated["nodes"] if n["id"] != "atr"]
    mutated["nodes"].append({"id": "rsi1", "type": "rsi", "params": {"period": 14}})
    diff = bp_mod.diff_blueprints(_GOOD_LOOM, mutated)
    added = {c["id"] for c in diff["added"]}
    removed = {c["id"] for c in diff["removed"]}
    assert "rsi1" in added
    assert "atr" in removed
