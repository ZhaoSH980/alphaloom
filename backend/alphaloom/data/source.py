from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterator

_BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
           "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}

def bar_to_ms(bar: str) -> int:
    return _BAR_MS[bar]

class DataSource(ABC):
    """多市场行情抽象：D1 只有 SQLite 实现；D4 加 OKX 实时。"""

    @abstractmethod
    def iter_candles(self, inst: str, bar: str,
                     start_ms: int | None = None,
                     end_ms: int | None = None) -> Iterator[dict]: ...
