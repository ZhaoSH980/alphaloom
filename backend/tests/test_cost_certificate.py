import json
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

import tests.test_compiler_typecheck  # noqa: F401

@node(type="tc_llm", category="test",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(llm_calls_per_bar=2, max_tokens_per_call=4000,
                          latency_class="llm", deterministic=False))
class TcLlm:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

def _compile(node_type):
    return compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "brain", "type": node_type},
            {"id": "risk", "type": "tc_riskgate"},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "brain.candle"},
            {"from": "brain.signal", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "ex.signal"},
        ],
    })), bars_per_day=1440)

def test_deterministic_graph_certificate():
    c = _compile("tc_brain").certificate
    assert c.llm_calls_per_bar == 0 and c.daily_token_ceiling == 0
    assert c.worst_latency_class == "fast" and c.deterministic_ratio == 1.0

def test_llm_graph_certificate():
    c = _compile("tc_llm").certificate
    assert c.llm_calls_per_bar == 2
    assert c.daily_token_ceiling == 2 * 4000 * 1440
    assert c.worst_latency_class == "llm"
    assert 0 < c.deterministic_ratio < 1
    d = c.to_dict()
    json.dumps(d)
    assert d["llm_calls_per_bar"] == 2
