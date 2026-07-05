"""确定性种子录制生成器（AlphaLoom D3 Task 11）—— **绝不联网**。

用一个纯本地的 fake transport（检查 request 的 system prompt 判断"谁在问"，返回有效
JSON 的 canned 响应）驱动 RecordingLLMClient 的 **record 模式**，把若干条
request-hash -> response 写进 ``data/llm_calls.sqlite``（该库经 .gitignore 例外入库，
供 ``ALPHALOOM_OFFLINE=1`` 断网零配额演示回放）。

录制两条演示路径：
  1) agent_committee.loom 的一段 run_backtest —— 触发 Committee（策略师/风控官/主席
     三角色 LLM 调用）+ knowledge_retrieve + require_citations + experience_retrieve
     + reflector + experience_write 全链（后四者不调 LLM，纯检索/反思，但把 committee
     的三次 LLM 调用录进库）。
  2) 一次 copilot.text_to_blueprint 生成 —— 展示"先返回绕风控坏图 → 读 CompileError
     → 修正图"的编译期自修复（第一次调用返回绕风控图触发 TYPE_MISMATCH，第二次返回
     修正图）。

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
from alphaloom.graph.model import load_loom_file  # noqa: E402
from alphaloom.llm.recording import RecordingLLMClient  # noqa: E402
from alphaloom.backtest.runner import run_backtest  # noqa: E402
import alphaloom.nodes  # noqa: E402,F401  触发全部内置节点注册
from alphaloom.nodes.registry import REGISTRY  # noqa: E402

# --- 固定资源路径 ------------------------------------------------------------ #
MODEL = "spark-x1"  # 必须与 OFFLINE_DEFAULTS 一致，否则离线 replay key miss
RECORD_DB = _ROOT / "data" / "llm_calls.sqlite"
DEMO_MARKET_DB = _ROOT / "data" / "demo.sqlite"
DEMO_BLUEPRINT = _ROOT / "blueprints" / "agent_committee.loom"

# 演示回测窗口：demo.sqlite 的 BTC-USDT-SWAP 1m 一段（够 warmup + 若干笔交易，
# 又不至于把录制库撑大——committee 每根 bar 3 次 LLM 调用，窗口越大录制条目越多）。
DEMO_INST = "BTC-USDT-SWAP"
DEMO_BAR = "1m"
DEMO_START_MS = 0
DEMO_END_MS = 300 * 60_000  # 前 ~300 根 1m bar


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


def make_fake_transport():
    """返回一个纯本地 transport(request)->response。

    路由靠 system prompt 关键字。committee 三角色各返回随市场变化的 JSON；copilot 首次
    返回绕风控坏图、其后返回修正图（自修复演示）。所有响应都是有效 JSON canned 内容。
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
        RECORD_DB.unlink()  # 幂等：删旧库重建（避免陈旧条目串味）

    if not DEMO_MARKET_DB.exists():
        raise SystemExit(
            f"demo market db missing: {DEMO_MARKET_DB}\n"
            "run scripts/ensure_demo_db.py first (deterministic, offline).")

    transport = make_fake_transport()
    client = RecordingLLMClient(transport, RECORD_DB, model=MODEL, offline=False)

    # --- 演示 1：agent_committee.loom 回测（触发 committee 三角色 + RAG + 反思全链） ---
    bp = load_loom_file(DEMO_BLUEPRINT)
    source = SQLiteMarketData(DEMO_MARKET_DB)
    report = run_backtest(
        bp, source, inst=DEMO_INST, bar=DEMO_BAR,
        start_ms=DEMO_START_MS, end_ms=DEMO_END_MS, llm=client)
    source.close()

    # --- 演示 2：copilot text_to_blueprint（编译期自修复：坏图 -> 修正图） ---
    copilot_out = _copilot.text_to_blueprint(
        "an EMA-cross trend follower that routes every order through the risk gate",
        REGISTRY, client, max_retries=3)

    n_rows = _count_rows(RECORD_DB)
    return {
        "recorded_rows": n_rows,
        "backtest_bars": report.bars,
        "backtest_cache_misses": client.cache_misses,
        "backtest_cache_hits": client.cache_hits,
        "copilot_notes": copilot_out["notes"],
        "copilot_final_nodes": len(copilot_out["loom"]["nodes"]),
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
# --------------------------------------------------------------------------- #
def verify_offline_replay() -> dict:
    """用 offline=True 的 RecordingLLMClient 重跑同一演示回测，断言零配额。

    transport 换成"一调用就炸"的哨兵——若任何请求未命中缓存会先抛 ReplayMissError
    （offline 模式），根本走不到 transport；哨兵是双保险，证明确实没联网。
    """
    def _forbidden(_request):
        raise AssertionError("offline replay must not hit the transport (network)")

    client = RecordingLLMClient(_forbidden, RECORD_DB, model=MODEL, offline=True)
    bp = load_loom_file(DEMO_BLUEPRINT)
    source = SQLiteMarketData(DEMO_MARKET_DB)
    report = run_backtest(
        bp, source, inst=DEMO_INST, bar=DEMO_BAR,
        start_ms=DEMO_START_MS, end_ms=DEMO_END_MS, llm=client)
    source.close()
    return {
        "bars": report.bars,
        "cache_hits": client.cache_hits,
        "cache_misses": client.cache_misses,
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    do_verify = "--verify" in argv

    stats = seed(overwrite=True)
    print("=== seed_recordings ===")
    print(f"recorded rows in {RECORD_DB.name}: {stats['recorded_rows']}")
    print(f"backtest: {stats['backtest_bars']} bars, "
          f"cache_misses={stats['backtest_cache_misses']} (record mode), "
          f"cache_hits={stats['backtest_cache_hits']}")
    print(f"copilot self-repair notes: {stats['copilot_notes']}")
    print(f"copilot final blueprint nodes: {stats['copilot_final_nodes']}")

    if do_verify:
        v = verify_offline_replay()
        print("=== offline replay verification (ALPHALOOM_OFFLINE path) ===")
        print(f"replayed {v['bars']} bars: cache_hits={v['cache_hits']} "
              f"cache_misses={v['cache_misses']}")
        if v["cache_misses"] != 0:
            print("FAIL: offline replay had cache misses (not zero-quota)")
            return 1
        if v["cache_hits"] <= 0:
            print("FAIL: offline replay had no cache hits (recordings not exercised)")
            return 1
        print("OK: offline replay is zero-quota (cache_hits>0, cache_misses=0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
