# scripts/ensure_demo_db.py
"""幂等生成确定性 demo 行情库（秒级，零联网）。dev.bat/demo.bat 启动前调用。"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from fixtures.synth import gen_candles  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "demo.sqlite"

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteMarketData(OUT)
    if db.bounds("BTC-USDT-SWAP", "1m"):
        print(f"demo db ready: {OUT}")
        return 0
    up = gen_candles(2000, seed=11, trend=0.0008, start_price=60_000, vol=0.003)
    down = gen_candles(1200, seed=12, trend=-0.0009, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"], vol=0.004)
    chop = gen_candles(800, seed=13, trend=0.0, start_ts=down[-1]["ts"] + 60_000,
                       start_price=down[-1]["close"], vol=0.002)
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down + chop)
    eth = gen_candles(4000, seed=21, trend=0.0003, start_price=3000, vol=0.004)
    db.insert_candles("ETH-USDT-SWAP", "1m", eth)
    print(f"demo db built: {OUT} (BTC 4000 + ETH 4000 bars)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
