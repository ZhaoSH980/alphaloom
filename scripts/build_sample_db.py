"""从 OKX 公共 REST 拉取 1m K 线到 data/sample.sqlite（可断点续传）。

用法:  backend/.venv/Scripts/python scripts/build_sample_db.py --days 90 \
           --inst BTC-USDT-SWAP ETH-USDT-SWAP
仅公共端点、无鉴权；限流退避；测试/CI 绝不调用本脚本。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402

BASE = "https://www.okx.com/api/v5/market/history-candles"
UA = {"User-Agent": "alphaloom-sample-builder/0.1"}

def fetch(inst: str, before_ms: int | None, bar: str = "1m") -> list[list]:
    q = {"instId": inst, "bar": bar, "limit": "100"}
    if before_ms is not None:
        q["after"] = str(before_ms)     # OKX: after=ts 返回更旧的数据
    url = BASE + "?" + urllib.parse.urlencode(q)
    for attempt in range(8):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=15) as r:
                body = json.loads(r.read().decode("utf-8"))
            if body.get("code") == "0":
                return body["data"]
            time.sleep(2 ** attempt)     # 限流/繁忙：指数退避
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"OKX fetch failed for {inst} before={before_ms}")

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--inst", nargs="+", default=["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
    ap.add_argument("--out", default="data/sample.sqlite")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteMarketData(args.out)
    cutoff = int(time.time() * 1000) - args.days * 86_400_000
    for inst in args.inst:
        bounds = db.bounds(inst, "1m")
        before = bounds[0] if bounds else None   # 续传：从已有最旧处继续往回拉
        total = 0
        while True:
            rows = fetch(inst, before)
            if not rows:
                break
            candles = [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                       for r in rows]
            db.insert_candles(inst, "1m", candles)
            total += len(candles)
            before = min(c["ts"] for c in candles)
            print(f"{inst}: {total} bars, oldest={before}")
            if before <= cutoff:
                break
            time.sleep(0.25)                      # 温和限速
    print("done")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
