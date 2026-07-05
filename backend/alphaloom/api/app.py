# backend/alphaloom/api/app.py
from __future__ import annotations
import asyncio
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
import alphaloom.nodes  # noqa: F401
from alphaloom.api.runs_store import RunsStore
from alphaloom.api.schemas import (CompileIn, CopilotBlueprintIn, CopilotExplainIn,
                                   CopilotOptimizeIn, CustomNodeIn, EvalAblationIn,
                                   EvalFidelityIn, EvalLeaderboardIn, EvalScorecardIn,
                                   EvolveIn, RunIn, SaveBlueprintIn)
from alphaloom.api.serialize import sanitize
from alphaloom.api.service import RunService
from alphaloom.copilot import blueprint as copilot
from alphaloom.data.source import bar_to_ms
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import dumps_loom, loads_loom
from alphaloom.nodes.registry import REGISTRY
from alphaloom.runtime.recorder import from_json
from alphaloom.sandbox.errors import SandboxError
from alphaloom.sandbox.node_sandbox import compile_node_source

_BARS = ["1m", "5m", "15m", "1H", "4H", "1D"]

def _build_llm_client(llm_db):
    """从 env 构建生产 RecordingLLMClient：LLMConfig.from_env + openai_transport
    + with_retry（429 退避），offline 跟 ALPHALOOM_OFFLINE。llm_db=None → data/llm_calls.sqlite。

    构建失败（如非 offline 且缺 .env 配置）返回 None——LLM 节点缺席的 D1/D2 蓝图照跑，
    LLM 节点在场时 ctx.llm is None 会抛清晰 RuntimeError → run failed（不崩服务）。
    """
    from alphaloom.llm.client import LLMConfig, openai_transport
    from alphaloom.llm.recording import RecordingLLMClient
    from alphaloom.llm.retry import with_retry
    try:
        cfg = LLMConfig.from_env()
    except KeyError:
        return None   # 未配置 .env 且非 offline —— 无 LLM 客户端（LLM 节点会 run failed）
    db = Path(llm_db) if llm_db is not None else Path("data") / "llm_calls.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    transport = with_retry(openai_transport(cfg))
    return RecordingLLMClient(transport, db, model=cfg.model)

