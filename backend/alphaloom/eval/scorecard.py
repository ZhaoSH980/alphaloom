"""蓝图记分卡 —— 聚合一次回测的全部证据并给出**诚实综合分**（0-100）。

`scorecard(train_report, valid_report=None, *, ladder=None, cost_cert=None,
ablation=None) -> Scorecard`

聚合四类证据：
- **样本内/验证窗表现**：train/valid 两段 BacktestReport（或 dict）。泛化差距
  = train.return_pct - valid.return_pct（正 = 样本内吹牛）。
- **保真度衰减**：LadderReport（optimism_gap 与 L1→L3 pnl 衰减，见 fidelity.py）。
- **成本证书**：llm_calls_per_bar / deterministic_ratio（未显式给则自动取
  train_report.certificate）。
- **消融摘要**（可选占位）：ablation dict 原样嵌入，D4-T4 产出后接上。

综合分设计（gallery 按证据排序的排序键，必须可解释、可批判）
=================================================================
composite = Σ weight_i * component_i，四个分项各 0-100：

- **valid_performance（0.40，单项最大）**：验证窗 return_pct 经
  tanh(r / RETURN_SQUASH_SCALE_PCT) 压缩到 0-100（0% 收益 = 50 分中性）。
  **为什么是验证窗不是样本内**：样本内表现是策略自己出的题自己判的卷；保真度
  阶梯（T2）已证明回测会撒谎，记分卡不能再让样本内数字进排序键。缺 valid 时
  以样本内分 × NO_VALID_DISCOUNT(0.5) 保守代入——样本内证据不值全价。
- **generalization（0.25）**：泛化差距惩罚。gap ≤ 0（验证窗不比样本内差）满分；
  gap 每 1 个百分点扣 GAP_PENALTY_PER_PCT(10) 分——样本内 +30%/验证 +10% 的
  "20 点差距"直接把该项打到 0。这是过拟合的直接价格。缺 valid 时按
  MISSING_EVIDENCE_SCORE 计（无法证伪 ≠ 无罪）。
- **fidelity（0.20）**：保真度衰减惩罚。以 L1（PaperBroker 基线语义）pnl 的
  绝对值为参照尺度（下限 FIDELITY_SCALE_FLOOR_FRAC × 初始资金，防零除），
  惩罚 optimism_gap（L0-L3）与 L1→L3 衰减的相对规模各半：
  score = 100 × max(0, 1 - 0.5×gap/scale - 0.5×(L1-L3)/scale)。
  盈利在更真实成交假设下蒸发得越多，分越低。缺 ladder 按 MISSING_EVIDENCE_SCORE。
- **determinism（0.15）**：确定性加分 = 100 × deterministic_ratio。可复现性是
  证据强度：全确定性蓝图（ratio=1.0）任何人可零成本复算，LLM 重节点的结果
  依赖录制回放才可复现。缺 cost cert 按 MISSING_EVIDENCE_SCORE。

**缺证据 = 低证据分，不是满分**：MISSING_EVIDENCE_SCORE=25——缺失维度按
"弱证据"计而非中性 50 或满分，且 evidence_coverage 字段如实标注缺了什么。
设计上限：无验证窗的记分卡 composite 封顶约 50 出头——没有样本外证据的策略
不配站上排行榜上半区。

可批判点（如实列出）：tanh 压缩尺度（10% 收益 ≈ 76 分）是主观锚点；四权重
是设计决策不是统计推断；fidelity 的尺度下限使小额 pnl 策略的衰减惩罚偏宽松。
全部常量集中在模块顶部，欢迎批判与重调。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 评分常量（全部集中于此，设计理由见模块 docstring）
# ---------------------------------------------------------------------------
WEIGHTS = {
    "valid_performance": 0.40,   # 验证窗表现（不是样本内！）——单项最大
    "generalization": 0.25,      # 泛化差距惩罚（过拟合的直接价格）
    "fidelity": 0.20,            # 保真度衰减惩罚（回测乐观谎言的价格）
    "determinism": 0.15,         # 确定性加分（可复现性 = 证据强度）
}
MISSING_EVIDENCE_SCORE = 25.0    # 缺证据维度的保守分（弱证据 ≠ 中性 50 ≠ 满分）
NO_VALID_DISCOUNT = 0.5          # 缺 valid 时样本内表现的折价系数
RETURN_SQUASH_SCALE_PCT = 10.0   # tanh 压缩尺度：+10% 收益 ≈ 76 分
GAP_PENALTY_PER_PCT = 10.0       # 泛化差距每 1 个百分点扣 10 分
FIDELITY_SCALE_FLOOR_FRAC = 0.01  # 保真度参照尺度下限 = 1% 初始资金（防零除）


# ---------------------------------------------------------------------------
# 输入形状兼容 + JSON 安全
# ---------------------------------------------------------------------------
def _json_safe(obj):
    """递归把 inf/nan 浮点转 None（JSON 安全），其余原样。"""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _summary_of(report):
    """取 report 的 summary dict：兼容 BacktestReport / {"summary": ...} /
    裸 summary dict。None 原样返回。"""
    if report is None:
        return None
    if hasattr(report, "summary"):
        return dict(report.summary)
    if isinstance(report, dict):
        return dict(report["summary"]) if "summary" in report else dict(report)
    raise TypeError(f"unsupported report shape: {type(report)!r}")


def _ladder_dict(ladder):
    if ladder is None:
        return None
    if hasattr(ladder, "to_dict"):
        return ladder.to_dict()
    return dict(ladder)


def _cert_of(cost_cert, train_report):
    """成本证书：显式参数优先，否则从 train_report.certificate 自动拉取。"""
    cert = cost_cert
    if cert is None:
        if hasattr(train_report, "certificate"):
            cert = train_report.certificate
        elif isinstance(train_report, dict):
            cert = train_report.get("certificate")
    if cert is not None and hasattr(cert, "to_dict"):
        cert = cert.to_dict()
    return dict(cert) if cert is not None else None


def _initial_equity(report, default: float = 10_000.0) -> float:
    """从 equity_curve 首点估初始资金（fidelity 尺度下限用），拿不到用默认。"""
    curve = getattr(report, "equity_curve", None)
    if curve is None and isinstance(report, dict):
        curve = report.get("equity_curve")
    if curve:
        first = curve[0]
        if isinstance(first, (list, tuple)) and len(first) == 2:
            return float(first[1])
        if isinstance(first, (int, float)):
            return float(first)
    return default


# ---------------------------------------------------------------------------
# 分项评分（各 0-100）
# ---------------------------------------------------------------------------
def _squash_return(return_pct: float) -> float:
    """收益率 → 0-100：0% = 50 中性，±tanh 压缩（尺度见常量）。"""
    return 50.0 * (1.0 + math.tanh(return_pct / RETURN_SQUASH_SCALE_PCT))


def _score_valid_performance(train: dict, valid: dict | None) -> float:
    if valid is not None:
        return _squash_return(float(valid.get("return_pct", 0.0)))
    # 缺验证窗：样本内表现折价代入——样本内证据不值全价（缺证据 ≠ 满分）
    return NO_VALID_DISCOUNT * _squash_return(float(train.get("return_pct", 0.0)))


def _score_generalization(gap: float | None) -> float:
    if gap is None:
        return MISSING_EVIDENCE_SCORE
    if gap <= 0.0:
        return 100.0
    return max(0.0, 100.0 - gap * GAP_PENALTY_PER_PCT)


def _score_fidelity(ladder: dict | None, initial_equity: float) -> float:
    if ladder is None:
        return MISSING_EVIDENCE_SCORE
    pnl = {lv.get("level"): float(lv.get("net_pnl", 0.0))
           for lv in ladder.get("levels", []) if isinstance(lv, dict)}
    if not {"L0", "L1", "L3"} <= set(pnl):
        return MISSING_EVIDENCE_SCORE      # 阶梯残缺 = 证据残缺，保守计
    scale = max(abs(pnl["L1"]), FIDELITY_SCALE_FLOOR_FRAC * abs(initial_equity), 1e-9)
    gap_rel = max(0.0, pnl["L0"] - pnl["L3"]) / scale        # optimism_gap 相对规模
    decay_rel = max(0.0, pnl["L1"] - pnl["L3"]) / scale      # L1→L3 衰减相对规模
    return 100.0 * max(0.0, 1.0 - 0.5 * gap_rel - 0.5 * decay_rel)


def _score_determinism(cert: dict | None) -> float:
    if cert is None or "deterministic_ratio" not in cert:
        return MISSING_EVIDENCE_SCORE
    ratio = min(1.0, max(0.0, float(cert["deterministic_ratio"])))
    return 100.0 * ratio


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scorecard:
    composite: float                 # 0-100 综合分（gallery 排序键）
    components: dict                 # 四分项各 0-100
    weights: dict                    # 权重回显（可批判性：分随权出）
    evidence_coverage: dict          # {维度: bool, "ratio": 0-1}
    generalization_gap: float | None  # train - valid return_pct（无 valid = None）
    in_sample_only: bool
    train_summary: dict
    valid_summary: dict | None
    fidelity: dict | None            # ladder.to_dict()（原证据随卡携带）
    cost: dict | None                # 成本证书摘要
    ablation: dict | None = None     # T4 消融占位：原样嵌入
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return _json_safe({
            "composite": self.composite,
            "components": self.components,
            "weights": self.weights,
            "evidence_coverage": self.evidence_coverage,
            "generalization_gap": self.generalization_gap,
            "in_sample_only": self.in_sample_only,
            "train_summary": self.train_summary,
            "valid_summary": self.valid_summary,
            "fidelity": self.fidelity,
            "cost": self.cost,
            "ablation": self.ablation,
            "notes": list(self.notes),
        })


def scorecard(train_report, valid_report=None, *, ladder=None,
              cost_cert=None, ablation=None) -> Scorecard:
    """聚合证据并打诚实综合分。评分设计与全部常量见模块 docstring。

    纯数值、零 LLM、零网络。缺输入按保守处理（缺证据 = 低证据分）并在
    evidence_coverage 如实标注。
    """
    train = _summary_of(train_report)
    if train is None:
        raise ValueError("train_report is required")
    valid = _summary_of(valid_report)
    ladder_d = _ladder_dict(ladder)
    cert = _cert_of(cost_cert, train_report)
    init_eq = _initial_equity(train_report)

    gap = None
    if valid is not None:
        gap = round(float(train.get("return_pct", 0.0))
                    - float(valid.get("return_pct", 0.0)), 6)

    components = {
        "valid_performance": round(_score_valid_performance(train, valid), 4),
        "generalization": round(_score_generalization(gap), 4),
        "fidelity": round(_score_fidelity(ladder_d, init_eq), 4),
        "determinism": round(_score_determinism(cert), 4),
    }
    composite = sum(WEIGHTS[k] * components[k] for k in WEIGHTS)
    composite = round(min(100.0, max(0.0, composite)), 2)

    coverage_flags = {
        "valid_window": valid is not None,
        "fidelity_ladder": ladder_d is not None,
        "cost_certificate": cert is not None,
        "ablation": ablation is not None,
    }
    coverage = dict(coverage_flags)
    coverage["ratio"] = round(sum(coverage_flags.values()) / len(coverage_flags), 4)

    notes = []
    if valid is None:
        notes.append("no validation window: performance is in-sample only "
                     "(discounted), generalization unverifiable")
    if ladder_d is None:
        notes.append("no fidelity ladder: execution-optimism unquantified")
    if cert is None:
        notes.append("no cost certificate: determinism/reproducibility unknown")

    return Scorecard(
        composite=composite,
        components=components,
        weights=dict(WEIGHTS),
        evidence_coverage=coverage,
        generalization_gap=gap,
        in_sample_only=valid is None,
        train_summary=train,
        valid_summary=valid,
        fidelity=ladder_d,
        cost=cert,
        ablation=ablation,
        notes=notes,
    )
