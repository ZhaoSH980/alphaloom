from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

@node(type="position_sizer", category="risk",
      inputs={"signal": PinType.SIGNAL, "candle": PinType.CANDLE},
      outputs={"sized": PinType.SIGNAL}, params={"risk_pct": float})
class PositionSizerNode:
    def setup(self, params):
        self.risk_pct = float(params.get("risk_pct", 0.02))
    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        if sig["side"] not in ("long", "short") or sig.get("stop") is None:
            return {"sized": sig}
        close = float(inputs["candle"]["close"])
        dist = abs(close - float(sig["stop"]))
        if dist <= 0:
            return {"sized": dict(sig, side="hold", reason="zero stop distance")}
        equity = ctx.broker.equity() if ctx.broker else 10_000.0
        if equity <= 0:
            return {"sized": dict(sig, side="hold", reason="non-positive equity")}
        sig["qty"] = equity * self.risk_pct / dist
        return {"sized": sig}
