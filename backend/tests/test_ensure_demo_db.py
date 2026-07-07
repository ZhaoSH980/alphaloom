import importlib
from pathlib import Path

from alphaloom.data.sqlite_source import SQLiteMarketData


def test_ensure_demo_db_adds_real_sol_rows_when_btc_already_exists(tmp_path, monkeypatch):
    script = importlib.import_module("scripts.ensure_demo_db")

    out = tmp_path / "demo.sqlite"
    real = tmp_path / "real_okx.sqlite"
    demo_db = SQLiteMarketData(out)
    demo_db.insert_candles("BTC-USDT-SWAP", "1m", [
        {"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ])
    demo_db.close()
    real_db = SQLiteMarketData(real)
    real_db.insert_candles("SOL-USDT-SWAP", "1m", [
        {"ts": 10, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 100},
        {"ts": 70, "open": 2.5, "high": 4, "low": 2, "close": 3.5, "volume": 110},
    ])
    real_db.close()

    monkeypatch.setattr(script, "OUT", out)
    monkeypatch.setattr(script, "REAL_OKX_DB", real, raising=False)

    assert script.main() == 0

    checked = SQLiteMarketData(out)
    assert checked.bounds("BTC-USDT-SWAP", "1m") == (0, 0)
    assert checked.bounds("SOL-USDT-SWAP", "1m") == (10, 70)
    checked.close()
