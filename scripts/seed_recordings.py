"""确定性种子录制生成器（AlphaLoom D3 Task 11）—— **绝不联网**。

用一个纯本地的 fake transport（检查 request 的 system prompt 判断"谁在问"，返回有效
JSON 的 canned 响应）驱动 RecordingLLMClient 的 **record 模式**，把若干条
request-hash -> response 写进 ``data/llm_calls.sqlite``（该库经 .gitignore 例外入库，
供 ``ALPHALOOM_OFFLINE=1`` 断网零配额演示回放）。

录制四条演示路径（离线 demo 需渲染全部 5 个 eval 可视化，D4-T8）：
  1) agent_committee.loom 的一段 run_backtest —— 触发 Committee（策略师/风控官/主席
     三角色 LLM 调用）+ knowledge_retrieve + require_citations + experience_retrieve
     + reflector + experience_write 全链（后四者不调 LLM，纯检索/反思，但把 committee
     的三次 LLM 调用录进库）。
  2) 一次 copilot.text_to_blueprint 生成 —— 展示"先返回绕风控坏图 → 读 CompileError
     → 修正图"的编译期自修复（第一次调用返回绕风控图触发 TYPE_MISMATCH，第二次返回
     修正图）。
  3) committee_ablation 三臂消融（``/api/eval/ablation`` 的确切代码路径）—— full /
     no_risk_officer / no_rag 同数据同窗口跑对照组，量化 LLM 护栏价值。full 与 no_rag
     臂的 committee 调用逐字相同（no_rag 只做图手术旁路 RAG，不改 committee 提示），
     故 no_rag 臂回放全命中 full 臂录制；no_risk_officer 臂跳过风控官角色、主席 user
     prompt 少一段 → 独立 hash，单独录制。
  4) evolve 小规模进化（``/api/evolve`` 的确切代码路径）—— ema_cross 种子 param_only
     进化 population=2 generations=2，唯一 LLM 消耗是变异算子。fake 变异 LLM 按父代
     蓝图参数**内容路由**返回确定性 patch（好变异 + 一个 param_only 下被拒的结构变异
     触发自修复重试），种子回测本身零 LLM。

**坐标对齐（D3-T11 核心教训：record 与 verify/端点必须走同一代码路径、同一坐标，
否则 request hash 对不上 → 回放 miss）**：消融/进化种子的 inst/bar/窗口/blueprint/
规模全部锁进本文件常量（``DEMO_ABLATION_*`` / ``DEMO_EVOLVE_*``），即"官方 demo
坐标"——离线演示（前端默认值 / README）用这套坐标调 ``/api/eval/ablation`` 与
``/api/evolve`` 即全命中回放（committee_ablation / evolve 与端点调用同函数同参数）。

fake transport 的响应有**变化**（不是恒 long）：策略师读市场 JSON 的价格几何决定
side；风控官在波动过大时 veto；主席合成。model 固定 "spark-x1"（与 OFFLINE_DEFAULTS
一致，否则离线 replay key miss）。

用法：
    python scripts/seed_recordings.py            # 生成（幂等：已有则默认覆盖重建）
    python scripts/seed_recordings.py --verify   # 生成后跑离线回放验证零配额
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from alphaloom.copilot import blueprint as _copilot  # noqa: E402
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402
from alphaloom.eval.ablation import committee_ablation  # noqa: E402
# 消融/进化 demo 的官方规范坐标 —— **单一真源在后端**（eval/demo_coords.py）。
# seed 从后端 import（而非自己定义），确保 seed 与 /api 端点永远同源、杜绝漂移
# （D4-T8 修复）。此处 import 的坐标值即录制用值，逐字与端点 demo=True 一致。
from alphaloom.eval.demo_coords import (  # noqa: E402
    DEMO_ABLATION_BLUEPRINT_ID, DEMO_ABLATION_START_MS, DEMO_ABLATION_END_MS,
    DEMO_EVOLVE_BLUEPRINT_ID, DEMO_EVOLVE_TRAIN, DEMO_EVOLVE_VALID,
    DEMO_EVOLVE_POPULATION, DEMO_EVOLVE_GENERATIONS, DEMO_EVOLVE_PARAM_ONLY,
    DEMO_INST as _DEMO_INST, DEMO_BAR as _DEMO_BAR)
from alphaloom.evolve.lab import evolve  # noqa: E402
from alphaloom.graph.model import load_loom_file  # noqa: E402
from alphaloom.llm.recording import RecordingLLMClient  # noqa: E402
from alphaloom.backtest.runner import run_backtest  # noqa: E402
import alphaloom.nodes  # noqa: E402,F401  触发全部内置节点注册
from alphaloom.nodes.registry import REGISTRY  # noqa: E402

# --- 固定资源路径 ------------------------------------------------------------ #
MODEL = "spark-x1"  # 必须与 OFFLINE_DEFAULTS 一致，否则离线 replay key miss
# 真实讯飞录制（astron-code-latest）行数——种子重建绝不能触碰（D3-T11 事故根因）。
# 种子只删 model==spark-x1 的行；此常量在 seed 末尾硬断言 astron 分布纹丝不动。
_EXPECTED_ASTRON = 123
RECORD_DB = _ROOT / "data" / "llm_calls.sqlite"
DEMO_MARKET_DB = _ROOT / "data" / "demo.sqlite"
DEMO_BLUEPRINT = _ROOT / "blueprints" / "agent_committee.loom"

# 演示回测窗口：demo.sqlite 的 BTC-USDT-SWAP 1m 一段（够 warmup + 若干笔交易，
# 又不至于把录制库撑大——committee 每根 bar 3 次 LLM 调用，窗口越大录制条目越多）。
# inst/bar 用后端共享真源（demo_coords）——committee demo 与消融/进化同标的同周期。
DEMO_INST = _DEMO_INST
DEMO_BAR = _DEMO_BAR
DEMO_START_MS = 0
DEMO_END_MS = 300 * 60_000  # 前 ~300 根 1m bar

# --- 消融演示"官方坐标" —— 从后端 demo_coords import（单一真源，见文件头 import）。
# 端点 demo=True 与本录制逐字同坐标 → 离线全命中回放。blueprint 复用招牌蓝图；
# demo_coords 只存 blueprint_id，此处映射回本仓库 .loom 路径（seed 走文件加载）。
DEMO_ABLATION_BLUEPRINT = _ROOT / "blueprints" / f"{DEMO_ABLATION_BLUEPRINT_ID}.loom"

# --- 进化演示"官方坐标" —— 同样从后端 demo_coords import（单一真源）。
DEMO_EVOLVE_BLUEPRINT = _ROOT / "blueprints" / f"{DEMO_EVOLVE_BLUEPRINT_ID}.loom"


# --------------------------------------------------------------------------- #
# Fake transport：检查 request 内容路由到对应角色，返回有效 JSON canned 响应。
# **不联网**——纯本地确定性函数。
# --------------------------------------------------------------------------- #
def _wrap(content: str) -> dict:
    """包成 OpenAI 兼容的 chat.completions 响应结构（recording 层原样缓存）。"""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _user_market(messages: list[dict]) -> dict:
    """从 user 消息里取出市场 JSON（committee 各角色 user prompt 里都带它）。"""
    for m in messages:
        if m.get("role") != "user":
            continue
        obj = _try_json(m.get("content", ""))
        if isinstance(obj, dict):
            # 风控官/主席的 user 是 {"market":..., "strategist":...} 或
            # {"strategist":..., "risk_officer":...}；策略师是裸市场 JSON。
            if "market" in obj and isinstance(obj["market"], dict):
                return obj["market"]
            if "close" in obj:
                return obj
    return {}


def _try_json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _strategist_response(market: dict) -> str:
    """策略师提案：读价格几何决定 side（**有变化**，不是恒 long）。

    以 close 在 [low, high] 区间的相对位置作方向依据（收在区间上沿 → 多头动能，
    下沿 → 空头动能，中部 → 观望），确定性且随每根 bar 变化。
    """
    close = float(market.get("close", 0.0) or 0.0)
    high = float(market.get("high", close) or close)
    low = float(market.get("low", close) or close)
    rng = high - low
    pos = 0.5 if rng <= 0 else (close - low) / rng  # 0=收在最低, 1=收在最高
    if pos >= 0.62:
        side, conf, why = "long", round(0.55 + 0.35 * pos, 3), "close near the high — upside momentum"
    elif pos <= 0.38:
        side, conf, why = "short", round(0.55 + 0.35 * (1 - pos), 3), "close near the low — downside pressure"
    else:
        side, conf, why = "hold", 0.4, "close mid-range — no clear edge"
    return json.dumps({"side": side, "rationale": why, "confidence": conf})


def _risk_response(market: dict, strategist: dict) -> str:
    """风控官：波动过大（atr 相对价位偏高）时 veto，否则放行并给 concern。"""
    close = float(market.get("close", 0.0) or 0.0)
    atr = market.get("atr")
    atr_f = float(atr) if atr is not None else 0.0
    side = strategist.get("side", "hold")
    # 相对波动率：atr / close 超过阈值 且 策略师想开仓 → veto（宁可错过不可做错）。
    # 阈值取 demo.sqlite BTC 1m 波动分布的 ~p90，使约一成开仓被否决——种子里真实
    # 出现 veto，展示"风控官 veto → 主席强制 hold"的代码级强制（不是纸面功能）。
    rel_vol = (atr_f / close) if close > 0 else 0.0
    veto = bool(side in ("long", "short") and rel_vol > 0.0052)
    if veto:
        concern = f"relative volatility {rel_vol:.3%} too high for a {side} entry"
        return json.dumps({"veto": True, "concern": concern, "confidence": 0.2})
    concern = "volatility acceptable; keep the ATR stop tight"
    return json.dumps({"veto": False, "concern": concern, "confidence": 0.7})


def _chair_response(strategist: dict, risk: dict) -> str:
    """主席合成：尊重风控官 veto（veto → hold），否则跟随策略师方向。"""
    if bool(risk.get("veto")):
        return json.dumps({
            "side": "hold",
            "rationale": "chair defers to the risk officer's veto",
            "confidence": 0.1,
        })
    side = strategist.get("side", "hold")
    conf = float(strategist.get("confidence", 0.5) or 0.5)
    # 主席略微保守：把策略师置信度打个折
    return json.dumps({
        "side": side,
        "rationale": f"committee endorses the strategist's {side} thesis",
        "confidence": round(conf * 0.9, 3),
    })


# --- Copilot 自修复演示：第一次坏图（绕风控），第二次修正图 ------------------- #
def _copilot_bad_bypass_loom() -> dict:
    """绕风控坏图：sizer.sized(signal) 直连 exec.signal(risk_stamped_signal) → TYPE_MISMATCH。"""
    return {
        "id": "copilot_demo_v1",
        "name": "Copilot demo (bypasses risk gate)",
        "nodes": [
            {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
            {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
            {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
            {"id": "atr", "type": "atr", "params": {"period": 14}},
            {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
            {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
            {"id": "exec", "type": "execute_order", "params": {}},
        ],
        "edges": [
            {"from": "feed.out", "to": "ema_fast.candle"},
            {"from": "feed.out", "to": "ema_slow.candle"},
            {"from": "feed.out", "to": "atr.candle"},
            {"from": "ema_fast.value", "to": "cross.fast"},
            {"from": "ema_slow.value", "to": "cross.slow"},
            {"from": "feed.out", "to": "cross.candle"},
            {"from": "atr.value", "to": "cross.atr"},
            {"from": "cross.signal", "to": "sizer.signal"},
            {"from": "feed.out", "to": "sizer.candle"},
            {"from": "sizer.sized", "to": "exec.signal"},
        ],
        "meta": {},
    }


def _copilot_fixed_loom() -> dict:
    """修正图：signal 过 risk_gate 拿到 risk_stamped_signal 再进 execute_order。"""
    loom = _copilot_bad_bypass_loom()
    loom["name"] = "Copilot demo (EMA cross through risk gate)"
    loom["nodes"].append(
        {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": True}})
    # 去掉绕风控那条边，改成 sizer -> risk -> exec
    loom["edges"] = [e for e in loom["edges"] if e != {"from": "sizer.sized", "to": "exec.signal"}]
    loom["edges"].append({"from": "sizer.sized", "to": "risk.signal"})
    loom["edges"].append({"from": "risk.stamped", "to": "exec.signal"})
    return loom


# --- 进化变异算子演示：按父代蓝图**内容**路由确定性 patch（不用状态计数器） --- #
# D3-T11 教训：fake 响应必须是 request 内容的纯函数（record 与 verify 走同一路径、
# 同一坐标即同一 hash → 全命中回放）。用状态计数器会让 record/verify 调用序稍有偏差
# 就崩，故这里按父代蓝图参数指纹路由——同一父代恒得同一 patch，与调用序无关。
def _bp_params(bp_json: dict) -> dict:
    """{nodeId: {param: value}}——从变异 user 消息里的父代蓝图取参数指纹。"""
    return {n.get("id"): dict(n.get("params") or {}) for n in bp_json.get("nodes", [])}


def _mutation_patch(user_obj: dict, last_user_text: str) -> dict:
    """确定性变异 patch（param_only 模式）——父代蓝图 + 反馈的纯函数。

    路由：
      - 反馈含 ``[PATCH_REJECTED]``（param_only 拒了结构变异）→ 返回合法 set_params 修正
        （自修复演示：一次变异被拒 → 读反馈 → 改出只动参数的 patch → 过编译，
        compile_status=repaired）。
      - 种子（ema_fast.period==12）→ 收紧快 EMA 12→9、放宽 ATR 止损 2.0→2.5（好变异，
        在 train 窗超越种子）。
      - 快 EMA 已是 9（上代好孩子做父）→ **故意提结构变异**（加 atr2 节点），param_only
        应用侧 MutationRejected → 触发上面的自修复分支。
      - 其余（含空/异常）→ 温和合法参数微调（好变异兜底）。
    """
    if "[PATCH_REJECTED]" in last_user_text:
        return {"summary": "param-only repair: slow the fast EMA to 15 instead",
                "set_params": {"ema_fast": {"period": 15}}}
    params = _bp_params(user_obj.get("blueprint", {}))
    fast = params.get("ema_fast", {}).get("period")
    if fast == 12:                      # 种子：好变异
        return {"summary": "tighten fast EMA to 9 and widen the ATR stop to 2.5",
                "set_params": {"ema_fast": {"period": 9}, "cross": {"atr_mult": 2.5}}}
    if fast == 9:                       # 结构变异（param_only 必拒 → 自修复）
        return {"summary": "add a second ATR node (illegal in param-only mode)",
                "add_nodes": [{"id": "atr2", "type": "atr", "params": {"period": 7}}]}
    return {"summary": "raise risk to 3% and slow the fast EMA to 10",
            "set_params": {"sizer": {"risk_pct": 0.03}, "ema_fast": {"period": 10}}}


def make_fake_transport():
    """返回一个纯本地 transport(request)->response。

    路由靠 system prompt 关键字。committee 三角色各返回随市场变化的 JSON；copilot 首次
    返回绕风控坏图、其后返回修正图（自修复演示）；进化变异算子按父代蓝图内容路由确定性
    patch。所有响应都是有效 JSON canned 内容。**纯本地、不联网。**
    """
    copilot_calls = {"n": 0}

    def transport(request: dict) -> dict:
        messages = request.get("messages", [])
        system = ""
        for m in messages:
            if m.get("role") == "system":
                system = str(m.get("content", ""))
                break
        low = system.lower()

        # --- Copilot text_to_blueprint（自修复演示） ---
        # 必须先判 copilot：其 system prompt 内嵌全节点目录（含 committee 的
        # strategist_persona/strategist_prompt 等参数名），"strategist"/"risk"
        # 字样会误命中下面的委员会角色分支，故 copilot 优先。
        if "translate a user's plain-language strategy" in low:
            copilot_calls["n"] += 1
            loom = _copilot_bad_bypass_loom() if copilot_calls["n"] == 1 else _copilot_fixed_loom()
            return _wrap(json.dumps(loom))

        # --- 进化变异算子（自修复演示；按父代蓝图内容路由，无状态） ---
        # 其 system prompt 内嵌全节点目录（含 "committee" 字样），故须在委员会角色分支
        # 之前判定，靠 "mutation operator" 独有关键字精确命中，避免误路由。
        if "mutation operator" in low:
            users = [m for m in messages if m.get("role") == "user"]
            first_user: dict = {}
            if users:
                obj = _try_json(users[0].get("content", ""))
                if isinstance(obj, dict):
                    first_user = obj
            last_user_text = str(users[-1].get("content", "")) if users else ""
            return _wrap(json.dumps(_mutation_patch(first_user, last_user_text)))

        # --- Committee 三角色（顺序：strategist -> risk officer -> chair） ---
        if "committee's strategist" in low:
            market = _user_market(messages)
            return _wrap(_strategist_response(market))
        if "committee's risk officer" in low:
            market = _user_market(messages)
            strat = {}
            for m in messages:
                if m.get("role") == "user":
                    obj = _try_json(m.get("content", ""))
                    if isinstance(obj, dict) and isinstance(obj.get("strategist"), dict):
                        strat = obj["strategist"]
            return _wrap(_risk_response(market, strat))
        if "committee chair" in low:
            strat, risk = {}, {}
            for m in messages:
                if m.get("role") == "user":
                    obj = _try_json(m.get("content", ""))
                    if isinstance(obj, dict):
                        if isinstance(obj.get("strategist"), dict):
                            strat = obj["strategist"]
                        if isinstance(obj.get("risk_officer"), dict):
                            risk = obj["risk_officer"]
            return _wrap(_chair_response(strat, risk))

        # --- 单角色 LLMAnalyst（若被用到，兜底：读市场几何给方向） ---
        if "trading analyst" in low:
            return _wrap(_strategist_response(_user_market(messages)))

        # 未识别的请求：返回安全 hold（不联网、不炸）
        return _wrap(json.dumps({"side": "hold", "rationale": "unrecognized prompt", "confidence": 0.0}))

    return transport


# --------------------------------------------------------------------------- #
# 三类 demo 的共享调用路径（record 与 verify 必须走同一函数、同一坐标 —— D3-T11 教训）
# --------------------------------------------------------------------------- #
def _run_committee_demo(client) -> object:
    """演示 1：agent_committee.loom 回测（committee 三角色 + RAG + 反思全链）。"""
    bp = load_loom_file(DEMO_BLUEPRINT)
    source = SQLiteMarketData(DEMO_MARKET_DB)
    try:
        return run_backtest(
            bp, source, inst=DEMO_INST, bar=DEMO_BAR,
            start_ms=DEMO_START_MS, end_ms=DEMO_END_MS, llm=client)
    finally:
        source.close()


def _run_ablation_demo(client) -> object:
    """演示 3：committee_ablation 三臂（``/api/eval/ablation`` 的确切代码路径与坐标）。"""
    bp = load_loom_file(DEMO_ABLATION_BLUEPRINT)
    source = SQLiteMarketData(DEMO_MARKET_DB)
    try:
        return committee_ablation(
            bp, source, inst=DEMO_INST, bar=DEMO_BAR,
            start_ms=DEMO_ABLATION_START_MS, end_ms=DEMO_ABLATION_END_MS,
            llm=client)
    finally:
        source.close()


def _run_evolve_demo(client) -> object:
    """演示 4：evolve 小规模进化（``/api/evolve`` 的确切代码路径与坐标）。"""
    seed_bp = load_loom_file(DEMO_EVOLVE_BLUEPRINT)
    source = SQLiteMarketData(DEMO_MARKET_DB)
    try:
        return evolve(
            seed_bp, source, inst=DEMO_INST, bar=DEMO_BAR,
            train_window=DEMO_EVOLVE_TRAIN, valid_window=DEMO_EVOLVE_VALID,
            llm=client, population=DEMO_EVOLVE_POPULATION,
            generations=DEMO_EVOLVE_GENERATIONS,
            param_only=DEMO_EVOLVE_PARAM_ONLY)
    finally:
        source.close()


def _model_distribution(db_path: Path) -> dict:
    """录制库按 model 统计行数（astron 保护自证：断言 astron-code-latest 未被冲掉）。"""
    import json as _json
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(db_path))
    try:
        dist: dict = {}
        for (rj,) in conn.execute("SELECT request_json FROM llm_calls").fetchall():
            m = _json.loads(rj).get("model")
            dist[m] = dist.get(m, 0) + 1
        return dist
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 种子生成
# --------------------------------------------------------------------------- #
def seed(*, overwrite: bool = True) -> dict:
    """生成种子录制到 RECORD_DB（record 模式，offline=False，但 transport 不联网）。

    返回统计 dict（录制条数 / committee 触发次数 / copilot 尝试次数）。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    RECORD_DB.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and RECORD_DB.exists():
        # 幂等：只删本脚本 model 的确定性种子行，**保留其他 model 的真实录制**
        # （如真实讯飞 astron-code-latest 录制——绝不能被种子重建冲掉，否则回退诚实修复）。
        import json as _json
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(RECORD_DB)
        try:
            _rows = _conn.execute("SELECT hash, request_json FROM llm_calls").fetchall()
            _stale = [h for h, rj in _rows if _json.loads(rj).get("model") == MODEL]
            _conn.executemany("DELETE FROM llm_calls WHERE hash=?", [(h,) for h in _stale])
            _conn.commit()
        finally:
            _conn.close()

    if not DEMO_MARKET_DB.exists():
        raise SystemExit(
            f"demo market db missing: {DEMO_MARKET_DB}\n"
            "run scripts/ensure_demo_db.py first (deterministic, offline).")

    transport = make_fake_transport()
    client = RecordingLLMClient(transport, RECORD_DB, model=MODEL, offline=False)

    # --- 演示 1：agent_committee.loom 回测（committee 三角色 + RAG + 反思全链） ---
    report = _run_committee_demo(client)

    # --- 演示 2：copilot text_to_blueprint（编译期自修复：坏图 -> 修正图） ---
    copilot_out = _copilot.text_to_blueprint(
        "an EMA-cross trend follower that routes every order through the risk gate",
        REGISTRY, client, max_retries=3)

    # --- 演示 3：committee_ablation 三臂消融（/api/eval/ablation 代码路径） ---
    ablation = _run_ablation_demo(client)

    # --- 演示 4：evolve 小规模进化（/api/evolve 代码路径） ---
    genealogy = _run_evolve_demo(client)

    dist = _model_distribution(RECORD_DB)
    astron = dist.get("astron-code-latest", 0)
    # 保护 123 真实讯飞（D3-T11 事故：种子重建曾冲掉真实录制）。种子只删 spark-x1
    # 行、保留其他 model —— 此处硬断言 astron 未被触碰（少于既有即立即失败，不静默）。
    if astron != _EXPECTED_ASTRON:
        raise SystemExit(
            f"astron-code-latest recordings changed: expected {_EXPECTED_ASTRON}, "
            f"got {astron} — the seed must never touch non-spark-x1 rows "
            f"(restore from the scratchpad backup and investigate).")
    return {
        "recorded_rows": _count_rows(RECORD_DB),
        "model_distribution": dist,
        "backtest_bars": report.bars,
        "backtest_cache_misses": client.cache_misses,
        "backtest_cache_hits": client.cache_hits,
        "copilot_notes": copilot_out["notes"],
        "copilot_final_nodes": len(copilot_out["loom"]["nodes"]),
        "ablation_arms": [a.arm for a in ablation.arms],
        "ablation_guardrail_helped": (
            None if ablation.guardrail_value is None
            else ablation.guardrail_value.get("guardrail_helped")),
        "evolve_nodes": len(genealogy.nodes),
        "evolve_statuses": sorted({n.compile_status for n in genealogy.nodes}),
        "evolve_winner": genealogy.winner.get("id"),
    }


