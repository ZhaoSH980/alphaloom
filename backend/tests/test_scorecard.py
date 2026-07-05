"""蓝图记分卡测试 —— 诚实综合分（零 LLM 配额，纯数值）。

锁定行为：
- **缺证据 = 低证据分，不是满分**：缺 valid / 缺 ladder / 缺 cost cert 时对应
  维度按 MISSING_EVIDENCE_SCORE 保守计，evidence_coverage 如实降级。
- **过拟合对照**：train 好 valid 差（大泛化差距 + 大 optimism_gap）的 composite
  必须显著低于 train/valid 双好 + 低 optimism_gap 的稳健案例。
- to_dict() JSON 安全（inf/nan → None）。
"""
from __future__ import annotations
import json
import math

from alphaloom.backtest.runner import BacktestReport
from alphaloom.eval.fidelity import LadderReport, LevelResult
from alphaloom.eval.scorecard import (
    MISSING_EVIDENCE_SCORE,
    WEIGHTS,
    Scorecard,
    scorecard,
)


# ---------------------------------------------------------------------------
# 夹具：手工构造已知答案的 summary / report / ladder / cert
# ---------------------------------------------------------------------------
def _summary(return_pct, *, net_pnl=None, max_dd=0.05, win_rate=0.5,
             num_trades=10, profit_factor=1.5):
    return {"net_pnl": net_pnl if net_pnl is not None else return_pct * 100.0,
            "return_pct": return_pct, "max_drawdown": max_dd,
            "num_trades": num_trades, "win_rate": win_rate,
            "profit_factor": profit_factor, "halted": False, "halt_reason": ""}


def _report(return_pct, **kw):
    return {"summary": _summary(return_pct, **kw),
            "equity_curve": [[0, 10_000.0], [60_000, 10_000.0 + return_pct * 100.0]]}


def _ladder(l0, l1, l2, l3):
    def lv(name, pnl):
        return {"level": name, "net_pnl": pnl, "max_dd": 0.1,
                "num_trades": 3, "profit_factor": 1.2}
    return {"levels": [lv("L0", l0), lv("L1", l1), lv("L2", l2), lv("L3", l3)],
            "optimism_gap": l0 - l3}


CERT = {"llm_calls_per_bar": 0, "daily_token_ceiling": 0,
        "worst_latency_class": "fast", "deterministic_ratio": 1.0}


