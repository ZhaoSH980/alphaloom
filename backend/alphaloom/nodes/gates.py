from __future__ import annotations
from collections import deque
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

def _sig(side="hold", qty=0.0, stop=None, reason=""):
    return {"side": side, "qty": qty, "stop": stop, "reason": reason}

@node(type="cross_signal", category="decision",
      inputs={"fast": PinType.SERIES, "slow": PinType.SERIES,
              "candle": PinType.CANDLE, "atr": PinType.SERIES},
      outputs={"signal": PinType.SIGNAL}, params={"atr_mult": float})
class CrossSignalNode:
    def setup(self, params):
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.prev_diff = None
    def on_bar(self, ctx, inputs):
        fast, slow, atr = inputs["fast"], inputs["slow"], inputs["atr"]
        candle = inputs["candle"]
        if fast is None or slow is None or atr is None:
            return {"signal": _sig()}
        diff = fast - slow
        prev = self.prev_diff
        self.prev_diff = diff
        if prev is None:
            return {"signal": _sig()}
        close = float(candle["close"])
        if prev <= 0 < diff:
            return {"signal": _sig("long", 0.0, close - self.atr_mult * atr, "ema cross up")}
        if prev >= 0 > diff:
            return {"signal": _sig("short", 0.0, close + self.atr_mult * atr, "ema cross down")}
        return {"signal": _sig()}

@node(type="scenario_gate", category="decision",
      inputs={"candle": PinType.CANDLE, "atr": PinType.SERIES},
      outputs={"signal": PinType.SIGNAL},
      params={"lookback": int, "cooldown": int, "atr_mult": float})
class ScenarioGateNode:
    """突破场景状态机：waiting → triggered → cooldown → waiting"""
    def setup(self, params):
        self.lookback = int(params.get("lookback", 20))
        self.cooldown = int(params.get("cooldown", 10))
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.highs = deque(maxlen=self.lookback)
        self.lows = deque(maxlen=self.lookback)
        self.cool = 0
        self.state["phase"] = "waiting"
    def on_bar(self, ctx, inputs):
        c, atr = inputs["candle"], inputs["atr"]
        close = float(c["close"])
        sig = _sig()
        if self.cool > 0:
            self.cool -= 1
            self.state["phase"] = "cooldown"
        elif len(self.highs) == self.lookback and atr:
            if close > max(self.highs):
                sig = _sig("long", 0.0, close - self.atr_mult * atr, "breakout up")
                self.cool = self.cooldown
                self.state["phase"] = "triggered"
            elif close < min(self.lows):
                sig = _sig("short", 0.0, close + self.atr_mult * atr, "breakout down")
                self.cool = self.cooldown
                self.state["phase"] = "triggered"
            else:
                self.state["phase"] = "waiting"
        self.highs.append(float(c["high"]))
        self.lows.append(float(c["low"]))
        return {"signal": sig}

@node(type="risk_gate", category="risk",
      inputs={"signal": PinType.SIGNAL},
      outputs={"stamped": PinType.RISK_STAMPED_SIGNAL, "blocked": PinType.BOOL},
      params={"max_qty": float, "require_stop": bool})
class RiskGateNode:
    """全宇宙唯一能产出 risk_stamped_signal 的内置节点 —— 类型系统即合规官。"""
    def setup(self, params):
        self.max_qty = float(params.get("max_qty", 10.0))
        self.require_stop = bool(params.get("require_stop", True))
    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        checks: list[str] = []
        if sig["side"] in ("long", "short"):
            if self.require_stop and sig.get("stop") is None:
                checks.append("missing stop: attach a stop-loss to every entry signal")
            if sig.get("qty", 0) > self.max_qty:
                checks.append(f"qty {sig['qty']} exceeds max_qty {self.max_qty}")
        blocked = bool(checks)
        if blocked:
            sig = _sig("hold", reason="blocked by risk gate")
        sig["risk"] = {"checked": True, "blocked": blocked, "checks": checks}
        return {"stamped": sig, "blocked": blocked}

@node(type="kill_switch", category="risk",
      inputs={"candle": PinType.CANDLE}, outputs={"halted": PinType.BOOL},
      params={"max_drawdown_pct": float})
class KillSwitchNode:
    def setup(self, params):
        self.max_dd = float(params.get("max_drawdown_pct", 0.2))
        self.peak = None
    def on_bar(self, ctx, inputs):
        broker = ctx.broker
        if broker is None:
            return {"halted": False}
        eq = broker.equity()
        self.peak = eq if self.peak is None else max(self.peak, eq)
        dd = (self.peak - eq) / self.peak if self.peak > 0 else 0.0
        if dd >= self.max_dd and not broker.halted:
            broker.halt(f"kill switch: drawdown {dd:.1%} >= {self.max_dd:.1%}")
        return {"halted": broker.halted}