def _count_rows(db_path: Path) -> int:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 离线回放验证：ALPHALOOM_OFFLINE 路径应零配额（全 replay 命中，misses==0）。
# 覆盖三类调 LLM 的 demo：committee 回测 / 消融三臂 / 进化——全部走与录制**逐字相同**
# 的 helper（同函数同坐标），证明离线可回放。copilot 自修复的录制被消融/committee
# 复用无关，此处不单列（其 LLM 调用是 committee/copilot demo 的一部分，已在库中）。
# --------------------------------------------------------------------------- #
def _forbidden_transport(_request):
    """哨兵 transport：离线回放绝不该走到网络——命中缓存则根本不调它；一调即炸。"""
    raise AssertionError("offline replay must not hit the transport (network)")


def _verify_one(runner) -> dict:
    """用 offline=True + 哨兵 transport 重跑一个 demo，返回 hits/misses。

    offline 模式下任何未命中即先抛 ReplayMissError（走不到 transport）；哨兵是双保险，
    证明确实零网络。runner 与录制走同一函数同一坐标，故命中即证明离线可回放。
    """
    client = RecordingLLMClient(_forbidden_transport, RECORD_DB, model=MODEL,
                                offline=True)
    runner(client)
    return {"cache_hits": client.cache_hits, "cache_misses": client.cache_misses}


