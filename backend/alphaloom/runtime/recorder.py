# backend/alphaloom/runtime/recorder.py
from __future__ import annotations
import json
import sqlite3
from alphaloom.graph.types import Stamped

def _enc(o):
    if isinstance(o, Stamped):
        return {"__stamped__": o.as_of, "value": o.value}
    raise TypeError(f"not JSON serializable: {type(o)}")

def to_json(obj: dict) -> str:
    return json.dumps(obj, default=_enc, ensure_ascii=False)

class Recorder:
    def __init__(self, path):
        self._db = sqlite3.connect(str(path))
        self._closed = False
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS node_io ("
            " run_id TEXT, event_idx INTEGER, ts INTEGER, node_id TEXT,"
            " inputs_json TEXT, outputs_json TEXT)")
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_io ON node_io(run_id, node_id, event_idx)")
        self._db.commit()

    def record(self, run_id, event_idx, ts, node_id, inputs, outputs):
        self._db.execute("INSERT INTO node_io VALUES (?,?,?,?,?,?)",
                         (run_id, event_idx, ts, node_id, to_json(inputs), to_json(outputs)))

    def flush(self):
        if not self._closed:
            self._db.commit()

    def fetch(self, run_id, node_id=None):
        q = "SELECT * FROM node_io WHERE run_id=?"
        args = [run_id]
        if node_id:
            q += " AND node_id=?"
            args.append(node_id)
        q += " ORDER BY event_idx, rowid"
        cur = self._db.execute(q, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self):
        if self._closed:
            return
        self._db.commit()
        self._db.close()
        self._closed = True

def from_json(text: str) -> dict:
    def hook(d):
        if set(d) == {"__stamped__", "value"}:
            return Stamped(d["value"], d["__stamped__"])
        return d
    return json.loads(text, object_hook=hook)
