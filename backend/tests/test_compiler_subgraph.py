import json
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

import tests.test_compiler_typecheck  # noqa: F401  确保 tc_* 已注册

@node(type="tc_loop", category="test",
      inputs={"sig_in": PinType.SIGNAL}, outputs={"candle_out": PinType.CANDLE})
class TcLoop:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

INNER = {
    "id": "inner", "name": "inner",
    "nodes": [
        {"id": "brain", "type": "tc_brain"},
        {"id": "risk", "type": "tc_riskgate"},
    ],
    "edges": [{"from": "brain.signal", "to": "risk.signal"}],
}

def test_subgraph_expansion_and_typecheck():
    outer = {
        "id": "outer", "name": "outer",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "sub", "type": "subgraph", "params": {
                "blueprint": INNER,
                "inputs": {"candle_in": "brain.candle"},
                "outputs": {"stamped_out": "risk.stamped"},
            }},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "sub.candle_in"},
            {"from": "sub.stamped_out", "to": "ex.signal"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(outer)))
    assert r.ok, [e.to_dict() for e in r.errors]
    assert "sub/brain" in r.order and "sub/risk" in r.order
    assert r.order.index("feed") < r.order.index("sub/brain") < r.order.index("sub/risk") < r.order.index("ex")
    b = {x.dst_port: x for x in r.bindings["ex"]}
    assert b["signal"].src_node == "sub/risk"

def test_subgraph_cannot_bypass_risk_type():
    outer = {
        "id": "outer", "name": "outer",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "sub", "type": "subgraph", "params": {
                "blueprint": INNER,
                "inputs": {"candle_in": "brain.candle"},
                "outputs": {"raw_out": "brain.signal"},
            }},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "sub.candle_in"},
            {"from": "sub.raw_out", "to": "ex.signal"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(outer)))
    assert not r.ok and any(e.code == "TYPE_MISMATCH" for e in r.errors)

def test_feedback_cycle_legal_and_illegal():
    base = {
        "id": "c", "name": "c",
        "nodes": [{"id": "a", "type": "tc_brain"}, {"id": "b", "type": "tc_loop"}],
        "edges": [
            {"from": "a.signal", "to": "b.sig_in"},
            {"from": "b.candle_out", "to": "a.candle"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(base)))
    assert not r.ok and r.errors[0].code == "ILLEGAL_CYCLE"
    assert "feedback" in r.errors[0].fix_hint
    base["edges"][1]["feedback"] = True
    r2 = compile_blueprint(loads_loom(json.dumps(base)))
    assert r2.ok
    fb = [x for x in r2.bindings["a"] if x.dst_port == "candle"][0]
    assert fb.feedback is True

def test_nesting_depth_limit():
    bp = INNER
    for i in range(9):
        bp = {"id": f"w{i}", "name": f"w{i}",
              "nodes": [{"id": "s", "type": "subgraph",
                         "params": {"blueprint": bp, "inputs": {}, "outputs": {}}}],
              "edges": []}
    r = compile_blueprint(loads_loom(json.dumps(bp)))
    assert not r.ok and any(e.code == "PARAM_INVALID" for e in r.errors)
