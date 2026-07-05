from __future__ import annotations
from dataclasses import dataclass, field
from graphlib import TopologicalSorter, CycleError
from alphaloom.graph.model import BlueprintSpec
from alphaloom.graph.errors import CompileError
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import REGISTRY

@dataclass(frozen=True)
class InputBinding:
    dst_port: str
    src_node: str
    src_port: str
    feedback: bool

@dataclass
class CompileResult:
    ok: bool
    errors: list[CompileError]
    order: list[str] = field(default_factory=list)
    bindings: dict[str, list[InputBinding]] = field(default_factory=dict)
    certificate: object | None = None   # Task 6 填充
    nodes: dict = field(default_factory=dict)   # 展开后的 {node_id: NodeSpec}，runner 实例化用

_HINTS = {
    PinType.RISK_STAMPED_SIGNAL: (
        "This input only accepts risk_stamped_signal, which is produced solely by a "
        "RiskGate node. Route the signal through a RiskGate before this node."),
}

def compile_blueprint(bp: BlueprintSpec, *, bars_per_day: int = 1440) -> CompileResult:
    errors: list[CompileError] = []
    seen: set[str] = set()
    for n in bp.nodes:
        if n.id in seen:
            errors.append(CompileError("DUP_NODE_ID", f"duplicate node id {n.id!r}", node_id=n.id))
        seen.add(n.id)
        if n.type not in REGISTRY:
            errors.append(CompileError("UNKNOWN_NODE_TYPE", f"unknown node type {n.type!r}",
                                       node_id=n.id,
                                       fix_hint=f"Available types: {sorted(REGISTRY)[:20]}"))
    if errors:
        return CompileResult(False, errors)

    defs = {n.id: REGISTRY[n.type] for n in bp.nodes}
    bindings: dict[str, list[InputBinding]] = {n.id: [] for n in bp.nodes}
    for e in bp.edges:
        src_ok = e.src.node_id in defs and e.src.port in defs[e.src.node_id].outputs
        dst_ok = e.dst.node_id in defs and e.dst.port in defs[e.dst.node_id].inputs
        if not src_ok or not dst_ok:
            errors.append(CompileError(
                "BAD_PORT_REF",
                f"edge {e.src.node_id}.{e.src.port} -> {e.dst.node_id}.{e.dst.port} references unknown node/port",
                node_id=(e.src.node_id if not src_ok else e.dst.node_id)))
            continue
        t_out = defs[e.src.node_id].outputs[e.src.port]
        t_in = defs[e.dst.node_id].inputs[e.dst.port]
        if t_out is not t_in:
            errors.append(CompileError(
                "TYPE_MISMATCH",
                f"{e.dst.node_id}.{e.dst.port} expects {t_in.value}, got {t_out.value} "
                f"from {e.src.node_id}.{e.src.port}",
                node_id=e.dst.node_id, port=e.dst.port,
                fix_hint=_HINTS.get(t_in, f"Produce a {t_in.value} value upstream.")))
            continue
        bindings[e.dst.node_id].append(InputBinding(e.dst.port, e.src.node_id, e.src.port, e.feedback))
    if errors:
        return CompileResult(False, errors)

    deps = {n.id: {b.src_node for b in bindings[n.id] if not b.feedback} for n in bp.nodes}
    try:
        order = list(TopologicalSorter(deps).static_order())
    except CycleError as ce:
        return CompileResult(False, [CompileError(
            "ILLEGAL_CYCLE", f"cycle without feedback edge: {ce.args[1]}",
            fix_hint="Mark exactly the intentional back edge with \"feedback\": true; "
                     "feedback values are delivered on the NEXT bar.")])
    return CompileResult(True, [], order, bindings,
                         nodes={n.id: n for n in bp.nodes})
