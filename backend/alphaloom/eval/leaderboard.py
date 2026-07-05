"""基线排行榜 —— 让 Agent 蓝图有对手（零 LLM 配额）。

`leaderboard(entries) -> Board`，entry = {name, train_report, valid_report?, kind}。

三个基线生成器（全部产出 BacktestReport 形状，certificate.llm_calls_per_bar=0）：
- **baseline_buy_hold**：首 bar 开盘全仓买入持有到末 bar 收盘。纯数值自算
  pnl/max_dd/权益曲线，不走蓝图引擎。**fee 语义与 PaperBroker._fill 完全一致**：
  fee = qty × price × fee_rate 每腿收；全仓 = 含入场费打满
  （qty = cash / (open₀ × (1+fee_rate))，买后现金恰为 0）。
- **baseline_ema_default**：默认参数 ema_cross 蓝图（blueprints/ema_cross.loom）
  跑真实 run_backtest——纯确定性节点、零 LLM。
- **baseline_random**：随机进出场（长仓开/平，固定 seed 可复现，决策次 bar
  开盘成交对齐 PaperBroker 时序）。**披露：这是"运气基线"**——certificate 带
  luck_baseline=True 与 seed；它存在的意义是给排行榜一个"纯运气能拿多少"的
  参照，任何打不过它的策略都不配谈信号。

诚实要求（不可妥协）：
- 排序默认按**验证窗** return_pct；无 valid 的行用 train 排序并标
  in_sample_only=true（行指标同样取排序窗口，绝不展示样本内的漂亮数字）。
- 蓝图打不过基线时如实排在下面，不做任何"美化"。
- generalization_gap = train.return_pct - valid.return_pct（无 valid = None），
  过拟合直接暴露在行上。

对齐 runner 的口径（便于同表可比）：权益曲线按各 bar 收盘 mark、长度=数据根数；
期末残仓按末 bar 收盘结算且结算腿不入权益曲线（同 run_backtest 的 eod_close）；
max_drawdown/win_rate/profit_factor 公式与 PaperBroker.summary 相同。
"""
from __future__ import annotations
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from alphaloom.backtest.runner import BacktestReport, run_backtest
from alphaloom.data.source import DataSource, bar_to_ms
from alphaloom.eval.scorecard import _json_safe, _summary_of
from alphaloom.graph.model import load_loom_file

# 默认参数 ema_cross 蓝图（仓库根 blueprints/，与测试 test_fidelity 同一路径解析）
_EMA_BLUEPRINT = Path(__file__).resolve().parents[3] / "blueprints" / "ema_cross.loom"

# 基线成本证书：纯数值零 LLM、全确定性（random 基线额外披露 luck_baseline/seed）
_BASELINE_CERT = {"llm_calls_per_bar": 0, "daily_token_ceiling": 0,
                  "worst_latency_class": "fast", "deterministic_ratio": 1.0}


# ---------------------------------------------------------------------------
# summary 口径（与 PaperBroker.summary 相同公式）
# ---------------------------------------------------------------------------
def _summarize(equity_values: list[float], round_trips: list[float],
               initial_cash: float, final_equity: float) -> dict:
    eq = equity_values or [initial_cash]
    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0.0)
    wins = [x for x in round_trips if x > 0]
    losses = [-x for x in round_trips if x < 0]
    return {
        "net_pnl": round(final_equity - initial_cash, 8),
        "return_pct": round((final_equity / initial_cash - 1) * 100, 4),
        "max_drawdown": round(max_dd, 6),
        "num_trades": len(round_trips),
        "win_rate": round(len(wins) / len(round_trips), 4) if round_trips else 0.0,
        "profit_factor": round(sum(wins) / sum(losses), 4) if losses
                         else (float("inf") if wins else 0.0),
        "halted": False,
        "halt_reason": "",
    }


def _fill(ts: int, side: str, qty: float, price: float, fee: float, tag: str) -> dict:
    return {"ts": int(ts), "side": side, "qty": qty, "price": price,
            "fee": fee, "tag": tag}


