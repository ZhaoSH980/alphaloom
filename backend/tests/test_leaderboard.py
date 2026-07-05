"""基线排行榜测试 —— buy-hold / 默认 ema / 随机基线 + 诚实排序（零 LLM 配额）。

锁定行为：
- buy_hold 手算对（首 bar 开盘全仓买入、末 bar 收盘卖出，fee 语义 = PaperBroker
  的 fee = qty*price*fee_rate 每腿收）。
- random 基线固定 seed 双跑一致（"运气基线"，披露在 certificate）。
- 排序默认按验证窗 return_pct；无 valid 用 train 并标 in_sample_only=true；
  蓝图打不过 buy_hold 时如实排在下面，零美化。
- 全部零 LLM 配额（纯数值 / 确定性蓝图，certificate.llm_calls_per_bar == 0）。
"""
from __future__ import annotations
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures.synth import gen_candles  # noqa: E402

from alphaloom.backtest.runner import BacktestReport
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.eval.leaderboard import (
    Board,
    baseline_buy_hold,
    baseline_ema_default,
    baseline_random,
    leaderboard,
)

INST, BAR = "BTC-USDT-SWAP", "1m"


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def _db(candles):
    tmp = Path(tempfile.mkdtemp())
    db = SQLiteMarketData(tmp / "m.sqlite")
    db.insert_candles(INST, BAR, candles)
    return db


# 已知答案 K 线：open0=100，末 close=120（手算 buy_hold 用）
KNOWN = [
    _bar(0, 100.0, 106.0, 99.0, 105.0),
    _bar(60_000, 105.0, 111.0, 104.0, 110.0),
    _bar(120_000, 110.0, 121.0, 109.0, 120.0),
]


# ---------------------------------------------------------------------------
# buy_hold：手算自证
# ---------------------------------------------------------------------------
def test_buy_hold_hand_calc_zero_fee():
    rep = baseline_buy_hold(_db(KNOWN), INST, BAR, initial_cash=10_000.0, fee_rate=0.0)
    # qty = 10000/100 = 100；买 @100（首 bar 开盘）卖 @120（末 bar 收盘）→ +2000
    assert math.isclose(rep.summary["net_pnl"], 2_000.0, abs_tol=1e-9)
    assert math.isclose(rep.summary["return_pct"], 20.0, abs_tol=1e-9)
    assert rep.summary["num_trades"] == 1 and rep.summary["win_rate"] == 1.0
    assert rep.bars == 3 and len(rep.equity_curve) == 3
    # 权益曲线按各 bar 收盘 mark：100*105 / 100*110 / 100*120
    assert [round(e, 6) for _, e in rep.equity_curve] == [10_500.0, 11_000.0, 12_000.0]
    assert rep.summary["max_drawdown"] == 0.0   # 单调上行无回撤


def test_buy_hold_hand_calc_with_fee_matches_paperbroker_semantics():
    fee_rate = 0.0005
    rep = baseline_buy_hold(_db(KNOWN), INST, BAR,
                            initial_cash=10_000.0, fee_rate=fee_rate)
    # 全仓含费买入：qty*(open0*(1+fee_rate)) = 10000 → qty = 10000/100.05
    qty = 10_000.0 / (100.0 * (1.0 + fee_rate))
    buy_fee = qty * 100.0 * fee_rate
    sell_fee = qty * 120.0 * fee_rate
    expected_net = qty * 120.0 - sell_fee - 10_000.0   # 买后现金恰为 0
    assert math.isclose(rep.summary["net_pnl"], expected_net, abs_tol=1e-6)
    # fee 语义与 PaperBroker._fill 完全一致：fee = qty*price*fee_rate 每腿收
    buy, sell = rep.fills
    assert buy["side"] == "buy" and buy["ts"] == 0
    assert math.isclose(buy["fee"], buy_fee, abs_tol=1e-9)
    # 结算腿 ts = 末 bar ts + bar_ms（对齐 runner/baseline_random 的 eod_close 语义）
    assert sell["side"] == "sell" and sell["ts"] == 180_000
    assert math.isclose(sell["fee"], sell_fee, abs_tol=1e-9)
    assert math.isclose(sell["price"], 120.0, abs_tol=1e-9)   # 末 bar 收盘价


