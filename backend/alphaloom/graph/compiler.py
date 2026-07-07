from __future__ import annotations
from dataclasses import dataclass, field
from graphlib import TopologicalSorter, CycleError
from alphaloom.graph.model import BlueprintSpec, EdgeSpec, NodeSpec, PortRef, loads_loom
from alphaloom.graph.errors import CompileError
from alphaloom.graph.types import PinType
from alphaloom.graph.cost import build_certificate
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

_MAX_DEPTH = 8

def _parse_ref_str(s) -> PortRef:
    if not isinstance(s, str) or s.count(".") != 1:
        raise ValueError(f"expected 'node.port', got {s!r}")
    n, p = s.split(".")
    if not n or not p:
        raise ValueError(f"expected 'node.port', got {s!r}")
    return PortRef(n, p)

def _expand_subgraphs(bp, depth=0):
    if depth > _MAX_DEPTH:
        return None, [CompileError("PARAM_INVALID", f"subgraph nesting exceeds {_MAX_DEPTH}",
                                   fix_hint="Flatten your subgraph hierarchy.")]
    if not any(n.type == "subgraph" for n in bp.nodes):
        return bp, []
    import json as _json
    nodes, edges, errors = [], [], []
    in_map, out_map = {}, {}
    for n in bp.nodes:
        if n.type != "subgraph":
            nodes.append(n)
            continue
        try:
            inner = loads_loom(_json.dumps(n.params["blueprint"]))
        except Exception as exc:
            errors.append(CompileError("PARAM_INVALID", f"subgraph {n.id}: bad blueprint ({exc!r})",
                                       node_id=n.id))
            continue
        inner, sub_errs = _expand_subgraphs(inner, depth + 1)
        if sub_errs:
            errors.extend(sub_errs)
            continue
        pre = f"{n.id}/"
        nodes.extend(NodeSpec(pre + m.id, m.type, m.params) for m in inner.nodes)
        edges.extend(EdgeSpec(PortRef(pre + e.src.node_id, e.src.port),
                              PortRef(pre + e.dst.node_id, e.dst.port), e.feedback)
                     for e in inner.edges)
        try:
            for outer_port, ref in n.params.get("inputs", {}).items():
                r = _parse_ref_str(ref)
                in_map[PortRef(n.id, outer_port)] = PortRef(pre + r.node_id, r.port)
            for outer_port, ref in n.params.get("outputs", {}).items():
                r = _parse_ref_str(ref)
                out_map[PortRef(n.id, outer_port)] = PortRef(pre + r.node_id, r.port)
        except (ValueError, AttributeError, TypeError) as exc:
            errors.append(CompileError("PARAM_INVALID",
                                       f"subgraph {n.id}: bad port mapping ({exc})",
                                       node_id=n.id,
                                       fix_hint="inputs/outputs must map port names to "
                                                "'innerNode.port' strings."))
            continue
    if errors:
        return None, errors
    for e in bp.edges:
        edges.append(EdgeSpec(out_map.get(e.src, e.src), in_map.get(e.dst, e.dst), e.feedback))
    flat = BlueprintSpec(bp.id, bp.name, nodes, edges, bp.meta)
    return _expand_subgraphs(flat, depth + 1)

def compile_blueprint(bp: BlueprintSpec, *, bars_per_day: int = 1440) -> CompileResult:
    bp2, exp_errors = _expand_subgraphs(bp)
    if exp_errors:
        return CompileResult(False, exp_errors)
    bp = bp2
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
    taken: set[tuple[str, str]] = set()
    for e in bp.edges:
        src_ok = e.src.node_id in defs and e.src.port in defs[e.src.node_id].outputs
        dst_ok = e.dst.node_id in defs and e.dst.port in defs[e.dst.node_id].inputs
        if not src_ok or not dst_ok:
            bad_node = e.src.node_id if not src_ok else e.dst.node_id
            hint = None
            if bad_node in defs:
                d = defs[bad_node]
                hint = (f"Node {bad_node!r} has inputs {sorted(d.inputs)} "
                        f"and outputs {sorted(d.outputs)}.")
            errors.append(CompileError(
                "BAD_PORT_REF",
                f"edge {e.src.node_id}.{e.src.port} -> {e.dst.node_id}.{e.dst.port} references unknown node/port",
                node_id=bad_node, fix_hint=hint))
            continue
        key = (e.dst.node_id, e.dst.port)
        if key in taken:
            errors.append(CompileError(
                "DUP_INPUT", f"input port {e.dst.node_id}.{e.dst.port} is wired more than once",
                node_id=e.dst.node_id, port=e.dst.port,
                fix_hint="Each input port accepts exactly one incoming edge; remove the extra edge."))
            continue
        taken.add(key)
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
    for n in bp.nodes:
        optional = getattr(defs[n.id], "optional_inputs", frozenset())
        for port, pin in defs[n.id].inputs.items():
            if port in optional:
                continue
            if (n.id, port) not in taken:
                errors.append(CompileError(
                    "MISSING_INPUT",
                    f"input port {n.id}.{port} expects {pin.value} but is not wired",
                    node_id=n.id, port=port,
                    fix_hint="Connect this input to an upstream output of the same pin type."))
    if errors:
        return CompileResult(False, errors)

    deps = {n.id: {b.src_node for b in bindings[n.id] if not b.feedback} for n in bp.nodes}
    ts = TopologicalSorter(deps)
    try:
        ts.prepare()
    except CycleError as ce:
        return CompileResult(False, [CompileError(
            "ILLEGAL_CYCLE", f"cycle without feedback edge: {ce.args[1]}",
            fix_hint="Mark exactly the intentional back edge with \"feedback\": true; "
                     "feedback values are delivered on the NEXT bar.")])
    order: list[str] = []
    while ts.is_active():                 # 排序波次 Kahn：规范拓扑序
        ready = sorted(ts.get_ready())    # 跨进程/跨声明序确定（录制回放依赖）
        order.extend(ready)
        ts.done(*ready)
    cert = build_certificate([defs[nid] for nid in order], bars_per_day)
    return CompileResult(True, [], order, bindings, cert,
                         nodes={n.id: n for n in bp.nodes})
