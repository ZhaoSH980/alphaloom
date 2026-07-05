# backend/alphaloom/api/runs_store.py
from __future__ import annotations
import sqlite3
import threading

class RunsStore:
    """run 生命周期注册表。连接串行化（check_same_thread=False + 锁），D2 单进程足够。"""

    def __init__(self, path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            " run_id TEXT PRIMARY KEY, blueprint_id TEXT, blueprint_json TEXT,"
            " params_json TEXT, status TEXT, report_json TEXT, error TEXT,"
            " recording_path TEXT, created_ms INTEGER)")
        self._db.commit()

    def create(self, run_id, blueprint_id, blueprint_json, params_json, created_ms):
        with self._lock:
            self._db.execute(
                "INSERT INTO runs VALUES (?,?,?,?, 'running', NULL, NULL, NULL, ?)",
                (run_id, blueprint_id, blueprint_json, params_json, created_ms))
            self._db.commit()

    def set_status(self, run_id, status, report_json=None, error=None, recording_path=None):
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status=?,"
                " report_json=COALESCE(?, report_json),"
                " error=COALESCE(?, error),"
                " recording_path=COALESCE(?, recording_path) WHERE run_id=?",
                (status, report_json, error, recording_path, run_id))
            self._db.commit()

    def get(self, run_id):
        with self._lock:
            cur = self._db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([c[0] for c in cur.description], row))

    def list(self):
        with self._lock:
            cur = self._db.execute(
                "SELECT run_id, blueprint_id, params_json, status, error, created_ms"
                " FROM runs ORDER BY created_ms DESC, run_id")
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