def test_buy_hold_drawdown_on_dip():
    candles = [
        _bar(0, 100.0, 106.0, 99.0, 105.0),
        _bar(60_000, 105.0, 106.0, 88.0, 90.0),    # 深跌 bar
        _bar(120_000, 90.0, 121.0, 89.0, 120.0),
    ]
    rep = baseline_buy_hold(_db(candles), INST, BAR, fee_rate=0.0)
    # 权益 10500 → 9000 → 12000：max_dd = (10500-9000)/10500
    assert math.isclose(rep.summary["max_drawdown"], 1_500.0 / 10_500.0, abs_tol=1e-6)


def test_buy_hold_empty_data():
    rep = baseline_buy_hold(_db([]), INST, BAR)
    assert rep.summary["net_pnl"] == 0.0 and rep.summary["num_trades"] == 0
    assert rep.bars == 0 and rep.fills == []


def test_buy_hold_zero_llm_certificate():
    rep = baseline_buy_hold(_db(KNOWN), INST, BAR)
    assert rep.certificate["llm_calls_per_bar"] == 0
    assert rep.certificate["deterministic_ratio"] == 1.0


# ---------------------------------------------------------------------------
# random：固定 seed 可复现 + 运气基线披露
# ---------------------------------------------------------------------------
def test_random_baseline_same_seed_identical():
    candles = gen_candles(120, seed=5, trend=0.001)
    r1 = baseline_random(_db(candles), INST, BAR, seed=99)
    r2 = baseline_random(_db(candles), INST, BAR, seed=99)
    assert r1.summary == r2.summary
    assert r1.fills == r2.fills
    assert [e for _, e in r1.equity_curve] == [e for _, e in r2.equity_curve]


def test_random_baseline_discloses_luck():
    rep = baseline_random(_db(gen_candles(60, seed=6)), INST, BAR, seed=1)
    assert rep.certificate["luck_baseline"] is True     # 披露：这是运气基线
    assert rep.certificate["llm_calls_per_bar"] == 0
    assert rep.certificate["seed"] == 1


def test_random_baseline_ends_flat():
    rep = baseline_random(_db(gen_candles(120, seed=5)), INST, BAR, seed=3)
    signed = sum(f["qty"] if f["side"] == "buy" else -f["qty"] for f in rep.fills)
    assert abs(signed) < 1e-9    # 期末必平（残仓按末 bar 收盘结算）


# ---------------------------------------------------------------------------
# ema_default：默认参数蓝图跑真实回测引擎，纯确定性零 LLM
# ---------------------------------------------------------------------------
def test_ema_default_runs_offline_zero_llm():
    up = gen_candles(150, seed=11, trend=0.006)
    down = gen_candles(150, seed=12, trend=-0.006,
                       start_ts=up[-1]["ts"] + 60_000, start_price=up[-1]["close"])
    rep = baseline_ema_default(_db(up + down), INST, BAR)
    assert isinstance(rep, BacktestReport)
    assert rep.bars == 300
    assert rep.certificate["llm_calls_per_bar"] == 0     # 零 LLM 配额自证
    assert rep.certificate["deterministic_ratio"] == 1.0
    assert rep.blueprint_id == "ema_cross_v1"


# ---------------------------------------------------------------------------
# leaderboard：验证窗优先排序 + 诚实垫底
# ---------------------------------------------------------------------------
def _entry(name, kind, train_pct, valid_pct=None):
    def rep(pct):
        return {"summary": {"net_pnl": pct * 100.0, "return_pct": pct,
                            "max_drawdown": 0.1, "num_trades": 5,
                            "win_rate": 0.6, "profit_factor": 1.4}}
    e = {"name": name, "kind": kind, "train_report": rep(train_pct)}
    if valid_pct is not None:
        e["valid_report"] = rep(valid_pct)
    return e


