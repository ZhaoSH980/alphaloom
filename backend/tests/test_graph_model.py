import json
import pytest
from alphaloom.graph.types import PinType, Stamped, CostAnnotation
from alphaloom.graph.model import (
    PortRef, EdgeSpec, NodeSpec, BlueprintSpec, loads_loom, dumps_loom,
)

LOOM = {
    "id": "bp1", "name": "demo",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP"}},
        {"id": "ema1", "type": "ema", "params": {"period": 20}},
    ],
    "edges": [
        {"from": "feed.out", "to": "ema1.candle"},
        {"from": "ema1.value", "to": "feed.dummy", "feedback": True},
    ],
    "meta": {"author": "test"},
}

def test_roundtrip():
    bp = loads_loom(json.dumps(LOOM))
    assert bp.id == "bp1" and len(bp.nodes) == 2
    e0 = bp.edges[0]
    assert e0.src == PortRef("feed", "out") and e0.dst == PortRef("ema1", "candle")
    assert bp.edges[1].feedback is True and e0.feedback is False
    again = loads_loom(dumps_loom(bp))
    assert again == bp

def test_bad_port_ref_raises():
    bad = dict(LOOM, edges=[{"from": "no_dot", "to": "a.b"}])
    with pytest.raises(ValueError, match="port ref"):
        loads_loom(json.dumps(bad))

def test_stamped_and_cost_defaults():
    s = Stamped(42.0, as_of=1700000000000)
    assert s.value == 42.0 and s.as_of == 1700000000000
    c = CostAnnotation()
    assert c.llm_calls_per_bar == 0 and c.deterministic is True
    assert PinType.RISK_STAMPED_SIGNAL.value == "risk_stamped_signal"
