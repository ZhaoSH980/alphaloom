from __future__ import annotations
import sqlite3
from typing import Iterator
from alphaloom.data.source import DataSource

class SQLiteMarketData(DataSource):
    def __init__(self, path):
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS candles ("
            " inst TEXT, bar TEXT, ts INTEGER,"
            " open REAL, high REAL, low REAL, close REAL, volume REAL,"
            " PRIMARY KEY (inst, bar, ts))")

    def insert_candles(self, inst: str, bar: str, candles: list[dict]) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [(inst, bar, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"])
             for c in candles])
        self._db.commit()

    def iter_candles(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
        q = "SELECT ts, open, high, low, close, volume FROM candles WHERE inst=? AND bar=?"
        args: list = [inst, bar]
        if start_ms is not None:
            q += " AND ts>=?"; args.append(start_ms)
        if end_ms is not None:
            q += " AND ts<=?"; args.append(end_ms)
        q += " ORDER BY ts"
        for ts, o, h, l, c, v in self._db.execute(q, args):
            yield {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}

    def bounds(self, inst: str, bar: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE inst=? AND bar=?",
            (inst, bar)).fetchone()
        return None if row[0] is None else (row[0], row[1])

    def close(self) -> None:
        self._db.close()
