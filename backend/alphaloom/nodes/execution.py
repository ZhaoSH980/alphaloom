from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node
from alphaloom.brokers.base import Order

@node(type="execute_order", category="execution",
      inputs={"signal": PinType.RISK_STAMPED_SIGNAL},
      outputs={"submitted": PinType.BOOL})
class ExecuteOrderNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        sig = inputs["signal"]
        broker = ctx.broker
        if sig is None or broker is None or broker.halted or sig["side"] == "hold":
            return {"submitted": False}
        cur = broker.position().qty
        target = {"long": sig["qty"], "short": -sig["qty"], "flat": 0.0}[sig["side"]]
        delta = target - cur
        if abs(delta) < 1e-12:
            return {"submitted": False}
        stop = sig.get("stop")
        mark = broker.last_price()
        # 防御：止损在错误一侧（多单止损>=现价 / 空单止损<=现价）会在成交当根立即触发，
        # 视为无效止损丢弃——典型场景：反手信号残留旧方向 stop
        if stop is not None and mark > 0:
            if sig["side"] == "long" and stop >= mark:
                stop = None
            elif sig["side"] == "short" and stop <= mark:
                stop = None
        ok = broker.submit(Order(side="buy" if delta > 0 else "sell",
                                 qty=abs(delta), stop=stop,
                                 tag=sig.get("reason", "")))
        return {"submitted": bool(ok)}
