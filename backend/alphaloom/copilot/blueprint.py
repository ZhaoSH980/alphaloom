"""Copilot 蓝图生成/解释/优化（AlphaLoom D3 Task 6）。

招牌演示核心 —— text_to_blueprint 的自修复循环：
    LLM 生成 loom JSON → compile → ok 则自动布局返回；
    失败则把 CompileError（含 fix_hint）塞回提示，让 LLM 读着编译器反馈自己改，重试 ≤3 次。
这是 "Agent 在可验证环境自修复" 的自证：编译器（尤其类型系统的风控硬约束）报错真喂回
LLM，坏图（绕风控 TYPE_MISMATCH）→ CompileError → fix_hint 进提示 → 修正图 → ok。

接缝：真实调用时 compile_fn 传的是一个把 loom dict → BlueprintSpec → compile_blueprint 的
薄封装（见 tests / api 层）。生成的 loom 经 layout 后 position 进 meta.positions（前端读）。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from alphaloom.copilot import layout as _layout
from alphaloom.copilot import prompts as _prompts


class BlueprintGenerationError(Exception):
    """LLM 在 max_retries 内没能产出可编译的蓝图。message 含最后一轮编译错误。"""


def _content(response: dict) -> str:
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_json(text: str) -> dict | None:
    """提取文本里第一个平衡的 {...} 对象并解析（模型常在 JSON 外裹说明文字）。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def default_compile_fn(loom: dict):
    """copilot 层拥有的 loom dict → BlueprintSpec → compile_blueprint 转换（接缝所在）。

    真实调用（api 层 / 演示）可直接把它作为 compile_fn 传入，无需自己拼 loads_loom；
    测试则注入自己的 compile_fn（可复用真 compile_blueprint，见 test_copilot）。
    坏 JSON（loads_loom 抛错）包成一个 not-ok 结果，让自修复循环把解析错误也喂回 LLM。
    """
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.graph.errors import CompileError
    from alphaloom.graph.model import loads_loom

    class _Result:
        def __init__(self, ok, errors, order=()):
            self.ok = ok
            self.errors = list(errors)
            self.order = list(order)

    try:
        spec = loads_loom(json.dumps(loom))
    except Exception as exc:  # noqa: BLE001 — 任意结构错误都要喂回 LLM
        return _Result(False, [CompileError(
            "PARAM_INVALID", f"blueprint JSON is malformed: {exc}",
            fix_hint="Emit a loom object with 'nodes' (each with id/type/params) and 'edges'.")])
    return compile_blueprint(spec)


def _errors_to_feedback(errors) -> str:
    """把 CompileError 列表渲染成喂回 LLM 的结构化反馈（code + message + fix_hint）。"""
    lines = []
    for e in errors:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        parts = [f"[{d.get('code')}] {d.get('message')}"]
        if d.get("node_id"):
            parts.append(f"(node {d['node_id']})")
        if d.get("fix_hint"):
            parts.append(f"FIX: {d['fix_hint']}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def text_to_blueprint(
    nl: str,
    defs: Mapping[str, Any],
    llm,
    compile_fn: Callable[[dict], Any] | None = None,
    *,
    max_retries: int = 3,
    temperature: float = 0.2,
) -> dict:
    """自然语言 → 合法 loom。自修复循环：编译失败把 CompileError.fix_hint 喂回重试 ≤max_retries。

    返回 {"loom": <dict with meta.positions>, "notes": [<每轮轨迹>]}。
    始终失败 → BlueprintGenerationError（含最后一轮编译报告）。
    """
    if compile_fn is None:
        compile_fn = default_compile_fn
    system = _prompts.build_system_prompt(defs)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Build a .loom blueprint for: {nl}"},
    ]
    notes: list[str] = []
    last_feedback = ""

    for attempt in range(1, max_retries + 1):
        response = llm.chat(messages, temperature=temperature, max_tokens=4096)
        loom = _extract_json(_content(response))
        if loom is None:
            last_feedback = "Your reply was not a single JSON object. Reply with ONLY the loom JSON."
            notes.append(f"attempt {attempt}: reply was not valid JSON")
            messages.append({"role": "assistant", "content": _content(response)})
            messages.append({"role": "user", "content": last_feedback})
            continue

        result = compile_fn(loom)
        if getattr(result, "ok", False):
            positions = _layout.layout(loom, list(getattr(result, "order", [])))
            loom.setdefault("meta", {})
            loom["meta"]["positions"] = positions
            notes.append(f"attempt {attempt}: compiled OK ({len(loom['nodes'])} nodes)")
            return {"loom": loom, "notes": notes}

        # 编译失败：把结构化错误（含 fix_hint）喂回 LLM 让它自己改
        last_feedback = _errors_to_feedback(result.errors)
        notes.append(f"attempt {attempt}: compile failed -> {last_feedback}")
        messages.append({"role": "assistant", "content": json.dumps(loom, ensure_ascii=False)})
        messages.append({
            "role": "user",
            "content": (
                "The blueprint failed to compile with these errors. Fix EXACTLY these "
                f"and reply with ONLY the corrected loom JSON:\n{last_feedback}"),
        })

    raise BlueprintGenerationError(
        f"could not generate a compilable blueprint after {max_retries} attempts; "
        f"last compile errors:\n{last_feedback}")


