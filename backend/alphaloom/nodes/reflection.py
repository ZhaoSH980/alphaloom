"""反思闭环节点：ReflectorNode（过程/结局分离四象限打分，AlphaLoom D3 Task 5）。

港自 Hindsight 的 reasonable_but_wrong 分类学——把"决策过程好坏"与"结局好坏"分开评判，
不让运气污染对过程的评价：

    过程好 × 结局好 → reasonable_and_right   （好本事好运气：值得复现）
    过程好 × 结局坏 → reasonable_but_wrong   （好过程坏运气：**不惩罚**，招牌卖点）
    过程坏 × 结局好 → lucky                    （坏过程好运气：别当本事）
    过程坏 × 结局坏 → bad_process              （坏过程坏运气：该改）

数据流接缝：pnl 不占决策引脚——ReflectorNode 读 ``ctx.broker.closed_trades``（PaperBroker
每笔往返平仓后追加的 {ts, pnl, entry_side}），用 ``self.state`` 记住已消费条数，只在**平仓
那根 bar**（closed_trades 增长时）产 verdict，否则 verdict=None。这样 Reflector 在真实蓝图里
接得到平仓 pnl，反思闭环不是纸面功能。

成本：cost 全 0 deterministic True——确定性四象限分类不调 LLM（计划 Task 5 默认确定性档）。
"""
from __future__ import annotations

from alphaloom.graph.types import CostAnnotation, PinType
from alphaloom.memory.experience_store import derive_regime_bucket
from alphaloom.nodes.registry import node

# 过程好坏判据阈值：confidence >= 此值 且 有 rationale/citations 之一 → 过程健全。
_GOOD_CONFIDENCE = 0.5


def _process_is_sound(signal: dict) -> bool:
    """过程好坏：决策是否基于合理信号——confidence 足够高，且有 rationale 或 citations 背书。"""
    if not isinstance(signal, dict):
        return False
    try:
        confidence = float(signal.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    rationale = str(signal.get("rationale") or "").strip()
    citations = signal.get("citations") or []
    has_evidence = bool(rationale) or bool(citations)
    return confidence >= _GOOD_CONFIDENCE and has_evidence


def _classify(process_sound: bool, pnl: float) -> str:
    """四象限：过程好坏 × 结局好坏（pnl > 0 为好结局）。"""
    outcome_good = pnl > 0
    if process_sound and outcome_good:
        return "reasonable_and_right"
    if process_sound and not outcome_good:
        return "reasonable_but_wrong"   # 招牌：好过程坏结局被正确区分，不惩罚运气
    if not process_sound and outcome_good:
        return "lucky"
    return "bad_process"


def _config_summary(signal: dict) -> str:
    """配置摘要：从平仓信号提炼一句可读的决策快照（写进经验库供人回看）。"""
    side = signal.get("side", "?")
    rationale = str(signal.get("rationale") or "").strip() or "(no rationale)"
    return f"side={side}; rationale={rationale}"


@node(
    type="reflector",
    category="reflection",
    inputs={
        "signal": PinType.SIGNAL,
        "ema": PinType.SERIES,
        "atr": PinType.SERIES,
    },
    outputs={"verdict": PinType.SERIES},
    optional_inputs={"ema", "atr"},
    cost=CostAnnotation(
        llm_calls_per_bar=0,
        max_tokens_per_call=0,
        latency_class="fast",
        deterministic=True,   # 确定性四象限分类：同输入同输出，不调 LLM
    ),
)
class ReflectorNode:
    """过程/结局分离四象限反思：只在平仓那根 bar 产 verdict。

    读 ctx.broker.closed_trades 拿最近平仓 pnl（数据流接缝），用 self.state["consumed"]
    保证每笔平仓只反思一次（幂等消费）。verdict 载荷含四象限判定 + 市场状态桶 + pnl +
    config_summary + lesson + trade_key，供下游 ExperienceWrite 落库。
    """

    def setup(self, params):
        self.state.setdefault("consumed", 0)
        self.state.setdefault("ema_prev", None)

    def _none(self, ema):
        # 无平仓那根也要推进 ema 历史，否则桶斜率算错（记住上一根 ema）
        self.state["ema_prev"] = ema
        return {"verdict": None}

    def on_bar(self, ctx, inputs):
        ema = inputs.get("ema")
        ema_prev = self.state.get("ema_prev")
        broker = getattr(ctx, "broker", None)
        closed = getattr(broker, "closed_trades", None) if broker is not None else None
        if not closed:
            return self._none(ema)

        consumed = self.state.get("consumed", 0)
        if len(closed) <= consumed:
            return self._none(ema)   # 本根 bar 无新平仓 → 不反思

        # 消费**最旧的未反思**那笔，游标只前进 1（每根 bar 出一条 verdict）。
        # 一根 bar 多笔平仓（反手后止损、多执行路径）→ 后续 bar 逐笔排空，一笔都不丢。
        idx = consumed
        trade = closed[idx]
        self.state["consumed"] = idx + 1
        self.state["ema_prev"] = ema

        signal = inputs.get("signal") or {}
        pnl = float(trade.get("pnl", 0.0))
        process_sound = _process_is_sound(signal)
        verdict_label = _classify(process_sound, pnl)
        bucket = derive_regime_bucket(ema=ema, ema_prev=ema_prev, atr=inputs.get("atr"))

        # trade_key 含全局序号 idx → 同一根 bar 内多笔同向平仓也各自唯一（不被 UPSERT 合并）
        trade_key = f"{idx}:{trade.get('ts', 0)}:{trade.get('entry_side', '?')}"
        lesson = self._lesson(verdict_label, bucket, signal)

        return {"verdict": {
            "verdict": verdict_label,
            "bucket": bucket,
            "pnl": pnl,
            "trade_key": trade_key,
            "config_summary": _config_summary(signal),
            "lesson": lesson,
        }}

    @staticmethod
    def _lesson(verdict_label: str, bucket: str, signal: dict) -> str:
        side = signal.get("side", "?")
        templates = {
            "reasonable_and_right": f"In {bucket}, {side} on sound signal paid off — reinforce.",
            "reasonable_but_wrong": (
                f"In {bucket}, {side} was well-reasoned but lost to the market — "
                "process was fine, don't over-correct on one bad outcome."),
            "lucky": f"In {bucket}, {side} won without a sound rationale — luck, not skill.",
            "bad_process": f"In {bucket}, {side} was poorly reasoned and lost — tighten the process.",
        }
        return templates.get(verdict_label, "")
