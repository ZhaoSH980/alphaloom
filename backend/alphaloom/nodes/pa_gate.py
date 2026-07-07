"""PADecisionTree：纯确定性数值门控（AlphaLoom D3）。

"不信 LLM 嘴、code disposes" 的确定性对照：读上游 signal，用数值规则收紧/否决——
不调 LLM，cost 全 0 deterministic True。语义（二元决策树）：

- 上游 hold/flat → 无条件透传（门控只收紧不放宽）。
- 上游 long/short：
  - ema 或 atr 缺失（未 warmup）→ 降级 hold（数据不足不冒进）。
  - atr < min_atr（波动过小，突破无意义）→ 降级 hold。
  - long 但 close < ema（价在均线下方，趋势不支持多头）→ 降级 hold。
  - short 但 close > ema（价在均线上方，趋势不支持空头）→ 降级 hold。
  - 否则透传原信号（趋势方向与信号一致、波动足够）。
"""
from __future__ import annotations

from alphaloom.graph.types import CostAnnotation, PinType
from alphaloom.nodes.registry import node


@node(
    type="pa_decision_tree",
    category="decision",
    inputs={
        "candle": PinType.CANDLE,
        "ema": PinType.SERIES,
        "atr": PinType.SERIES,
        "signal": PinType.SIGNAL,
    },
    outputs={"signal": PinType.SIGNAL},
    params={"min_atr": float},
    optional_inputs={"ema", "atr"},
    cost=CostAnnotation(
        llm_calls_per_bar=0,
        max_tokens_per_call=0,
        latency_class="fast",
        deterministic=True,   # 纯数值门控：同输入同输出，不调 LLM
    ),
)
class PADecisionTreeNode:
    """确定性数值门控：用 close/ema/atr 收紧或否决上游 signal，绝不调 LLM。"""

    def setup(self, params):
        self.min_atr = float(params.get("min_atr", 0.0))

    def _demote(self, sig, reason):
        out = dict(sig)
        out["side"] = "hold"
        out["qty"] = 0.0
        out["stop"] = None
        out["reason"] = reason
        return {"signal": out}

    def on_bar(self, ctx, inputs):
        sig = inputs["signal"]
        side = sig.get("side")
        # hold/flat 本就不交易 → 原样透传（门控只收紧不放宽）
        if side not in ("long", "short"):
            return {"signal": dict(sig)}

        candle = inputs["candle"]
        ema = inputs.get("ema")
        atr = inputs.get("atr")
        close = float(candle["close"])

        # 数据不足（未 warmup）→ 不冒进
        if ema is None or atr is None:
            return self._demote(sig, "gated: ema/atr not warmed up")

        atr_f = float(atr)
        if atr_f < self.min_atr:
            return self._demote(
                sig, f"gated: atr {atr_f:.4g} below min_atr {self.min_atr:.4g}")

        ema_f = float(ema)
        if side == "long" and close < ema_f:
            return self._demote(
                sig, f"gated: long but close {close:.4g} < ema {ema_f:.4g}")
        if side == "short" and close > ema_f:
            return self._demote(
                sig, f"gated: short but close {close:.4g} > ema {ema_f:.4g}")

        # 趋势方向与信号一致、波动足够 → 透传原信号
        return {"signal": dict(sig)}
