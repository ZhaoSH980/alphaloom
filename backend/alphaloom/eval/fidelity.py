"""保真度阶梯 L0-L3 —— 回测测谎仪（零 LLM 配额）。

把一次回测**已生成的成交序列（Fill 列表）**在四档成交模型下**重新撮合**，
量化"回测在哪一档开始撒谎"。**不重跑 LLM**：决策（哪根 bar 想 long/short/平仓）
不变，只变成交撮合假设，故零配额、纯数值。

四档成交模型（越往下越真实、越悲观）：
- **L0 天真收盘成交**：最乐观——进出场都取"信号 bar 收盘价 vs 次 bar 开盘价"中对
  交易者更有利的一侧（无滑点、无时序延迟惩罚）。
- **L1 次 bar 开盘**：PaperBroker 现状（D1 基线）——信号次 bar 开盘价成交。
- **L2 盘中路径代理**：用执行 bar 的 OHLC 路径估更差成交——进出场都在次 bar 开盘价
  上叠加"半个 bar 振幅 (high-low)/2"的**不利偏移**（买更贵、卖更便宜）。
- **L3 手续费+滑点加压**：L2 之上再叠加 `slippage_bps` 名义额滑点（永远不利）。

**单调性契约（测谎仪心脏）**：net_pnl L0 ≥ L1 ≥ L2 ≥ L3。实现保证方式——
每档对每笔成交施加一个**逐档单调非减的不利价格偏移**（相对 L1 基准价）：
L0 偏移 ≤ 0（更优）≤ L1(=0) ≤ L2 ≤ L3。故净利单调下降**按构造成立**，与行情
方向无关（涨跌都单调）。若测试发现不单调 → 成交模型有 bug。
"""
from __future__ import annotations
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 下单意图：从 fills 反推——保真度阶梯需要"哪根 bar 想成交、往哪个方向"才能在
# 不同成交模型下重放。每笔 Fill 即一个意图：其 ts 是 L1 语义下的执行 bar，信号
# bar 是紧邻前一根（次 bar 开盘成交语义）。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Intent:
    side: str            # "buy" | "sell"
    qty: float
    exec_ts: int         # L1 执行 bar 的 ts（= 原 Fill.ts）
    signal_ts: int       # 信号 bar 的 ts（紧邻 exec 前一根；首根无前根则退化为 exec_ts）
    tag: str = ""


def _fill_field(f, name):
    """兼容 Fill 数据类与 run_backtest 产出的 dict（report.fills 是 f.__dict__）。"""
    return f[name] if isinstance(f, dict) else getattr(f, name)


def replay_intents(fills, candles) -> list[Intent]:
    """从 fills + candles 反推下单意图序列。

    每笔 fill 的 ts 对应 L1 执行 bar；信号 bar = candles 中紧邻其前的一根。
    首根 bar 执行（无前根）时 signal_ts 退化为执行 bar 自身，避免越界。
    fills 可为 Fill 对象或 dict（run_backtest 的 report.fills）。
    """
    ts_list = [int(c["ts"]) for c in candles]
    ts_index = {t: i for i, t in enumerate(ts_list)}
    last_ts = ts_list[-1] if ts_list else 0
    intents: list[Intent] = []
    for f in fills:
        raw_ts = int(_fill_field(f, "ts"))
        # eod_close 等收盘强平的 ts = 末根+bar_ms（数据外合成 bar，不在 candles）。
        # 归位到最后一根真实 bar，其 close 即 runner 的 EOD 结算价。
        exec_ts = raw_ts if raw_ts in ts_index else last_ts
        i = ts_index.get(exec_ts)
        if i is not None and i > 0:
            signal_ts = ts_list[i - 1]
        else:
            signal_ts = exec_ts
        intents.append(Intent(side=_fill_field(f, "side"),
                              qty=float(_fill_field(f, "qty")),
                              exec_ts=exec_ts, signal_ts=signal_ts,
                              tag=_fill_field(f, "tag")))
    return intents


# ---------------------------------------------------------------------------
# 逐档成交定价
# ---------------------------------------------------------------------------
_LEVELS = ("L0", "L1", "L2", "L3")


def _adverse_sign(side: str) -> float:
    """不利方向：买入价格越高越差(+1)，卖出价格越低越差(-1)。"""
    return 1.0 if side == "buy" else -1.0


def _price_for_level(level: str, intent: Intent, by_ts: dict,
                     slippage_bps: float) -> float:
    """给定档位算某笔意图的成交价。所有偏移相对 L1 基准价（次 bar 开盘）单调施加。"""
    exec_bar = by_ts[intent.exec_ts]
    base = float(exec_bar["open"])          # L1 基准价：次 bar 开盘
    s = _adverse_sign(intent.side)          # +1 买 / -1 卖
    half_range = (float(exec_bar["high"]) - float(exec_bar["low"])) / 2.0
    if half_range < 0:
        half_range = 0.0

    if level == "L1":
        return base
    if level == "L0":
        # 最乐观：信号 bar 收盘 vs 次 bar 开盘，取对交易者更有利一侧。
        sig_bar = by_ts.get(intent.signal_ts)
        sig_close = float(sig_bar["close"]) if sig_bar is not None else base
        # 买入取更低价，卖出取更高价 → 相对 base 的偏移 ≤ 0（不利量 ≤ L1）。
        return min(base, sig_close) if intent.side == "buy" else max(base, sig_close)
    if level == "L2":
        # 盘中路径代理：叠加半振幅不利偏移（买 +，卖 -）。
        return base + s * half_range
    # L3：L2 + 额外 slippage_bps 名义额滑点（永远不利）。
    l2 = base + s * half_range
    return l2 * (1.0 + s * slippage_bps / 10_000.0)


