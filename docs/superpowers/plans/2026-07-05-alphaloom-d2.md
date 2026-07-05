# AlphaLoom D2 Implementation Plan — 加固/API/WS/Studio 画布/Terminal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 AlphaLoom 的可视化半边：D2 前加固批次（清偿 Carryover 债务）、FastAPI+WS 服务层（run 生命周期/断点桥/录制查询）、React 前端（Blueprint Studio 画布 + Terminal 交易页），达成"画布连线→编译反馈→一键回测→运行时流光→断点暂停检查→Terminal 看结果"的完整演示环。

**Architecture:** 后端在 D1 内核之上加薄 API 层（`alphaloom/api/`：RunService 线程驱动引擎 + SQLite run 注册表 + WS 事件流 + 断点桥）；前端 Vite+React+TS+Tailwind，React Flow 画布双向映射 .loom，lightweight-charts 行情图；hash 路由免 router 依赖；全离线（无 LLM，演示数据由确定性合成器生成）。

**Tech Stack:** FastAPI + uvicorn + starlette TestClient(httpx)；React 18 + TypeScript + Vite + Tailwind + @xyflow/react + lightweight-charts + vitest。

**执行约定（沿用 D1）：** 每任务实现者+单审查者两阶段审查（后端：spec 逐字节 diff 先行；前端 Task 5-8：以"锁定契约一致 + 构建/测试绿 + 行为走查清单"替代逐字节）；计划即权威，偏差先改计划（sanctioned deviation）；审查者提示词加 "You ARE the reviewer, do the work yourself in this session"；venv `backend/.venv`；CLI/服务入口 utf-8 reconfigure；`.env` 绝不入库。

---

## 文件结构总览

```
alphaloom/
├── backend/
│   ├── alphaloom/
│   │   ├── api/
│   │   │   ├── __init__.py          # Task 2
│   │   │   ├── serialize.py         # Task 1：sanitize()（inf/nan→None 递归）
│   │   │   ├── runs_store.py        # Task 2：runs 注册表（SQLite）
│   │   │   ├── service.py           # Task 2：RunService 线程驱动 + 断点桥 BreakBridge
│   │   │   ├── app.py               # Task 3：FastAPI REST + SPA fallback；Task 4：WS
│   │   │   └── schemas.py           # Task 3：pydantic 请求/响应模型
│   │   ├── runtime/
│   │   │   ├── engine.py            # Task 1：毒化标志 _dead/EngineDead
│   │   │   └── recorder.py          # Task 1：fetch 全序 + from_json 解码器
│   │   ├── brokers/paper.py         # Task 1：submit 防御 + halt 清 pending
│   │   ├── nodes/gates.py           # Task 1：risk_gate isfinite/负 qty/side 缺失防御
│   │   ├── nodes/sizing.py          # Task 1：equity<=0 → hold
│   │   └── cli.py                   # Task 1：summary 经 sanitize 输出
│   └── tests/
│       ├── test_hardening.py        # Task 1
│       ├── test_runs_store.py       # Task 2
│       ├── test_api_rest.py         # Task 3
│       └── test_api_ws.py           # Task 4
├── frontend/                        # Task 5-7（详见各任务）
├── scripts/ensure_demo_db.py        # Task 3：确定性合成 demo 库（幂等，秒级）
├── dev.bat / demo.bat               # Task 8
└── .claude/launch.json              # Task 8（preview_start 用）
```

## 锁定契约（跨任务，改动即 sanctioned deviation）

**Stamped 解码**（Carryover 19）：`recorder.from_json(text) -> dict`，`object_hook` 仅在 dict 恰为 `{"__stamped__", "value"}` 双键时还原为 `Stamped(value, as_of)`，其余原样（歧义防护：三键及以上不碰）。

**sanitize**（Carryover 15②）：`api/serialize.py::sanitize(obj)` 递归处理 dict/list，`float('inf')/-inf/nan → None`，其余原样返回。CLI 与 API 的所有 summary 出口都过它。

**Engine 毒化**（Carryover 14①）：`step()` 内任何异常先置 `self._dead = True` 再 raise；`_dead` 时调用 `step()` 抛 `EngineDead(RuntimeError)`（engine.py 顶部定义）。

**runs 表 schema**：`runs(run_id TEXT PRIMARY KEY, blueprint_id TEXT, blueprint_json TEXT, params_json TEXT, status TEXT, report_json TEXT, error TEXT, recording_path TEXT, created_ms INTEGER)`；status ∈ `running|completed|failed|halted`。

