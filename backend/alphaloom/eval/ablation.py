"""委员会消融实验 —— 护栏价值量化（D4-T4，spec §7 / D3 Carryover #1）。

三臂**由图变换生成**（消融 = 可编程的图手术，不是三份手写蓝图——这是卖点）：

- **full**：原蓝图原样（深拷贝，跑臂不污染原图）。
- **no_risk_officer**：参数手术——committee 节点加 ``skip_risk_officer=True``，
  跳过 LLM 风控官角色（"软护栏"）。**RiskGate 节点保留**：它是全宇宙唯一能产出
  ``risk_stamped_signal`` 的节点，``execute_order`` 只接受该类型——想把它也消融，
  编译器直接 TYPE_MISMATCH 拒绝（``graph_bypass`` 不做好心拦截，交给类型系统
  裁决；测试锁定）。**消融能拆的只有 LLM 软护栏；类型系统硬护栏不可消融——
  这是卖点，不是缺陷。**
- **no_rag**：图手术——旁路全部 ``require_citations``（其 signal 输入的上游源
  直连原下游），再移除 ``knowledge_retrieve`` 子链。委员会信号不再有知识库
  背书门槛（require_citations 是 SIGNAL→SIGNAL 透传门，旁路后类型仍闭合，
  编译照过——软约定 vs 硬类型的差别在此显形）。

每臂同数据同窗口 ``run_backtest``，经 ``breakpoints="all"`` 的 on_pause 旁路
收集运行期证据（零改 runner）：

- **num_vetoes**：下游节点输入 signal 里 ``committee_trace`` 含 ``veto=true``
  的 bar 数（按 bar 去重——同一信号扇出到多个下游只计一次）。依赖 committee
  输出至少接一个下游节点（真实蓝图恒成立；输出悬空的委员会本来就没有可
  消融的下游效应）。
- **verdict_counts**：反思四象限计数（reflector verdict 载荷按 trade_key 去重；
  蓝图无反思链则为空 dict）。

``guardrail_value`` 是 full 与 no_risk_officer 两臂的验证窗指标差——**纯计算，
正负都如实**。合成剧本里风控官拦下暴跌前的危险提案 → 正值；若真实录制里
风控官净拦盈利交易 → 负值原样展示（测试含反向剧本锁定此诚实性）。

零配额：本模块不创建任何 LLM 客户端——``llm`` 由调用方注入（测试 = 确定性
fake transport；演示 = 录制回放）。规模锁定：默认 3 臂 × 同一窗口。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from alphaloom.backtest.runner import run_backtest
from alphaloom.eval.scorecard import _json_safe
from alphaloom.graph.model import BlueprintSpec, EdgeSpec, NodeSpec, PortRef

DEFAULT_ARMS = ("full", "no_risk_officer", "no_rag")


# --------------------------------------------------------------------------- #
# 图手术原语
# --------------------------------------------------------------------------- #
def graph_bypass(bp: BlueprintSpec, *, node_type: str,
                 in_port: str, out_port: str) -> BlueprintSpec:
    """旁路手术：把所有 ``node_type`` 节点从图上拆下——其 ``out_port`` 的每个
    消费者改接该节点 ``in_port`` 的上游源，节点及其余边一并移除。

    **故意不做类型检查**——交给编译器。这正是硬护栏卖点的机制：旁路
    require_citations（SIGNAL→SIGNAL 透传门）后图仍类型闭合；旁路 risk_gate
    （SIGNAL→RISK_STAMPED_SIGNAL 换类型门）后 execute_order 拿到裸 SIGNAL，
    编译必 TYPE_MISMATCH。软护栏可消融、硬护栏不可消融，由类型系统裁决。
    """
    targets = {n.id for n in bp.nodes if n.type == node_type}
    if not targets:
        raise ValueError(f"no {node_type!r} node in blueprint {bp.id!r} to bypass")
    upstream: dict[str, PortRef] = {}
    for e in bp.edges:
        if e.dst.node_id in targets and e.dst.port == in_port:
            upstream[e.dst.node_id] = e.src
    edges: list[EdgeSpec] = []
    for e in bp.edges:
        from_target = e.src.node_id in targets
        to_target = e.dst.node_id in targets
        if (from_target and not to_target and e.src.port == out_port
                and e.src.node_id in upstream):
            edges.append(EdgeSpec(upstream[e.src.node_id], e.dst, e.feedback))
        elif from_target or to_target:
            continue                       # 目标节点的其余边随节点一并移除
        else:
            edges.append(e)
    nodes = [n for n in bp.nodes if n.id not in targets]
    return BlueprintSpec(bp.id, bp.name, nodes, edges, dict(bp.meta))


def _drop_nodes(bp: BlueprintSpec, node_type: str) -> BlueprintSpec:
    """移除全部 ``node_type`` 节点与触及它们的边（悬空边一并清除）。"""
    victims = {n.id for n in bp.nodes if n.type == node_type}
    return BlueprintSpec(
        bp.id, bp.name,
        [n for n in bp.nodes if n.id not in victims],
        [e for e in bp.edges
         if e.src.node_id not in victims and e.dst.node_id not in victims],
        dict(bp.meta))


def _copy_nodes(nodes) -> list[NodeSpec]:
    return [NodeSpec(n.id, n.type, dict(n.params)) for n in nodes]


def arm_blueprint(bp: BlueprintSpec, arm: str) -> BlueprintSpec:
    """按臂名做图变换，返回独立蓝图副本（绝不就地改 ``bp``）。"""
    if arm == "full":
        return BlueprintSpec(bp.id, bp.name, _copy_nodes(bp.nodes),
                             list(bp.edges), dict(bp.meta))
    if arm == "no_risk_officer":
        if not any(n.type == "committee" for n in bp.nodes):
            raise ValueError(
                f"blueprint {bp.id!r} has no committee node to ablate")
        nodes = [NodeSpec(n.id, n.type, dict(n.params, skip_risk_officer=True))
                 if n.type == "committee" else NodeSpec(n.id, n.type, dict(n.params))
                 for n in bp.nodes]
        return BlueprintSpec(f"{bp.id}__no_risk_officer",
                             f"{bp.name} (no risk officer)",
                             nodes, list(bp.edges), dict(bp.meta))
    if arm == "no_rag":
        if not any(n.type == "require_citations" for n in bp.nodes):
            raise ValueError(
                f"blueprint {bp.id!r} has no require_citations node to ablate "
                "(no RAG chain)")
        out = graph_bypass(bp, node_type="require_citations",
                           in_port="signal", out_port="signal")
        out = _drop_nodes(out, "knowledge_retrieve")
        return BlueprintSpec(f"{bp.id}__no_rag", f"{bp.name} (no RAG)",
                             _copy_nodes(out.nodes), out.edges, out.meta)
    raise ValueError(f"unknown ablation arm {arm!r}; expected one of {DEFAULT_ARMS}")


# --------------------------------------------------------------------------- #
# 运行期证据旁路收集（on_pause 钩子，零改 runner）
# --------------------------------------------------------------------------- #
class _ArmCollector:
    """从各节点输入里旁路收集 veto bar（去重）与反思四象限 verdict（按 trade_key 去重）。"""

    def __init__(self):
        self.veto_bars: set[int] = set()
        self.verdicts: dict[str, str] = {}

    def on_pause(self, node_id, ev, raw_inputs) -> None:
        for val in raw_inputs.values():
            if not isinstance(val, dict):
                continue
            trace = val.get("committee_trace")
            if isinstance(trace, (list, tuple)) and any(
                    isinstance(t, dict) and t.get("veto") is True for t in trace):
                self.veto_bars.add(ev.ts_open)
            if "trade_key" in val and "verdict" in val:
                self.verdicts[str(val["trade_key"])] = str(val["verdict"])


# --------------------------------------------------------------------------- #
# 报告结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArmResult:
    arm: str
    run_id: str
    blueprint_id: str
    bars: int
    summary: dict
    num_vetoes: int
    num_trades: int
    verdict_counts: dict

    def to_dict(self) -> dict:
        return _json_safe({
            "arm": self.arm,
            "run_id": self.run_id,
            "blueprint_id": self.blueprint_id,
            "bars": self.bars,
            "summary": self.summary,
            "num_vetoes": self.num_vetoes,
            "num_trades": self.num_trades,
            "verdict_counts": self.verdict_counts,
        })


@dataclass(frozen=True)
class AblationReport:
    arms: list[ArmResult] = field(default_factory=list)
    guardrail_value: dict | None = None   # full vs no_risk_officer 指标差（缺对照臂 = None）

    def to_dict(self) -> dict:
        return _json_safe({
            "arms": [a.to_dict() for a in self.arms],
            "guardrail_value": self.guardrail_value,
        })


def _guardrail_value(by_arm: dict[str, ArmResult]) -> dict | None:
    """full − no_risk_officer 的指标差摘要。**纯计算，正负都如实**：
    delta > 0 = 风控官净避免了亏损（护栏有正价值）；delta < 0 = 风控官净拦掉了
    盈利交易（护栏在该窗口是负资产）——两种结果都原样报告，不做任何美化。"""
    full = by_arm.get("full")
    ablated = by_arm.get("no_risk_officer")
    if full is None or ablated is None:
        return None
    f, a = full.summary, ablated.summary
    delta = round(float(f.get("net_pnl", 0.0)) - float(a.get("net_pnl", 0.0)), 8)
    return {
        "arms_compared": ["full", "no_risk_officer"],
        "net_pnl_full": f.get("net_pnl", 0.0),
        "net_pnl_no_risk_officer": a.get("net_pnl", 0.0),
        "net_pnl_delta": delta,
        "return_pct_delta": round(float(f.get("return_pct", 0.0))
                                  - float(a.get("return_pct", 0.0)), 6),
        "max_drawdown_delta": round(float(f.get("max_drawdown", 0.0))
                                    - float(a.get("max_drawdown", 0.0)), 6),
        "num_vetoes_full": full.num_vetoes,
        "num_trades_full": full.num_trades,
        "num_trades_no_risk_officer": ablated.num_trades,
        "guardrail_helped": bool(delta > 0),
        "note": ("computed from paired runs on the same data/window; the sign is "
                 "whatever the data says - a negative delta (risk officer "
                 "net-blocked winners) is reported as-is"),
    }


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def committee_ablation(base_blueprint: BlueprintSpec, source, *, inst: str, bar: str,
                       start_ms: int | None = None, end_ms: int | None = None,
                       llm=None, arms=None, initial_cash: float = 10_000.0,
                       fee_rate: float = 0.0005) -> AblationReport:
    """三臂消融：同数据同窗口跑对照组，量化 LLM 护栏价值。

    ``arms`` 默认 :data:`DEFAULT_ARMS` 全三臂；可传子集（如
    ``("full", "no_risk_officer")``）。``llm`` 由调用方注入（录制回放 /
    fake transport）——本函数自身零配额、不建任何网络客户端。
    """
    names = list(arms) if arms is not None else list(DEFAULT_ARMS)
    for name in names:
        if name not in DEFAULT_ARMS:
            raise ValueError(
                f"unknown ablation arm {name!r}; expected a subset of {DEFAULT_ARMS}")
    results: list[ArmResult] = []
    for name in names:
        abp = arm_blueprint(base_blueprint, name)
        collector = _ArmCollector()
        report = run_backtest(abp, source, inst=inst, bar=bar,
                              start_ms=start_ms, end_ms=end_ms,
                              initial_cash=initial_cash, fee_rate=fee_rate,
                              llm=llm, breakpoints="all",
                              on_pause=collector.on_pause)
        results.append(ArmResult(
            arm=name,
            run_id=report.run_id,
            blueprint_id=report.blueprint_id,
            bars=report.bars,
            summary=dict(report.summary),
            num_vetoes=len(collector.veto_bars),
            num_trades=int(report.summary.get("num_trades", 0)),
            verdict_counts=dict(Counter(collector.verdicts.values())),
        ))
    return AblationReport(
        arms=results,
        guardrail_value=_guardrail_value({r.arm: r for r in results}))
