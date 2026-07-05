import json
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

# 测试专用最小节点集（与内置节点解耦，前缀 tc_）
@node(type="tc_feed", category="test", inputs={}, outputs={"out": PinType.CANDLE})
class TcFeed:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_brain", category="test",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL})
class TcBrain:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_riskgate", category="test",
      inputs={"signal": PinType.SIGNAL}, outputs={"stamped": PinType.RISK_STAMPED_SIGNAL})
class TcRisk:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_exec", category="test",
      inputs={"signal": PinType.RISK_STAMPED_SIGNAL}, outputs={})
class TcExec:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

def _bp(edges):
    return loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "brain", "type": "tc_brain"},
            {"id": "risk", "type": "tc_riskgate"},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": edges,
    }))

GOOD = [
    {"from": "feed.out", "to": "brain.candle"},
    {"from": "brain.signal", "to": "risk.signal"},
    {"from": "risk.stamped", "to": "ex.signal"},
]

def test_good_graph_compiles_with_topo_order():
    r = compile_blueprint(_bp(GOOD))
    assert r.ok and r.errors == []
    assert r.order.index("feed") < r.order.index("brain") < r.order.index("risk") < r.order.index("ex")
    b = {x.dst_port: x for x in r.bindings["ex"]}
    assert b["signal"].src_node == "risk" and b["signal"].feedback is False

def test_bypassing_riskgate_is_type_error():
    bad = [
        {"from": "feed.out", "to": "brain.candle"},
        {"from": "brain.signal", "to": "ex.signal"},   # 直连下单 → 必须编译失败
    ]
    r = compile_blueprint(_bp(bad))
    assert not r.ok
    err = [e for e in r.errors if e.code == "TYPE_MISMATCH"][0]
    assert err.node_id == "ex" and err.port == "signal"
    assert "RiskGate" in err.fix_hint  # 报错为 LLM 消费设计

def test_unknown_node_and_bad_port():
    r = compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [{"id": "a", "type": "no_such"}],
        "edges": [{"from": "a.x", "to": "a.y"}],
    })))
    codes = {e.code for e in r.errors}
    assert "UNKNOWN_NODE_TYPE" in codes

def test_duplicate_node_id():
    r = compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [{"id": "a", "type": "tc_feed"}, {"id": "a", "type": "tc_feed"}],
        "edges": [],
    })))
    assert any(e.code == "DUP_NODE_ID" for e in r.errors)

def test_error_json_serializable():
    r = compile_blueprint(_bp([{"from": "brain.signal", "to": "ex.signal"}]))
    d = r.errors[0].to_dict()
    assert set(d) == {"code", "message", "node_id", "port", "fix_hint"}
    json.dumps(d)  # 不抛即通过
