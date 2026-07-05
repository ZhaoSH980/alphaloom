from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker

def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}

def test_market_fill_next_bar_open():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0))
    assert b.fills == []
    b.on_bar(_bar(60_000, 12, 13, 11, 12))
    assert len(b.fills) == 1 and b.fills[0].price == 12.0
    assert b.position().qty == 1.0

def test_stop_loss_triggers():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0, stop=8.0))
    b.on_bar(_bar(60_000, 10, 11, 9, 10))
    b.on_bar(_bar(120_000, 9, 9.5, 7.5, 8.5))
    assert b.position().qty == 0.0
    exit_fill = b.fills[-1]
    assert exit_fill.side == "sell" and exit_fill.price == 8.0

def test_equity_and_summary():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    bars = [_bar(0, 10, 11, 9, 10), _bar(60_000, 10, 12, 10, 12),
            _bar(120_000, 12, 13, 11, 13), _bar(180_000, 13, 13, 12, 12)]
    b.on_bar(bars[0]); b.submit(Order(side="buy", qty=1.0))
    b.on_bar(bars[1])
    b.on_bar(bars[2]); b.submit(Order(side="sell", qty=1.0))
    b.on_bar(bars[3])
    assert b.equity() == 1000.0 + 3.0
    s = b.summary()
    assert s["num_trades"] == 1 and s["net_pnl"] == 3.0
    assert s["win_rate"] == 1.0 and s["max_drawdown"] >= 0.0
    assert len(b.equity_curve) == 4

def test_fee_applied():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.001)
    b.on_bar(_bar(0, 10, 10, 10, 10))
    b.submit(Order(side="buy", qty=2.0))
    b.on_bar(_bar(60_000, 10, 10, 10, 10))
    assert b.fills[0].fee == 2.0 * 10 * 0.001

def test_halted_broker_rejects():
    b = PaperBroker(initial_cash=1000.0)
    b.halt("kill switch")
    assert b.submit(Order(side="buy", qty=1.0)) is False

def test_reversal_resets_avg_price():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 10, 10, 10))
    b.submit(Order(side="buy", qty=2.0))
    b.on_bar(_bar(60_000, 10, 10, 10, 10))      # 多 2 @10
    b.submit(Order(side="sell", qty=3.0))
    b.on_bar(_bar(120_000, 12, 12, 12, 12))     # 反手：平 2 开空 1 @12
    p = b.position()
    assert p.qty == -1.0 and p.avg_price == 12.0
    assert b.summary()["num_trades"] == 1        # 只有平掉的 2 手计一笔往返