def _fallback_summary(loom: dict) -> str:
    """LLM 空返回时的确定性兜底叙述（保证 explain 契约 "非空"）。"""
    types = [n.get("type", "?") for n in loom.get("nodes", [])]
    name = loom.get("name") or loom.get("id") or "blueprint"
    return (
        f"Blueprint '{name}' wires {len(types)} nodes "
        f"({', '.join(types) or 'none'}) into a trading pipeline; "
        "orders are gated through a RiskGate before execution.")


def explain(loom: dict, llm, *, temperature: float = 0.2) -> str:
    """自然语言解释图在干什么。返回**非空**叙述（LLM 空返回则确定性兜底）。"""
    messages = _prompts.build_explain_messages(loom)
    response = llm.chat(messages, temperature=temperature, max_tokens=1024)
    text = _content(response).strip()
    return text or _fallback_summary(loom)


def diff_blueprints(before: dict, after: dict) -> dict:
    """结构化 diff：新增/删除/改动节点 + 新增/删除边（前端 diff 预览的数据源）。

    改动节点 = 两图同 id 但 type 或 params 不同。边按 (from, to, feedback) 三元组比对。
    """
    before_nodes = {n["id"]: n for n in before.get("nodes", [])}
    after_nodes = {n["id"]: n for n in after.get("nodes", [])}

    added = [after_nodes[i] for i in after_nodes if i not in before_nodes]
    removed = [before_nodes[i] for i in before_nodes if i not in after_nodes]
    changed = []
    for i in before_nodes.keys() & after_nodes.keys():
        b, a = before_nodes[i], after_nodes[i]
        if b.get("type") != a.get("type") or b.get("params", {}) != a.get("params", {}):
            changed.append({"id": i, "before": b, "after": a})

    def _edge_key(e):
        return (e["from"], e["to"], bool(e.get("feedback", False)))

    before_edges = {_edge_key(e) for e in before.get("edges", [])}
    after_edges = {_edge_key(e) for e in after.get("edges", [])}
    added_edges = [
        {"from": f, "to": t, "feedback": fb}
        for (f, t, fb) in after_edges - before_edges]
    removed_edges = [
        {"from": f, "to": t, "feedback": fb}
        for (f, t, fb) in before_edges - after_edges]

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
    }


def optimize(
    loom: dict,
    report: dict,
    llm,
    compile_fn: Callable[[dict], Any] | None = None,
    *,
    defs: Mapping[str, Any] | None = None,
    max_retries: int = 3,
    temperature: float = 0.3,
) -> dict:
    """读回测报告 → LLM 提出图变异 → 返回 {loom, diff, notes}。

    变异图同样过编译（失败自修复重试，复用 text_to_blueprint 的反馈环），保证优化结果可运行。
    diff 相对原图高亮新增/删除/改动节点与边，供前端 diff 预览（用户点应用才落地）。
    """
    if compile_fn is None:
        compile_fn = default_compile_fn
    if defs is None:
        from alphaloom.nodes.registry import REGISTRY
        defs = REGISTRY

    messages = _prompts.build_optimize_messages(loom, report, defs)
    notes: list[str] = []
    last_feedback = ""

    for attempt in range(1, max_retries + 1):
        response = llm.chat(messages, temperature=temperature, max_tokens=4096)
        mutated = _extract_json(_content(response))
        if mutated is None:
            last_feedback = "Reply was not a single JSON object. Reply with ONLY the mutated loom JSON."
            notes.append(f"attempt {attempt}: reply was not valid JSON")
            messages.append({"role": "assistant", "content": _content(response)})
            messages.append({"role": "user", "content": last_feedback})
            continue

        result = compile_fn(mutated)
        if getattr(result, "ok", False):
            positions = _layout.layout(mutated, list(getattr(result, "order", [])))
            mutated.setdefault("meta", {})
            mutated["meta"]["positions"] = positions
            notes.append(f"attempt {attempt}: mutated blueprint compiled OK")
            return {"loom": mutated, "diff": diff_blueprints(loom, mutated), "notes": notes}

        last_feedback = _errors_to_feedback(result.errors)
        notes.append(f"attempt {attempt}: compile failed -> {last_feedback}")
        messages.append({"role": "assistant", "content": json.dumps(mutated, ensure_ascii=False)})
        messages.append({
            "role": "user",
            "content": (
                "The mutated blueprint failed to compile with these errors. Fix EXACTLY "
                f"these and reply with ONLY the corrected loom JSON:\n{last_feedback}"),
        })

    raise BlueprintGenerationError(
        f"optimize could not produce a compilable blueprint after {max_retries} attempts; "
        f"last compile errors:\n{last_feedback}")
