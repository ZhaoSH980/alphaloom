# backend/tests/test_engine.py
import json
import pytest
from alphaloom.graph.types import PinType, Stamped
from alphaloom.nodes.registry import node, create_instance
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.context import SimClock, RunContext, CausalityError
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.recorder import Recorder

@node(type="te_src", category="test", inputs={}, outputs={"v": PinType.SERIES})
class TeSrc:
    def setup(self, params): self.i = 0
    def on_bar(self, ctx, inputs):
        self.i += 1
        return {"v": float(self.i)}

@node(type="te_double", category="test",
      inputs={"x": PinType.SERIES}, outputs={"y": PinType.SERIES})
class TeDouble:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {"y": inputs["x"] * 2}

@node(type="te_echo_prev", category="test",
      inputs={"cur": PinType.SERIES, "prev": PinType.SERIES},
      outputs={"out": PinType.SERIES})
class TeEchoPrev:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs):
        return {"out": inputs["cur"] if inputs["prev"] is None else inputs["prev"]}

@node(type="te_evil", category="test", inputs={}, outputs={"v": PinType.SERIES})
class TeEvil:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs):
        return {"v": Stamped(99.0, as_of=ctx.clock.now + 999_999)}

def _mk(bp_json):
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    instances = {n.id: create_instance(n) for n in bp.nodes}
    return compiled, instances

def _events(n):
    return [BarEvent({"ts": i * 60_000, "open": 1, "high": 1, "low": 1,
                      "close": 1, "volume": 1}, 60_000) for i in range(n)]

LINEAR = {"id": "l", "name": "l",
          "nodes": [{"id": "s", "type": "te_src"}, {"id": "d", "type": "te_double"}],
          "edges": [{"from": "s.v", "to": "d.x"}]}

def test_linear_dataflow(tmp_path):
    compiled, inst = _mk(LINEAR)
    rec = Recorder(tmp_path / "rec.sqlite")
    ctx = RunContext(clock=SimClock(), run_id="r1", recorder=rec)
    Engine(compiled, inst, ctx).run(_events(3))
    rows = rec.fetch("r1", node_id="d")
    outs = [json.loads(r["outputs_json"])["y"]["value"] for r in rows]
    assert outs == [2.0, 4.0, 6.0]

def test_feedback_edge_prev_wave():
    bp = {"id": "f", "name": "f",
          "nodes": [{"id": "s", "type": "te_src"}, {"id": "e", "type": "te_echo_prev"}],
          "edges": [{"from": "s.v", "to": "e.cur"},
                    {"from": "s.v", "to": "e.prev", "feedback": True}]}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r2"))
    seen = []
    eng.after_node = lambda nid, outs: seen.append(outs["out"].value) if nid == "e" else None
    eng.run(_events(3))
    assert seen == [1.0, 1.0, 2.0]

def test_breakpoint_callback():
    compiled, inst = _mk(LINEAR)
    hits = []
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r3"),
                 breakpoints={"d"},
                 on_pause=lambda nid, ev, inputs: hits.append((nid, inputs["x"])))
    eng.run(_events(2))
    assert hits == [("d", 1.0), ("d", 2.0)]

def test_causality_guard_kills_run():
    bp = {"id": "e", "name": "e", "nodes": [{"id": "bad", "type": "te_evil"}], "edges": []}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r4"))
    with pytest.raises(CausalityError):
        eng.run(_events(1))

def test_recorder_row_count(tmp_path):
    compiled, inst = _mk(LINEAR)
    rec = Recorder(tmp_path / "rec.sqlite")
    Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r5", recorder=rec)).run(_events(4))
    assert len(rec.fetch("r5")) == 4 * 2
