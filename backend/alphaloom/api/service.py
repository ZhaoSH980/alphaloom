# backend/alphaloom/api/service.py
from __future__ import annotations
import json
import threading
import time
import uuid
import alphaloom.nodes  # noqa: F401
from alphaloom.api.serialize import sanitize
from alphaloom.backtest.runner import run_backtest, CompileFailed
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import BlueprintSpec, dumps_loom

class BreakBridge:
    """引擎 on_pause ↔ 外部命令的线程桥。engine 以全节点为断点，桥内过滤。"""

    def __init__(self, user_breakpoints, sink):
        self.user_breakpoints = set(user_breakpoints)
        self.step_mode = False
        self._gate = threading.Event()
        self._sink = sink
        self._stopped = False

    def on_pause(self, node_id, ev, inputs):
        try:
            if self._stopped:
                return
            if not self.step_mode and node_id not in self.user_breakpoints:
                return
            self._gate.clear()
            self._sink({"type": "paused", "node_id": node_id,
                        "event_idx": getattr(ev, "ts_close", 0),
                        "inputs": sanitize(_jsonable(inputs))})
            self._gate.wait()
        except Exception:
            pass  # Carryover 14②：断点桥绝不让异常泄进引擎

    def command(self, cmd):
        if cmd == "step":
            self.step_mode = True
            self._gate.set()
        elif cmd == "resume":
            self.step_mode = False
            self._gate.set()
        elif cmd == "stop":
            self._stopped = True
            self.step_mode = False
            self._gate.set()

def _jsonable(obj):
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return repr(obj)

class RunService:
    def __init__(self, store, db_path, record_dir):
        self.store = store
        self.db_path = db_path
        self.record_dir = record_dir
        self._threads: dict[str, threading.Thread] = {}
        self._bridges: dict[str, BreakBridge] = {}

    def start(self, bp: BlueprintSpec, params: dict, sink,
              run_id: str | None = None) -> str:
        run_id = run_id or uuid.uuid4().hex[:12]
        self.store.create(run_id, bp.id, dumps_loom(bp), json.dumps(params),
                          int(time.time() * 1000))
        bridge = BreakBridge(params.get("breakpoints", []), sink)
        self._bridges[run_id] = bridge
        t = threading.Thread(target=self._worker, args=(run_id, bp, params, sink, bridge),
                             daemon=True)
        self._threads[run_id] = t
        t.start()
        return run_id

    def command(self, run_id, cmd):
        bridge = self._bridges.get(run_id)
        if bridge:
            bridge.command(cmd)

    def join(self, run_id, timeout=None):
        t = self._threads.get(run_id)
        if t:
            t.join(timeout)

    def _worker(self, run_id, bp, params, sink, bridge):
        sink({"type": "status", "status": "running"})
        try:
            source = SQLiteMarketData(self.db_path)
            playback = params.get("playback_ms", 0) / 1000.0
            want_break = bool(params.get("breakpoints"))

            def on_bar_event(payload):
                sink({"type": "bar", **payload})
                if playback > 0:
                    time.sleep(playback)

            report = run_backtest(
                bp, source, inst=params["inst"], bar=params["bar"],
                start_ms=params.get("start_ms"), end_ms=params.get("end_ms"),
                initial_cash=params.get("cash", 10_000.0),
                fee_rate=params.get("fee_rate", 0.0005),
                record_dir=self.record_dir, run_id=run_id,
                breakpoints="all" if want_break else None,
                on_pause=bridge.on_pause if want_break else None,
                on_bar=on_bar_event)
            status = "halted" if report.summary.get("halted") else "completed"
            payload = {"run_id": report.run_id, "blueprint_id": report.blueprint_id,
                       "bars": report.bars, "summary": sanitize(report.summary),
                       "certificate": report.certificate,
                       "equity_curve": report.equity_curve, "fills": report.fills}
            self.store.set_status(run_id, status, report_json=json.dumps(payload),
                                  recording_path=report.recording_path)
            sink({"type": "done", "report": payload})
        except CompileFailed as cf:
            self.store.set_status(run_id, "failed",
                                  error=json.dumps([e.to_dict() for e in cf.errors]))
            sink({"type": "error", "message": "compile failed"})
        except Exception as exc:  # Engine 崩溃契约：任何异常 → failed，实例弃用
            self.store.set_status(run_id, "failed", error=str(exc))
            sink({"type": "error", "message": str(exc)})
        finally:
            self._bridges.pop(run_id, None)