def create_app(*, db_path, runs_db, record_dir, blueprints_dir, user_blueprints_dir,
               frontend_dist, llm_client=None, llm_db=None) -> FastAPI:
    store = RunsStore(runs_db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # startup：兜底抓 event loop（D2-T4：sink 从后台线程用
        # loop.call_soon_threadsafe 推事件，需要一个 running loop 句柄）。这是兜底——
        # 真正服务某 WS 连接时，ws_run handler 内会用连接期的 running loop 覆盖它
        # （TestClient 每个 websocket_connect 起独立 portal/新 loop ≠ 此 startup loop，
        # 那处覆盖是死锁修复的关键，不能删；uvicorn 生产单 loop 下二者等价无害）。
        app.state.loop = asyncio.get_running_loop()
        yield
        # shutdown：关闭 RunsStore 的 sqlite 连接（连接 finalizer，避免 ResourceWarning
        # 泄漏；D2 Carryover 9② / T3 前瞻）。
        store.close()

    app = FastAPI(title="AlphaLoom API", lifespan=lifespan)

    # I2：沙箱节点触发的 LLM 剥离逃逸拦截 → 干净 422（带解释），不是通用 500。
    # offline 下含"偷调 ctx.llm 的沙箱节点"的蓝图会走进回测、on_bar 里访问被剥夺
    # 的 ctx.llm → SandboxEscapeError。集中转 422 解释性响应（Layer 1 生效的证据，
    # 而非裸 500）。
    from alphaloom.runtime.engine import SandboxEscapeError as _SandboxEscapeError

    @app.exception_handler(_SandboxEscapeError)
    async def _sandbox_escape_handler(_request, exc):
        return JSONResponse(
            status_code=422,
            content={"error": "sandbox_escape",
                     "message": f"a sandboxed node attempted a forbidden capability: "
                                f"{exc}. Sandbox nodes are stripped of the LLM handle; "
                                "this blueprint cannot be evaluated."})

    app.state.store = store
    # LLM 注入接缝：预构建 llm_client（测试注入 fake transport 的 RecordingLLMClient）优先；
    # 否则从 env 构建生产客户端（offline 跟 ALPHALOOM_OFFLINE）。构建失败则 None。
    if llm_client is None:
        llm_client = _build_llm_client(llm_db)
    app.state.llm = llm_client
    service = RunService(store=store, db_path=db_path, record_dir=record_dir, llm=llm_client)
    user_dir = Path(user_blueprints_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    app.state.service = service
    app.state.ws_queues = {}          # run_id -> list[asyncio.Queue]（Task 4 消费）
    app.state.event_log = {}          # run_id -> list[event]（重放缓冲，20k 上限）
    app.state.loop = None             # lifespan startup 兜底抓；ws_run 内按连接覆盖

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

    def _require_llm():
        llm = app.state.llm
        if llm is None:
            raise HTTPException(
                503, "no LLM client configured; set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL "
                     "in .env or ALPHALOOM_OFFLINE=1 with recorded calls")
        return llm

    # —— Copilot 元 Agent 端点（Text-to-Blueprint / explain / optimize）——
    @app.post("/api/copilot/blueprint")
    def copilot_blueprint(body: CopilotBlueprintIn):
        llm = _require_llm()
        try:
            # text_to_blueprint 自带 default_compile_fn（真 compile_blueprint）+ 自修复循环
            return copilot.text_to_blueprint(body.nl, REGISTRY, llm)
        except copilot.BlueprintGenerationError as exc:
            raise HTTPException(422, {"error": "generation_failed", "message": str(exc)})

    @app.post("/api/copilot/explain")
    def copilot_explain(body: CopilotExplainIn):
        llm = _require_llm()
        return {"explanation": copilot.explain(body.blueprint, llm)}

    @app.post("/api/copilot/optimize")
    def copilot_optimize(body: CopilotOptimizeIn):
        llm = _require_llm()
        try:
            return copilot.optimize(body.blueprint, body.report, llm, defs=REGISTRY)
        except copilot.BlueprintGenerationError as exc:
            raise HTTPException(422, {"error": "generation_failed", "message": str(exc)})

    # —— 评估 / 进化端点（D4-T6）——
    #
    # 同步执行（plain def → FastAPI 自动丢线程池，不阻塞事件循环，与既有 def 端点
    # 同款）。数据量锁定小规模（离线 ≤400 bar 数秒级；消融 ≤3 臂、进化 pop≤4/gen≤3），
    # 走 RunService 异步只会徒增复杂度；同步直接返回报告是本 demo 的正解。
    #
    # 配额守门（LLM 蓝图离线安全）：含 LLM 节点的蓝图（编译证书 llm_calls_per_bar>0）
    # 会在每根 bar / 每臂 / 每孩子调 LLM——非 offline 客户端跑它就烧真配额。因此
    # LLM 蓝图仅在 llm 客户端 offline（录制回放 / 本地剧本，零配额）时放行，否则 409。
    # 纯确定性蓝图（llm_calls_per_bar==0）无此风险，无条件放行（连 llm=None 也照跑）。
    def _compile_or_422(bp, bar):
        r = compile_blueprint(bp, bars_per_day=86_400_000 // bar_to_ms(bar))
        if not r.ok:
            raise HTTPException(422, {"errors": [e.to_dict() for e in r.errors]})
        return r

    def _has_sandbox_node(compiled) -> bool:
        """蓝图是否含任何沙箱热注册（不受信）的节点类型。守门不信任沙箱节点的
        成本证书自证——沙箱节点可声明 llm_calls_per_bar=0 却运行期偷调 LLM（C1）。
        虽运行期受限 ctx 已根治偷调，此处按"可能烧配额"兜底拒绝，防未来同类信任
        缺口（深度防御，C1 修复 #2）。"""
        for spec in getattr(compiled, "nodes", {}).values():
            d = REGISTRY.get(getattr(spec, "type", None))
            if d is not None and getattr(d, "sandboxed", False):
                return True
        return False

    def _needs_llm(compiled) -> bool:
        cert = getattr(compiled, "certificate", None)
        if cert is not None and getattr(cert, "llm_calls_per_bar", 0) > 0:
            return True
        # 兜底：含沙箱节点即不信任其零 LLM 自证（证书信任根在沙箱节点在场时失效）。
        return _has_sandbox_node(compiled)

    def _llm_quota_safe() -> bool:
        """当前 llm 客户端是否零配额安全（offline 录制回放 / 本地剧本）。"""
        return getattr(app.state.llm, "offline", False) is True

    def _guard_llm_blueprint(compiled):
        """LLM 蓝图（或含不受信沙箱节点的蓝图）须 offline 客户端；否则 409
        （不烧真配额，评估拒绝跑）。"""
        if _needs_llm(compiled) and not _llm_quota_safe():
            reason = ("an untrusted sandbox-registered node (its zero-LLM cost "
                      "certificate is not trusted)" if _has_sandbox_node(compiled)
                      else "LLM node(s)")
            raise HTTPException(
                409, f"blueprint contains {reason}; evaluation refuses to run it "
                     "against live quota. Provide recorded calls and set "
                     "ALPHALOOM_OFFLINE=1 (offline replay), or inject an offline "
                     "LLM client. Deterministic built-in-only blueprints run freely.")

    def _eval_source():
        from alphaloom.data.sqlite_source import SQLiteMarketData
        return SQLiteMarketData(db_path)

    def _load_demo_blueprint(basename: str):
        """按文件名从预置 blueprints_dir 加载 demo 蓝图（消融/进化 demo 预设服务端硬用）。

        坐标真源是 ``eval.demo_coords`` 的 ``*_BLUEPRINT_ID``（文件基名，与 seed 录制
        逐字同源）。找不到即 500——这是部署缺文件的运维错误，不该静默成回放 miss。
        """
        f = Path(blueprints_dir) / f"{basename}.loom"
        if not f.exists():
            raise HTTPException(
                500, f"demo blueprint {basename!r} missing from presets; "
                     "offline demo preset cannot run")
        return loads_loom(f.read_text(encoding="utf-8"))

    @app.post("/api/eval/fidelity")
    def eval_fidelity(body: EvalFidelityIn):
        """保真度阶梯 L0-L3（回测测谎仪，零 LLM 配额）：从一个已完成 run 取
        fills + 同窗 candles 在四档成交模型下重放。run 不存在 → 404、未完成 → 409。"""
        from alphaloom.eval.fidelity import fidelity_ladder
        row = store.get(body.run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        if row["status"] != "completed" or not row["report_json"]:
            raise HTTPException(
                409, f"run status is {row['status']!r}; fidelity ladder needs a "
                     "completed run with a recorded fill sequence to replay")
        report = json.loads(row["report_json"])
        fills = report.get("fills", [])
        params = json.loads(row["params_json"] or "{}")
        inst = params.get("inst")
        bar = params.get("bar", "1m")
        src = _eval_source()
        try:
            candles = list(src.iter_candles(inst, bar, params.get("start_ms"),
                                            params.get("end_ms")))
        finally:
            src.close()
        ladder = fidelity_ladder(fills, candles, initial_cash=body.initial_cash,
                                 fee_rate=body.fee_rate, slippage_bps=body.slippage_bps)
        return sanitize(ladder.to_dict())

    @app.post("/api/eval/scorecard")
    def eval_scorecard(body: EvalScorecardIn):
        """蓝图记分卡：把前端已算好的证据碎片（run 报告 / 保真度阶梯 / 消融）聚合成
        权威综合分。**评分数学只在后端一份实现**（tanh 压缩 / 四权重 / 缺证据保守分），
        前端绝不重实现以防与诚实评分口径漂移——故设此端点（纯数值零 LLM）。
        train_report 缺失 → 422（scorecard 的唯一硬性输入）。"""
        from alphaloom.eval.scorecard import scorecard
        try:
            card = scorecard(body.train_report, body.valid_report,
                             ladder=body.ladder, cost_cert=body.cost_cert,
                             ablation=body.ablation)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        return sanitize(card.to_dict())

    @app.post("/api/eval/leaderboard")
    def eval_leaderboard(body: EvalLeaderboardIn):
        """基线排行榜：buy-hold / 默认参数 / 随机三基线 + 可选指定蓝图，同窗对比。
        指定蓝图含 LLM 节点且非 offline → 409（守门）。基线纯确定性零 LLM。"""
        from alphaloom.eval.leaderboard import (baseline_buy_hold,
                                                baseline_ema_default,
                                                baseline_random, leaderboard)
        if body.bar not in _BARS:
            raise HTTPException(422, f"bar must be one of {_BARS}")
        has_valid = body.valid_start_ms is not None or body.valid_end_ms is not None
        src = _eval_source()
        try:
            def _pair(fn_or_bp, *, is_bp=False, name=None, bp=None):
                train = _run_eval_backtest(bp, src, body) if is_bp else fn_or_bp(
                    src, body.inst, body.bar, body.start_ms, body.end_ms,
                    initial_cash=body.initial_cash, fee_rate=body.fee_rate)
                valid = None
                if has_valid:
                    valid = (_run_eval_backtest(bp, src, body, valid=True) if is_bp
                             else fn_or_bp(src, body.inst, body.bar,
                                           body.valid_start_ms, body.valid_end_ms,
                                           initial_cash=body.initial_cash,
                                           fee_rate=body.fee_rate))
                return {"name": name, "kind": "baseline" if not is_bp else "blueprint",
                        "train_report": train, "valid_report": valid}

            entries = [
                _pair(baseline_buy_hold, name="baseline_buy_hold"),
                _pair(baseline_ema_default, name="baseline_ema_default"),
                _pair(baseline_random, name="baseline_random"),
            ]
            if body.blueprint is not None:
                try:
                    bp = loads_loom(json.dumps(body.blueprint))
                except (ValueError, KeyError, TypeError) as exc:
                    raise HTTPException(422, f"bad loom: {exc}")
                _guard_llm_blueprint(_compile_or_422(bp, body.bar))
                entries.append(_pair(None, is_bp=True, name=body.blueprint_name, bp=bp))
            board = leaderboard(entries)
        finally:
            src.close()
        return sanitize(board.to_dict())

    def _run_eval_backtest(bp, src, body, *, valid=False):
        from alphaloom.backtest.runner import run_backtest
        start = body.valid_start_ms if valid else body.start_ms
        end = body.valid_end_ms if valid else body.end_ms
        return run_backtest(bp, src, inst=body.inst, bar=body.bar,
                            start_ms=start, end_ms=end,
                            initial_cash=body.initial_cash, fee_rate=body.fee_rate,
                            llm=app.state.llm)

    @app.post("/api/eval/ablation")
    def eval_ablation(body: EvalAblationIn):
        """委员会消融三臂（护栏价值量化）：full / no_risk_officer / no_rag。含 LLM
        节点必然为真（committee）——须 offline 客户端（否则 409）。offline 空录制
        库的 ReplayMissError → 干净 422（带解释），不是 500 栈。"""
        from alphaloom.eval.ablation import DEFAULT_ARMS, committee_ablation
        from alphaloom.eval import demo_coords as _dc
        from alphaloom.llm.recording import ReplayMissError
        # demo=True：离线 demo 预设——服务端硬用规范 demo 坐标（demo_coords，与种子录制
        # 逐字同源），忽略请求体 blueprint/inst/窗口，杜绝前端传错 → 离线命中种子回放。
        if body.demo:
            bp = _load_demo_blueprint(_dc.DEMO_ABLATION_BLUEPRINT_ID)
            inst, bar = _dc.DEMO_INST, _dc.DEMO_BAR
            start_ms, end_ms = _dc.DEMO_ABLATION_START_MS, _dc.DEMO_ABLATION_END_MS
        else:
            if body.blueprint is None or body.inst is None:
                raise HTTPException(422, "blueprint and inst are required (or set demo=true)")
            if body.bar not in _BARS:
                raise HTTPException(422, f"bar must be one of {_BARS}")
            try:
                bp = loads_loom(json.dumps(body.blueprint))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"bad loom: {exc}")
            inst, bar = body.inst, body.bar
            start_ms, end_ms = body.start_ms, body.end_ms
        _guard_llm_blueprint(_compile_or_422(bp, bar))
        # 只跑蓝图实际支持的臂（无 committee → no_risk_officer 无对象；无 RAG →
        # no_rag 无对象）。arm_blueprint 对缺席目标抛 ValueError，此处预筛以给
        # 干净的 4xx（而非在 committee_ablation 里炸出 500）。
        types = {n.type for n in bp.nodes}
        arms = [a for a in DEFAULT_ARMS
                if a == "full"
                or (a == "no_risk_officer" and "committee" in types)
                or (a == "no_rag" and "require_citations" in types)]
        if "no_risk_officer" not in arms:
            raise HTTPException(
                422, "blueprint has no committee node to ablate; the ablation "
                     "study needs a committee (soft-guardrail) to remove")
        src = _eval_source()
        try:
            rep = committee_ablation(
                bp, src, inst=inst, bar=bar, start_ms=start_ms,
                end_ms=end_ms, llm=app.state.llm, arms=tuple(arms),
                initial_cash=body.initial_cash, fee_rate=body.fee_rate)
        except ReplayMissError as exc:
            raise HTTPException(
                422, {"error": "replay_miss",
                      "message": f"offline replay miss: {exc}. Ablation arms have no "
                                 "recorded LLM calls yet (recording is a later task); "
                                 "re-run in record mode to capture them."})
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        finally:
            src.close()
        return sanitize(rep.to_dict())

    @app.post("/api/evolve")
    def evolve_ep(body: EvolveIn):
        """进化实验室：LLM 变异算子 + 编译守门 + 谱系树。规模超限 → 422（pydantic +
        evolve 内 ValueError 双保险）；LLM 种子蓝图非 offline → 409。变异算子用
        app.state.llm（须 offline 安全，否则每孩子变异烧真配额）。"""
        from alphaloom.evolve.lab import evolve
        from alphaloom.eval import demo_coords as _dc
        from alphaloom.llm.recording import ReplayMissError
        # demo=True：离线 demo 预设——服务端硬用规范 demo 坐标（demo_coords，与种子录制
        # 逐字同源），忽略请求体 blueprint/inst/窗口/规模，杜绝前端传错 → 命中种子回放。
        if body.demo:
            bp = _load_demo_blueprint(_dc.DEMO_EVOLVE_BLUEPRINT_ID)
            inst, bar = _dc.DEMO_INST, _dc.DEMO_BAR
            train_window, valid_window = _dc.DEMO_EVOLVE_TRAIN, _dc.DEMO_EVOLVE_VALID
            population, generations = _dc.DEMO_EVOLVE_POPULATION, _dc.DEMO_EVOLVE_GENERATIONS
            param_only = _dc.DEMO_EVOLVE_PARAM_ONLY
            # 变异算子系统提示内嵌节点目录 —— demo 录制是对**内置节点目录**录的（seed
            # 脚本只 import alphaloom.nodes，无自定义/无测试夹具节点）。运行期若多注册过
            # 自定义（sandboxed）节点、或进程内混入测试夹具（category=="test"）节点，全局
            # REGISTRY 目录字符串会变 → 变异请求 hash 变 → 种子录制 miss。demo 回放固定
            # 用与录制同源的内置子集（排除 sandboxed 与 test 类），稳定命中。
            evolve_defs = {t: d for t, d in REGISTRY.items()
                           if not getattr(d, "sandboxed", False)
                           and getattr(d, "category", None) != "test"}
        else:
            if body.blueprint is None or body.inst is None:
                raise HTTPException(422, "blueprint and inst are required (or set demo=true)")
            if body.bar not in _BARS:
                raise HTTPException(422, f"bar must be one of {_BARS}")
            try:
                bp = loads_loom(json.dumps(body.blueprint))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"bad loom: {exc}")
            inst, bar = body.inst, body.bar
            train_window = (body.train_start_ms, body.train_end_ms)
            valid_window = (body.valid_start_ms, body.valid_end_ms)
            population, generations = body.population, body.generations
            param_only = body.param_only
            evolve_defs = None    # 非 demo：用全局 REGISTRY（现状，含自定义节点）
        _guard_llm_blueprint(_compile_or_422(bp, bar))
        # 变异算子本身也调 LLM——非 offline 客户端跑进化会烧真配额，一并守门。
        if not _llm_quota_safe():
            raise HTTPException(
                409, "evolution runs the LLM mutation operator every child; it "
                     "requires an offline LLM client (recorded replay / local "
                     "script, zero quota). Set ALPHALOOM_OFFLINE=1 with recorded "
                     "calls or inject an offline client.")
        src = _eval_source()
        try:
            g = evolve(bp, src, inst=inst, bar=bar,
                       train_window=train_window, valid_window=valid_window,
                       llm=app.state.llm, population=population,
                       generations=generations, param_only=param_only,
                       initial_cash=body.initial_cash, fee_rate=body.fee_rate,
                       defs=evolve_defs)
        except ReplayMissError as exc:
            raise HTTPException(
                422, {"error": "replay_miss",
                      "message": f"offline replay miss: {exc}. Re-run in record "
                                 "mode to capture the mutation-operator calls."})
        except ValueError as exc:                 # 规模超限 / 窗口重叠 → 422
            raise HTTPException(422, str(exc))
        finally:
            src.close()
        return sanitize(g.to_dict())

    # —— Text-to-Node 沙箱注册（AST 白名单，热注册进全局 REGISTRY）——
    # 命名空间假设（单用户，D4 Carryover）：REGISTRY 是进程级全局，注册的自定义
    # 节点跨请求/跨 create_app 实例/跨用户可见——本端点无 session 隔离。AlphaLoom
    # 当前是单用户本地/演示部署，此语义可接受（详见 nodes/registry.py 模块 docstring）。
    # 多用户生产部署需按 session/租户命名空间注册（D4 Carryover：并入沙箱资源限额批次）。
    @app.post("/api/nodes/custom")
    def custom_node(body: CustomNodeIn):
        result = compile_node_source(body.source)
        if isinstance(result, SandboxError):
            raise HTTPException(422, result.to_dict())   # {reason, message, lineno}
        return {"type": result.type, "category": result.category}

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
