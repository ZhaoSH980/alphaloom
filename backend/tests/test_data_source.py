from alphaloom.data.source import bar_to_ms
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

def test_bar_to_ms():
    assert bar_to_ms("1m") == 60_000
    assert bar_to_ms("3m") == 180_000
    assert bar_to_ms("15m") == 900_000
    assert bar_to_ms("30m") == 1_800_000
    assert bar_to_ms("1H") == 3_600_000
    assert bar_to_ms("2H") == 7_200_000
    assert bar_to_ms("12H") == 43_200_000

def test_synth_deterministic():
    a = gen_candles(50, seed=7, trend=0.001)
    b = gen_candles(50, seed=7, trend=0.001)
    assert a == b and len(a) == 50
    for c in a:
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]
    assert a[1]["ts"] - a[0]["ts"] == 60_000

def test_sqlite_roundtrip(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    candles = gen_candles(100, seed=1)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles[:10])  # 重复插入幂等
    got = list(db.iter_candles("BTC-USDT-SWAP", "1m",
                               candles[10]["ts"], candles[19]["ts"]))
    assert len(got) == 10 and got[0]["ts"] == candles[10]["ts"]
    assert [c["ts"] for c in got] == sorted(c["ts"] for c in got)
    assert db.bounds("BTC-USDT-SWAP", "1m") == (candles[0]["ts"], candles[-1]["ts"])

def test_sqlite_aggregates_missing_higher_timeframe_from_1m(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    candles = gen_candles(12, seed=2)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles)

    got = list(db.iter_candles("BTC-USDT-SWAP", "5m"))

    first = candles[:5]
    assert len(got) == 3
    assert got[0] == {
        "ts": candles[0]["ts"],
        "open": first[0]["open"],
        "high": max(c["high"] for c in first),
        "low": min(c["low"] for c in first),
        "close": first[-1]["close"],
        "volume": sum(c["volume"] for c in first),
    }

def test_sqlite_derived_bar_buckets_are_epoch_anchored_across_windows(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    candles = [
        {"ts": i * 60_000, "open": float(i), "high": float(i) + 0.5,
         "low": float(i) - 0.5, "close": float(i) + 0.25, "volume": 1.0}
        for i in range(30)
    ]
    db.insert_candles("BTC-USDT-SWAP", "1m", candles)

    full = list(db.iter_candles("BTC-USDT-SWAP", "5m"))
    shifted = list(db.iter_candles("BTC-USDT-SWAP", "5m",
                                   start_ms=7 * 60_000,
                                   end_ms=29 * 60_000))

    assert [c["ts"] // 60_000 for c in full] == [0, 5, 10, 15, 20, 25]
    assert [c["ts"] // 60_000 for c in shifted] == [5, 10, 15, 20, 25]
    assert shifted[0]["open"] == candles[7]["open"]
    assert shifted[0]["close"] == candles[9]["close"]

def test_sqlite_uses_wal_and_long_busy_timeout(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")

    journal_mode = db._db.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = db._db.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 30_000

def test_sqlite_catalog_exposes_derived_bars_when_1m_exists(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    candles = gen_candles(120, seed=3)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles)

    rows = db.catalog()
    bars = [row["bar"] for row in rows if row["inst"] == "BTC-USDT-SWAP"]

    assert bars == ["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"]
    five = next(row for row in rows if row["bar"] == "5m")
    assert five["count"] == 24
