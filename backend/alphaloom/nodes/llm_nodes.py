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
        response = ctx.llm.chat(messages, temperature=0.2, max_tokens=512)
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


# --------------------------------------------------------------------------- #
# Committee 节点：策略师 → 风控官 → 主席（三角色扇出 + 结构化 JSON 交接 + 表决）
# --------------------------------------------------------------------------- #

_STRATEGIST_SYSTEM = (
    "You are {persona}, the committee's strategist. Read the latest candle and ATR, "
    "then propose a trade. Reply with ONLY a JSON object: "
    '{{"side": "long|short|flat|hold", "rationale": "<one sentence>", '
    '"confidence": <0..1 float>}}. No prose outside the JSON.'
)

_RISK_SYSTEM = (
    "You are {persona}, the committee's risk officer. You are given the strategist's "
    "proposal as JSON. Scrutinize it for risk. Reply with ONLY a JSON object: "
    '{{"veto": <true|false>, "concern": "<one sentence>", '
    '"confidence": <0..1 float>}}. Set veto=true to block the trade outright. '
    "No prose outside the JSON."
)

_CHAIR_SYSTEM = (
    "You are {persona}, the committee chair. You are given the strategist's proposal "
    "and the risk officer's assessment as JSON. Synthesize the final decision. "
    "If the risk officer vetoed, you MUST return side=hold. Reply with ONLY a JSON "
    'object: {{"side": "long|short|flat|hold", "rationale": "<one sentence>", '
    '"confidence": <0..1 float>}}. No prose outside the JSON.'
)

_DEFAULT_PERSONAS = {
    "strategist": "a disciplined strategist",
    "risk": "a conservative risk officer",
    "chair": "an impartial chair",
}