def _empty_report(blueprint_id: str, certificate: dict,
                  initial_cash: float) -> BacktestReport:
    return BacktestReport(run_id=f"{blueprint_id}-{uuid.uuid4().hex[:8]}",
                          blueprint_id=blueprint_id, bars=0,
                          summary=_summarize([], [], initial_cash, initial_cash),
                          certificate=certificate, equity_curve=[], fills=[])


# ---------------------------------------------------------------------------
# 基线 1：buy & hold
# ---------------------------------------------------------------------------
def baseline_buy_hold(source: DataSource, inst: str, bar: str,
                      start_ms: int | None = None, end_ms: int | None = None, *,
                      initial_cash: float = 10_000.0,
                      fee_rate: float = 0.0005) -> BacktestReport:
    """首 bar 开盘全仓买入，持有到末 bar 收盘卖出。纯数值，零 LLM。

    fee 语义 = PaperBroker：fee = qty × price × fee_rate 每腿收。全仓含费：
    qty = cash / (open₀ × (1+fee_rate))，买入后现金恰为 0。
    """
    cert = dict(_BASELINE_CERT, baseline="buy_hold")
    candles = list(source.iter_candles(inst, bar, start_ms, end_ms))
    if not candles:
        return _empty_report("baseline_buy_hold", cert, initial_cash)

    open0 = float(candles[0]["open"])
    qty = initial_cash / (open0 * (1.0 + fee_rate))
    buy_fee = qty * open0 * fee_rate
    cash = initial_cash - qty * open0 - buy_fee        # == 0（构造使然）
    equity = [(int(c["ts"]), cash + qty * float(c["close"])) for c in candles]

    last_px = float(candles[-1]["close"])
    sell_fee = qty * last_px * fee_rate
    final_cash = cash + qty * last_px - sell_fee
    trip = (last_px - open0) * qty - buy_fee - sell_fee
    # 结算腿 ts = 末 bar ts + bar_ms：与 runner / baseline_random 的 eod_close
    # 语义统一（数据外合成结算时刻），防下游按 ts 对齐时误当末 bar 内成交。
    fills = [_fill(candles[0]["ts"], "buy", qty, open0, buy_fee, "bh_entry"),
             _fill(int(candles[-1]["ts"]) + bar_to_ms(bar), "sell", qty,
                   last_px, sell_fee, "eod_close")]

    return BacktestReport(
        run_id=f"bh-{uuid.uuid4().hex[:8]}", blueprint_id="baseline_buy_hold",
        bars=len(candles),
        summary=_summarize([e for _, e in equity], [trip], initial_cash, final_cash),
        certificate=cert, equity_curve=equity, fills=fills)


# ---------------------------------------------------------------------------
# 基线 2：默认参数 ema_cross 蓝图（真实回测引擎，纯确定性零 LLM）
# ---------------------------------------------------------------------------
def baseline_ema_default(source: DataSource, inst: str, bar: str,
                         start_ms: int | None = None, end_ms: int | None = None, *,
                         initial_cash: float = 10_000.0,
                         fee_rate: float = 0.0005) -> BacktestReport:
    """blueprints/ema_cross.loom 以默认参数跑 run_backtest（llm=None）。"""
    bp = load_loom_file(_EMA_BLUEPRINT)
    return run_backtest(bp, source, inst=inst, bar=bar,
                        start_ms=start_ms, end_ms=end_ms,
                        initial_cash=initial_cash, fee_rate=fee_rate)


