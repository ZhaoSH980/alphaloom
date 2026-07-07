from __future__ import annotations
import math
import sqlite3
import time
from itertools import chain
from typing import Iterator
from alphaloom.data.source import DataSource, bar_to_ms

_BAR_ORDER = ("1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D")

class SQLiteMarketData(DataSource):
    def __init__(self, path):
        self._db = sqlite3.connect(str(path), timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._execute(
            "CREATE TABLE IF NOT EXISTS candles ("
            " inst TEXT, bar TEXT, ts INTEGER,"
            " open REAL, high REAL, low REAL, close REAL, volume REAL,"
            " PRIMARY KEY (inst, bar, ts))")

    def _with_retry(self, fn):
        for attempt in range(6):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.025 * (2 ** attempt))
        raise RuntimeError("unreachable sqlite retry state")

    def _execute(self, sql: str, args: tuple | list = ()):
        return self._with_retry(lambda: self._db.execute(sql, args))

    def _executemany(self, sql: str, rows):
        return self._with_retry(lambda: self._db.executemany(sql, rows))

    def _commit(self) -> None:
        self._with_retry(self._db.commit)

    def insert_candles(self, inst: str, bar: str, candles: list[dict]) -> None:
        self._executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [(inst, bar, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"])
             for c in candles])
        self._commit()

    def _iter_exact(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
        q = "SELECT ts, open, high, low, close, volume FROM candles WHERE inst=? AND bar=?"
        args: list = [inst, bar]
        if start_ms is not None:
            q += " AND ts>=?"; args.append(start_ms)
        if end_ms is not None:
            q += " AND ts<=?"; args.append(end_ms)
        q += " ORDER BY ts"
        for ts, o, h, l, c, v in self._execute(q, args):
            yield {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}

    def _has_bar(self, inst: str, bar: str) -> bool:
        return self._execute(
            "SELECT 1 FROM candles WHERE inst=? AND bar=? LIMIT 1",
            (inst, bar)).fetchone() is not None

    def _iter_aggregated_from_1m(self, inst, bar, start_ms=None,
                                 end_ms=None) -> Iterator[dict]:
        base = self._iter_exact(inst, "1m", start_ms, end_ms)
        try:
            first = next(base)
        except StopIteration:
            return
        bar_ms = bar_to_ms(bar)
        current_bucket = None
        agg = None

        for candle in chain([first], base):
            bucket = (int(candle["ts"]) // bar_ms) * bar_ms
            if current_bucket is None or bucket != current_bucket:
                if agg is not None:
                    yield agg
                current_bucket = bucket
                agg = {
                    "ts": bucket,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
                continue
            agg["high"] = max(agg["high"], candle["high"])
            agg["low"] = min(agg["low"], candle["low"])
            agg["close"] = candle["close"]
            agg["volume"] += candle["volume"]
        if agg is not None:
            yield agg

    def iter_candles(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
        if self._has_bar(inst, bar):
            yield from self._iter_exact(inst, bar, start_ms, end_ms)
            return
        if bar != "1m" and self._has_bar(inst, "1m"):
            yield from self._iter_aggregated_from_1m(inst, bar, start_ms, end_ms)

    def bounds(self, inst: str, bar: str) -> tuple[int, int] | None:
        row = self._execute(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE inst=? AND bar=?",
            (inst, bar)).fetchone()
        return None if row[0] is None else (row[0], row[1])

    def catalog(self) -> list[dict]:
        exact = self._execute(
            "SELECT inst, bar, MIN(ts), MAX(ts), COUNT(*) "
            "FROM candles GROUP BY inst, bar ORDER BY inst, bar").fetchall()
        rows = [
            {"inst": inst, "bar": bar, "start_ms": start, "end_ms": end, "count": count}
            for inst, bar, start, end, count in exact
        ]
        seen = {(row["inst"], row["bar"]) for row in rows}
        base_rows = [row for row in rows if row["bar"] == "1m"]
        for base in base_rows:
            span_ms = base["end_ms"] - base["start_ms"] + bar_to_ms("1m")
            for bar in _BAR_ORDER:
                key = (base["inst"], bar)
                if key in seen:
                    continue
                count = max(1, math.ceil(span_ms / bar_to_ms(bar)))
                rows.append({
                    "inst": base["inst"],
                    "bar": bar,
                    "start_ms": base["start_ms"],
                    "end_ms": base["end_ms"],
                    "count": count,
                })
                seen.add(key)
        order = {bar: idx for idx, bar in enumerate(_BAR_ORDER)}
        return sorted(rows, key=lambda row: (row["inst"], order.get(row["bar"], 999)))

    def close(self) -> None:
        self._db.close()