**REST 面**（全部 `/api` 前缀，JSON；错误统一 `{"detail": ...}`）：
- `GET /api/nodes` → `[{type, category, inputs:{port:pin}, outputs:{port:pin}, params:{name:type_str}, cost:{...}}]`（排除 category=="test"）
- `POST /api/compile` body `{blueprint: <loom dict>, bar: "1m"}` → `{ok, errors:[CompileError.to_dict], certificate, order}`（bars_per_day = 86_400_000//bar_to_ms(bar)）
- `GET /api/blueprints` → `[{id, name, meta, source: "preset"|"user"}]`（扫 blueprints/*.loom 与 blueprints/user/*.loom）
- `GET /api/blueprints/{id}` → loom dict；`POST /api/blueprints` body `{blueprint}` → 存 `blueprints/user/{server-slug}.loom`（slug=`re.sub(r'[^a-z0-9_-]','',id.lower())[:64]`，防路径注入；空 slug → 422）
- `GET /api/market/candles?inst=&bar=&start=&end=&limit=` → `[{ts,open,high,low,close,volume}]`（demo 库；limit 默认 1000 上限 5000）
- `POST /api/runs` body `{blueprint, inst, bar, start_ms?, end_ms?, cash=10000, fee_rate=0.0005, breakpoints:[], playback_ms=15}` → `{run_id}`（编译失败 → 422 + errors）
- `GET /api/runs` → 列表（按 created_ms 倒序，report_json 不含）；`GET /api/runs/{id}` → `{run_id, status, params, report?}`（report 内 summary 已 sanitize）
- `GET /api/runs/{id}/trace?node_id=&event_idx=&limit=` → 录制行（outputs/inputs 经 from_json 还原后再 sanitize 序列化；limit 默认 200）
- SPA fallback：非 /api、非 /ws 路径服务 `frontend/dist`（不存在时返回提示 JSON）

**WS 面** `WS /ws/runs/{run_id}`：
- server→client：`{"type":"bar","idx","ts","close","equity","active":[node_id],"fills":[{side,qty,price,tag}]}`（每 bar 一条，service 每 bar sleep playback_ms 毫秒，0=不限速）；`{"type":"paused","node_id","ts","inputs":{port: any}}`（ts=bar 收盘毫秒；**不叫 event_idx**——与 trace API 的 bar 计数器不同轴，避免前端错配查询）；`{"type":"status","status"}`；`{"type":"done","report"}`；`{"type":"error","message"}`
- client→server：`{"cmd":"resume"}` / `{"cmd":"step"}` / `{"cmd":"stop"}`
- **断点桥语义**：POST /api/runs 带非空 breakpoints 或 step 意图时，Engine 以 `breakpoints=set(全部节点)` 构造；`BreakBridge.on_pause(node_id,...)` 内部过滤——仅当 `node_id ∈ user_breakpoints` 或 `step_mode` 时真正阻塞（`threading.Event.wait()`）；`step` 命令 = 放行一次并置 `step_mode=True`（下一节点即停）；`resume` = 放行并 `step_mode=False`；on_pause 回调整体 try/except 自吞（Carryover 14②）。run 线程异常 → status=failed + error 记录（Engine 崩溃契约）。

**前端锁定 TS 类型**（`frontend/src/lib/loom.ts`）：
```ts
export type PinType = "exec"|"candle"|"series"|"signal"|"risk_stamped_signal"|"bool";
export interface NodeDef { type: string; category: string; inputs: Record<string,PinType>;
  outputs: Record<string,PinType>; params: Record<string,string>; cost: Record<string, unknown>; }
export interface LoomNode { id: string; type: string; params: Record<string, unknown>; }
export interface LoomEdge { from: string; to: string; feedback?: boolean; }
export interface Loom { id: string; name: string; nodes: LoomNode[]; edges: LoomEdge[]; meta: Record<string, unknown>; }
```
映射函数：`loomToFlow(loom, defs) -> {nodes: RFNode[], edges: RFEdge[]}`、`flowToLoom(nodes, edges, base) -> Loom`（handle id 约定：源 `out:<port>`、目标 `in:<port>`；feedback 边 `data.feedback=true` 虚线渲染）。引脚色：exec #e2e8f0 / candle #38bdf8 / series #a78bfa / signal #fbbf24 / risk_stamped_signal #f59e0b 描边加金 / bool #34d399。分类色（节点头）：data #0ea5e9 / indicator #8b5cf6 / decision #f59e0b / risk #ef4444 / execution #22c55e / reflection #14b8a6。

---

### Task 1: D2 前加固批次（Carryover 14①③/15①②/17①②/19 清偿）

**Files:**
- Create: `backend/alphaloom/api/__init__.py`（空）, `backend/alphaloom/api/serialize.py`
- Modify: `backend/alphaloom/runtime/engine.py`, `backend/alphaloom/runtime/recorder.py`, `backend/alphaloom/brokers/paper.py`, `backend/alphaloom/nodes/gates.py`, `backend/alphaloom/nodes/sizing.py`, `backend/alphaloom/cli.py`
- Test: `backend/tests/test_hardening.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_hardening.py
import json
import math
import pytest
import alphaloom.nodes  # noqa: F401
from alphaloom.api.serialize import sanitize
from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker
from alphaloom.graph.model import NodeSpec, loads_loom
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.types import Stamped
from alphaloom.nodes.registry import create_instance
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine, EngineDead
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.recorder import Recorder, from_json, to_json

def _bar(ts, px=100.0):
    return {"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1.0}

# ---- 15①: broker submit 防御 ----
def test_broker_rejects_nonpositive_qty():
    b = PaperBroker()
    assert b.submit(Order(side="buy", qty=0.0)) is False
    assert b.submit(Order(side="buy", qty=-1.0)) is False
    assert b._pending == []

# ---- 15②: halt 清空 pending ----
def test_halt_clears_pending():
    b = PaperBroker()
    b.on_bar(_bar(0))
    b.submit(Order(side="buy", qty=1.0))
    b.halt("test")
    b.on_bar(_bar(60_000))
    assert b.fills == [] and b.position().qty == 0.0

# ---- 15②: sanitize ----
def test_sanitize_inf_nan():
    d = {"pf": float("inf"), "x": [1.0, float("nan")], "n": {"y": float("-inf")}, "ok": 1.5}
    s = sanitize(d)
    assert s == {"pf": None, "x": [1.0, None], "n": {"y": None}, "ok": 1.5}
    json.dumps(s)  # 严格可序列化

# ---- 17①: risk_gate 恶意载荷 ----
@pytest.mark.parametrize("sig", [
    {"side": "long", "qty": float("nan"), "stop": 95.0, "reason": "x"},
    {"side": "long", "qty": 2.0, "stop": float("nan"), "reason": "x"},
    {"side": "long", "qty": -5.0, "stop": 95.0, "reason": "x"},
    {"qty": 1.0, "stop": 95.0, "reason": "no side key"},
])
def test_risk_gate_blocks_malformed(sig):
    gate = create_instance(NodeSpec("r", "risk_gate", {"max_qty": 10.0}))
    out = gate.on_bar(RunContext(clock=SimClock(), run_id="t"), {"signal": sig})
    assert out["blocked"] is True and out["stamped"]["side"] == "hold"

# ---- 17②: sizer 负 equity ----
def test_sizer_nonpositive_equity_holds():
    broker = PaperBroker(initial_cash=0.0)
    broker.on_bar(_bar(0))
    ctx = RunContext(clock=SimClock(), run_id="t"); ctx.broker = broker
    sizer = create_instance(NodeSpec("s", "position_sizer", {"risk_pct": 0.02}))
    out = sizer.on_bar(ctx, {"signal": {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "x"},
                             "candle": _bar(0)})
    assert out["sized"]["side"] == "hold"

# ---- 14①: 引擎毒化 ----
def test_engine_poisoned_after_crash():
    from tests.test_engine import _mk, _events  # 复用 te_* 图构造
    bp = {"id": "e", "name": "e", "nodes": [{"id": "bad", "type": "te_evil"}], "edges": []}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r"))
    with pytest.raises(Exception):
        eng.run(_events(1))
    assert eng._dead is True
    with pytest.raises(EngineDead):
        eng.step(_events(2)[1])

# ---- 14③: fetch 全序 ----
def test_recorder_fetch_execution_order(tmp_path):
    rec = Recorder(tmp_path / "r.sqlite")
    for nid in ["zeta", "alpha", "mid"]:   # 故意乱序字典序
        rec.record("r1", 0, 60_000, nid, {}, {})
    rows = rec.fetch("r1")
    assert [r["node_id"] for r in rows] == ["zeta", "alpha", "mid"]  # 按写入(rowid)序

# ---- 19: Stamped 解码 ----
def test_stamped_json_roundtrip():
    src = {"out": Stamped({"close": 1.5}, 60_000), "plain": 3.0}
    back = from_json(to_json(src))
    assert isinstance(back["out"], Stamped) and back["out"].as_of == 60_000
    assert back["out"].value == {"close": 1.5} and back["plain"] == 3.0

def test_stamped_decode_ambiguity_guard():
    txt = json.dumps({"x": {"__stamped__": 1, "value": 2, "extra": 3}})
    back = from_json(txt)
    assert back["x"] == {"__stamped__": 1, "value": 2, "extra": 3}  # 三键不还原

# ---- CLI 出口 sanitize（全赢 profit_factor=inf 场景）----
def test_cli_summary_sanitized(tmp_path, capsys):
    from alphaloom.cli import main
    from alphaloom.data.sqlite_source import SQLiteMarketData
    from tests.fixtures.synth import gen_candles
    from pathlib import Path
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    up = gen_candles(300, seed=11, trend=0.002)
    down = gen_candles(300, seed=12, trend=-0.002, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    repo = Path(__file__).resolve().parents[2]
    rc = main(["run", str(repo / "blueprints" / "breakout_scenario.loom"),
               "--db", str(tmp_path / "m.sqlite"), "--inst", "BTC-USDT-SWAP", "--bar", "1m"])
    out = json.loads(capsys.readouterr().out)   # 裸 Infinity 会让严格解析炸
    assert rc == 0
    pf = out["summary"]["profit_factor"]
    assert pf is None or isinstance(pf, (int, float))
    assert "Infinity" not in json.dumps(out)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_hardening.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.api / EngineDead 不存在等）

- [ ] **Step 3: 实现**

`backend/alphaloom/api/serialize.py`：

```python
from __future__ import annotations
import math

def sanitize(obj):
    """递归把 inf/-inf/nan 变 None，保证严格 RFC 8259 JSON（Carryover 15②）。"""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj
```

`engine.py`：顶部加 `class EngineDead(RuntimeError): ...`；`__init__` 加 `self._dead = False`；`step` 改为：

```python
    def step(self, ev: BarEvent) -> None:
        if self._dead:
            raise EngineDead("engine crashed earlier; discard this instance (crash contract)")
        try:
            self._step_inner(ev)
        except Exception:
            self._dead = True
            raise
```

（原 step 逻辑整体改名 `_step_inner`，内容不动。）

`recorder.py`：`fetch` 的排序改 `ORDER BY event_idx, rowid`；文件末尾加：

```python
def from_json(text: str) -> dict:
    def hook(d):
        if set(d) == {"__stamped__", "value"}:
            return Stamped(d["value"], d["__stamped__"])
        return d
    return json.loads(text, object_hook=hook)
```

`paper.py`：`submit` 开头加 `if order.qty <= 0: return False`；`halt` 加 `self._pending.clear()`（docstring 注明"熔断=冻结：清挂单、拒新单、持仓保留现场"）。

`gates.py` RiskGateNode.on_bar 的 checks 段改为（完整替换该段）：

```python
        side = sig.get("side")
        if side not in ("long", "short", "flat", "hold"):
            checks.append(f"unknown side {side!r}: must be one of long/short/flat/hold")
        elif side in ("long", "short"):
            qty = sig.get("qty", 0.0)
            stop = sig.get("stop")
            if not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty < 0:
                checks.append(f"qty {qty!r} must be a finite non-negative number")
            elif qty > self.max_qty:
                checks.append(f"qty {qty} exceeds max_qty {self.max_qty}")
            if self.require_stop and stop is None:
                checks.append("missing stop: attach a stop-loss to every entry signal")
            elif stop is not None and (not isinstance(stop, (int, float)) or not math.isfinite(stop)):
                checks.append(f"stop {stop!r} must be a finite number")
```

（文件顶部 `import math`；unknown-side 旧检查被此段吸收，`test_risk_gate_rejects_unknown_side` 断言不变仍绿。）

`sizing.py`：`equity = ...` 之后加：

```python
        if equity <= 0:
            return {"sized": dict(sig, side="hold", reason="non-positive equity")}
```

`cli.py`：`from alphaloom.api.serialize import sanitize`；run 分支输出改 `"summary": sanitize(report.summary)`；compile 分支的 certificate 同样包 sanitize（防未来注解出 inf）。

- [ ] **Step 4: 全量回归**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 73 passed（60 + 13 新增）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix(hardening): D2 debt batch - broker guards, halt freeze, engine poison, gate/sizer payload guards, stamped decoder, sanitize"
```

---

### Task 2: Run 注册表 + RunService/断点桥（api/runs_store.py, api/service.py）

**Files:**
- Create: `backend/alphaloom/api/runs_store.py`, `backend/alphaloom/api/service.py`
- Test: `backend/tests/test_runs_store.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_runs_store.py
import json
import queue
import threading
import time
import pytest
import alphaloom.nodes  # noqa: F401
from pathlib import Path
from alphaloom.api.runs_store import RunsStore
from alphaloom.api.service import RunService, BreakBridge
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import load_loom_file
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]

def _db(tmp_path, n=120):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(n, seed=5, trend=0.001))
    return tmp_path / "m.sqlite"

def _params(tmp_path, **kw):
    p = {"inst": "BTC-USDT-SWAP", "bar": "1m", "cash": 10_000.0,
         "fee_rate": 0.0005, "breakpoints": [], "playback_ms": 0}
    p.update(kw)
    return p

def test_store_crud(tmp_path):
    st = RunsStore(tmp_path / "runs.sqlite")
    st.create("r1", "bp", "{}", "{}", 123)
    st.set_status("r1", "completed", report_json='{"ok":1}', recording_path="x.sqlite")
    row = st.get("r1")
    assert row["status"] == "completed" and json.loads(row["report_json"]) == {"ok": 1}
    assert [r["run_id"] for r in st.list()] == ["r1"]
    assert st.get("nope") is None

def test_service_completes_run(tmp_path):
    db_path = _db(tmp_path)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    events = queue.Queue()
    run_id = svc.start(bp, _params(tmp_path), sink=events.put)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "completed"
    report = json.loads(row["report_json"])
    assert report["bars"] == 120 and "summary" in report
    types = set()
    while not events.empty():
        types.add(events.get()["type"])
    assert {"status", "bar", "done"} <= types

def test_service_marks_failed_on_crash(tmp_path):
    db_path = _db(tmp_path)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    import tests.test_engine  # noqa: F401  注册 te_evil
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    bp.nodes.append(type(bp.nodes[0])("bad", "te_evil", {}))
    run_id = svc.start(bp, _params(tmp_path), sink=lambda e: None)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "failed" and "future" in row["error"]

def test_break_bridge_pause_resume(tmp_path):
    db_path = _db(tmp_path, n=30)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    events = queue.Queue()
    run_id = svc.start(bp, _params(tmp_path, breakpoints=["risk"]), sink=events.put)
    ev = _wait_for(events, "paused", 15)
    assert ev["node_id"] == "risk" and "signal" in ev["inputs"]
    svc.command(run_id, "step")            # 放行一次，下一节点即停
    ev2 = _wait_for(events, "paused", 15)
    assert ev2["node_id"] != "risk" or ev2["ts"] != ev["ts"]
    svc.command(run_id, "resume")          # 之后每次 risk 命中仍会停 → 连续 resume 清完
    deadline = time.time() + 30
    while time.time() < deadline:
        if svc.store.get(run_id)["status"] == "completed":
            break
        svc.command(run_id, "resume")
        time.sleep(0.05)
    assert svc.store.get(run_id)["status"] == "completed"

def test_raising_sink_does_not_kill_run(tmp_path):
    db_path = _db(tmp_path, n=40)
    svc = RunService(store=RunsStore(tmp_path / "runs.sqlite"),
                     db_path=db_path, record_dir=tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    def evil_sink(event):
        raise RuntimeError("ws is gone")
    run_id = svc.start(bp, _params(tmp_path), sink=evil_sink)
    svc.join(run_id, timeout=30)
    row = svc.store.get(run_id)
    assert row["status"] == "completed"          # 推送失败绝不杀回测
    assert run_id not in svc._bridges            # 无 bridge 泄漏

def _wait_for(q, typ, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ev = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if ev["type"] == typ:
            return ev
    pytest.fail(f"no {typ} event within {timeout}s")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_runs_store.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
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
```

```python
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
        sink = _safe_sink(sink)
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
```

**runner 扩展（本任务一并做，sanctioned 提前声明）**：`run_backtest` 增加可选参数 `run_id=None, breakpoints=None, on_pause=None, on_bar=None`——`run_id` 外部指定（默认沿用 uuid）；`breakpoints="all"` 时 Engine 以 `set(compiled.order)` 构造并挂 on_pause；每根 bar 处理后若 `on_bar` 非空，回调 `{"idx": bars-1, "ts": candle["ts"], "close": candle["close"], "equity": broker.equity(), "active": compiled.order, "fills": [f.__dict__ for f in broker.fills[fills_seen:]]}`（fills_seen 为上一 bar 末计数，即"本 bar 新增成交"）。签名向后兼容（全部默认 None），D1 测试零改动。

- [ ] **Step 4: 全量回归**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 78 passed（73 + 5）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(api): runs store + run service with break bridge, runner hooks"
```

---

### Task 3: FastAPI REST + demo 库脚本（api/app.py, api/schemas.py, scripts/ensure_demo_db.py）

**Files:**
- Create: `backend/alphaloom/api/schemas.py`, `backend/alphaloom/api/app.py`, `scripts/ensure_demo_db.py`
- Modify: `backend/pyproject.toml`（dependencies 加 `"fastapi>=0.115"`, `"uvicorn>=0.30"`；dev 加 `"httpx>=0.27"`）
- Modify: `backend/alphaloom/data/sqlite_source.py`（T3 审查 Minor-2 sanctioned：加 `def close(self) -> None: self._db.close()`）
- Test: `backend/tests/test_api_rest.py`

- [ ] **Step 1: 安装依赖**

```bash
cd backend && .venv/Scripts/python -m pip install "fastapi>=0.115" "uvicorn>=0.30" "httpx>=0.27" && .venv/Scripts/python -m pip install -e .
```

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_api_rest.py
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
import alphaloom.nodes  # noqa: F401
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]

@pytest.fixture()
def client(tmp_path):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    up = gen_candles(200, seed=11, trend=0.002)
    down = gen_candles(200, seed=12, trend=-0.002, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    app = create_app(db_path=tmp_path / "demo.sqlite",
                     runs_db=tmp_path / "runs.sqlite",
                     record_dir=tmp_path,
                     blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "user_bp",
                     frontend_dist=tmp_path / "no_dist")
    return TestClient(app)

def test_nodes_endpoint_excludes_test_category(client):
    r = client.get("/api/nodes")
    assert r.status_code == 200
    types = {n["type"] for n in r.json()}
    assert "risk_gate" in types and "candle_feed" in types
    assert not any(t.startswith(("tc_", "te_", "t_", "tb_")) for t in types)
    rg = next(n for n in r.json() if n["type"] == "risk_gate")
    assert rg["outputs"]["stamped"] == "risk_stamped_signal" and rg["category"] == "risk"

def test_compile_endpoint_ok_and_errors(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/compile", json={"blueprint": loom, "bar": "1H"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["certificate"]["deterministic_ratio"] == 1.0
    bad = dict(loom, edges=[e for e in loom["edges"]
                            if e["to"] != "exec.signal"] + [{"from": "cross.signal",
                                                             "to": "exec.signal"}])
    r2 = client.post("/api/compile", json={"blueprint": bad})
    assert r2.json()["ok"] is False
    assert any(e["code"] == "TYPE_MISMATCH" for e in r2.json()["errors"])

def test_blueprints_list_get_save(client):
    lst = client.get("/api/blueprints").json()
    ids = {b["id"] for b in lst}
    assert {"ema_cross_v1", "breakout_scenario_v1"} <= ids
    one = client.get("/api/blueprints/ema_cross_v1")
    assert one.status_code == 200 and one.json()["id"] == "ema_cross_v1"
    custom = dict(one.json(), id="My Custom#1", name="c")
    r = client.post("/api/blueprints", json={"blueprint": custom})
    assert r.status_code == 200
    saved_id = r.json()["id"]
    assert saved_id == "mycustom1"          # server-slug 防注入
    assert client.get(f"/api/blueprints/{saved_id}").status_code == 200
    evil = dict(custom, id="../../etc/passwd")
    r2 = client.post("/api/blueprints", json={"blueprint": evil})
    assert r2.status_code in (200, 422)
    if r2.status_code == 200:
        assert "/" not in r2.json()["id"] and ".." not in r2.json()["id"]

def test_market_candles_window(client):
    r = client.get("/api/market/candles",
                   params={"inst": "BTC-USDT-SWAP", "bar": "1m", "limit": 50})
    rows = r.json()
    assert r.status_code == 200 and len(rows) == 50
    assert list(rows[0]) == ["ts", "open", "high", "low", "close", "volume"]

def test_run_lifecycle_and_trace(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"blueprint": loom, "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    import time
    for _ in range(100):
        row = client.get(f"/api/runs/{run_id}").json()
        if row["status"] != "running":
            break
        time.sleep(0.1)
    assert row["status"] == "completed"
    assert row["report"]["bars"] == 400
    assert "Infinity" not in json.dumps(row)
    lst = client.get("/api/runs").json()
    assert any(x["run_id"] == run_id for x in lst)
    tr = client.get(f"/api/runs/{run_id}/trace", params={"node_id": "risk", "limit": 5})
    assert tr.status_code == 200 and len(tr.json()) == 5
    assert tr.json()[0]["node_id"] == "risk" and "outputs" in tr.json()[0]

def test_run_compile_failure_422(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    bad = dict(loom, edges=[{"from": "cross.signal", "to": "exec.signal"}])
    r = client.post("/api/runs", json={"blueprint": bad, "inst": "BTC-USDT-SWAP", "bar": "1m"})
    assert r.status_code == 422
    assert any(e["code"] == "TYPE_MISMATCH" for e in r.json()["detail"]["errors"])

def test_unknown_run_404(client):
    assert client.get("/api/runs/nope").status_code == 404

def test_spa_fallback_no_path_traversal(tmp_path):
    # T3 审查 Critical-1 回归：编码穿越不得读出 dist 之外的文件
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path / "SECRET.txt").write_text("TOP-SECRET", encoding="utf-8")
    app = create_app(db_path=tmp_path / "d.sqlite", runs_db=tmp_path / "r.sqlite",
                     record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "ubp", frontend_dist=dist)
    c = TestClient(app)
    for evil in ["..%2FSECRET.txt", "%2e%2e%2fSECRET.txt", "..%2F..%2FSECRET.txt"]:
        r = c.get(f"/{evil}")
        assert "TOP-SECRET" not in r.text, evil
    assert "ok" in c.get("/anything/deep").text   # SPA fallback 正常路径不受影响

def test_run_window_bounds_rejected(client):
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"blueprint": loom, "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "end_ms": 10**19})
    assert r.status_code == 422
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_api_rest.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.api.app）

- [ ] **Step 4: 实现**

```python
# backend/alphaloom/api/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field

class CompileIn(BaseModel):
    blueprint: dict
    bar: str = "1m"

class SaveBlueprintIn(BaseModel):
    blueprint: dict

class RunIn(BaseModel):
    blueprint: dict
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)   # ≤2100 年，防 int64 溢出穿到 sqlite
    end_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    breakpoints: list[str] = Field(default_factory=list)
    playback_ms: int = 15
```

```python
# backend/alphaloom/api/app.py
from __future__ import annotations
import asyncio
import json
import re
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
import alphaloom.nodes  # noqa: F401
from alphaloom.api.runs_store import RunsStore
from alphaloom.api.schemas import CompileIn, RunIn, SaveBlueprintIn
from alphaloom.api.serialize import sanitize
from alphaloom.api.service import RunService
from alphaloom.data.source import bar_to_ms
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import dumps_loom, loads_loom
from alphaloom.nodes.registry import REGISTRY
from alphaloom.runtime.recorder import from_json

_BARS = ["1m", "5m", "15m", "1H", "4H", "1D"]

def create_app(*, db_path, runs_db, record_dir, blueprints_dir, user_blueprints_dir,
               frontend_dist) -> FastAPI:
    app = FastAPI(title="AlphaLoom API")
    store = RunsStore(runs_db)
    service = RunService(store=store, db_path=db_path, record_dir=record_dir)
    user_dir = Path(user_blueprints_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    app.state.service = service
    app.state.ws_queues = {}          # run_id -> list[asyncio.Queue]（Task 4 消费）
    app.state.loop = None

    @app.on_event("startup")
    async def _grab_loop():
        app.state.loop = asyncio.get_running_loop()

    def _sink_for(run_id):
        def sink(event):
            loop = app.state.loop
            if loop is None:
                return
            for q in list(app.state.ws_queues.get(run_id, [])):
                loop.call_soon_threadsafe(q.put_nowait, event)
        return sink

    @app.get("/api/nodes")
    def nodes():
        out = []
        for d in REGISTRY.values():
            if d.category == "test":
                continue
            out.append({"type": d.type, "category": d.category,
                        "inputs": {k: v.value for k, v in d.inputs.items()},
                        "outputs": {k: v.value for k, v in d.outputs.items()},
                        "params": {k: getattr(v, "__name__", str(v))
                                   for k, v in d.params.items()},
                        "cost": d.cost.__dict__})
        return sorted(out, key=lambda x: (x["category"], x["type"]))

    @app.post("/api/compile")
    def compile_ep(body: CompileIn):
        if body.bar not in _BARS:
            raise HTTPException(422, f"bar must be one of {_BARS}")
        try:
            bp = loads_loom(json.dumps(body.blueprint))
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "errors": [{"code": "PARAM_INVALID",
                                             "message": f"bad loom: {exc}",
                                             "node_id": None, "port": None,
                                             "fix_hint": None}],
                    "certificate": None, "order": []}
        r = compile_blueprint(bp, bars_per_day=86_400_000 // bar_to_ms(body.bar))
        return {"ok": r.ok, "errors": [e.to_dict() for e in r.errors],
                "certificate": sanitize(r.certificate.to_dict()) if r.certificate else None,
                "order": r.order}

    def _iter_blueprints():
        for src, folder in (("preset", Path(blueprints_dir)), ("user", user_dir)):
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*.loom")):
                try:
                    raw = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                yield src, f, raw

    @app.get("/api/blueprints")
    def blueprints_list():
        return [{"id": raw["id"], "name": raw.get("name", raw["id"]),
                 "meta": raw.get("meta", {}), "source": src}
                for src, _f, raw in _iter_blueprints()]

    @app.get("/api/blueprints/{bp_id}")
    def blueprint_get(bp_id: str):
        for _src, _f, raw in _iter_blueprints():
            if raw["id"] == bp_id:
                return raw
        raise HTTPException(404, "blueprint not found")

    @app.post("/api/blueprints")
    def blueprint_save(body: SaveBlueprintIn):
        try:
            bp = loads_loom(json.dumps(body.blueprint))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"bad loom: {exc}")
        slug = re.sub(r"[^a-z0-9_-]", "", bp.id.lower())[:64]
        if not slug:
            raise HTTPException(422, "blueprint id yields empty slug")
        data = dict(body.blueprint, id=slug)
        (user_dir / f"{slug}.loom").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"id": slug}

    @app.get("/api/market/candles")
    def candles(inst: str, bar: str = "1m", start: int | None = None,
                end: int | None = None, limit: int = 1000):
        if bar not in _BARS:
            raise HTTPException(422, f"bar must be one of {_BARS}")
        limit = max(1, min(int(limit), 5000))
        from alphaloom.data.sqlite_source import SQLiteMarketData
        src = SQLiteMarketData(db_path)
        try:
            rows = []
            for c in src.iter_candles(inst, bar, start, end):
                rows.append(c)
                if len(rows) >= limit:
                    break
            return rows
        finally:
            src.close()

    @app.post("/api/runs")
    def run_start(body: RunIn):
        if body.bar not in _BARS:
            raise HTTPException(422, f"bar must be one of {_BARS}")
        try:
            bp = loads_loom(json.dumps(body.blueprint))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, {"errors": [{"code": "PARAM_INVALID",
                                                  "message": str(exc)}]})
        r = compile_blueprint(bp, bars_per_day=86_400_000 // bar_to_ms(body.bar))
        if not r.ok:
            raise HTTPException(422, {"errors": [e.to_dict() for e in r.errors]})
        params = body.model_dump(exclude={"blueprint"})
        import uuid as _uuid
        run_id = _uuid.uuid4().hex[:12]          # 两段式：先定 run_id 再构造 sink
        service.start(bp, params, sink=_sink_for(run_id), run_id=run_id)
        return {"run_id": run_id}

    @app.get("/api/runs")
    def runs_list():
        return store.list()

    @app.get("/api/runs/{run_id}")
    def run_get(run_id: str):
        row = store.get(run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        out = {"run_id": row["run_id"], "status": row["status"],
               "params": json.loads(row["params_json"] or "{}"),
               "error": row["error"]}
        if row["report_json"]:
            out["report"] = sanitize(json.loads(row["report_json"]))
        return out

    @app.get("/api/runs/{run_id}/trace")
    def run_trace(run_id: str, node_id: str | None = None,
                  event_idx: int | None = None, limit: int = 200):
        row = store.get(run_id)
        if row is None or not row["recording_path"]:
            raise HTTPException(404, "run or recording not found")
        db = sqlite3.connect(row["recording_path"])
        q = "SELECT run_id, event_idx, ts, node_id, inputs_json, outputs_json FROM node_io WHERE run_id=?"
        args: list = [run_id]
        if node_id:
            q += " AND node_id=?"; args.append(node_id)
        if event_idx is not None:
            q += " AND event_idx=?"; args.append(event_idx)
        q += " ORDER BY event_idx, rowid LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        try:
            rows = db.execute(q, args).fetchall()
        finally:
            db.close()
        out = []
        for r_id, idx, ts, nid, ij, oj in rows:
            out.append({"event_idx": idx, "ts": ts, "node_id": nid,
                        "inputs": sanitize(_decode(ij)), "outputs": sanitize(_decode(oj))})
        return out

    def _decode(text):
        d = from_json(text)
        return {k: ({"as_of": v.as_of, "value": v.value}
                    if hasattr(v, "as_of") else v) for k, v in d.items()}

    # SPA fallback（/api /ws 之外）
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        dist = Path(frontend_dist)
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        dist_root = dist.resolve()
        candidate = (dist / path).resolve()
        # 收容检查：编码穿越（%2F/%2e）在 uvicorn 解码后会以字面 ../ 到达这里（T3 审查 Critical-1）
        if path and candidate.is_file() and candidate.is_relative_to(dist_root):
            return FileResponse(candidate)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"hint": "frontend not built; run npm run build"}, status_code=200)

    return app
```

**时序说明**：sink 需要 run_id 而线程由 service 启动——采用两段式（app 层先生成 run_id 再传入 `service.start(..., run_id=run_id)`），Task 2 的 `start` 签名已带 `run_id: str | None = None` 默认参数。

```python
# scripts/ensure_demo_db.py
"""幂等生成确定性 demo 行情库（秒级，零联网）。dev.bat/demo.bat 启动前调用。"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from fixtures.synth import gen_candles  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "demo.sqlite"

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteMarketData(OUT)
    if db.bounds("BTC-USDT-SWAP", "1m"):
        print(f"demo db ready: {OUT}")
        return 0
    up = gen_candles(2000, seed=11, trend=0.0008, start_price=60_000, vol=0.003)
    down = gen_candles(1200, seed=12, trend=-0.0009, start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"], vol=0.004)
    chop = gen_candles(800, seed=13, trend=0.0, start_ts=down[-1]["ts"] + 60_000,
                       start_price=down[-1]["close"], vol=0.002)
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down + chop)
    eth = gen_candles(4000, seed=21, trend=0.0003, start_price=3000, vol=0.004)
    db.insert_candles("ETH-USDT-SWAP", "1m", eth)
    print(f"demo db built: {OUT} (BTC 4000 + ETH 4000 bars)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

`data/demo.sqlite` 加入 `.gitignore`（`data/` 行已覆盖 sample.sqlite，改为整行 `data/*.sqlite`——检查现状按需调整）。

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_api_rest.py -q`
Expected: `9 passed`；全量 87 passed

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(api): REST surface - nodes/compile/blueprints/candles/runs/trace + SPA fallback + demo db script"
```

---

### Task 4: WS 事件流 + 断点命令通道（api/app.py 扩展）

**Files:**
- Modify: `backend/alphaloom/api/app.py`
- Test: `backend/tests/test_api_ws.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_ws.py
import json
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
import alphaloom.nodes  # noqa: F401
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]

@pytest.fixture()
def client(tmp_path):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(60, seed=5, trend=0.001))
    app = create_app(db_path=tmp_path / "demo.sqlite", runs_db=tmp_path / "runs.sqlite",
                     record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
                     user_blueprints_dir=tmp_path / "ubp", frontend_dist=tmp_path / "nd")
    return TestClient(app)

def _loom():
    return json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))

def _collect_until(ws, typ, limit=500):
    for _ in range(limit):
        ev = ws.receive_json()
        if ev["type"] == typ:
            return ev
    pytest.fail(f"no {typ} in {limit} events")

def test_ws_streams_bars_and_done(client):
    r = client.post("/api/runs", json={"blueprint": _loom(), "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0, "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        first = ws.receive_json()
        assert first["type"] in ("status", "bar")
        done = _collect_until(ws, "done")
        assert done["report"]["bars"] == 60
        assert "Infinity" not in json.dumps(done)

def test_ws_breakpoint_pause_resume(client):
    r = client.post("/api/runs", json={"blueprint": _loom(), "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0,
                                       "breakpoints": ["risk"], "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        paused = _collect_until(ws, "paused")
        assert paused["node_id"] == "risk" and "signal" in paused["inputs"]
        ws.send_json({"cmd": "step"})
        paused2 = _collect_until(ws, "paused")
        assert (paused2["node_id"], paused2["ts"]) != (paused["node_id"], paused["ts"])
        ws.send_json({"cmd": "stop"})
        end = _collect_until(ws, "done")
        assert end["report"]["bars"] == 60

def test_ws_unknown_run_closes(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/runs/nope") as ws:
            ws.receive_json()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_api_ws.py -q`
Expected: FAIL（无 /ws 路由 / ws_wait_ms 未知字段被忽略但无缓冲导致事件丢失）

- [ ] **Step 3: 实现（app.py 增补）**

设计要点（锁定）：
1. **事件缓冲防竞态**：run 线程可能在 WS 连上之前就完成——`RunIn` 加字段 `ws_wait_ms: int = 0`：service 侧 sink 先写入 `app.state.event_log[run_id]`（内存 list，上限 20_000 条），WS 连接时先重放 event_log 再订阅增量；`ws_wait_ms > 0` 时 run 线程在发出第一个 bar 事件前 sleep 该毫秒数（给测试/前端连接窗口；生产默认 0 靠重放兜底）。
2. `stop` 命令：`service.command(run_id, "stop")` 使 BreakBridge 永久放行（不再暂停），run 跑完自然收尾（D2 不做硬中断——引擎崩溃契约禁止半途丢弃后复用）。
3. 未知 run_id：accept 后立即 close(code=4404)。
4. **service._worker 的 source 连接收尾（T3 复审前瞻）**：`_worker` 的 `source = SQLiteMarketData(...)` 用 try/finally 关闭；`RunsStore` 加 `close()`（app 生命周期单例，可不主动关但补齐接口）。属本任务顺带清偿。

```python
    # —— WS（app.py 内，create_app 尾部 SPA 路由之前）——
    @app.websocket("/ws/runs/{run_id}")
    async def ws_run(ws: WebSocket, run_id: str):
        await ws.accept()
        if store.get(run_id) is None:
            await ws.close(code=4404)
            return
        q: asyncio.Queue = asyncio.Queue()
        app.state.ws_queues.setdefault(run_id, []).append(q)
        try:
            for ev in list(app.state.event_log.get(run_id, [])):
                await ws.send_json(ev)                      # 重放
            while True:
                recv = asyncio.create_task(ws.receive_json())
                pull = asyncio.create_task(q.get())
                done_set, pending = await asyncio.wait(
                    {recv, pull}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                if recv in done_set:
                    try:
                        msg = recv.result()
                    except Exception:
                        break
                    cmd = msg.get("cmd")
                    if cmd in ("resume", "step", "stop"):
                        service.command(run_id, cmd)
                if pull in done_set:
                    ev = pull.result()
                    await ws.send_json(ev)
                    if ev["type"] in ("done", "error"):
                        break
        except WebSocketDisconnect:
            pass
        finally:
            app.state.ws_queues.get(run_id, []).remove(q)
```

sink 侧同步改造（app.py `_sink_for`）：

```python
    app.state.event_log = {}

    def _sink_for(run_id):
        def sink(event):
            log = app.state.event_log.setdefault(run_id, [])
            if len(log) < 20_000:
                log.append(event)
            loop = app.state.loop
            if loop is not None:
                for q in list(app.state.ws_queues.get(run_id, [])):
                    loop.call_soon_threadsafe(q.put_nowait, event)
        return sink
```

`schemas.py` RunIn 加 `ws_wait_ms: int = 0`；service `_worker` 在首个 bar 回调前 `time.sleep(params.get("ws_wait_ms", 0)/1000)`（实现：`on_bar_event` 闭包内用 `first` 标志）。BreakBridge 的 `stop` 语义已在 Task 2 落地（`_stopped=True` 永久放行）。

**Sanctioned deviation（T4 实现期发现，2026-07-05）**：`ws_run` 顶部（`accept()` 之后）须加一行 `app.state.loop = asyncio.get_running_loop()`。原因：sink 从后台 run 线程用 `loop.call_soon_threadsafe(q.put_nowait, ...)` 推事件，必须调度到**真正服务本连接**的 event loop。starlette `TestClient` 每个 `websocket_connect` 会起独立 anyio portal + 新 loop，与 `@on_event("startup")` 里捕获的 loop 不是同一个——若沿用 startup loop，threadsafe 回调派到已死/错误的 loop，服务端 `q.get()` 永不唤醒 → WS 流事件全丢、`receive_json` 死锁。在 handler 内捕获运行中 loop 修复此问题；uvicorn 生产单 loop 下该赋值等价无害（幂等覆盖为同一 loop）。startup 的 `_grab_loop` 保留（首连接前的兜底，无害）。

- [ ] **Step 4: 全量回归**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 90 passed（87 + 3）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(api): websocket event stream with replay buffer + breakpoint command channel"
```

---

### Task 5: 前端脚手架（Vite+React+TS+Tailwind+基础库）

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tailwind.config.js`, `frontend/postcss.config.js`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles/index.css`, `frontend/src/lib/api.ts`, `frontend/src/lib/ws.ts`, `frontend/src/lib/i18n.ts`, `frontend/src/lib/loom.ts`, `frontend/src/lib/__tests__/loom.test.ts`

**验收方式（前端任务通用）**：`npm run build` 零错误 + `npx vitest run` 全绿 + 锁定契约（TS 类型/handle 约定/色板）与计划一致。组件内部实现允许等价重写（**契约与行为清单不许动**），审查者按行为清单走查。

- [ ] **Step 1: 初始化与依赖**

```bash
cd F:/AIProjects/my_show/alphaloom/frontend  # 目录不存在则先 mkdir
npm init -y
npm i react@^18.3 react-dom@^18.3 @xyflow/react@^12 lightweight-charts@^4.2
npm i -D typescript@~5.6 vite@^5.4 @vitejs/plugin-react@^4.3 tailwindcss@^3.4 postcss autoprefixer vitest@^2 @types/react@^18 @types/react-dom@^18 jsdom
```

- [ ] **Step 2: 配置文件（逐字）**

```json
// frontend/package.json 的 scripts 段（合并进 npm init 产物）
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "preview": "vite preview"
  }
}
```

```ts
// frontend/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  test: { environment: "jsdom" },
} as never);
```

```json
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022", "lib": ["ES2022", "DOM", "DOM.Iterable"], "module": "ESNext",
    "moduleResolution": "bundler", "jsx": "react-jsx", "strict": true,
    "skipLibCheck": true, "noEmit": true, "types": ["vite/client"]
  },
  "include": ["src"]
}
```

```js
// frontend/tailwind.config.js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#060913", panel: "#0b1020", edge: "#1e2a44",
        loom: { blue: "#38bdf8", violet: "#a78bfa", amber: "#fbbf24",
                gold: "#f59e0b", green: "#34d399", red: "#ef4444" },
      },
      boxShadow: { glow: "0 0 12px 2px rgba(56,189,248,0.55)" },
    },
  },
  plugins: [],
};
```

```js
// frontend/postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="zh">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AlphaLoom — The graph IS the agent</title>
  </head>
  <body class="bg-void text-slate-200">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```css
/* frontend/src/styles/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

:root { color-scheme: dark; }
body { background: radial-gradient(1200px 800px at 70% -10%, #101a33 0%, #060913 55%); }

.panel { @apply bg-panel/90 border border-edge rounded-lg; }
.hud-label { @apply text-[10px] uppercase tracking-[0.2em] text-slate-500; }
.node-glow { box-shadow: 0 0 14px 3px rgba(56, 189, 248, 0.6); }
.node-blocked { box-shadow: 0 0 14px 3px rgba(239, 68, 68, 0.7); }
@media (prefers-reduced-motion: reduce) {
  .node-glow, .node-blocked { transition: none; }
}
```

- [ ] **Step 3: 基础库（逐字，锁定契约所在）**

```ts
// frontend/src/lib/loom.ts —— 锁定 TS 类型 + 映射 + 色板（契约区，见计划头）
export type PinType = "exec" | "candle" | "series" | "signal" | "risk_stamped_signal" | "bool";
export interface NodeDef {
  type: string; category: string;
  inputs: Record<string, PinType>; outputs: Record<string, PinType>;
  params: Record<string, string>; cost: Record<string, unknown>;
}
export interface LoomNode { id: string; type: string; params: Record<string, unknown>; }
export interface LoomEdge { from: string; to: string; feedback?: boolean; }
export interface Loom {
  id: string; name: string; nodes: LoomNode[]; edges: LoomEdge[];
  meta: Record<string, unknown>;
}

export const PIN_COLORS: Record<PinType, string> = {
  exec: "#e2e8f0", candle: "#38bdf8", series: "#a78bfa",
  signal: "#fbbf24", risk_stamped_signal: "#f59e0b", bool: "#34d399",
};
export const CATEGORY_COLORS: Record<string, string> = {
  data: "#0ea5e9", indicator: "#8b5cf6", decision: "#f59e0b",
  risk: "#ef4444", execution: "#22c55e", reflection: "#14b8a6",
};

export interface FlowNode {
  id: string; type: "loomNode"; position: { x: number; y: number };
  data: { def: NodeDef; params: Record<string, unknown> };
}
export interface FlowEdge {
  id: string; source: string; sourceHandle: string;
  target: string; targetHandle: string;
  data: { feedback: boolean };
}

const GRID_X = 260, GRID_Y = 150, COLS = 4;

export function loomToFlow(loom: Loom, defs: Record<string, NodeDef>):
    { nodes: FlowNode[]; edges: FlowEdge[] } {
  const pos = (loom.meta?.positions ?? {}) as Record<string, { x: number; y: number }>;
  const nodes: FlowNode[] = loom.nodes.map((n, i) => ({
    id: n.id, type: "loomNode",
    position: pos[n.id] ?? { x: (i % COLS) * GRID_X + 40, y: Math.floor(i / COLS) * GRID_Y + 40 },
    data: { def: defs[n.type], params: n.params },
  }));
  const edges: FlowEdge[] = loom.edges.map((e, i) => {
    const [sn, sp] = e.from.split(".");
    const [tn, tp] = e.to.split(".");
    return { id: `e${i}`, source: sn, sourceHandle: `out:${sp}`,
             target: tn, targetHandle: `in:${tp}`, data: { feedback: !!e.feedback } };
  });
  return { nodes, edges };
}

export function flowToLoom(nodes: FlowNode[], edges: FlowEdge[], base: Loom): Loom {
  const positions: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) positions[n.id] = { x: Math.round(n.position.x), y: Math.round(n.position.y) };
  return {
    ...base,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.def.type, params: n.data.params })),
    edges: edges.map((e) => ({
      from: `${e.source}.${(e.sourceHandle ?? "").replace(/^out:/, "")}`,
      to: `${e.target}.${(e.targetHandle ?? "").replace(/^in:/, "")}`,
      ...(e.data?.feedback ? { feedback: true } : {}),
    })),
    meta: { ...base.meta, positions },
  };
}

export function nextNodeId(existing: Set<string>, type: string): string {
  let i = 1;
  while (existing.has(`${type}_${i}`)) i += 1;
  return `${type}_${i}`;
}
```

```ts
// frontend/src/lib/api.ts
import type { Loom, NodeDef } from "./loom";

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status, body: await res.text() });
  return res.json();
}
export const getNodes = () => j<NodeDef[]>(fetch("/api/nodes"));
export const compileLoom = (blueprint: Loom, bar = "1m") =>
  j<{ ok: boolean; errors: { code: string; message: string; node_id?: string; port?: string; fix_hint?: string }[];
      certificate: Record<string, unknown> | null; order: string[] }>(
    fetch("/api/compile", { method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ blueprint, bar }) }));
export const listBlueprints = () =>
  j<{ id: string; name: string; meta: Record<string, unknown>; source: string }[]>(fetch("/api/blueprints"));
export const getBlueprint = (id: string) => j<Loom>(fetch(`/api/blueprints/${id}`));
export const saveBlueprint = (blueprint: Loom) =>
  j<{ id: string }>(fetch("/api/blueprints", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ blueprint }) }));
export const startRun = (body: Record<string, unknown>) =>
  j<{ run_id: string }>(fetch("/api/runs", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
export const getRun = (id: string) => j<Record<string, any>>(fetch(`/api/runs/${id}`));
export const listRuns = () => j<Record<string, any>[]>(fetch("/api/runs"));
export const getCandles = (inst: string, bar: string, limit = 5000) =>
  j<{ ts: number; open: number; high: number; low: number; close: number; volume: number }[]>(
    fetch(`/api/market/candles?inst=${encodeURIComponent(inst)}&bar=${bar}&limit=${limit}`));
export const getTrace = (runId: string, nodeId?: string, eventIdx?: number, limit = 200) => {
  const q = new URLSearchParams();
  if (nodeId) q.set("node_id", nodeId);
  if (eventIdx !== undefined) q.set("event_idx", String(eventIdx));
  q.set("limit", String(limit));
  return j<Record<string, any>[]>(fetch(`/api/runs/${runId}/trace?${q}`));
};
```

```ts
// frontend/src/lib/ws.ts
export interface RunEvent { type: string; [k: string]: any; }
export function openRunSocket(runId: string, onEvent: (e: RunEvent) => void,
                              onClose?: () => void) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`);
  ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch { /* ignore */ } };
  ws.onclose = () => onClose?.();
  return {
    send: (cmd: "resume" | "step" | "stop") => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd }));
    },
    close: () => ws.close(),
  };
}
```

```ts
// frontend/src/lib/i18n.ts
import { useSyncExternalStore } from "react";

