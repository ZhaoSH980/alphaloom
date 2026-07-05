from alphaloom.data.source import bar_to_ms
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

def test_bar_to_ms():
    assert bar_to_ms("1m") == 60_000
    assert bar_to_ms("15m") == 900_000
    assert bar_to_ms("1H") == 3_600_000

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
