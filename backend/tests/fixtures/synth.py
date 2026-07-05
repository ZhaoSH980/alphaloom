"""确定性合成 K 线 —— 测试与离线演示专用，绝不联网。"""
from __future__ import annotations
import random

def gen_candles(n: int, *, start_ts: int = 0, bar_ms: int = 60_000,
                seed: int = 42, trend: float = 0.0, start_price: float = 100.0,
                vol: float = 0.004) -> list[dict]:
    rng = random.Random(seed)
    out, close = [], start_price
    for i in range(n):
        o = close
        drift = trend + rng.gauss(0, vol)
        c = max(0.01, o * (1 + drift))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        out.append({"ts": start_ts + i * bar_ms, "open": round(o, 6),
                    "high": round(hi, 6), "low": round(lo, 6),
                    "close": round(c, 6), "volume": round(abs(rng.gauss(10, 3)), 3)})
        close = c
    return out