const dict = {
  zh: { studio: "蓝图工坊", terminal: "交易终端", run: "回测运行", compileOk: "编译通过",
        compileFail: "编译失败", cost: "成本证书", gallery: "蓝图库", save: "保存",
        resume: "继续", step: "单步", stop: "停止", paused: "已暂停", trades: "成交",
        equity: "权益", summary: "汇总", noRuns: "暂无运行", breakpointHint: "点节点圆点设断点" },
  en: { studio: "Studio", terminal: "Terminal", run: "Run backtest", compileOk: "Compiled",
        compileFail: "Compile failed", cost: "Cost certificate", gallery: "Gallery", save: "Save",
        resume: "Resume", step: "Step", stop: "Stop", paused: "Paused", trades: "Trades",
        equity: "Equity", summary: "Summary", noRuns: "No runs yet", breakpointHint: "Click node dot to set breakpoint" },
} as const;
export type LangKey = keyof typeof dict.zh;

let lang: "zh" | "en" = (localStorage.getItem("alphaloom.lang") as "zh" | "en") || "zh";
const subs = new Set<() => void>();
export function setLang(l: "zh" | "en") {
  lang = l; localStorage.setItem("alphaloom.lang", l); subs.forEach((f) => f());
}
export function useLang() {
  const l = useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => lang);
  return { lang: l, t: (k: LangKey) => dict[l][k], setLang };
}
```

```tsx
// frontend/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import "@xyflow/react/dist/style.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
```

```tsx
// frontend/src/App.tsx —— hash 路由外壳
import { useEffect, useState, lazy, Suspense } from "react";
import { useLang } from "./lib/i18n";

