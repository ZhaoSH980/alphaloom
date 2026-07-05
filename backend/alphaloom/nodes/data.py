from __future__ import annotations
from alphaloom.graph.types import PinType, Stamped
from alphaloom.nodes.registry import node

@node(type="candle_feed", category="data", inputs={},
      outputs={"out": PinType.CANDLE}, params={"inst": str, "bar": str})
class CandleFeedNode:
    def setup(self, params):
        self.inst = params.get("inst", "")
        self.bar = params.get("bar", "1m")
    def on_bar(self, ctx, inputs):
        ev = ctx.current_event
        # 浅拷贝：candle 值全是标量，防止下游节点原地篡改污染同波其他节点
        return {"out": Stamped(dict(ev.candle), ev.ts_close)}
