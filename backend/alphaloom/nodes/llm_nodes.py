"""LLM 决策类节点（AlphaLoom D3）。

LLMAnalystNode 通过 ``ctx.llm``（RecordingLLMClient）产出决策信号。成本注解如实
（llm_calls_per_bar=1 / deterministic=False / latency_class="llm"，兑现 D1 Carryover 10）；
离线回放走录制层，同 prompt 命中缓存即确定性重放。
"""
from __future__ import annotations

import json

from alphaloom.graph.types import CostAnnotation, PinType
from alphaloom.nodes.registry import node

_VALID_SIDES = ("long", "short", "flat", "hold")


def _content(response: dict) -> str:
    """从 OpenAI 兼容响应里取出 assistant 文本内容。"""
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_json(text: str) -> dict | None:
    """提取文本里第一个平衡的 {...} 对象并解析。模型常在 JSON 外包裹说明文字。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


_SYSTEM = (
    "You are {persona}, a disciplined trading analyst. Read the latest candle and ATR, "
    "then decide. Reply with ONLY a JSON object: "
    '{{"side": "long|short|flat|hold", "rationale": "<one sentence>", '
    '"confidence": <0..1 float>}}. No prose outside the JSON.'
)


@node(
    type="llm_analyst",
    category="decision",
    inputs={"candle": PinType.CANDLE, "atr": PinType.SERIES},
    outputs={"signal": PinType.SIGNAL},
    params={"persona": str, "atr_mult": float},
    cost=CostAnnotation(
        llm_calls_per_bar=1,
        max_tokens_per_call=512,
        latency_class="llm",
        deterministic=False,   # 诚实：调 LLM 就不是确定性（D1 Carryover 10）
    ),
)
class LLMAnalystNode:
    """人格化 LLM 分析师：每根 bar 调一次 LLM 产出 side/stop/rationale/confidence/citations。"""

    def setup(self, params):
        self.persona = str(params.get("persona", "an analyst"))
        self.atr_mult = float(params.get("atr_mult", 2.0))

    def _hold(self, rationale="hold"):
        return {"signal": {"side": "hold", "qty": 0.0, "stop": None, "reason": rationale,
                           "rationale": rationale, "confidence": 0.0, "citations": []}}

    def on_bar(self, ctx, inputs):
        if ctx.llm is None:
            raise RuntimeError(
                "no LLM client bound; run via the service or pass llm= to run_backtest")
        candle, atr = inputs["candle"], inputs["atr"]
        close = float(candle["close"])
        system = _SYSTEM.format(persona=self.persona)
        user = json.dumps({
            "close": close, "high": float(candle["high"]), "low": float(candle["low"]),
            "atr": None if atr is None else float(atr),
        }, sort_keys=True)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        response = ctx.llm.chat(messages, temperature=0.2)
        if ctx.audit is not None:
            ctx.audit.record(
                tool="llm_chat",
                params={"node": getattr(self, "node_id", "llm_analyst"),
                        "persona": self.persona},
                data_max_ts=int(candle["ts"]),
                note="llm_analyst decision",
            )

        parsed = _extract_json(_content(response))
        side = parsed.get("side") if isinstance(parsed, dict) else None
        if parsed is None or side not in _VALID_SIDES:
            return self._hold("parse failed")

        rationale = str(parsed.get("rationale", ""))
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        stop = None
        if side in ("long", "short") and atr is not None:
            atr_f = float(atr)
            stop = close - self.atr_mult * atr_f if side == "long" else close + self.atr_mult * atr_f

        return {"signal": {
            "side": side, "qty": 0.0, "stop": stop, "reason": rationale,
            "rationale": rationale, "confidence": confidence, "citations": [],
        }}