const Studio = lazy(() => import("./pages/Studio"));
const Terminal = lazy(() => import("./pages/Terminal"));

export default function App() {
  const { t, lang, setLang } = useLang();
  const [route, setRoute] = useState(location.hash || "#/studio");
  useEffect(() => {
    const f = () => setRoute(location.hash || "#/studio");
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  const tab = (h: string, label: string) => (
    <a href={h} className={`px-3 py-1.5 rounded-md text-sm ${route.startsWith(h)
      ? "bg-loom-blue/20 text-loom-blue" : "text-slate-400 hover:text-slate-200"}`}>{label}</a>
  );
  return (
    <div className="h-screen flex flex-col">
      <header className="flex items-center gap-4 px-4 py-2 border-b border-edge bg-panel/70">
        <div className="font-semibold tracking-wide text-loom-gold">AlphaLoom</div>
        <span className="hud-label">the graph is the agent</span>
        <nav className="flex gap-1 ml-6">{tab("#/studio", t("studio"))}{tab("#/terminal", t("terminal"))}</nav>
        <button className="ml-auto text-xs text-slate-400 hover:text-slate-200"
                onClick={() => setLang(lang === "zh" ? "en" : "zh")}>{lang === "zh" ? "EN" : "中"}</button>
      </header>
      <main className="flex-1 min-h-0">
        <Suspense fallback={<div className="p-8 text-slate-500">loading…</div>}>
          {route.startsWith("#/terminal") ? <Terminal /> : <Studio />}
        </Suspense>
      </main>
    </div>
  );
}
```

（本任务先建两个占位页让构建通过：`src/pages/Studio.tsx` 与 `src/pages/Terminal.tsx` 各导出 `export default function X(){return <div className="p-8">…</div>}`，Task 6/7 替换。）

- [ ] **Step 4: 映射 vitest（逐字）**

```ts
// frontend/src/lib/__tests__/loom.test.ts
import { describe, expect, it } from "vitest";
import { flowToLoom, loomToFlow, nextNodeId, type Loom, type NodeDef } from "../loom";

const defs: Record<string, NodeDef> = {
  ema: { type: "ema", category: "indicator", inputs: { candle: "candle" },
         outputs: { value: "series" }, params: { period: "int" }, cost: {} },
  candle_feed: { type: "candle_feed", category: "data", inputs: {},
                 outputs: { out: "candle" }, params: {}, cost: {} },
};

const loom: Loom = {
  id: "t", name: "t",
  nodes: [{ id: "feed", type: "candle_feed", params: {} },
          { id: "ema1", type: "ema", params: { period: 12 } }],
  edges: [{ from: "feed.out", to: "ema1.candle" },
          { from: "ema1.value", to: "feed.out" as never, feedback: true } as never],
  meta: {},
};

describe("loom mapping", () => {
  it("roundtrips nodes, edges, feedback and positions", () => {
    const { nodes, edges } = loomToFlow(loom, defs);
    expect(nodes).toHaveLength(2);
    expect(edges[0].sourceHandle).toBe("out:out");
    expect(edges[0].targetHandle).toBe("in:candle");
    expect(edges[1].data.feedback).toBe(true);
    nodes[0].position = { x: 123.4, y: 56.6 };
    const back = flowToLoom(nodes, edges, loom);
    expect(back.nodes.map(n => n.id)).toEqual(["feed", "ema1"]);
    expect(back.edges[0]).toEqual({ from: "feed.out", to: "ema1.candle" });
    expect(back.edges[1].feedback).toBe(true);
    expect((back.meta.positions as Record<string, unknown>).feed).toEqual({ x: 123, y: 57 });
  });
  it("nextNodeId avoids collisions", () => {
    expect(nextNodeId(new Set(["ema_1"]), "ema")).toBe("ema_2");
    expect(nextNodeId(new Set(), "ema")).toBe("ema_1");
  });
});
```

- [ ] **Step 5: 验证与提交**

```bash
cd frontend && npx vitest run     # 2 passed
npm run build                     # 零错误产出 dist/
cd .. && git add -A && git commit -m "feat(frontend): scaffold - vite/react/ts/tailwind, api/ws/i18n/loom libs, app shell"
```

（`frontend/node_modules/`、`frontend/dist/` 加入 .gitignore——本步骤内完成。）

---

### Task 6: Blueprint Studio 画布

**Files:**
- Create: `frontend/src/components/NodeCard.tsx`, `frontend/src/components/ErrorPanel.tsx`, `frontend/src/components/CertPanel.tsx`, `frontend/src/components/PausedInspector.tsx`
- Replace: `frontend/src/pages/Studio.tsx`

**行为清单（审查走查依据，逐条验证）**：
1. 载入时并发拉 `/api/nodes` 与 `/api/blueprints`；左侧调色板按 category 分组（色板=锁定 CATEGORY_COLORS），点击项在画布中央偏移处新增节点（id 用 `nextNodeId`）。
2. 画布节点=NodeCard：头部分类色条+类型名+节点 id；左列输入 Handle（`in:<port>`）右列输出 Handle（`out:<port>`），引脚圆点色=PIN_COLORS；params 以只读 JSON 微字体显示（双击节点弹 prompt 编辑 JSON——D2 极简参数编辑，D3 换表单）；右上角断点圆点（点击切换，红色=已设）。
3. 连线即触发 500ms 防抖编译（`compileLoom(flowToLoom(...))`）；编译失败：ErrorPanel 列出 code/message/fix_hint，涉事节点加 `node-blocked` 红光；编译通过：CertPanel 显示证书四项（llm_calls_per_bar/daily_token_ceiling/worst_latency_class/deterministic_ratio 环形或文本），顶栏绿点+“编译通过”。
4. feedback 边以虚线+↺ 标记渲染（`data.feedback`），右键边切换 feedback（React Flow `onEdgeContextMenu`）。
5. 蓝图库抽屉：列出 preset/user 蓝图，点击载入（覆盖画布前 confirm）；保存按钮 POST 后刷新列表。
6. Run 按钮：编译未过则禁用；点击 `startRun({blueprint, inst:"BTC-USDT-SWAP", bar:"1m", playback_ms:15, ws_wait_ms:300, breakpoints:[已设断点]})` → `openRunSocket` 订阅：`bar` 事件把 `active` 节点集加 `node-glow`（150ms 后清除，靠 setTimeout 批处理）、顶栏显示 bar 进度与 equity；`paused` 事件弹 PausedInspector（端口值 JSON 树 + 继续/单步/停止三按钮，发送对应 cmd）；`done`→顶栏完成态+“去 Terminal 看结果”链接（#/terminal?run=<id>）；`error`→红条。
7. 断点存在时 Run 自动附带；无断点时 Inspector 永不出现。
8. 语言切换即时生效（useLang）。

- [ ] **Step 1: NodeCard（逐字）**

```tsx
// frontend/src/components/NodeCard.tsx
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { CATEGORY_COLORS, PIN_COLORS, type NodeDef, type PinType } from "../lib/loom";

export interface NodeCardData {
  def: NodeDef; params: Record<string, unknown>;
  active?: boolean; blocked?: boolean; breakpoint?: boolean;
  onToggleBreakpoint?: (id: string) => void;
}

function Pin({ side, port, pin, idx }: { side: "in" | "out"; port: string; pin: PinType; idx: number }) {
  const y = 34 + idx * 18;
  return (
    <Handle id={`${side}:${port}`} type={side === "in" ? "target" : "source"}
            position={side === "in" ? Position.Left : Position.Right}
            style={{ top: y, background: PIN_COLORS[pin], width: 9, height: 9,
                     border: pin === "risk_stamped_signal" ? "2px solid #f59e0b" : "1px solid #0b1020" }}>
      <span className={`absolute text-[9px] text-slate-400 ${side === "in" ? "left-3" : "right-3"}`}
            style={{ top: -6, whiteSpace: "nowrap" }}>{port}</span>
    </Handle>
  );
}

export default function NodeCard({ id, data }: NodeProps) {
  const d = data as unknown as NodeCardData;
  const color = CATEGORY_COLORS[d.def.category] ?? "#64748b";
  const rows = Math.max(Object.keys(d.def.inputs).length, Object.keys(d.def.outputs).length);
  return (
    <div className={`panel min-w-[170px] pb-2 ${d.active ? "node-glow" : ""} ${d.blocked ? "node-blocked" : ""}`}
         style={{ minHeight: 40 + rows * 18 }}>
      <div className="flex items-center gap-2 px-2 py-1 rounded-t-lg"
           style={{ background: `${color}22`, borderBottom: `1px solid ${color}55` }}>
        <span className="w-2 h-2 rounded-full" style={{ background: color }} />
        <span className="text-xs font-medium">{d.def.type}</span>
        <span className="text-[9px] text-slate-500 ml-auto">{id}</span>
        <button title="breakpoint" onClick={(e) => { e.stopPropagation(); d.onToggleBreakpoint?.(id); }}
                className={`w-3 h-3 rounded-full border ${d.breakpoint ? "bg-loom-red border-loom-red" : "border-slate-600"}`} />
      </div>
      {Object.entries(d.def.inputs).map(([p, t], i) => <Pin key={p} side="in" port={p} pin={t} idx={i} />)}
      {Object.entries(d.def.outputs).map(([p, t], i) => <Pin key={p} side="out" port={p} pin={t} idx={i} />)}
      <div className="px-2 pt-1 text-[9px] text-slate-500 font-mono truncate" style={{ marginTop: rows * 18 }}>
        {JSON.stringify(d.params)}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: ErrorPanel / CertPanel / PausedInspector（逐字）**

```tsx
// frontend/src/components/ErrorPanel.tsx
export interface CompileError {
  code: string; message: string; node_id?: string | null; port?: string | null; fix_hint?: string | null;
}
export default function ErrorPanel({ errors, onFocus }: {
  errors: CompileError[]; onFocus: (nodeId: string) => void }) {
  if (!errors.length) return null;
  return (
    <div className="panel p-3 space-y-2 max-h-64 overflow-auto">
      <div className="hud-label text-loom-red">compile errors</div>
      {errors.map((e, i) => (
        <div key={i} className="text-xs border-l-2 border-loom-red pl-2 cursor-pointer"
             onClick={() => e.node_id && onFocus(e.node_id)}>
          <div className="font-mono text-loom-red">{e.code}</div>
          <div className="text-slate-300">{e.message}</div>
          {e.fix_hint && <div className="text-slate-500 italic">💡 {e.fix_hint}</div>}
        </div>
      ))}
    </div>
  );
}
```

```tsx
// frontend/src/components/CertPanel.tsx
import { useLang } from "../lib/i18n";
export default function CertPanel({ cert }: { cert: Record<string, unknown> | null }) {
  const { t } = useLang();
  if (!cert) return null;
  const item = (k: string, v: unknown, accent = "") => (
    <div className="flex justify-between text-xs py-0.5">
      <span className="text-slate-500">{k}</span>
      <span className={`font-mono ${accent}`}>{String(v)}</span>
    </div>
  );
  const det = Number(cert.deterministic_ratio ?? 1);
  return (
    <div className="panel p-3">
      <div className="hud-label text-loom-gold mb-1">{t("cost")}</div>
      {item("llm calls/bar", cert.llm_calls_per_bar)}
      {item("daily token ceiling", Number(cert.daily_token_ceiling).toLocaleString())}
      {item("worst latency", cert.worst_latency_class,
            cert.worst_latency_class === "llm" ? "text-loom-amber" : "text-loom-green")}
      {item("deterministic", `${(det * 100).toFixed(1)}%`, det === 1 ? "text-loom-green" : "text-loom-amber")}
    </div>
  );
}
```

```tsx
// frontend/src/components/PausedInspector.tsx
import { useLang } from "../lib/i18n";
export default function PausedInspector({ ev, onCmd }: {
  ev: { node_id: string; ts: number; inputs: Record<string, unknown> } | null;
  onCmd: (c: "resume" | "step" | "stop") => void }) {
  const { t } = useLang();
  if (!ev) return null;
  return (
    <div className="panel p-3 border-loom-amber/60 space-y-2">
      <div className="hud-label text-loom-amber">{t("paused")} · {ev.node_id} @ {ev.ts}</div>
      <pre className="text-[10px] font-mono text-slate-300 max-h-48 overflow-auto whitespace-pre-wrap">
        {JSON.stringify(ev.inputs, null, 2)}
      </pre>
      <div className="flex gap-2">
        <button className="px-2 py-1 text-xs rounded bg-loom-green/20 text-loom-green"
                onClick={() => onCmd("resume")}>{t("resume")}</button>
        <button className="px-2 py-1 text-xs rounded bg-loom-blue/20 text-loom-blue"
                onClick={() => onCmd("step")}>{t("step")}</button>
        <button className="px-2 py-1 text-xs rounded bg-loom-red/20 text-loom-red"
                onClick={() => onCmd("stop")}>{t("stop")}</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Studio 页（逐字；~200 行，实现者可等价重写内部 hooks，行为清单与所用 lib 契约不变）**

```tsx
// frontend/src/pages/Studio.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Controls, addEdge, useEdgesState, useNodesState,
         type Connection, type Edge, type Node } from "@xyflow/react";
import NodeCard from "../components/NodeCard";
import ErrorPanel, { type CompileError } from "../components/ErrorPanel";
import CertPanel from "../components/CertPanel";
import PausedInspector from "../components/PausedInspector";
import { compileLoom, getBlueprint, getNodes, listBlueprints, saveBlueprint, startRun } from "../lib/api";
import { openRunSocket } from "../lib/ws";
import { CATEGORY_COLORS, flowToLoom, loomToFlow, nextNodeId,
         type Loom, type NodeDef } from "../lib/loom";
import { useLang } from "../lib/i18n";

const EMPTY: Loom = { id: "untitled", name: "Untitled", nodes: [], edges: [], meta: {} };
const nodeTypes = { loomNode: NodeCard };

export default function Studio() {
  const { t } = useLang();
  const [defs, setDefs] = useState<Record<string, NodeDef>>({});
  const [gallery, setGallery] = useState<{ id: string; name: string; source: string }[]>([]);
  const [base, setBase] = useState<Loom>(EMPTY);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [errors, setErrors] = useState<CompileError[]>([]);
  const [cert, setCert] = useState<Record<string, unknown> | null>(null);
  const [bps, setBps] = useState<Set<string>>(new Set());
  const [runState, setRunState] = useState<{ id?: string; bar?: number; equity?: number;
    status?: string; paused?: any }>({});
  const sock = useRef<ReturnType<typeof openRunSocket> | null>(null);
  const glowTimer = useRef<number>();

  useEffect(() => {
    getNodes().then((ns) => setDefs(Object.fromEntries(ns.map((n) => [n.type, n]))));
    listBlueprints().then(setGallery);
    return () => sock.current?.close();
  }, []);

  const currentLoom = useCallback(() =>
    flowToLoom(nodes as never, edges as never, base), [nodes, edges, base]);

  // 500ms 防抖编译
  useEffect(() => {
    if (!nodes.length) { setErrors([]); setCert(null); return; }
    const h = setTimeout(() => {
      compileLoom(currentLoom()).then((r) => {
        setErrors(r.errors);
        setCert(r.ok ? r.certificate : null);
        const bad = new Set(r.errors.map((e) => e.node_id).filter(Boolean));
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, blocked: bad.has(n.id) } })));
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(h);
  }, [nodes.length, edges, currentLoom, setNodes]);

  const load = async (id: string) => {
    if (nodes.length && !confirm("覆盖当前画布?")) return;
    const loom = await getBlueprint(id);
    setBase(loom);
    const f = loomToFlow(loom, defs);
    setNodes(f.nodes.map((n) => ({ ...n, data: { ...n.data,
      breakpoint: false, onToggleBreakpoint: toggleBp } })) as never);
    setEdges(f.edges.map((e) => ({ ...e, animated: e.data.feedback,
      style: e.data.feedback ? { strokeDasharray: "6 3" } : undefined })) as never);
    setBps(new Set());
  };

  const toggleBp = useCallback((id: string) => {
    setBps((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }, []);
  useEffect(() => {
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, breakpoint: bps.has(n.id),
      onToggleBreakpoint: toggleBp } })));
  }, [bps, setNodes, toggleBp]);

  const addNode = (type: string) => {
    const def = defs[type];
    const id = nextNodeId(new Set(nodes.map((n) => n.id)), type);
    setNodes((ns) => [...ns, { id, type: "loomNode",
      position: { x: 120 + Math.random() * 240, y: 120 + Math.random() * 160 },
      data: { def, params: {}, onToggleBreakpoint: toggleBp } } as never]);
  };

  const onConnect = useCallback((c: Connection) =>
    setEdges((es) => addEdge({ ...c, data: { feedback: false } }, es)), [setEdges]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    setEdges((es) => es.map((x) => x.id === edge.id
      ? { ...x, data: { feedback: !(x.data as any)?.feedback },
          animated: !(x.data as any)?.feedback,
          style: !(x.data as any)?.feedback ? { strokeDasharray: "6 3" } : undefined }
      : x));
  }, [setEdges]);

  const onNodeDoubleClick = useCallback((_: unknown, node: Node) => {
    const txt = prompt("params JSON", JSON.stringify((node.data as any).params ?? {}));
    if (txt == null) return;
    try {
      const p = JSON.parse(txt);
      setNodes((ns) => ns.map((n) => n.id === node.id
        ? { ...n, data: { ...n.data, params: p } } : n));
    } catch { alert("bad JSON"); }
  }, [setNodes]);

  const run = async () => {
    sock.current?.close();               // 关旧连接，防重复 Run 泄漏 WS（T6 审查 Important）
    const { run_id } = await startRun({ blueprint: currentLoom(), inst: "BTC-USDT-SWAP",
      bar: "1m", playback_ms: 15, ws_wait_ms: 300, breakpoints: [...bps] });
    setRunState({ id: run_id, status: "running" });
    sock.current = openRunSocket(run_id, (ev) => {
      if (ev.type === "bar") {
        setRunState((s) => ({ ...s, bar: ev.idx, equity: ev.equity }));
        setNodes((ns) => ns.map((n) => ({ ...n,
          data: { ...n.data, active: ev.active?.includes(n.id) } })));
        window.clearTimeout(glowTimer.current);
        glowTimer.current = window.setTimeout(() => setNodes((ns) =>
          ns.map((n) => ({ ...n, data: { ...n.data, active: false } }))), 150);
      } else if (ev.type === "paused") {
        setRunState((s) => ({ ...s, status: "paused", paused: ev }));
      } else if (ev.type === "done") {
        setRunState((s) => ({ ...s, status: "done", paused: null }));
      } else if (ev.type === "error") {
        setRunState((s) => ({ ...s, status: `error: ${ev.message}`, paused: null }));
      } else if (ev.type === "status") {
        setRunState((s) => ({ ...s, status: ev.status }));
      }
    });
  };

  const palette = useMemo(() => {
    const groups: Record<string, NodeDef[]> = {};
    Object.values(defs).forEach((d) => (groups[d.category] ??= []).push(d));
    return Object.entries(groups).sort();
  }, [defs]);

  return (
    <div className="h-full grid grid-cols-[200px_1fr_280px] gap-2 p-2">
      <aside className="panel p-2 overflow-auto space-y-3">
        {palette.map(([cat, list]) => (
          <div key={cat}>
            <div className="hud-label mb-1" style={{ color: CATEGORY_COLORS[cat] }}>{cat}</div>
            {list.map((d) => (
              <button key={d.type} onClick={() => addNode(d.type)}
                      className="block w-full text-left text-xs px-2 py-1 rounded hover:bg-edge/60">
                {d.type}
              </button>
            ))}
          </div>
        ))}
        <div className="border-t border-edge pt-2">
          <div className="hud-label mb-1">{t("gallery")}</div>
          {gallery.map((g) => (
            <button key={g.id} onClick={() => load(g.id)}
                    className="block w-full text-left text-xs px-2 py-1 rounded hover:bg-edge/60">
              {g.name} <span className="text-slate-600">({g.source})</span>
            </button>
          ))}
        </div>
      </aside>
      <section className="panel relative">
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
                   onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                   onConnect={onConnect} onEdgeContextMenu={onEdgeContextMenu}
                   onNodeDoubleClick={onNodeDoubleClick} fitView proOptions={{ hideAttribution: true }}>
          <Background color="#1e2a44" gap={24} />
          <Controls />
        </ReactFlow>
        <div className="absolute top-2 left-2 right-2 flex items-center gap-3 pointer-events-none">
          <span className={`w-2 h-2 rounded-full ${errors.length ? "bg-loom-red" : "bg-loom-green"}`} />
          <span className="text-xs text-slate-400">
            {errors.length ? t("compileFail") : t("compileOk")}
          </span>
          {runState.id && (
            <span className="text-xs text-loom-blue font-mono">
              bar {runState.bar ?? "-"} · eq {runState.equity?.toFixed(2) ?? "-"} · {runState.status}
              {runState.status === "done" && (
                <a className="pointer-events-auto underline ml-2"
                   href={`#/terminal?run=${runState.id}`}>→ {t("terminal")}</a>)}
            </span>)}
          <button onClick={run} disabled={!!errors.length || !nodes.length}
                  className="pointer-events-auto ml-auto px-3 py-1 text-xs rounded bg-loom-gold/20 text-loom-gold disabled:opacity-30">
            ▶ {t("run")}
          </button>
        </div>
      </section>
      <aside className="space-y-2 overflow-auto">
        <CertPanel cert={cert} />
        <ErrorPanel errors={errors} onFocus={() => {}} />
        <PausedInspector ev={runState.paused ?? null}
                         onCmd={(c) => sock.current?.send(c)} />
        <div className="panel p-2 text-[10px] text-slate-500">{t("breakpointHint")}</div>
        <button onClick={() => saveBlueprint(currentLoom()).then(() => listBlueprints().then(setGallery))}
                className="w-full px-2 py-1 text-xs rounded bg-loom-blue/20 text-loom-blue">
          {t("save")}
        </button>
      </aside>
    </div>
  );
}
```

- [ ] **Step 4: 构建与提交**

```bash
cd frontend && npx vitest run && npm run build
cd .. && git add -A && git commit -m "feat(studio): react-flow canvas - palette, typed pins, compile feedback, cert panel, run glow, breakpoint inspector"
```

---

### Task 7: Terminal 页

**Files:**
- Create: `frontend/src/components/CandleChart.tsx`, `frontend/src/components/EquityChart.tsx`, `frontend/src/components/SummaryCards.tsx`, `frontend/src/components/TradesTable.tsx`
- Replace: `frontend/src/pages/Terminal.tsx`

**行为清单**：
1. 顶部 RunPicker（`listRuns`，倒序，显示 run_id 尾 6 位/blueprint_id/status 徽章色：completed 绿 / failed 红 / halted 琥珀 / running 蓝）；URL hash `#/terminal?run=<id>` 预选。
2. 选中 completed/halted run：`getRun` 取 report → K 线图（`getCandles(inst, bar, 5000)` + fills markers：buy ↑绿 / sell ↓红，eod_close 空心）、权益面积图（report.equity_curve）、SummaryCards（net_pnl/return_pct/max_drawdown/num_trades/win_rate/profit_factor，null 显示 "—"）、TradesTable（fills 逐行：ts/side/qty/price/fee/tag）。
3. failed run：显示 error 文本（红面板）。
4. 无 run：空态提示 t("noRuns")。

