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
    app.state.event_log = {}          # run_id -> list[event]（重放缓冲，20k 上限）
    app.state.loop = None

    @app.on_event("startup")
    async def _grab_loop():
        app.state.loop = asyncio.get_running_loop()

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

    # —— WS（SPA 路由之前）——
    @app.websocket("/ws/runs/{run_id}")
    async def ws_run(ws: WebSocket, run_id: str):
        await ws.accept()
        # sink 从后台线程用 loop.call_soon_threadsafe 推事件——必须指向真正服务本连接的
        # loop。TestClient 每个 websocket_connect 起独立 portal/新 loop（≠startup 的 loop），
        # 故在此捕获运行中 loop；uvicorn 生产单 loop 下等价无害。
        app.state.loop = asyncio.get_running_loop()
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