# ---------------------------------------------------------------------------
# 权重设计：必须归一、必须验证窗主导（不是样本内）
# ---------------------------------------------------------------------------
def test_weights_sum_to_one():
    assert math.isclose(sum(WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_valid_performance_is_dominant_weight():
    # 排序键的第一性原则：验证窗表现权重必须是单项最大——样本内表现不直接进分。
    assert WEIGHTS["valid_performance"] == max(WEIGHTS.values())


# ---------------------------------------------------------------------------
# 过拟合对照案例（任务锁定）：train 好 valid 差 → 低分；双好+低 gap → 高分
# ---------------------------------------------------------------------------
def test_overfit_case_scores_far_below_robust_case():
    # 过拟合：样本内 +30%、验证窗 -5%，保真度阶梯 L0=3000 → L3=100（乐观谎言巨大）
    overfit = scorecard(_report(30.0), _report(-5.0),
                        ladder=_ladder(3000.0, 1000.0, 400.0, 100.0),
                        cost_cert=CERT)
    # 稳健：样本内 +12%、验证窗 +10%，阶梯衰减轻微（L0=1050 → L3=900）
    robust = scorecard(_report(12.0), _report(10.0),
                       ladder=_ladder(1050.0, 1000.0, 950.0, 900.0),
                       cost_cert=CERT)
    assert robust.composite > overfit.composite
    assert robust.composite > 70.0, robust.composite
    assert overfit.composite < 40.0, overfit.composite


def test_generalization_gap_is_train_minus_valid_return():
    card = scorecard(_report(12.0), _report(10.0))
    assert card.generalization_gap is not None
    assert math.isclose(card.generalization_gap, 2.0, abs_tol=1e-9)


def test_wider_generalization_gap_scores_lower():
    tight = scorecard(_report(11.0), _report(10.0), cost_cert=CERT)
    wide = scorecard(_report(30.0), _report(10.0), cost_cert=CERT)
    # 同一验证窗表现，样本内吹得越高（泛化差距越大）分越低
    assert tight.composite > wide.composite


# ---------------------------------------------------------------------------
# 缺证据 = 低证据分（不是满分）+ evidence_coverage 如实降级
# ---------------------------------------------------------------------------
def test_missing_valid_degrades_coverage_and_score():
    card = scorecard(_report(50.0), cost_cert=CERT)   # 样本内 +50% 也救不了缺验证窗
    assert card.in_sample_only is True
    assert card.generalization_gap is None
    assert card.evidence_coverage["valid_window"] is False
    assert card.evidence_coverage["ratio"] < 1.0
    assert card.composite < 60.0, card.composite
    # 同一 train + 补上验证窗与阶梯证据 → 必须高于缺证据版本
    full = scorecard(_report(50.0), _report(45.0),
                     ladder=_ladder(1050.0, 1000.0, 950.0, 900.0), cost_cert=CERT)
    assert full.composite > card.composite


def test_missing_ladder_is_conservative_not_full_marks():
    without = scorecard(_report(10.0), _report(9.0), cost_cert=CERT)
    with_good = scorecard(_report(10.0), _report(9.0),
                          ladder=_ladder(1020.0, 1000.0, 980.0, 960.0),
                          cost_cert=CERT)
    assert without.components["fidelity"] == MISSING_EVIDENCE_SCORE
    assert without.evidence_coverage["fidelity_ladder"] is False
    assert with_good.components["fidelity"] > without.components["fidelity"]


def test_missing_cost_cert_is_conservative():
    card = scorecard(_report(10.0), _report(9.0))
    assert card.components["determinism"] == MISSING_EVIDENCE_SCORE
    assert card.evidence_coverage["cost_certificate"] is False


def test_large_optimism_gap_penalized():
    small = scorecard(_report(10.0), _report(9.0),
                      ladder=_ladder(1020.0, 1000.0, 990.0, 980.0), cost_cert=CERT)
    big = scorecard(_report(10.0), _report(9.0),
                    ladder=_ladder(2500.0, 1000.0, 500.0, 200.0), cost_cert=CERT)
    assert small.components["fidelity"] > big.components["fidelity"]
    assert small.composite > big.composite


def test_determinism_ratio_rewarded():
    det = scorecard(_report(10.0), _report(9.0),
                    cost_cert={**CERT, "deterministic_ratio": 1.0})
    llm_heavy = scorecard(_report(10.0), _report(9.0),
                          cost_cert={**CERT, "deterministic_ratio": 0.4})
    assert det.components["determinism"] > llm_heavy.components["determinism"]
    assert det.composite > llm_heavy.composite


# ---------------------------------------------------------------------------
# 零交易 = 零证据（审查实锤回归）：躺平策略不得靠"空洞满分"压过真实盈利者
# ---------------------------------------------------------------------------
def test_zero_trade_full_evidence_scores_as_missing_evidence():
    # 回归背景：0 交易 + 全证据曾拿 80.0——generalization/fidelity 在"没东西可
    # 过拟合/没东西可衰减"上白拿满分（合计 0.60 权重），压过真实盈利者。
    flat = scorecard(_report(0.0, num_trades=0), _report(0.0, num_trades=0),
                     ladder=_ladder(0.0, 0.0, 0.0, 0.0), cost_cert=CERT)
    # 排序窗 num_trades==0 → 三个交易依赖维度全按缺证据计
    assert flat.components["valid_performance"] == MISSING_EVIDENCE_SCORE
    assert flat.components["generalization"] == MISSING_EVIDENCE_SCORE
    assert flat.components["fidelity"] == MISSING_EVIDENCE_SCORE
    assert flat.components["determinism"] == 100.0   # 编译期属性，不依赖交易，保留
    assert math.isclose(flat.composite, 36.25, abs_tol=0.01), flat.composite
    assert flat.evidence_coverage["trading_activity"] is False
    assert any("zero trades" in n for n in flat.notes)


def test_zero_trade_loses_to_real_earners():
    # 对照组（审查者案例）：+3% 验证窗 / gap 3 / 零衰减阶梯的真实盈利者，
    # 全确定（78.33）与零确定（63.33）都必须压过躺平（36.25）。
    flat = scorecard(_report(0.0, num_trades=0), _report(0.0, num_trades=0),
                     ladder=_ladder(0.0, 0.0, 0.0, 0.0), cost_cert=CERT)
    earner_det = scorecard(_report(6.0), _report(3.0),
                           ladder=_ladder(300.0, 300.0, 300.0, 300.0),
                           cost_cert=CERT)
    earner_llm = scorecard(_report(6.0), _report(3.0),
                           ladder=_ladder(300.0, 300.0, 300.0, 300.0),
                           cost_cert={**CERT, "deterministic_ratio": 0.0})
    assert math.isclose(earner_det.composite, 78.33, abs_tol=0.01), earner_det.composite
    assert math.isclose(earner_llm.composite, 63.33, abs_tol=0.01), earner_llm.composite
    assert flat.composite < earner_llm.composite < earner_det.composite


def test_trading_activity_flag_true_when_trades_exist():
    card = scorecard(_report(10.0), _report(9.0), cost_cert=CERT)
    assert card.evidence_coverage["trading_activity"] is True


def test_zero_trade_rule_uses_ranking_window():
    # 排序窗 = valid（有 valid 时）：train 有交易但 valid 零交易 → 仍按零证据计
    card = scorecard(_report(10.0, num_trades=8), _report(0.0, num_trades=0),
                     cost_cert=CERT)
    assert card.evidence_coverage["trading_activity"] is False
    assert card.components["valid_performance"] == MISSING_EVIDENCE_SCORE
    assert card.components["generalization"] == MISSING_EVIDENCE_SCORE


# ---------------------------------------------------------------------------
# 消融占位：ablation dict 原样嵌入（T4 产出后接上）
# ---------------------------------------------------------------------------
def test_ablation_embedded_as_is():
    abl = {"arms": ["full", "no_risk", "no_rag"], "note": "T4 placeholder"}
    card = scorecard(_report(10.0), _report(9.0), ablation=abl)
    assert card.to_dict()["ablation"] == abl
    assert card.evidence_coverage["ablation"] is True
    none_card = scorecard(_report(10.0), _report(9.0))
    assert none_card.evidence_coverage["ablation"] is False


# ---------------------------------------------------------------------------
# 输入形状兼容：BacktestReport / LadderReport 数据类（cert 自动取自 report）
# ---------------------------------------------------------------------------
def test_accepts_dataclass_inputs_and_pulls_cert_from_report():
    rep = BacktestReport(run_id="r1", blueprint_id="bp1", bars=100,
                         summary=_summary(8.0), certificate=dict(CERT),
                         equity_curve=[(0, 10_000.0)], fills=[])
    ladder = LadderReport(
        levels=[LevelResult("L0", 900.0, 0.1, 3, 1.5),
                LevelResult("L1", 800.0, 0.1, 3, 1.4),
                LevelResult("L2", 750.0, 0.12, 3, 1.3),
                LevelResult("L3", 700.0, 0.12, 3, 1.2)],
        optimism_gap=200.0)
    card = scorecard(rep, ladder=ladder)
    assert isinstance(card, Scorecard)
    # cost cert 未显式给 → 从 train_report.certificate 拉取
    assert card.evidence_coverage["cost_certificate"] is True
    assert card.components["determinism"] == 100.0


# ---------------------------------------------------------------------------
# 边界 + JSON 安全
# ---------------------------------------------------------------------------
def test_composite_bounded_0_100_under_extremes():
    worst = scorecard(_report(300.0), _report(-90.0),
                      ladder=_ladder(50_000.0, 100.0, -500.0, -2_000.0),
                      cost_cert={**CERT, "deterministic_ratio": 0.0})
    best = scorecard(_report(50.0), _report(60.0),
                     ladder=_ladder(600.0, 600.0, 600.0, 600.0), cost_cert=CERT)
    for card in (worst, best):
        assert 0.0 <= card.composite <= 100.0


def test_to_dict_json_safe_inf_nan():
    train = _report(10.0, profit_factor=float("inf"))
    valid = _report(9.0, profit_factor=float("nan"))
    ladder = _ladder(1020.0, 1000.0, 980.0, 960.0)
    ladder["levels"][0]["profit_factor"] = float("inf")
    card = scorecard(train, valid, ladder=ladder, cost_cert=CERT)
    d = card.to_dict()
    json.dumps(d, allow_nan=False)   # 不得抛 —— inf/nan 必须已转 None
    assert d["train_summary"]["profit_factor"] is None
    assert d["valid_summary"]["profit_factor"] is None


def test_to_dict_carries_weights_and_components_for_critique():
    # 评分必须可解释可批判：weights + 分项得分随卡输出
    card = scorecard(_report(10.0), _report(9.0), cost_cert=CERT)
    d = card.to_dict()
    assert d["weights"] == WEIGHTS
    assert set(d["components"]) == set(WEIGHTS)
    assert "evidence_coverage" in d and "composite" in d