- [ ] **Step 1: 图表组件（逐字）**

```tsx
// frontend/src/components/CandleChart.tsx
import { createChart, type IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface Candle { ts: number; open: number; high: number; low: number; close: number; volume: number; }
export interface Fill { ts: number; side: string; qty: number; price: number; fee: number; tag: string; }

export default function CandleChart({ candles, fills }: { candles: Candle[]; fills: Fill[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  useEffect(() => {
    if (!ref.current) return;
    chart.current = createChart(ref.current, {
      height: 320, layout: { background: { color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#101a33" }, horzLines: { color: "#101a33" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    const series = chart.current.addCandlestickSeries({
      upColor: "#34d399", downColor: "#ef4444", borderVisible: false,
      wickUpColor: "#34d399", wickDownColor: "#ef4444",
    });
    series.setData(candles.map((c) => ({ time: (c.ts / 1000) as never,
      open: c.open, high: c.high, low: c.low, close: c.close })));
    series.setMarkers(fills.map((f) => ({
      time: (f.ts / 1000) as never,
      position: f.side === "buy" ? "belowBar" : "aboveBar",
      color: f.tag === "eod_close" ? "#94a3b8" : f.side === "buy" ? "#34d399" : "#ef4444",
      shape: f.side === "buy" ? "arrowUp" : "arrowDown",
      text: f.tag || f.side,
    })));
    chart.current.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.current?.applyOptions({ width: ref.current!.clientWidth }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.current?.remove(); };
  }, [candles, fills]);
  return <div ref={ref} className="w-full" />;
}
```

