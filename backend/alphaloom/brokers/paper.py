from __future__ import annotations
from alphaloom.brokers.base import Order, Fill, Position

class PaperBroker:
    def __init__(self, initial_cash: float = 10_000.0, fee_rate: float = 0.0005):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self._pos = Position()
        self._pending: list[Order] = []
        self.fills: list[Fill] = []
        self.equity_curve: list[tuple[int, float]] = []
        self._round_trips: list[float] = []
        self._entry_cost = 0.0
        self._halted = False
        self._halt_reason = ""
        self._last_close = 0.0

    def submit(self, order: Order) -> bool:
        if self._halted:
            return False
        self._pending.append(order)
        return True

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason

    @property
    def halted(self) -> bool:
        return self._halted

    def position(self) -> Position:
        return self._pos

    def equity(self) -> float:
        return self.cash + self._pos.qty * self._last_close

    def last_price(self) -> float:
        return self._last_close

    def on_bar(self, candle: dict) -> None:
        o = float(candle["open"])
        pending, self._pending = self._pending, []
        for od in pending:
            self._fill(int(candle["ts"]), od, o)
        p = self._pos
        if p.qty > 0 and p.stop is not None and float(candle["low"]) <= p.stop:
            self._fill(int(candle["ts"]), Order("sell", p.qty, tag="stop"), p.stop)
        elif p.qty < 0 and p.stop is not None and float(candle["high"]) >= p.stop:
            self._fill(int(candle["ts"]), Order("buy", -p.qty, tag="stop"), p.stop)
        self._last_close = float(candle["close"])
        self.equity_curve.append((int(candle["ts"]), self.equity()))

    def _fill(self, ts: int, od: Order, price: float) -> None:
        fee = od.qty * price * self.fee_rate
        signed = od.qty if od.side == "buy" else -od.qty
        p = self._pos
        closing = (p.qty > 0 > signed) or (p.qty < 0 < signed)
        crossed = closing and abs(signed) > abs(p.qty)   # 反手：平掉全部旧仓并反向开新仓
        if closing:
            closed_qty = min(abs(p.qty), abs(signed))
            pnl = (price - p.avg_price) * closed_qty * (1 if p.qty > 0 else -1)
            self._round_trips.append(pnl - fee - self._entry_cost)
            self._entry_cost = 0.0
        else:
            self._entry_cost += fee
        new_qty = p.qty + signed
        if not closing and (p.qty == 0 or abs(new_qty) > abs(p.qty)):
            total = p.avg_price * abs(p.qty) + price * abs(signed)
            p.avg_price = total / (abs(p.qty) + abs(signed))
        if crossed:
            p.avg_price = price          # 反手剩余部分按本次成交价计新仓成本
            p.stop = od.stop             # 反手 = 新仓位：不继承旧仓方向的止损（None 即无止损）
        if new_qty == 0:
            p.avg_price = 0.0
            p.stop = None
        elif od.stop is not None:
            p.stop = od.stop
        p.qty = new_qty
        self.cash -= signed * price + fee
        self.fills.append(Fill(ts, od.side, od.qty, price, fee, od.tag))

    def summary(self) -> dict:
        eq = [e for _, e in self.equity_curve] or [self.initial_cash]
        peak, max_dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0.0)
        wins = [x for x in self._round_trips if x > 0]
        losses = [-x for x in self._round_trips if x < 0]
        return {
            "net_pnl": round(self.equity() - self.initial_cash, 8),
            "return_pct": round((self.equity() / self.initial_cash - 1) * 100, 4),
            "max_drawdown": round(max_dd, 6),
            "num_trades": len(self._round_trips),
            "win_rate": round(len(wins) / len(self._round_trips), 4) if self._round_trips else 0.0,
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else (float("inf") if wins else 0.0),
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }
