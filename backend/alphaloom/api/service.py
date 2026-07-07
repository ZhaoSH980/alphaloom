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
            if self._stopped:
                return            # stop TOCTOU 闭合：clear 后复检（T2 审查 Important-2）
            self._sink({"type": "paused", "node_id": node_id,
                        "ts": getattr(ev, "ts_close", 0),
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

    def stopped(self) -> bool:
        return self._stopped

def _jsonable(obj):
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return repr(obj)

def _safe_sink(sink):
    """sink 是推送路径（WS），它的任何异常都不得影响 run 本身（T2 审查 Important-1）。"""
    def safe(event):
        try:
            sink(event)
        except Exception:
            pass
    return safe

class RunService:
    def __init__(self, store, db_path, record_dir, llm=None, max_active_runs: int = 4):
        self.store = store
        self.db_path = db_path
        self.record_dir = record_dir
        # 注入的 RecordingLLMClient（None → D2 行为：LLM 节点缺席时零影响，
        # LLM 节点在场时 on_bar 会因 ctx.llm is None 抛清晰 RuntimeError → run failed）。
        self.llm = llm
        self._threads: dict[str, threading.Thread] = {}
        self._bridges: dict[str, BreakBridge] = {}
        self._lock = threading.Lock()
        self.max_active_runs = max(1, int(max_active_runs))

    def _prune_threads(self) -> None:
        for rid, thread in list(self._threads.items()):
            if not thread.is_alive():
                self._threads.pop(rid, None)

    def start(self, bp: BlueprintSpec, params: dict, sink,
              run_id: str | None = None) -> str:
        run_id = run_id or uuid.uuid4().hex[:12]
        bridge = BreakBridge(params.get("breakpoints", []), sink)
        with self._lock:
            self._prune_threads()
            if len(self._threads) >= self.max_active_runs:
                raise RuntimeError("too many active runs; wait for one to finish")
            llm_snapshot = self.llm
            t = threading.Thread(
                target=self._worker,
                args=(run_id, bp, params, sink, bridge, llm_snapshot),
                daemon=True)
            self.store.create(run_id, bp.id, dumps_loom(bp), json.dumps(params),
                              int(time.time() * 1000))
            self._bridges[run_id] = bridge
            self._threads[run_id] = t
        t.start()
        return run_id

    def set_llm(self, llm) -> None:
        with self._lock:
            self.llm = llm

    def command(self, run_id, cmd):
        with self._lock:
            bridge = self._bridges.get(run_id)
        if bridge:
            bridge.command(cmd)

    def join(self, run_id, timeout=None):
        with self._lock:
            t = self._threads.get(run_id)
        if t:
            t.join(timeout)

    def _worker(self, run_id, bp, params, sink, bridge, llm):
        sink = _safe_sink(sink)
        sink({"type": "status", "status": "running"})
        source = None
        try:
            source = SQLiteMarketData(self.db_path)
            playback = params.get("playback_ms", 0) / 1000.0
            ws_wait = params.get("ws_wait_ms", 0) / 1000.0
            want_break = bool(params.get("breakpoints"))
            first = [True]

            def on_bar_event(payload):
                if first[0]:
                    first[0] = False
                    if ws_wait > 0:
                        time.sleep(ws_wait)   # 首个 bar 前给 WS 连接窗口
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
                on_bar=on_bar_event,
                llm=llm,   # LLM client snapshot from run start.
                should_stop=bridge.stopped)
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
            if source is not None:
                try:
                    source.close()   # T3 复审前瞻：source 连接收尾
                except Exception:
                    pass
            with self._lock:
                self._bridges.pop(run_id, None)
                self._threads.pop(run_id, None)