```tsx
// frontend/src/components/EquityChart.tsx
import { createChart, type IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

export default function EquityChart({ curve }: { curve: [number, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  useEffect(() => {
    if (!ref.current) return;
    chart.current = createChart(ref.current, {
      height: 160, layout: { background: { color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { visible: false }, horzLines: { color: "#101a33" } },
      timeScale: { timeVisible: true },
    });
    const s = chart.current.addAreaSeries({
      lineColor: "#f59e0b", topColor: "rgba(245,158,11,0.25)", bottomColor: "rgba(245,158,11,0.02)",
    });
    s.setData(curve.map(([ts, eq]) => ({ time: (ts / 1000) as never, value: eq })));
    chart.current.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.current?.applyOptions({ width: ref.current!.clientWidth }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.current?.remove(); };
  }, [curve]);
  return <div ref={ref} className="w-full" />;
}
```

```tsx
// frontend/src/components/SummaryCards.tsx
const FIELDS: [string, string][] = [
  ["net_pnl", "Net PnL"], ["return_pct", "Return %"], ["max_drawdown", "Max DD"],
  ["num_trades", "Trades"], ["win_rate", "Win rate"], ["profit_factor", "Profit factor"],
];
export default function SummaryCards({ summary }: { summary: Record<string, unknown> }) {
  return (
    <div className="grid grid-cols-3 lg:grid-cols-6 gap-2">
      {FIELDS.map(([k, label]) => {
        const v = summary[k];
        const num = typeof v === "number" ? v : null;
        const accent = k === "net_pnl" && num != null
          ? num >= 0 ? "text-loom-green" : "text-loom-red" : "text-slate-200";
        return (
          <div key={k} className="panel p-2">
            <div className="hud-label">{label}</div>
            <div className={`font-mono text-sm ${accent}`}>
              {v == null ? "—" : typeof v === "number" ? +v.toFixed(4) : String(v)}
            </div>
          </div>);
      })}
    </div>
  );
}
```

