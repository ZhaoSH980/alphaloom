from __future__ import annotations
from dataclasses import dataclass, field
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.graph.model import NodeSpec

@dataclass(frozen=True)
class NodeDef:
    type: str
    category: str
    cls: type
    inputs: dict[str, PinType]
    outputs: dict[str, PinType]
    params: dict[str, type] = field(default_factory=dict)
    cost: CostAnnotation = CostAnnotation()

REGISTRY: dict[str, NodeDef] = {}

def node(*, type: str, category: str, inputs: dict, outputs: dict,
         params: dict | None = None, cost: CostAnnotation = CostAnnotation()):
    def deco(cls):
        if type in REGISTRY:
            raise ValueError(f"node type {type!r} already registered")
        REGISTRY[type] = NodeDef(type, category, cls, dict(inputs), dict(outputs),
                                 dict(params or {}), cost)
        cls.node_type = type
        return cls
    return deco

def get_node_def(t: str) -> NodeDef:
    return REGISTRY[t]

def create_instance(spec: NodeSpec):
    d = get_node_def(spec.type)
    inst = d.cls()
    inst.state = {}
    inst.node_id = spec.id
    inst.def_ = d
    inst.setup(dict(spec.params))
    return inst
