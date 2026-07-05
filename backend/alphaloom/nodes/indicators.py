from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

@node(type="ema", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class EmaNode:
    def setup(self, params):
        self.k = 2 / (int(params["period"]) + 1)
        self.ema = None
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        self.ema = c if self.ema is None else c * self.k + self.ema * (1 - self.k)
        return {"value": self.ema}

@node(type="atr", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class AtrNode:
    def setup(self, params):
        self.period = int(params["period"])
        self.prev_close = None
        self.atr = None
        self.warm = []
    def on_bar(self, ctx, inputs):
        c = inputs["candle"]
        h, l = float(c["high"]), float(c["low"])
        if self.prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        self.prev_close = float(c["close"])
        if self.atr is None:
            self.warm.append(tr)
            if len(self.warm) <= self.period:
                return {"value": None}
            self.atr = sum(self.warm) / len(self.warm)
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / self.period
        return {"value": self.atr}

@node(type="rsi", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class RsiNode:
    def setup(self, params):
        self.period = int(params["period"])
        self.prev = None
        self.avg_gain = None
        self.avg_loss = None
        self.warm_g, self.warm_l = [], []
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        if self.prev is None:
            self.prev = c
            return {"value": None}
        chg = c - self.prev
        self.prev = c
        g, l = max(chg, 0.0), max(-chg, 0.0)
        if self.avg_gain is None:
            self.warm_g.append(g); self.warm_l.append(l)
            if len(self.warm_g) < self.period:
                return {"value": None}
            self.avg_gain = sum(self.warm_g) / self.period
            self.avg_loss = sum(self.warm_l) / self.period
        else:
            self.avg_gain = (self.avg_gain * (self.period - 1) + g) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + l) / self.period
        if self.avg_loss == 0:
            return {"value": 100.0}
        rs = self.avg_gain / self.avg_loss
        return {"value": 100 - 100 / (1 + rs)}