```tsx
// frontend/src/components/TradesTable.tsx
import type { Fill } from "./CandleChart";
export default function TradesTable({ fills }: { fills: Fill[] }) {
  return (
    <div className="panel overflow-auto max-h-64">
      <table className="w-full text-xs">
        <thead className="text-slate-500 sticky top-0 bg-panel">
          <tr>{["time", "side", "qty", "price", "fee", "tag"].map((h) =>
            <th key={h} className="text-left px-2 py-1 font-normal">{h}</th>)}</tr>
        </thead>
        <tbody className="font-mono">
          {fills.map((f, i) => (
            <tr key={i} className="border-t border-edge/50">
              <td className="px-2 py-0.5 text-slate-400">{new Date(f.ts).toISOString().slice(0, 16)}</td>
              <td className={`px-2 ${f.side === "buy" ? "text-loom-green" : "text-loom-red"}`}>{f.side}</td>
              <td className="px-2">{+f.qty.toFixed(6)}</td>
              <td className="px-2">{+f.price.toFixed(4)}</td>
              <td className="px-2 text-slate-500">{+f.fee.toFixed(4)}</td>
              <td className="px-2 text-slate-500">{f.tag}</td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Terminal 页（逐字）**

```tsx
// frontend/src/pages/Terminal.tsx
import { useEffect, useMemo, useState } from "react";
import CandleChart, { type Candle, type Fill } from "../components/CandleChart";
import EquityChart from "../components/EquityChart";
import SummaryCards from "../components/SummaryCards";
import TradesTable from "../components/TradesTable";
import { getCandles, getRun, listRuns } from "../lib/api";
import { useLang } from "../lib/i18n";

const BADGE: Record<string, string> = {
  completed: "bg-loom-green/20 text-loom-green", failed: "bg-loom-red/20 text-loom-red",
  halted: "bg-loom-amber/20 text-loom-amber", running: "bg-loom-blue/20 text-loom-blue",
};

export default function Terminal() {
  const { t } = useLang();
  const [runs, setRuns] = useState<Record<string, any>[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [run, setRun] = useState<Record<string, any> | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);

  useEffect(() => {
    listRuns().then((rs) => {
      setRuns(rs);
      const fromHash = new URLSearchParams(location.hash.split("?")[1] ?? "").get("run");
      setSel(fromHash ?? rs[0]?.run_id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!sel) return;
    getRun(sel).then((r) => {
      setRun(r);
      const p = r.params ?? {};
      if (r.report) getCandles(p.inst ?? "BTC-USDT-SWAP", p.bar ?? "1m").then(setCandles);
    });
  }, [sel]);

  const fills: Fill[] = useMemo(() => run?.report?.fills ?? [], [run]);
  const curve: [number, number][] = useMemo(() => run?.report?.equity_curve ?? [], [run]);

  if (!runs.length) return <div className="p-10 text-slate-500">{t("noRuns")}</div>;
  return (
    <div className="h-full overflow-auto p-3 space-y-3">
      <div className="flex gap-2 flex-wrap">
        {runs.map((r) => (
          <button key={r.run_id} onClick={() => setSel(r.run_id)}
                  className={`px-2 py-1 rounded text-xs font-mono border ${sel === r.run_id
                    ? "border-loom-gold text-loom-gold" : "border-edge text-slate-400"}`}>
            …{r.run_id.slice(-6)} · {r.blueprint_id}
            <span className={`ml-2 px-1.5 rounded ${BADGE[r.status] ?? ""}`}>{r.status}</span>
          </button>))}
      </div>
      {run?.status === "failed" && (
        <div className="panel p-3 border-loom-red/60 text-xs text-loom-red font-mono">
          {String(run.error)}
        </div>)}
      {run?.report && (
        <>
          <SummaryCards summary={run.report.summary ?? {}} />
          <div className="panel p-2"><div className="hud-label mb-1">market · fills</div>
            <CandleChart candles={candles} fills={fills} /></div>
          <div className="panel p-2"><div className="hud-label mb-1">{t("equity")}</div>
            <EquityChart curve={curve} /></div>
          <div><div className="hud-label mb-1 px-1">{t("trades")} ({fills.length})</div>
            <TradesTable fills={fills} /></div>
        </>)}
    </div>
  );
}
```

- [ ] **Step 3: 构建与提交**

```bash
cd frontend && npx vitest run && npm run build
cd .. && git add -A && git commit -m "feat(terminal): runs picker, candle+fills chart, equity curve, summary cards, trades table"
```

---

### Task 8: 集成走查 + 一键脚本 + d2-complete

**Files:**
- Create: `backend/alphaloom/serve.py`, `dev.bat`, `demo.bat`, `.claude/launch.json`
- Test: `backend/tests/test_serve.py`

- [ ] **Step 1: serve 入口（逐字）+ 测试**

```python
# backend/alphaloom/serve.py
"""单进程入口：uvicorn alphaloom.serve:app --port 8000（demo.bat 用）。"""
from __future__ import annotations
import sys
from pathlib import Path
from alphaloom.api.app import create_app

REPO = Path(__file__).resolve().parents[2]

def create_default_app():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = REPO / "data"
    runs_dir = REPO / "runs"
    runs_dir.mkdir(exist_ok=True)
    return create_app(db_path=data / "demo.sqlite", runs_db=data / "runs.sqlite",
                      record_dir=runs_dir, blueprints_dir=REPO / "blueprints",
                      user_blueprints_dir=REPO / "blueprints" / "user",
                      frontend_dist=REPO / "frontend" / "dist")

app = create_default_app()
```

```python
# backend/tests/test_serve.py
from alphaloom.serve import create_default_app

def test_default_app_builds():
    app = create_default_app()
    paths = {r.path for r in app.routes}
    assert "/api/nodes" in paths and "/ws/runs/{run_id}" in paths
```

`.gitignore` 追加：`data/*.sqlite`、`runs/`（已有则跳过）、`blueprints/user/`。

- [ ] **Step 2: 脚本（逐字）**

```bat
:: dev.bat —— 双窗口热更新
@echo off
cd /d %~dp0
backend\.venv\Scripts\python scripts\ensure_demo_db.py
start "alphaloom-api" cmd /k backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --reload --app-dir backend
start "alphaloom-web" cmd /k "cd frontend && npm run dev"
echo Studio: http://localhost:5173  API: http://localhost:8000
```

```bat
:: demo.bat —— 离线单进程全站
@echo off
cd /d %~dp0
backend\.venv\Scripts\python scripts\ensure_demo_db.py
cd frontend && call npm run build && cd ..
backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
```

```json
// .claude/launch.json
{
  "version": "0.0.1",
  "configurations": [
    { "name": "alphaloom-api", "runtimeExecutable": "backend/.venv/Scripts/python",
      "runtimeArgs": ["-m", "uvicorn", "alphaloom.serve:app", "--port", "8000", "--app-dir", "backend"],
      "port": 8000 },
    { "name": "alphaloom-web", "runtimeExecutable": "npm",
      "runtimeArgs": ["run", "dev", "--prefix", "frontend"], "port": 5173 }
  ]
}
```

- [ ] **Step 3: 全站走查（审查者与实现者都要跑）**

1. `backend/.venv/Scripts/python scripts/ensure_demo_db.py` → demo 库就绪
2. 后端全量 `pytest -q` → 91 passed（90 + serve 1）
3. 前端 `npx vitest run && npm run build` → 绿
4. 用 preview 工具（launch.json 双服务）或手动走查清单：
   - Studio 载入 → 面板见 10 节点分组 → gallery 载入 ema_cross → 编译绿 + 证书 deterministic 100%
   - 删除 risk→exec 边，直连 cross→exec → 500ms 内 ErrorPanel 出现 TYPE_MISMATCH + RiskGate hint，exec 节点红光
   - 撤销恢复 → 在 risk 节点设断点 → Run → 节点流光推进 → Inspector 弹出（inputs 含 signal）→ 单步一次 → 继续（清断点后 resume 到完成）→ "→ Terminal" 链接
   - Terminal：run 徽章 completed、六卡片无 "Infinity"、K 线含成交箭头、权益曲线、成交表含 eod_close
   - 语言切换 zh/en 即时生效；`#/terminal?run=<id>` 直达
5. 截图 `docs/screenshots/d2-{studio,terminal}.png` **延至 D4**（与 README/banner 一批做；D2 控制器 live 走查视觉证据已确认三页正常渲染，仅文件产物延后，避免为两张图装 300MB 浏览器工具链）

- [ ] **Step 4: Commit + tag**

```bash
git add -A && git commit -m "feat(app): serve entry, dev/demo scripts, launch config, walkthrough screenshots"
git tag d2-complete
```

---

### Task 9: 实况走查缺陷修复（控制器 T8 走查发现）

控制器亲自 preview 走查发现两个真实缺陷，单元测试（TestClient）+ 静态审查全漏——正是集成断层。

**缺陷 A（Critical）：WS 生产环境全死。** `pyproject.toml` 声明 `uvicorn>=0.30`（裸版无 WebSocket 库），uvicorn 服务下所有 `/ws/runs/{id}` → 404 + "No supported WebSocket library detected"。90 个后端测试全绿是因为 Starlette TestClient 用内存 WS 传输，不经 uvicorn。旗舰"实时流光+断点"在真实 demo.bat/dev.bat 下完全不工作。

**缺陷 B（Important）：Studio 防抖编译无限循环。** 编译 effect 内 `setNodes((ns)=>ns.map(...))` 每次产生新 nodes 引用 → `currentLoom` useCallback 身份变 → effect 重触发 → 每 500ms 无限编译（走查实测 4 次/2 秒，永不停）。运行时的 active 流光 setNodes 也会触发。持续锤服务器。

**Files:**
- Modify: `backend/pyproject.toml`（`uvicorn>=0.30` → `uvicorn[standard]>=0.30`）
- Modify: `frontend/src/pages/Studio.tsx`（编译 effect 依赖改结构签名）
- Test: `backend/tests/test_ws_lib.py`（新建，锁 websockets 依赖可导入）

**缺陷 A 修复：** pyproject dependencies 的 `"uvicorn>=0.30"` 改 `"uvicorn[standard]>=0.30"`；`backend/.venv` 已手动装 websockets 16.0（控制器走查时装的），实现者 `pip install -e .` 确认 `uvicorn[standard]` 拉齐依赖。新增测试：

```python
# backend/tests/test_ws_lib.py
def test_websocket_library_available():
    """uvicorn[standard] 必须带 WS 库，否则 /ws 全 404（TestClient 测不出，故显式锁依赖）。"""
    import importlib
    assert importlib.util.find_spec("websockets") is not None,         "websockets missing: uvicorn[standard] not installed, WS will 404 under uvicorn"
```

**缺陷 B 修复：** Studio.tsx 的编译 effect —— 提取一个不含瞬态 data（blocked/active）的结构签名，用它做依赖，使流光/blocked 的 setNodes 不再触发重编译。在 Studio 组件内加：

```ts
  const structuralKey = useMemo(
    () => JSON.stringify({
      n: nodes.map((n) => ({ id: n.id, t: (n.data as { def: { type: string } }).def.type,
                             p: (n.data as { params: unknown }).params,
                             x: Math.round(n.position.x), y: Math.round(n.position.y) })),
      e: edges.map((e) => ({ s: e.source, sh: e.sourceHandle, t: e.target, th: e.targetHandle,
                             f: !!(e.data as { feedback?: boolean } | undefined)?.feedback })),
    }),
    [nodes, edges],
  );
```

编译 effect 的依赖数组从 `[nodes.length, edges, currentLoom, setNodes]` 改为 **`[structuralKey, setNodes]`**（控制器复走查修正：必须**移除 currentLoom**——它身份每次 setNodes 都 churn，React 只要任一依赖变就重跑，光加 structuralKey 挡不住；effect 体内 `currentLoom()` 走闭包仍拿最新图，加 eslint-disable exhaustive-deps）——**注意 effect 体内仍调 currentLoom()，但 currentLoom 的身份变化不再单独触发（structuralKey 才是闸门）**；因 structuralKey 只在真实结构变时才变，blocked/active 的 setNodes 不改结构 → 不重编译。验证：`npm run build` + 走查确认编译只在连线/改参/拖动时触发一次，静止时零编译。

**验收：** 后端全量 `pytest -q` → 92 passed（91 + ws_lib 1）；前端 build 绿 + vitest 2 passed；控制器复走查确认（静止零编译 + WS 实时流）。

**Commit：** `fix(app): uvicorn[standard] for production WS, Studio compile-loop via structural key`

---

## D2 Carryover（D3 输入）

1. Studio 参数编辑是 prompt-JSON 极简版——D3 换 schema 驱动表单（registry params 已含类型名）。
2. 子图节点画布内是普通节点（展开由编译器负责）；子图折叠/导航 UI 排 D4 打磨（spec 3.1 承诺过，届时用 React Flow group node）。
3. WS event_log 内存上限 20k 条/run，重启即失——录制 sqlite 才是权威；D3 回放模式直接读录制。
4. `stop` 命令是"放行到结束"非硬中断（引擎崩溃契约禁止半途复用）；D3 加速回放若需真取消，用"数据源迭代器提前耗尽"实现（在 source 层斩）。
5. run 线程无并发上限——D3 前在 RunService.start 加简单信号量（如 4）防手滑压爆。
6. Terminal K 线图全量 5000 根 + fills 全量 setMarkers，万根级会卡——D4 打磨时按可视窗口懒载。
7. test_runs_store 的 join(timeout=30) 在慢机器上可能边缘——若 CI 出现 flake，提高 timeout 而非改语义。
9. **D2 终审移交（D3 前处理）**：①WS `done` 事件的 equity_curve/fills 未内联 sanitize（恒有限值、Terminal 走 /api/runs 整体 sanitize 出口故无害，但 WS 直连消费者需注意）；②`@app.on_event("startup")` FastAPI deprecation → D3 迁 lifespan（顺带修 T3 遗留的 RunsStore/worker 连接 finalizers）；③test_ws_lib 只锁"当前 venv websockets 可导入"，pyproject 改回裸 uvicorn 但不重建 venv 时测试仍绿——clean reinstall 才拦得住，属"依赖锁+走查"双层兜底的边界；④stop 命令是"放行到结束"非硬取消（引擎崩溃契约），UI 应加"停止中"提示，真取消 D3 在数据源迭代器层斩。
8. **D2-T4 审查移交**：①`_sink_for` 用全局单 `app.state.loop`——生产（uvicorn 单 loop）无害且已实证，但 **TestClient 每连接起独立 loop，故禁止加"并发多客户端 WS"测试**（会确定性挂死 CI）；若 D3 需要该测试，先让 `_sink_for` 按队列记 loop（`q._loop.call_soon_threadsafe`）。②event_log 20k 上限是尾丢弃——超长 run（>~14 天 1m 线）的晚连接客户端会重放不全含丢 `done`；D4 长回测前改环形缓冲或对晚连接补发终态（演示数据 BTC/ETH 各 4000 bar 远未触及）。