@node(
    type="committee",
    category="decision",
    inputs={"candle": PinType.CANDLE, "atr": PinType.SERIES},
    outputs={"signal": PinType.SIGNAL},
    params={
        "atr_mult": float,
        # 可选人格/提示词覆盖（每角色一份）
        "strategist_persona": str,
        "risk_persona": str,
        "chair_persona": str,
        "strategist_prompt": str,
        "risk_prompt": str,
        "chair_prompt": str,
        # 消融开关（D4-T4）：跳过 LLM 风控官"软护栏"（策略师 → 主席两角色）。
        # RiskGate"硬护栏"由类型系统强制、无法被消融（见 eval/ablation.py）。
        "skip_risk_officer": bool,
    },
    cost=CostAnnotation(
        llm_calls_per_bar=3,   # 三角色各一次（策略师/风控官/主席）
        max_tokens_per_call=512,
        latency_class="llm",
        deterministic=False,   # 调 LLM → 非确定性（D1 Carryover 10）
    ),
)
class CommitteeNode:
    """委员会决策：策略师提案 → 风控官表决(可 veto) → 主席合成终案。

    结构化 JSON 交接——每角色输出 JSON，下游角色的 user prompt 里含上游 JSON。
    风控官 veto → 主席终案强制 side=hold。任一角色坏 JSON → 整体回退 hold
    （rationale 指明哪个角色 parse 失败）。输出 signal 附加
    committee_trace:[strategist_json, risk_json, chair_json] 供前端展示。

    ``skip_risk_officer=True``（消融臂，D4-T4）：跳过风控官——每 bar 2 次调用、
    主席只读策略师 JSON、trace 两项、无 veto 可能。cost 注解维持 3 次/bar 的
    **静态上界**（成本证书是编译期注解，不随参数收窄；只许高估不许低估）。
    """

    def setup(self, params):
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.skip_risk_officer = bool(params.get("skip_risk_officer", False))
        self.strategist_persona = str(
            params.get("strategist_persona", _DEFAULT_PERSONAS["strategist"]))
        self.risk_persona = str(
            params.get("risk_persona", _DEFAULT_PERSONAS["risk"]))
        self.chair_persona = str(
            params.get("chair_persona", _DEFAULT_PERSONAS["chair"]))
        self.strategist_prompt = str(params.get("strategist_prompt", _STRATEGIST_SYSTEM))
        self.risk_prompt = str(params.get("risk_prompt", _RISK_SYSTEM))
        self.chair_prompt = str(params.get("chair_prompt", _CHAIR_SYSTEM))
        # opt-in trend context: feed the strategist the last N closes + trend so it
        # isn't reasoning off a single candle. Default 0 → market blob byte-identical
        # to before → committed agent_committee recordings still replay (no hash drift).
        self.context_window = int(params.get("context_window", 0))
        self._recent: list[float] = []

    def _hold(self, rationale, trace):
        return {"signal": {
            "side": "hold", "qty": 0.0, "stop": None, "reason": rationale,
            "rationale": rationale, "confidence": 0.0, "citations": [],
            "committee_trace": trace,
        }}

    def _ask(self, ctx, system, user, *, role, ts):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        response = ctx.llm.chat(messages, temperature=0.2, max_tokens=512)
        if ctx.audit is not None:
            ctx.audit.record(
                tool=f"committee:{role}",
                params={"node": getattr(self, "node_id", "committee"), "role": role},
                data_max_ts=ts,
                note=f"committee {role} turn",
            )
        return _extract_json(_content(response))

    def on_bar(self, ctx, inputs):
        if ctx.llm is None:
            raise RuntimeError(
                "no LLM client bound; run via the service or pass llm= to run_backtest")
        candle, atr = inputs["candle"], inputs["atr"]
        close = float(candle["close"])
        ts = int(candle["ts"])
        atr_val = None if atr is None else float(atr)

        mkt = {"close": close, "high": float(candle["high"]),
               "low": float(candle["low"]), "atr": atr_val}
        if self.context_window > 0:
            self._recent.append(round(close, 2))
            if len(self._recent) > self.context_window:
                self._recent = self._recent[-self.context_window:]
            first = self._recent[0]
            chg = (close - first) / first * 100.0 if first else 0.0
            mkt["recent_closes"] = self._recent
            mkt["trend_pct"] = round(chg, 3)
            mkt["trend"] = "up" if chg > 0.3 else "down" if chg < -0.3 else "flat"
        market = json.dumps(mkt, sort_keys=True)

        # --- 角色 1：策略师（读 candle+atr → 提案） ---
        strat_sys = self.strategist_prompt.format(persona=self.strategist_persona)
        strat_json = self._ask(ctx, strat_sys, market, role="strategist", ts=ts)
        if not isinstance(strat_json, dict) or strat_json.get("side") not in _VALID_SIDES:
            return self._hold("strategist parse failed", [])

        # --- 角色 2：风控官（读策略师 JSON → veto/收紧） ---
        # 消融开关（skip_risk_officer，D4-T4）：跳过该角色——LLM 风控官是可被消融
        # 实验拆除做对照的"软护栏"；RiskGate"硬护栏"由类型系统强制，消融不掉
        # （旁路它的图编译必 TYPE_MISMATCH，见 eval/ablation.py + 测试锁定）。
        risk_json = None
        veto = False
        if not self.skip_risk_officer:
            risk_sys = self.risk_prompt.format(persona=self.risk_persona)
            risk_user = json.dumps(
                {"market": json.loads(market), "strategist": strat_json}, sort_keys=True)
            risk_json = self._ask(ctx, risk_sys, risk_user, role="risk", ts=ts)
            if not isinstance(risk_json, dict) or "veto" not in risk_json:
                return self._hold("risk officer parse failed", [strat_json])
            veto = bool(risk_json.get("veto"))

        # --- 角色 3：主席（读策略师[+风控官]JSON → 合成终案） ---
        chair_sys = self.chair_prompt.format(persona=self.chair_persona)
        chair_payload = {"strategist": strat_json}
        if risk_json is not None:
            chair_payload["risk_officer"] = risk_json
        chair_user = json.dumps(chair_payload, sort_keys=True)
        chair_json = self._ask(ctx, chair_sys, chair_user, role="chair", ts=ts)
        pre_trace = [strat_json] + ([risk_json] if risk_json is not None else [])
        if not isinstance(chair_json, dict) or chair_json.get("side") not in _VALID_SIDES:
            return self._hold("chair parse failed", pre_trace)

        trace = pre_trace + [chair_json]

        # 风控官 veto → 终案强制 hold（尊重否决，不信主席嘴）
        if veto:
            return self._hold(
                f"risk officer vetoed: {risk_json.get('concern', '')}".strip(), trace)

        side = chair_json.get("side")
        if side not in ("long", "short"):
            # 主席自选 flat/hold：无 stop
            rationale = str(chair_json.get("rationale", ""))
            try:
                confidence = float(chair_json.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            return {"signal": {
                "side": side, "qty": 0.0, "stop": None, "reason": rationale,
                "rationale": rationale, "confidence": confidence, "citations": [],
                "committee_trace": trace,
            }}

        rationale = str(chair_json.get("rationale", ""))
        try:
            confidence = float(chair_json.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        stop = None
        if atr_val is not None:
            stop = close - self.atr_mult * atr_val if side == "long" \
                else close + self.atr_mult * atr_val

        return {"signal": {
            "side": side, "qty": 0.0, "stop": stop, "reason": rationale,
            "rationale": rationale, "confidence": confidence, "citations": [],
            "committee_trace": trace,
        }}
