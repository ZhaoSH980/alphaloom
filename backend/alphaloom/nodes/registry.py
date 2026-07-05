"""节点注册表（进程级单例 REGISTRY）。

**命名空间假设（单用户，D4 Carryover）**：``REGISTRY`` 是进程级全局字典，内置
节点在 import 期注册，自定义节点经 ``POST /api/nodes/custom`` 沙箱编译后**热注册
进同一进程级 REGISTRY**。因此**自定义节点跨请求、跨 ``create_app`` 实例、跨用户
可见**——A 用户注册的 ``custom_double`` 立即对 B 用户的 ``GET /api/nodes`` /
``/api/compile`` 可见，重名注册返回 422（``registry.node`` 抛 ValueError → 沙箱
``exec_error``，优雅但仍污染全局命名空间）。

AlphaLoom 当前定位是**单用户本地/演示部署**，此语义可接受且已被测试锁定
（见 ``tests/test_registry.py::test_registry_is_process_global_single_user``）。
**多用户生产部署需引入 session/租户命名空间**（按会话前缀注册、或每会话独立
REGISTRY 视图），并入 D4 Carryover（沙箱资源限额批次）。届时 ``compile_node_source``
与 ``/api/nodes/custom`` 应带 session 键，隔离各租户的自定义节点。
"""
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

# 进程级单例：跨请求/跨 create_app/跨用户共享（单用户假设，见模块 docstring）。
# 多用户部署需 session 命名空间（D4 Carryover）。
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