# ---------------------------------------------------------------------------
# 基线 3：随机进出场（"运气基线"，固定 seed 可复现）
# ---------------------------------------------------------------------------
def baseline_random(source: DataSource, inst: str, bar: str,
                    start_ms: int | None = None, end_ms: int | None = None, *,
                    seed: int = 7, trade_prob: float = 0.08,
                    initial_cash: float = 10_000.0,
                    fee_rate: float = 0.0005) -> BacktestReport:
    """随机长仓进出场：每根 bar 以 trade_prob 概率翻转（平→开 / 持→平），
    决策次 bar 开盘成交（对齐 PaperBroker 时序），期末残仓按末 bar 收盘结算。

    **披露：这是"运气基线"**——无任何信号，certificate.luck_baseline=True。
    固定 seed 可复现（同 seed 双跑逐 fill 一致）。
    """
    cert = dict(_BASELINE_CERT, baseline="random", luck_baseline=True, seed=seed)
    candles = list(source.iter_candles(inst, bar, start_ms, end_ms))
    if not candles:
        return _empty_report("baseline_random", cert, initial_cash)

    rng = random.Random(seed)
    cash, qty, avg_price, entry_fee = initial_cash, 0.0, 0.0, 0.0
    trips: list[float] = []
    fills: list[dict] = []
    equity: list[tuple[int, float]] = []
    pending: str | None = None            # 上根 bar 的决策，本根开盘执行

    for c in candles:
        ts, o = int(c["ts"]), float(c["open"])
        if pending == "buy" and qty == 0.0:
            q = cash / (o * (1.0 + fee_rate))
            fee = q * o * fee_rate
            cash -= q * o + fee
            qty, avg_price, entry_fee = q, o, fee
            fills.append(_fill(ts, "buy", q, o, fee, "rnd_entry"))
        elif pending == "sell" and qty > 0.0:
            fee = qty * o * fee_rate
            cash += qty * o - fee
            trips.append((o - avg_price) * qty - fee - entry_fee)
            fills.append(_fill(ts, "sell", qty, o, fee, "rnd_exit"))
            qty, avg_price, entry_fee = 0.0, 0.0, 0.0
        pending = None
        if rng.random() < trade_prob:
            pending = "sell" if qty > 0.0 else "buy"
        equity.append((ts, cash + qty * float(c["close"])))

    final_cash = cash
    if qty > 0.0:                         # 期末残仓：末 bar 收盘结算（对齐 runner eod_close）
        px = float(candles[-1]["close"])
        fee = qty * px * fee_rate
        final_cash = cash + qty * px - fee
        trips.append((px - avg_price) * qty - fee - entry_fee)
        fills.append(_fill(int(candles[-1]["ts"]) + bar_to_ms(bar), "sell",
                           qty, px, fee, "eod_close"))

    return BacktestReport(
        run_id=f"rnd-{uuid.uuid4().hex[:8]}", blueprint_id="baseline_random",
        bars=len(candles),
        summary=_summarize([e for _, e in equity], trips, initial_cash, final_cash),
        certificate=cert, equity_curve=equity, fills=fills)


# ---------------------------------------------------------------------------
# 排行榜
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Board:
    rows: list = field(default_factory=list)   # 已按排序窗 return_pct 降序
    sort_key: str = "return_pct"

    def to_dict(self) -> dict:
        return _json_safe({"rows": self.rows, "sort_key": self.sort_key,
                           "ranking_window": "valid_first"})


def leaderboard(entries) -> Board:
    """entry = {name, train_report, valid_report?, kind: "blueprint"|"baseline"}。

    行指标取**排序窗口**（有 valid 用 valid，否则用 train 并标
    in_sample_only=true）——绝不用样本内数字给无验证窗的行贴金。
    排序按 return_pct 降序（平手按 net_pnl、再按 name 保证稳定）。
    蓝图打不过基线时如实垫底，零美化。
    """
    rows = []
    for e in entries:
        train = _summary_of(e["train_report"])
        valid = _summary_of(e.get("valid_report"))
        ranked = valid if valid is not None else train
        gap = None
        if valid is not None:
            gap = round(float(train.get("return_pct", 0.0))
                        - float(valid.get("return_pct", 0.0)), 6)
        rows.append({
            "name": e["name"],
            "kind": e.get("kind", "blueprint"),
            "net_pnl": ranked.get("net_pnl", 0.0),
            "return_pct": ranked.get("return_pct", 0.0),
            "max_dd": ranked.get("max_drawdown", 0.0),
            "win_rate": ranked.get("win_rate", 0.0),
            "num_trades": ranked.get("num_trades", 0),
            "generalization_gap": gap,
            "in_sample_only": valid is None,
        })
    rows.sort(key=lambda r: (-r["return_pct"], -r["net_pnl"], r["name"]))
    return Board(rows=rows, sort_key="return_pct")
