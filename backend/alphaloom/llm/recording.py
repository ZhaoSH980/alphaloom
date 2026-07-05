"""Record/replay layer over any transport (AlphaLoom D3).

Every request is canonicalized (sorted-keys JSON) and hashed; responses are
stored in SQLite. Cache hit -> no network. offline=True -> cache miss raises
ReplayMissError instead of calling the transport.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alphaloom.llm.client import Transport


class ReplayMissError(Exception):
    """Offline mode requested a call that was never recorded."""


class RecordingLLMClient:
    def __init__(
        self,
        transport: Transport,
        db_path: Path,
        model: str,
        offline: bool | None = None,
    ):
        self._transport = transport
        self.model = model
        if offline is None:
            offline = os.environ.get("ALPHALOOM_OFFLINE", "") == "1"
        self.offline = offline
        # provenance counters: how many chats replayed from cache vs hit the
        # real endpoint — surfaced per run so the UI can say WHY a run was instant
        self.cache_hits = 0
        self.cache_misses = 0
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_calls ("
            " hash TEXT PRIMARY KEY, request_json TEXT NOT NULL,"
            " response_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def _key(request: dict[str, Any]) -> str:
        canonical = json.dumps(request, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        **params: Any,
    ) -> dict[str, Any]:
        temperature = float(temperature)  # int/float must hash to the same replay key
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **params,
        }
        if tools is not None:
            request["tools"] = tools
        key = self._key(request)
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM llm_calls WHERE hash = ?", (key,)
            ).fetchone()
            if row:
                self.cache_hits += 1  # counters share the lock: chat() is a concurrent API
        if row:
            return json.loads(row[0])
        if self.offline:
            raise ReplayMissError(
                f"offline replay miss for request hash {key}; "
                "re-run in record mode to capture it"
            )
        with self._lock:
            self.cache_misses += 1
        response = self._transport(request)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_calls VALUES (?, ?, ?, ?)",
                (
                    key,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()
        return response