def verify_offline_replay() -> dict:
    """离线回放三类 demo，全部应 cache_hits>0 且 cache_misses==0（零配额自证）。"""
    return {
        "committee": _verify_one(_run_committee_demo),
        "ablation": _verify_one(_run_ablation_demo),
        "evolve": _verify_one(_run_evolve_demo),
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    do_verify = "--verify" in argv

    stats = seed(overwrite=True)
    print("=== seed_recordings ===")
    print(f"recorded rows in {RECORD_DB.name}: {stats['recorded_rows']}")
    print(f"model distribution: {stats['model_distribution']}")
    print(f"committee backtest: {stats['backtest_bars']} bars "
          f"(cumulative record-mode misses={stats['backtest_cache_misses']}, "
          f"hits={stats['backtest_cache_hits']})")
    print(f"copilot self-repair notes: {stats['copilot_notes']}")
    print(f"copilot final blueprint nodes: {stats['copilot_final_nodes']}")
    print(f"ablation arms recorded: {stats['ablation_arms']} "
          f"(guardrail_helped={stats['ablation_guardrail_helped']})")
    print(f"evolve genealogy: {stats['evolve_nodes']} nodes, "
          f"statuses={stats['evolve_statuses']}, winner={stats['evolve_winner']}")

    if do_verify:
        v = verify_offline_replay()
        print("=== offline replay verification (ALPHALOOM_OFFLINE path) ===")
        ok = True
        for name, r in v.items():
            hits, misses = r["cache_hits"], r["cache_misses"]
            print(f"{name}: cache_hits={hits} cache_misses={misses}")
            if misses != 0:
                print(f"FAIL: {name} offline replay had cache misses (not zero-quota)")
                ok = False
            if hits <= 0:
                print(f"FAIL: {name} offline replay had no cache hits "
                      "(recordings not exercised)")
                ok = False
        if not ok:
            return 1
        print("OK: all demos replay zero-quota (cache_hits>0, cache_misses=0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
