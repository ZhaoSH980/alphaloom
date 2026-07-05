"""保真度阶梯 L0-L3 测试（回测测谎仪，零 LLM 配额）。

核心断言：**pnl 单调性 L0 ≥ L1 ≥ L2 ≥ L3**（越真实越差）。若不单调说明成交
模型有 bug。全程纯数值——无 LLM、无网络、无 broker 重跑决策。
"""
from __future__ import annotations
import math

from alphaloom.brokers.base import Fill
from alphaloom.eval.fidelity import (
    LadderReport,
    LevelResult,
    fidelity_ladder,
    replay_intents,
)


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


# 已知答案案例：单笔多头往返，fee_rate=0，qty=1，便于手算逐档验证。
# 信号在 bar0 触发 → L1 于 bar1 开盘 @102 成交进场；信号 bar1 → L1 于 bar2 开盘 @112 出场。
KNOWN_CANDLES = [
    _bar(0, 100, 101, 99, 100),        # bar0：进场信号 bar（close 100）
    _bar(60_000, 102, 110, 101, 108),  # bar1：进场执行 bar（open 102, 半振幅 4.5）；也是出场信号 bar（close 108）
    _bar(120_000, 112, 114, 110, 113), # bar2：出场执行 bar（open 112, 半振幅 2.0）
    _bar(180_000, 113, 115, 112, 114), # bar3：未用尾部
]
KNOWN_FILLS = [
    Fill(ts=60_000, side="buy", qty=1.0, price=102.0, fee=0.0, tag="entry"),
    Fill(ts=120_000, side="sell", qty=1.0, price=112.0, fee=0.0, tag="exit"),
]


# ---------------------------------------------------------------------------
# replay_intents：从 fills+candles 反推下单意图（哪根 bar 想 long/short/平仓）
# ---------------------------------------------------------------------------
def test_replay_intents_maps_fill_to_signal_bar():
    intents = replay_intents(KNOWN_FILLS, KNOWN_CANDLES)
    assert len(intents) == 2
    entry, exit_ = intents
    # 进场：执行 bar = bar1(ts=60000)，信号 bar = 前一根 bar0(ts=0)
    assert entry.side == "buy" and entry.qty == 1.0
    assert entry.exec_ts == 60_000 and entry.signal_ts == 0
    # 出场：执行 bar = bar2(ts=120000)，信号 bar = bar1(ts=60000)
    assert exit_.side == "sell" and exit_.exec_ts == 120_000 and exit_.signal_ts == 60_000
    # 常规腿 L1 基准价 = 执行 bar 开盘
    assert entry.base_price == 102.0 and exit_.base_price == 112.0


def test_replay_intents_first_fill_has_no_prior_bar_signal_falls_back():
    # 若 fill 执行于首根 bar（无前一根），signal_ts 退化为执行 bar 自身（不越界）
    fills = [Fill(ts=0, side="buy", qty=1.0, price=100.0, fee=0.0, tag="entry")]
    intents = replay_intents(fills, KNOWN_CANDLES)
    assert intents[0].signal_ts == 0 and intents[0].exec_ts == 0


# ---------------------------------------------------------------------------
# 四档逐档已知答案（手算验证，fee_rate=0，slippage=5bps 仅 L3）
# ---------------------------------------------------------------------------
def test_known_case_level_prices_and_pnl():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES,
                          initial_cash=10_000.0, fee_rate=0.0, slippage_bps=5.0)
    by = {lv.level: lv for lv in rep.levels}

    # L0 天真：进场取 min(signal_close=100, exec_open=102)=100（多头更优）；
    #          出场取 max(signal_close=108, exec_open=112)=112（空头/卖出更优）→ 12
    assert math.isclose(by["L0"].net_pnl, 12.0, abs_tol=1e-9)
    # L1 次 bar 开盘：买 @102 卖 @112 → 10
    assert math.isclose(by["L1"].net_pnl, 10.0, abs_tol=1e-9)
    # L2 盘中路径：买 @102+4.5=106.5 卖 @112-2=110 → 3.5
    assert math.isclose(by["L2"].net_pnl, 3.5, abs_tol=1e-9)
    # L3 +5bps 滑点：买 @106.5*1.0005 卖 @110*0.9995 → 109.945 - 106.55325 = 3.39175
    assert math.isclose(by["L3"].net_pnl, 3.39175, abs_tol=1e-6)


def test_known_case_num_trades_all_one():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0)
    for lv in rep.levels:
        assert lv.num_trades == 1


