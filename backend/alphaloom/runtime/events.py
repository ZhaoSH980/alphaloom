from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class BarEvent:
    candle: dict
    bar_ms: int

    @property
    def ts_open(self) -> int:
        return int(self.candle["ts"])

    @property
    def ts_close(self) -> int:
        return int(self.candle["ts"]) + self.bar_ms
