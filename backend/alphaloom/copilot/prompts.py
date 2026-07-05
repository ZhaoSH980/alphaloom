"""Copilot 系统提示词构建（AlphaLoom D3 Task 6）。

`build_system_prompt(registry)` 动态从 REGISTRY 生成**全 NodeDef 目录**（每个节点的
类型/类别/输入引脚+类型/输出引脚+类型/参数），拼上 .loom JSON schema 说明，以及硬约束
"下单必须过 RiskGate"——这是招牌卖点：即便 LLM 也造不出绕过风控的图，因为
``execute_order`` 的输入只吃 ``risk_stamped_signal``，而全宇宙唯一能产它的是 RiskGate。
编译器的类型系统就是合规官，绕风控的图会被 TYPE_MISMATCH 拦下并把 fix_hint 喂回 LLM。
"""
from __future__ import annotations

from typing import Mapping


def _format_pins(pins) -> str:
    """{'signal': PinType.SIGNAL} -> 'signal:signal, candle:candle'（类型取 .value）。"""
    if not pins:
        return "(none)"
    return ", ".join(f"{name}:{ptype.value}" for name, ptype in pins.items())


def _format_node_line(node_def) -> str:
    params = ", ".join(sorted(node_def.params)) if node_def.params else "(none)"
    cost = node_def.cost
    return (
        f"- {node_def.type} [{node_def.category}] "
        f"inputs={{{_format_pins(node_def.inputs)}}} "
        f"outputs={{{_format_pins(node_def.outputs)}}} "
        f"params=[{params}] "
        f"llm_calls_per_bar={cost.llm_calls_per_bar}"
    )


def build_node_catalog(registry: Mapping[str, object]) -> str:
    """从 REGISTRY 动态生成节点清单（类型 + 引脚 + 类型 + 参数）。"""
    lines = [_format_node_line(registry[t]) for t in sorted(registry)]
    return "\n".join(lines)


_SCHEMA = """\
A .loom blueprint is a JSON object with this exact shape:
{
  "id": "<slug>",
  "name": "<human name>",
  "nodes": [{"id": "<unique>", "type": "<one of the catalog types>", "params": {...}}],
  "edges": [{"from": "<nodeId>.<outputPin>", "to": "<nodeId>.<inputPin>", "feedback": false}],
  "meta": {}
}
Every edge's "from" is a node's OUTPUT pin and "to" is a node's INPUT pin, written as
"nodeId.pinName". Types on the two ends of an edge MUST match exactly (see the catalog).
Set "feedback": true on exactly one intentional back-edge if you build a cycle."""


_RISK_CONSTRAINT = """\
HARD CONSTRAINT — orders must pass the RiskGate:
The execute_order node's "signal" input only accepts the type "risk_stamped_signal".
The ONLY node in the entire universe that produces "risk_stamped_signal" is the
risk_gate node (RiskGate). Therefore EVERY order-executing path MUST route the trading
signal through a risk_gate node before reaching execute_order. You CANNOT wire a plain
"signal" straight into execute_order — the compiler's type system is the compliance
officer and will reject it with a TYPE_MISMATCH error. Typical order path:
  ...decision(signal) -> position_sizer(signal->sized) -> risk_gate(signal->stamped)
  -> execute_order(signal)."""


_INSTRUCTIONS = """\
You translate a user's plain-language strategy description into a valid .loom blueprint.
Reply with ONLY the JSON object — no markdown fences, no prose before or after.
Use only node types from the catalog below. If a previous attempt failed to compile, you
will be given the compiler errors (with fix hints); fix exactly those and try again."""


def build_system_prompt(registry: Mapping[str, object]) -> str:
    """完整系统提示：指令 + 全节点目录 + loom schema + 风控硬约束。"""
    catalog = build_node_catalog(registry)
    return (
        f"{_INSTRUCTIONS}\n\n"
        f"NODE CATALOG (type [category] inputs/outputs/params):\n{catalog}\n\n"
        f"{_SCHEMA}\n\n"
        f"{_RISK_CONSTRAINT}\n"
    )


_EXPLAIN_SYSTEM = """\
You are a trading-systems tutor. Given a .loom blueprint JSON, explain in plain English
what the strategy does: the data it reads, the indicators, the decision logic, how risk is
enforced, and how orders are executed. Be concise (a short paragraph). Plain prose only."""


def build_explain_messages(loom: dict) -> list[dict]:
    import json

    return [
        {"role": "system", "content": _EXPLAIN_SYSTEM},
        {"role": "user", "content": json.dumps(loom, ensure_ascii=False, sort_keys=True)},
    ]


_OPTIMIZE_SYSTEM = """\
You improve a trading strategy. You are given the current .loom blueprint JSON and its
backtest report (summary metrics like num_trades, win_rate, total_return). Propose a
mutated blueprint that plausibly improves the metrics — tweak params, add/remove/rewire
nodes. Keep the same .loom schema and obey the HARD CONSTRAINT: orders must still pass a
risk_gate before execute_order. Reply with ONLY the mutated blueprint JSON object."""


def build_optimize_messages(loom: dict, report: dict, registry: Mapping[str, object]) -> list[dict]:
    import json

    catalog = build_node_catalog(registry)
    user = json.dumps(
        {"blueprint": loom, "backtest_report": report}, ensure_ascii=False, sort_keys=True)
    return [
        {"role": "system",
         "content": f"{_OPTIMIZE_SYSTEM}\n\nNODE CATALOG:\n{catalog}\n\n{_RISK_CONSTRAINT}"},
        {"role": "user", "content": user},
    ]