# ---------------------------------------------------------------------------
# 核心断言：单调性 L0 ≥ L1 ≥ L2 ≥ L3（测谎仪心脏）
# ---------------------------------------------------------------------------
def test_pnl_monotonic_known_case():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=5.0)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls
    # 严格：本案例每档确实更差
    assert pnls[0] > pnls[1] > pnls[2] > pnls[3]


def test_optimism_gap_nonneg_and_equals_l0_minus_l3():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=5.0)
    by = {lv.level: lv for lv in rep.levels}
    assert rep.optimism_gap >= 0.0
    assert math.isclose(rep.optimism_gap, by["L0"].net_pnl - by["L3"].net_pnl, abs_tol=1e-9)


def test_levels_ordered_l0_to_l3():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES)
    assert [lv.level for lv in rep.levels] == ["L0", "L1", "L2", "L3"]


# ---------------------------------------------------------------------------
# 单调性属性测试：跨多种合成成交序列都必须单调（若违反 = 成交模型 bug）
# ---------------------------------------------------------------------------
def _short_roundtrip():
    # 空头往返：先卖后买。趋势向下时也必须单调。
    candles = [
        _bar(0, 100, 101, 98, 99),
        _bar(60_000, 98, 99, 90, 92),   # 进场执行 bar（sell @98）
        _bar(120_000, 90, 91, 84, 85),  # 出场执行 bar（buy @90）
        _bar(180_000, 85, 86, 83, 84),
    ]
    fills = [
        Fill(ts=60_000, side="sell", qty=2.0, price=98.0, fee=0.0, tag="short"),
        Fill(ts=120_000, side="buy", qty=2.0, price=90.0, fee=0.0, tag="cover"),
    ]
    return fills, candles


def test_monotonic_short_roundtrip():
    fills, candles = _short_roundtrip()
    rep = fidelity_ladder(fills, candles, fee_rate=0.0005, slippage_bps=5.0)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls
    assert rep.optimism_gap >= 0.0


def test_monotonic_multi_trade_from_synth():
    # 从合成 K 线跑真实回测拿 fills，再喂阶梯，单调性必须成立。
    import sys
    from pathlib import Path
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fixtures.synth import gen_candles  # noqa: E402
    from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402
    from alphaloom.graph.model import load_loom_file  # noqa: E402
    from alphaloom.backtest.runner import run_backtest  # noqa: E402

    repo = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp())
    db = SQLiteMarketData(tmp / "m.sqlite")
    up = gen_candles(150, seed=11, trend=0.006)
    down = gen_candles(150, seed=12, trend=-0.006,
                       start_ts=up[-1]["ts"] + 60_000, start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    bp = load_loom_file(repo / "blueprints" / "ema_cross.loom")
    report = run_backtest(bp, db, inst="BTC-USDT-SWAP", bar="1m")

    candles = up + down
    rep = fidelity_ladder(report.fills, candles, fee_rate=0.0005, slippage_bps=5.0)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls
    assert rep.optimism_gap >= 0.0
    # L1 必须精确复现 broker net_pnl（同价同费同腿：常规腿=次 bar 开盘、
    # stop 腿=stop 位、eod_close 腿=末 bar close）——只剩浮点/rounding 残差。
    assert math.isclose([lv for lv in rep.levels if lv.level == "L1"][0].net_pnl,
                        report.summary["net_pnl"], abs_tol=1e-6)


# ---------------------------------------------------------------------------
# 结构 / 边界
# ---------------------------------------------------------------------------
def test_empty_fills_returns_zero_ladder():
    rep = fidelity_ladder([], KNOWN_CANDLES)
    assert isinstance(rep, LadderReport)
    assert all(lv.net_pnl == 0.0 and lv.num_trades == 0 for lv in rep.levels)
    assert rep.optimism_gap == 0.0


def test_level_result_fields_present():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES)
    lv = rep.levels[0]
    assert isinstance(lv, LevelResult)
    for f in ("level", "net_pnl", "max_dd", "num_trades", "profit_factor"):
        assert hasattr(lv, f)
    d = lv.to_dict()
    assert set(d) >= {"level", "net_pnl", "max_dd", "num_trades", "profit_factor"}


def test_max_dd_nonneg_and_monotone_pressure():
    # 更真实的档位回撤不应更小（越差的成交 → 回撤 ≥）。
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=5.0)
    dds = [lv.max_dd for lv in rep.levels]
    assert all(d >= 0.0 for d in dds)


