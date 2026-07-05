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
