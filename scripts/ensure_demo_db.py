# scripts/ensure_demo_db.py
"""幂等生成确定性 demo 行情库（秒级，零联网）。dev.bat/demo.bat 启动前调用。"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from fixtures.synth import gen_candles  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "demo.sqlite"
REAL_OKX_DB = Path(__file__).resolve().parents[1] / "data" / "real_okx_14d.sqlite"


def _copy_real_candles(db: SQLiteMarketData, inst: str, bar: str = "1m") -> int:
    if db.bounds(inst, bar):
        return 0
    if not REAL_OKX_DB.exists():
        return 0
    src = sqlite3.connect(REAL_OKX_DB)
    try:
        rows = src.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE inst=? AND bar=? ORDER BY ts",
            (inst, bar),
        ).fetchall()
    finally:
        src.close()
    if not rows:
        return 0
    db.insert_candles(inst, bar, [
        {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}
        for ts, o, h, l, c, v in rows
    ])
    return len(rows)

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteMarketData(OUT)
    built_synth = False
    if not db.bounds("BTC-USDT-SWAP", "1m"):
        up = gen_candles(2000, seed=11, trend=0.0008, start_price=60_000, vol=0.003)
        down = gen_candles(1200, seed=12, trend=-0.0009, start_ts=up[-1]["ts"] + 60_000,
                           start_price=up[-1]["close"], vol=0.004)
        chop = gen_candles(800, seed=13, trend=0.0, start_ts=down[-1]["ts"] + 60_000,
                           start_price=down[-1]["close"], vol=0.002)
        db.insert_candles("BTC-USDT-SWAP", "1m", up + down + chop)
        built_synth = True
    if not db.bounds("ETH-USDT-SWAP", "1m"):
        eth = gen_candles(4000, seed=21, trend=0.0003, start_price=3000, vol=0.004)
        db.insert_candles("ETH-USDT-SWAP", "1m", eth)
        built_synth = True
    copied_sol = _copy_real_candles(db, "SOL-USDT-SWAP")
    db.close()
    suffix = []
    if built_synth:
        suffix.append("BTC/ETH synthetic")
    if copied_sol:
        suffix.append(f"SOL real OKX {copied_sol} bars")
    detail = f" ({', '.join(suffix)})" if suffix else ""
    print(f"demo db ready: {OUT}{detail}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