def test_leaderboard_ranks_by_valid_return_blueprint_honestly_below_buyhold():
    # 蓝图样本内 +20% 吹上天，验证窗只有 +4%；buy_hold 验证窗 +12% → 蓝图必须在下面
    board = leaderboard([
        _entry("my_blueprint", "blueprint", 20.0, 4.0),
        _entry("buy_hold", "baseline", 8.0, 12.0),
        _entry("random", "baseline", 1.0, -2.0),
    ])
    names = [r["name"] for r in board.rows]
    assert names == ["buy_hold", "my_blueprint", "random"]   # 如实垫底，零美化
    bp = board.rows[1]
    assert bp["kind"] == "blueprint"
    assert math.isclose(bp["generalization_gap"], 16.0, abs_tol=1e-9)  # 20-4：过拟合暴露
    assert bp["in_sample_only"] is False
    assert bp["return_pct"] == 4.0            # 行指标取排序窗（验证窗），不是样本内 20


def test_leaderboard_row_shape():
    board = leaderboard([_entry("a", "baseline", 5.0, 3.0)])
    row = board.rows[0]
    assert set(row) == {"name", "kind", "net_pnl", "return_pct", "max_dd",
                        "win_rate", "num_trades", "generalization_gap",
                        "in_sample_only"}


def test_leaderboard_no_valid_marks_in_sample_only():
    board = leaderboard([
        _entry("with_valid", "baseline", 3.0, 6.0),
        _entry("train_only", "blueprint", 9.0),     # 无 valid → 用 train 排 + 打标
    ])
    by = {r["name"]: r for r in board.rows}
    assert by["train_only"]["in_sample_only"] is True
    assert by["train_only"]["generalization_gap"] is None
    assert by["train_only"]["return_pct"] == 9.0
    assert by["with_valid"]["in_sample_only"] is False
    # 排序仍按各自排序窗 return：9.0 > 6.0
    assert [r["name"] for r in board.rows] == ["train_only", "with_valid"]


def test_board_to_dict_json_safe():
    e = _entry("inf_pf", "baseline", 5.0, 3.0)
    e["valid_report"]["summary"]["profit_factor"] = float("inf")
    d = leaderboard([e]).to_dict()
    json.dumps(d, allow_nan=False)
    assert d["sort_key"] == "return_pct"
    assert d["rows"][0]["name"] == "inf_pf"


# ---------------------------------------------------------------------------
# 端到端：真实合成数据 train/valid 分窗，三基线 + ema 蓝图同台，零 LLM
# ---------------------------------------------------------------------------
def test_leaderboard_end_to_end_split_windows_zero_llm():
    candles = gen_candles(240, seed=21, trend=0.006)   # 强上行：buy_hold 难被打败
    db = _db(candles)
    t0, t1 = 0, 159 * 60_000                # train：前 160 根
    v0 = 160 * 60_000                       # valid：后 80 根
    entries = []
    for name, fn, kind in [
        ("buy_hold", baseline_buy_hold, "baseline"),
        ("ema_default", baseline_ema_default, "blueprint"),
    ]:
        entries.append({
            "name": name, "kind": kind,
            "train_report": fn(db, INST, BAR, t0, t1),
            "valid_report": fn(db, INST, BAR, v0, None),
        })
    entries.append({
        "name": "random", "kind": "baseline",
        "train_report": baseline_random(db, INST, BAR, t0, t1, seed=13),
        "valid_report": baseline_random(db, INST, BAR, v0, None, seed=13),
    })
    board = leaderboard(entries)
    # 排序正确：按验证窗 return_pct 降序
    rets = [r["return_pct"] for r in board.rows]
    assert rets == sorted(rets, reverse=True)
    assert all(r["in_sample_only"] is False for r in board.rows)
    assert all(r["generalization_gap"] is not None for r in board.rows)
    # 零 LLM 配额自证：所有真实 report 的成本证书 llm_calls_per_bar == 0
    for e in entries:
        for rep in (e["train_report"], e["valid_report"]):
            assert rep.certificate["llm_calls_per_bar"] == 0
    # 诚实性：强上行窗里 ema 蓝图若打不过 buy_hold，必须如实排在其下
    by = {r["name"]: r for r in board.rows}
    if by["ema_default"]["return_pct"] < by["buy_hold"]["return_pct"]:
        names = [r["name"] for r in board.rows]
        assert names.index("ema_default") > names.index("buy_hold")
