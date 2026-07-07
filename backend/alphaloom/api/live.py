from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import alphaloom.nodes  # noqa: F401
from alphaloom.api.serialize import sanitize
from alphaloom.backtest.runner import CompileFailed
from alphaloom.brokers.paper import PaperBroker
from alphaloom.data.source import bar_to_ms
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import BlueprintSpec, dumps_loom
from alphaloom.llm.recording import ReplayMissError
from alphaloom.nodes.registry import create_instance
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.recorder import Recorder
from alphaloom.sandbox.audit import AuditLog
from alphaloom.graph.types import Stamped


CandleFetcher = Callable[[str, str, int | None, int], list[dict]]


def _content(response: dict) -> str:
    try:
        return response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def okx_candle_fetcher(inst: str, bar: str, since_ts: int | None, limit: int = 5) -> list[dict]:
    params = {"instId": inst, "bar": bar, "limit": str(max(1, min(limit, 100)))}
    url = f"https://www.okx.com/api/v5/market/candles?{urlencode(params)}"
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "AlphaLoom/1.0 live-smoke",
    })
    with urlopen(request, timeout=10) as resp:  # noqa: S310 - fixed public OKX endpoint
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("data", [])
    candles: list[dict] = []
    for row in rows:
        if len(row) < 6:
            continue
        ts = int(row[0])
        if since_ts is not None and ts <= since_ts:
            continue
        candles.append({
            "ts": ts,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return sorted(candles, key=lambda c: c["ts"])


@dataclass
class LiveParams:
    inst: str
    bar: str = "1m"
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    poll_ms: int = 5_000
    analysis: bool = True
    analysis_every: int = 1
    context_bars: int = 30
    max_bars: int | None = None
    fetch_limit: int = 5
    ws_wait_ms: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "LiveParams":
        return cls(
            inst=str(d["inst"]),
            bar=str(d.get("bar", "1m")),
            cash=float(d.get("cash", 10_000.0)),
            fee_rate=float(d.get("fee_rate", 0.0005)),
            poll_ms=max(250, int(d.get("poll_ms", 5_000))),
            analysis=bool(d.get("analysis", True)),
            analysis_every=max(1, int(d.get("analysis_every", 1))),
            context_bars=max(1, min(int(d.get("context_bars", 30)), 120)),
            max_bars=(None if d.get("max_bars") is None else max(1, int(d.get("max_bars")))),
            fetch_limit=max(1, min(int(d.get("fetch_limit", 5)), 100)),
            ws_wait_ms=max(0, int(d.get("ws_wait_ms", 0))),
        )

    def to_dict(self) -> dict:
        return {
            "inst": self.inst,
            "bar": self.bar,
            "cash": self.cash,
            "fee_rate": self.fee_rate,
            "poll_ms": self.poll_ms,
            "analysis": self.analysis,
            "analysis_every": self.analysis_every,
            "context_bars": self.context_bars,
            "max_bars": self.max_bars,
            "fetch_limit": self.fetch_limit,
            "ws_wait_ms": self.ws_wait_ms,
            "mode": "live",
        }


class LiveAnalysisStore:
    def __init__(self, path):
        self.path = str(path)
        self._db = None
        self._closed = False

    def _conn(self):
        import sqlite3

        if self._db is None:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS live_analysis ("
                " session_id TEXT, event_idx INTEGER, bar_ts INTEGER,"
                " prompt_hash TEXT, model TEXT, input_json TEXT,"
                " output_json TEXT, created_ms INTEGER,"
                " PRIMARY KEY (session_id, event_idx))")
            self._db.commit()
        return self._db

    def record(self, session_id: str, event_idx: int, bar_ts: int, *,
               prompt_hash: str, model: str | None, input_summary: dict,
               output: dict) -> dict:
        row = {
            "session_id": session_id,
            "event_idx": event_idx,
            "bar_ts": bar_ts,
            "prompt_hash": prompt_hash,
            "model": model,
            "input_summary": sanitize(input_summary),
            "output": sanitize(output),
            "created_ms": int(time.time() * 1000),
        }
        self._conn().execute(
            "INSERT OR REPLACE INTO live_analysis VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                event_idx,
                bar_ts,
                prompt_hash,
                model,
                json.dumps(row["input_summary"], ensure_ascii=False),
                json.dumps(row["output"], ensure_ascii=False),
                row["created_ms"],
            ),
        )
        self._conn().commit()
        return row

    def list(self, session_id: str, limit: int = 200) -> list[dict]:
        rows = self._conn().execute(
            "SELECT session_id, event_idx, bar_ts, prompt_hash, model, input_json,"
            " output_json, created_ms FROM live_analysis WHERE session_id=?"
            " ORDER BY event_idx DESC LIMIT ?",
            (session_id, max(1, min(int(limit), 2000))),
        ).fetchall()
        out = []
        for sid, idx, ts, h, model, inp, out_json, created_ms in rows:
            out.append({
                "session_id": sid,
                "event_idx": idx,
                "bar_ts": ts,
                "prompt_hash": h,
                "model": model,
                "input_summary": json.loads(inp),
                "output": json.loads(out_json),
                "created_ms": created_ms,
            })
        return list(reversed(out))

    def close(self) -> None:
        if self._closed:
            return
        if self._db is not None:
            self._db.commit()
            self._db.close()
        self._closed = True


@dataclass
class LiveSessionState:
    session_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class LiveService:
    def __init__(self, *, store, db_path, record_dir, llm=None,
                 candle_fetcher: CandleFetcher | None = None,
                 max_active_sessions: int = 3):
        self.store = store
        self.db_path = db_path
        self.record_dir = Path(record_dir)
        self.llm = llm
        self.candle_fetcher = candle_fetcher or okx_candle_fetcher
        self.max_active_sessions = max(1, int(max_active_sessions))
        self._sessions: dict[str, LiveSessionState] = {}
        self._lock = threading.Lock()

    def set_llm(self, llm) -> None:
        with self._lock:
            self.llm = llm

    def has(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions or self.store.get(session_id) is not None

    def _prune(self) -> None:
        for sid, state in list(self._sessions.items()):
            if state.thread is not None and not state.thread.is_alive():
                self._sessions.pop(sid, None)

    def start(self, bp: BlueprintSpec, params: dict, sink,
              session_id: str | None = None) -> str:
        live_params = LiveParams.from_dict(params)
        session_id = session_id or uuid.uuid4().hex[:12]
        state = LiveSessionState(session_id=session_id)
        with self._lock:
            self._prune()
            if len(self._sessions) >= self.max_active_sessions:
                raise RuntimeError("too many active live sessions; stop one first")
            llm_snapshot = self.llm
            self.store.create(session_id, bp.id, dumps_loom(bp),
                              json.dumps(live_params.to_dict()),
                              int(time.time() * 1000))
            thread = threading.Thread(
                target=self._worker,
                args=(session_id, bp, live_params, sink, state.stop, llm_snapshot),
                daemon=True,
            )
            state.thread = thread
            self._sessions[session_id] = state
        thread.start()
        return session_id

    def command(self, session_id: str, cmd: str) -> bool:
        if cmd != "stop":
            return False
        with self._lock:
            state = self._sessions.get(session_id)
        if state:
            state.stop.set()
            return True
        return False

    def _worker(self, session_id: str, bp: BlueprintSpec, params: LiveParams,
                sink, stop: threading.Event, llm):
        sink = _safe_sink(sink)
        source = None
        recorder = None
        analysis_store = None
        try:
            sink({"type": "status", "status": "starting", "mode": "live"})
            bar_ms = bar_to_ms(params.bar)
            compiled = compile_blueprint(bp, bars_per_day=86_400_000 // bar_ms)
            if not compiled.ok:
                raise CompileFailed(compiled.errors)

            source = SQLiteMarketData(self.db_path)
            rec_path = str(self.record_dir / f"live_{session_id}.sqlite")
            recorder = Recorder(rec_path)
            analysis_store = LiveAnalysisStore(rec_path)
            broker = PaperBroker(initial_cash=params.cash, fee_rate=params.fee_rate)
            ctx = RunContext(clock=SimClock(), run_id=session_id,
                             broker=broker, recorder=recorder)
            ctx.llm = llm
            ctx.audit = AuditLog()
            instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
            engine = Engine(compiled, instances, ctx)
            latest_outputs: dict[str, dict] = {}

            def after_node(node_id, outputs):
                latest_outputs[node_id] = sanitize(_unstamp(outputs))

            engine.after_node = after_node
            seen_ts: set[int] = set()
            last_ts: int | None = None
            bars = 0
            fills_seen = 0
            recent: list[dict] = []
            first = True
            limit_reached = False
            fetch_errors = 0
            sink({"type": "status", "status": "running", "mode": "live"})
            while not stop.is_set():
                try:
                    candles = self.candle_fetcher(params.inst, params.bar, last_ts,
                                                  params.fetch_limit)
                except Exception as exc:
                    fetch_errors += 1
                    if fetch_errors > 5:
                        raise
                    delay_ms = min(params.poll_ms * (2 ** (fetch_errors - 1)), 30_000)
                    sink({
                        "type": "status",
                        "status": "retrying",
                        "mode": "live",
                        "attempt": fetch_errors,
                        "message": str(exc),
                        "next_retry_ms": delay_ms,
                    })
                    time.sleep(delay_ms / 1000.0)
                    continue
                if fetch_errors:
                    sink({"type": "status", "status": "running", "mode": "live",
                          "message": "live fetch recovered"})
                    fetch_errors = 0
                new_candles = [
                    c for c in sorted(candles, key=lambda row: row["ts"])
                    if int(c["ts"]) not in seen_ts
                    and (last_ts is None or int(c["ts"]) > last_ts)
                ]
                if not new_candles:
                    if params.max_bars is not None and bars >= params.max_bars:
                        break
                    time.sleep(params.poll_ms / 1000.0)
                    continue
                for candle in new_candles:
                    if stop.is_set():
                        break
                    if first and params.ws_wait_ms:
                        time.sleep(params.ws_wait_ms / 1000.0)
                    first = False
                    candle = _normalize_candle(candle)
                    seen_ts.add(int(candle["ts"]))
                    last_ts = int(candle["ts"])
                    source.insert_candles(params.inst, params.bar, [candle])
                    broker.on_bar(candle)
                    engine.step(BarEvent(candle, bar_ms))
                    recorder.flush()
                    recent.append(candle)
                    recent = recent[-params.context_bars:]
                    new_fills = [f.__dict__ for f in broker.fills[fills_seen:]]
                    fills_seen = len(broker.fills)
                    payload = {
                        "type": "bar",
                        "mode": "live",
                        "idx": bars,
                        "ts": candle["ts"],
                        "candle": candle,
                        "close": candle["close"],
                        "equity": broker.equity(),
                        "active": compiled.order,
                        "fills": sanitize(new_fills),
                    }
                    sink(payload)
                    if params.analysis and bars % params.analysis_every == 0:
                        analysis = self._analyze(
                            session_id=session_id,
                            event_idx=bars,
                            candle=candle,
                            recent=recent,
                            bp=bp,
                            compiled_order=compiled.order,
                            latest_outputs=latest_outputs,
                            broker=broker,
                            llm=llm,
                            store=analysis_store,
                        )
                        if analysis is not None:
                            sink({"type": "analysis", **analysis})
                    bars += 1
                    if params.max_bars is not None and bars >= params.max_bars:
                        limit_reached = True
                        break
                if limit_reached:
                    break

            status = "stopped" if stop.is_set() and not limit_reached else "completed"
            report = {
                "run_id": session_id,
                "blueprint_id": bp.id,
                "bars": bars,
                "summary": sanitize(broker.summary()),
                "certificate": sanitize(compiled.certificate.to_dict()),
                "equity_curve": broker.equity_curve,
                "fills": [f.__dict__ for f in broker.fills],
                "mode": "live",
            }
            self.store.set_status(session_id, status, report_json=json.dumps(report),
                                  recording_path=rec_path)
            sink({"type": "done", "report": report})
        except CompileFailed as cf:
            self.store.set_status(
                session_id, "failed",
                error=json.dumps([e.to_dict() for e in cf.errors]))
            sink({"type": "error", "message": "compile failed"})
        except Exception as exc:  # noqa: BLE001 - live thread must report and die cleanly
            self.store.set_status(session_id, "failed", error=str(exc))
            sink({"type": "error", "message": str(exc)})
        finally:
            for obj in (recorder, analysis_store, source):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
            with self._lock:
                self._sessions.pop(session_id, None)

    def _analyze(self, *, session_id: str, event_idx: int, candle: dict,
                 recent: list[dict], bp: BlueprintSpec, compiled_order: Iterable[str],
                 latest_outputs: dict[str, dict], broker: PaperBroker, llm,
                 store: LiveAnalysisStore | None) -> dict | None:
        if llm is None or store is None:
            return None
        input_summary = _analysis_input_summary(
            candle=candle,
            recent=recent,
            bp=bp,
            compiled_order=list(compiled_order),
            latest_outputs=latest_outputs,
            broker=broker,
        )
        messages = [
            {"role": "system", "content": (
                "You are AlphaLoom's live trading analyst sidecar. You explain the "
                "running blueprint; you never create orders or bypass RiskGate. "
                "Reply with ONLY JSON containing market_state, current_gate, "
                "risk_reason, suggestion, confidence."
            )},
            {"role": "user", "content": json.dumps(input_summary, sort_keys=True,
                                                   ensure_ascii=False)},
        ]
        prompt_hash = hashlib.sha256(
            json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        try:
            response = llm.chat(messages, temperature=0.1, max_tokens=500)
            text = _content(response)
            output = _extract_json(text) or {
                "market_state": "unparsed",
                "current_gate": "unknown",
                "risk_reason": "LLM output was not valid JSON",
                "suggestion": text[:500],
                "confidence": 0.0,
            }
        except ReplayMissError as exc:
            output = {
                "market_state": "offline replay miss",
                "current_gate": "analysis skipped",
                "risk_reason": str(exc),
                "suggestion": "Switch to live LLM mode or record this sidecar prompt.",
                "confidence": 0.0,
            }
        row = store.record(
            session_id,
            event_idx,
            int(candle["ts"]),
            prompt_hash=prompt_hash,
            model=getattr(llm, "model", None),
            input_summary=input_summary,
            output=output,
        )
        return row


def _analysis_input_summary(*, candle: dict, recent: list[dict], bp: BlueprintSpec,
                            compiled_order: list[str],
                            latest_outputs: dict[str, dict],
                            broker: PaperBroker) -> dict:
    equity = broker.equity()
    curve = [v for _, v in broker.equity_curve] or [broker.initial_cash]
    peak = max(curve) if curve else broker.initial_cash
    drawdown = (peak - equity) / peak if peak else 0.0
    risk_nodes = [n.id for n in bp.nodes if "risk" in n.type or n.type == "position_sizer"]
    reflection_nodes = [
        n.id for n in bp.nodes
        if "reflect" in n.type or "experience" in n.type or "memory" in n.type
    ]
    return {
        "blueprint": {"id": bp.id, "name": bp.name},
        "bar": candle,
        "recent_candles": recent[-30:],
        "compiled_order": compiled_order,
        "node_outputs": latest_outputs,
        "risk_outputs": {nid: latest_outputs.get(nid) for nid in risk_nodes},
        "reflection_memory": {nid: latest_outputs.get(nid) for nid in reflection_nodes},
        "position": broker.position().__dict__,
        "fills": [f.__dict__ for f in broker.fills[-5:]],
        "closed_trades": broker.closed_trades[-5:],
        "equity": equity,
        "drawdown": drawdown,
    }


def _normalize_candle(candle: dict) -> dict:
    return {
        "ts": int(candle["ts"]),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": float(candle["volume"]),
    }


def _unstamp(obj):
    if isinstance(obj, Stamped):
        return {"as_of": obj.as_of, "value": _unstamp(obj.value)}
    if isinstance(obj, dict):
        return {k: _unstamp(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_unstamp(v) for v in obj]
    return obj


def _safe_sink(sink):
    def safe(event):
        try:
            sink(event)
        except Exception:
            pass
    return safe
