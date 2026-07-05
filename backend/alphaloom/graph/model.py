from __future__ import annotations
import json
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PortRef:
    node_id: str
    port: str

def _parse_ref(s: str) -> PortRef:
    if s.count(".") != 1:
        raise ValueError(f"bad port ref {s!r}: expected 'node.port'")
    n, p = s.split(".")
    if not n or not p:
        raise ValueError(f"bad port ref {s!r}: empty segment")
    return PortRef(n, p)

@dataclass(frozen=True)
class EdgeSpec:
    src: PortRef
    dst: PortRef
    feedback: bool = False

@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str
    params: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.id, self.type))

@dataclass
class BlueprintSpec:
    id: str
    name: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    meta: dict = field(default_factory=dict)

    def __eq__(self, other):
        return (isinstance(other, BlueprintSpec)
                and (self.id, self.name, self.nodes, self.edges, self.meta)
                == (other.id, other.name, other.nodes, other.edges, other.meta))

def loads_loom(text: str) -> BlueprintSpec:
    raw = json.loads(text)
    nodes = [NodeSpec(n["id"], n["type"], dict(n.get("params", {}))) for n in raw["nodes"]]
    edges = [EdgeSpec(_parse_ref(e["from"]), _parse_ref(e["to"]), bool(e.get("feedback", False)))
             for e in raw.get("edges", [])]
    return BlueprintSpec(raw["id"], raw.get("name", raw["id"]), nodes, edges, dict(raw.get("meta", {})))

def dumps_loom(bp: BlueprintSpec) -> str:
    return json.dumps({
        "id": bp.id, "name": bp.name,
        "nodes": [{"id": n.id, "type": n.type, "params": n.params} for n in bp.nodes],
        "edges": [{"from": f"{e.src.node_id}.{e.src.port}", "to": f"{e.dst.node_id}.{e.dst.port}",
                   **({"feedback": True} if e.feedback else {})} for e in bp.edges],
        "meta": bp.meta,
    }, ensure_ascii=False, indent=2)

def load_loom_file(path) -> BlueprintSpec:
    from pathlib import Path
    return loads_loom(Path(path).read_text(encoding="utf-8"))
