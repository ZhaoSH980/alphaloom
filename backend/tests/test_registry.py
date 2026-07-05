import pytest
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node, get_node_def, create_instance, NodeDef, REGISTRY
from alphaloom.graph.model import NodeSpec

@node(type="t_add", category="test",
      inputs={"a": PinType.SERIES, "b": PinType.SERIES},
      outputs={"sum": PinType.SERIES},
      params={"scale": float},
      cost=CostAnnotation())
class AddNode:
    def setup(self, params):
        self.scale = params.get("scale", 1.0)
    def on_bar(self, ctx, inputs):
        return {"sum": (inputs["a"] + inputs["b"]) * self.scale}

def test_registered():
    d = get_node_def("t_add")
    assert isinstance(d, NodeDef) and d.cls is AddNode
    assert d.inputs["a"] is PinType.SERIES and d.outputs["sum"] is PinType.SERIES

def test_create_instance_runs_setup_and_state():
    inst = create_instance(NodeSpec("n1", "t_add", {"scale": 2.0}))
    assert inst.scale == 2.0 and inst.state == {}
    assert inst.on_bar(None, {"a": 1.0, "b": 2.0}) == {"sum": 6.0}

def test_unknown_type():
    with pytest.raises(KeyError):
        get_node_def("nope")

def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):
        node(type="t_add", category="test", inputs={}, outputs={})(AddNode)


def test_registry_is_process_global_single_user():
    """命名空间语义锁定（D4 Carryover 文档化）：REGISTRY 是进程级全局单例。

    自定义节点热注册后跨请求/跨 create_app 实例/跨用户可见——**单用户假设**。
    此测试锁定当前语义：沙箱编译注册一个节点后，它对同进程内任何 REGISTRY
    读者（含另起的 create_app）即刻可见，直到显式清理。多用户部署需 session
    命名空间（D4 Carryover），届时此测试须更新。
    """
    from alphaloom.sandbox.node_sandbox import compile_node_source
    from alphaloom.sandbox.errors import SandboxError

    type_name = "t_process_global_probe"
    src = f'''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{type_name}", category="indicator",
      inputs={{"candle": PinType.CANDLE}}, outputs={{"value": PinType.SERIES}},
      cost=CostAnnotation(deterministic=True))
class ProcessGlobalProbe:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {{"value": 1.0}}
'''
    assert type_name not in REGISTRY
    try:
        d = compile_node_source(src)
        assert not isinstance(d, SandboxError), getattr(d, "message", d)
        # 单用户假设：注册即对进程级 REGISTRY 的所有读者可见（跨请求/用户/app）。
        assert type_name in REGISTRY
        assert get_node_def(type_name).type == type_name
        # 重名再注册 → ValueError（进程级命名空间无 session 隔离，撞车但不静默覆盖）。
        with pytest.raises(ValueError, match="already registered"):
            node(type=type_name, category="indicator", inputs={}, outputs={})(AddNode)
    finally:
        REGISTRY.pop(type_name, None)