# ---------------------------------------------------------------------------
# 逐档重放撮合：按意图顺序推进有符号持仓，平仓时结算一笔往返 pnl。
# fee 在 L0/L1/L2 用 fee_rate，L3 额外滑点已并入成交价（不重复计）。
# ---------------------------------------------------------------------------
def _replay_level(level: str, intents: list[Intent], by_ts: dict, *,
                  initial_cash: float, fee_rate: float,
                  slippage_bps: float) -> "LevelResult":
    cash = initial_cash
    pos_qty = 0.0            # 有符号：+多 -空
    avg_price = 0.0
    entry_fee_acc = 0.0
    round_trips: list[float] = []
    equity_curve: list[float] = [initial_cash]
    mark = 0.0              # 持仓 mark 价：始终用 L1 基准价（次 bar 开盘，无档位不利偏移）

    for it in intents:
        price = _price_for_level(level, it, by_ts, slippage_bps)
        # mark 用 L1 基准价（各档一致）——档位差异只体现在成交价(price)与 fee 上，
        # 不污染未平仓头寸的盯市，否则未平仓的进场滑点会反向抬高盯市、破坏单调性。
        mark = float(by_ts[it.exec_ts]["open"])
        fee = it.qty * price * fee_rate
        signed = it.qty if it.side == "buy" else -it.qty
        closing = (pos_qty > 0 > signed) or (pos_qty < 0 < signed)
        crossed = closing and abs(signed) > abs(pos_qty)

        if closing:
            closed_qty = min(abs(pos_qty), abs(signed))
            pnl = (price - avg_price) * closed_qty * (1 if pos_qty > 0 else -1)
            net = pnl - fee - entry_fee_acc
            round_trips.append(net)
            entry_fee_acc = 0.0
        else:
            entry_fee_acc += fee

        new_qty = pos_qty + signed
        if not closing and (pos_qty == 0 or abs(new_qty) > abs(pos_qty)):
            total = avg_price * abs(pos_qty) + price * abs(signed)
            avg_price = total / (abs(pos_qty) + abs(signed))
        if crossed:
            avg_price = price
        if abs(new_qty) < 1e-12:
            new_qty = 0.0
            avg_price = 0.0
        pos_qty = new_qty
        cash -= signed * price + fee
        equity_curve.append(cash + pos_qty * mark)

    net_pnl = (cash + pos_qty * mark) - initial_cash
    return LevelResult(
        level=level,
        net_pnl=round(net_pnl, 8),
        max_dd=round(_max_drawdown(equity_curve), 6),
        num_trades=len(round_trips),
        profit_factor=_profit_factor(round_trips),
    )


def _max_drawdown(equity: list[float]) -> float:
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def _profit_factor(round_trips: list[float]) -> float:
    wins = sum(x for x in round_trips if x > 0)
    losses = -sum(x for x in round_trips if x < 0)
    if losses > 0:
        return round(wins / losses, 4)
    return float("inf") if wins > 0 else 0.0


# ---------------------------------------------------------------------------
# 报告结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LevelResult:
    level: str
    net_pnl: float
    max_dd: float
    num_trades: int
    profit_factor: float

    def to_dict(self) -> dict:
        pf = self.profit_factor
        return {
            "level": self.level,
            "net_pnl": self.net_pnl,
            "max_dd": self.max_dd,
            "num_trades": self.num_trades,
            "profit_factor": (None if pf == float("inf") else pf),
        }


@dataclass(frozen=True)
class LadderReport:
    levels: list[LevelResult] = field(default_factory=list)
    optimism_gap: float = 0.0   # L0.net_pnl - L3.net_pnl（≥0；越大回测越乐观）

    def to_dict(self) -> dict:
        return {
            "levels": [lv.to_dict() for lv in self.levels],
            "optimism_gap": self.optimism_gap,
        }


def fidelity_ladder(fills, candles, *, initial_cash: float = 10_000.0,
                    fee_rate: float = 0.0005,
                    slippage_bps: float = 5.0) -> LadderReport:
    """把 fills 在四档成交模型下重放，产出各档 {level, net_pnl, max_dd,
    num_trades, profit_factor} 与 optimism_gap = L0_net_pnl - L3_net_pnl。

    纯数值、零 LLM、零网络。单调性 L0 ≥ L1 ≥ L2 ≥ L3 按构造成立。
    """
    by_ts = {int(c["ts"]): c for c in candles}
    intents = replay_intents(fills, candles)
    levels = [
        _replay_level(lv, intents, by_ts, initial_cash=initial_cash,
                      fee_rate=fee_rate, slippage_bps=slippage_bps)
        for lv in _LEVELS
    ]
    by = {lv.level: lv for lv in levels}
    gap = round(by["L0"].net_pnl - by["L3"].net_pnl, 8)
    return LadderReport(levels=levels, optimism_gap=gap)
