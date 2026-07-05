from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Order:
    side: str                  # "buy" | "sell"
    qty: float
    kind: str = "market"       # D1 仅 market
    stop: float | None = None  # 开仓单附带止损
    tag: str = ""

@dataclass(frozen=True)
class Fill:
    ts: int
    side: str
    qty: float
    price: float
    fee: float
    tag: str = ""

@dataclass
class Position:
    qty: float = 0.0           # 有符号：+多 -空
    avg_price: float = 0.0
    stop: float | None = None