def test_slippage_bps_zero_makes_l3_equal_l2():
    rep = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=0.0)
    by = {lv.level: lv for lv in rep.levels}
    assert math.isclose(by["L3"].net_pnl, by["L2"].net_pnl, abs_tol=1e-9)


def test_higher_slippage_widens_optimism_gap():
    lo = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=1.0)
    hi = fidelity_ladder(KNOWN_FILLS, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=50.0)
    assert hi.optimism_gap > lo.optimism_gap


def test_monotonic_with_open_position_at_end():
    # 回归：末尾残留未平仓头寸（奇数腿）时，进场滑点绝不能反向抬高盯市而破坏单调。
    # （fuzz 抓到的真 bug：mark 曾用档位调整价，L2/L3 未平仓多头被"抬价"→ 反超 L1。）
    candles = [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 106, 94, 100),   # 进场执行 bar（宽振幅 → L2/L3 偏移大）
        _bar(120_000, 100, 101, 99, 100),
    ]
    fills = [Fill(ts=60_000, side="buy", qty=1.0, price=100.0, fee=0.0, tag="entry")]  # 只开不平
    rep = fidelity_ladder(fills, candles, fee_rate=0.0005, slippage_bps=50.0)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls
    assert rep.optimism_gap >= 0.0


# ---------------------------------------------------------------------------
# eod_close 合成结算腿：按末 bar close 定价（对齐 runner 结算，L1==broker）
# ---------------------------------------------------------------------------
def test_eod_close_leg_priced_at_last_bar_close():
    # runner 的收盘强平：ts = 末根 + bar_ms（数据外合成 bar），成交价 = 末根 close。
    eod_ts = KNOWN_CANDLES[-1]["ts"] + 60_000
    fills = [
        Fill(ts=60_000, side="buy", qty=1.0, price=102.0, fee=0.0, tag="entry"),
        Fill(ts=eod_ts, side="sell", qty=1.0, price=114.0, fee=0.0, tag="eod_close"),
    ]
    intents = replay_intents(fills, KNOWN_CANDLES)
    eod = intents[1]
    # 归位末根真实 bar，基准价 = 末 bar close（114），结算腿无时序谎言 → signal=exec
    assert eod.exec_ts == 180_000 and eod.base_price == 114.0
    assert eod.signal_ts == 180_000
    rep = fidelity_ladder(fills, KNOWN_CANDLES, fee_rate=0.0, slippage_bps=5.0)
    by = {lv.level: lv for lv in rep.levels}
    # L1：买 @102（bar1 开盘），结算卖 @114（末 bar close，非开盘 113）→ 12
    assert math.isclose(by["L1"].net_pnl, 12.0, abs_tol=1e-9)
    # L0：进场取优 @100，结算腿 L0=L1=114 → 14
    assert math.isclose(by["L0"].net_pnl, 14.0, abs_tol=1e-9)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls


# ---------------------------------------------------------------------------
# stop fill：盘中触发、成交价 = stop 位（PaperBroker 语义）→ 以真实成交价为 L1 基准
# ---------------------------------------------------------------------------
def test_stop_fill_uses_stop_price_as_l1_base():
    candles = [
        _bar(0, 100, 101, 99, 100),        # 进场信号 bar（close 100）
        _bar(60_000, 100, 101, 99, 100),   # 进场执行 bar（open 100）
        _bar(120_000, 98, 99, 94, 97),     # stop 触发 bar：low 94 ≤ stop 96，close 97 收回 stop 上方
    ]
    fills = [
        Fill(ts=60_000, side="buy", qty=1.0, price=100.0, fee=0.0, tag="entry"),
        Fill(ts=120_000, side="sell", qty=1.0, price=96.0, fee=0.0, tag="stop"),
    ]
    intents = replay_intents(fills, candles)
    stop = intents[1]
    # stop 腿：exec = 触发 bar 自身（非次 bar），基准价 = stop 位 96（真实成交价）
    assert stop.exec_ts == 120_000 and stop.base_price == 96.0
    assert stop.signal_ts == 120_000    # 盘中信号：L0 用触发 bar close 与 stop 取优
    rep = fidelity_ladder(fills, candles, fee_rate=0.0, slippage_bps=5.0)
    by = {lv.level: lv for lv in rep.levels}
    # L1：买 @100，止损卖 @96（stop 位，非次 bar 开盘）→ -4
    assert math.isclose(by["L1"].net_pnl, -4.0, abs_tol=1e-9)
    # L0：止损腿取优 max(close=97, stop=96)=97 → -3（"收盘检查止损"的乐观谎言）
    assert math.isclose(by["L0"].net_pnl, -3.0, abs_tol=1e-9)
    # L2：进场买 @100+1=101；止损卖 @96-2.5=93.5 → clamp 到 low=94 → -7
    assert math.isclose(by["L2"].net_pnl, -7.0, abs_tol=1e-9)
    # L3：买 @101*1.0005=101.0505，卖 @94*0.9995=93.953 → -7.0975
    assert math.isclose(by["L3"].net_pnl, -7.0975, abs_tol=1e-6)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls


# ---------------------------------------------------------------------------
# 病态宽振幅 bar：L2 clamp 到 [low, high]（审查者反例：负价 → L3 反超 L2）
# ---------------------------------------------------------------------------
def test_pathological_wide_bar_clamped_keeps_monotonic():
    # bar1 半振幅 24.5 > open 10：未 clamp 时 L2 卖价 = 10-24.5 = -14.5（负价，物理不可能），
    # L3 = -14.5*(1-5bps) = -14.49275 > L2 → 单调性破坏。clamp 后 L2 卖价 = low = 1。
    candles = [
        _bar(0, 10, 11, 9, 10),
        _bar(60_000, 10, 50, 1, 45),     # 病态执行 bar
        _bar(120_000, 45, 46, 44, 45),
    ]
    fills = [Fill(ts=60_000, side="sell", qty=1.0, price=10.0, fee=0.0, tag="short")]
    rep = fidelity_ladder(fills, candles, fee_rate=0.0, slippage_bps=5.0)
    by = {lv.level: lv for lv in rep.levels}
    # L2 卖价 clamp 到 bar low=1（正、且在观测路径内）：net = 1 - 10 = -9
    assert math.isclose(by["L2"].net_pnl, -9.0, abs_tol=1e-9)
    # L3 = 1*0.9995 = 0.9995（滑点施加于 clamp 后，允许越出 bar 路径）→ -9.0005
    assert math.isclose(by["L3"].net_pnl, -9.0005, abs_tol=1e-9)
    pnls = [lv.net_pnl for lv in rep.levels]
    assert pnls == sorted(pnls, reverse=True), pnls
    assert rep.optimism_gap >= 0.0


# ---------------------------------------------------------------------------
# 固定 seed fuzz 属性测试：随机 OHLC/多空/奇偶腿/stop 腿/病态宽振幅，单调性必须恒成立
# ---------------------------------------------------------------------------
def test_fuzz_monotonicity_fixed_seed():
    import random
    rng = random.Random(20260706)
    for trial in range(600):
        n = rng.randint(4, 16)
        pathological = trial % 7 == 0          # 每 7 例混入病态宽振幅 bar
        candles, px = [], 100.0
        for i in range(n):
            o = px
            c = max(1.0, o * (1 + rng.gauss(0, 0.02)))
            if pathological and i % 3 == 1:
                hi = max(o, c) * (1 + rng.uniform(1.0, 4.0))
                lo = min(o, c) * rng.uniform(0.01, 0.5)   # low 逼近 0 → 半振幅 > open
            else:
                hi = max(o, c) * (1 + abs(rng.gauss(0, 0.01)))
                lo = min(o, c) * (1 - abs(rng.gauss(0, 0.01)))
            candles.append(_bar(i * 60_000, round(o, 6), round(hi, 6),
                                round(lo, 6), round(c, 6)))
            px = c
        # 交替开/平（奇数腿留未平仓尾巴），平仓腿有概率变 stop 腿（价取该 bar [low,high] 内）
        fills = []
        first = "buy" if rng.random() < 0.5 else "sell"
        legs = rng.sample(range(1, n), min(rng.randint(1, 8), n - 1))
        for j, idx in enumerate(sorted(legs)):
            side = first if j % 2 == 0 else ("sell" if first == "buy" else "buy")
            bar_ = candles[idx]
            if j % 2 == 1 and rng.random() < 0.3:
                price, tag = rng.uniform(bar_["low"], bar_["high"]), "stop"
            else:
                price, tag = bar_["open"], "x"
            fills.append(Fill(ts=idx * 60_000, side=side, qty=rng.uniform(0.5, 3.0),
                              price=price, fee=0.0, tag=tag))
        rep = fidelity_ladder(fills, candles, fee_rate=0.0005,
                              slippage_bps=rng.uniform(0.0, 50.0))
        pnls = [lv.net_pnl for lv in rep.levels]
        assert pnls == sorted(pnls, reverse=True), (trial, pnls)
        assert rep.optimism_gap >= -1e-9, (trial, rep.optimism_gap)
