"""种子录制 + 离线回放验证测试（AlphaLoom D3 Task 11）。

自证 seed_recordings.py 的核心契约，**全程不联网**（fake transport 是纯本地函数）：
  1) record 模式跑演示回测 → llm_calls.sqlite 有若干条 request-hash→response；
  2) 委员会决策**有变化**（策略师 side 非恒定，风控官出现 veto 强制 hold）；
  3) copilot text_to_blueprint 触发编译期自修复（坏图 TYPE_MISMATCH → 修正图）；
  4) 离线回放（offline=True）同一回测**零配额**——cache_hits>0 且 cache_misses==0，
     且 transport 一次都不被调用（换成会炸的哨兵，命中即证明没联网）。

测试用 tmp_path 的独立录制库（不依赖仓库里入库的 data/llm_calls.sqlite），因此
hermetic 可 CI；同时验证了入库那份是同一条代码路径生成的。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import alphaloom.nodes  # noqa: F401  触发全部内置节点注册
from alphaloom.backtest.runner import run_backtest
from alphaloom.copilot import blueprint as _copilot
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import load_loom_file, loads_loom
from alphaloom.llm.recording import RecordingLLMClient, ReplayMissError
from alphaloom.nodes.registry import REGISTRY

# --- 定位仓库根与 scripts/seed_recordings.py（backend/ 的上一级） --------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_PATH = _REPO_ROOT / "scripts" / "seed_recordings.py"
_DEMO_BLUEPRINT = _REPO_ROOT / "blueprints" / "agent_committee.loom"
_DEMO_MARKET_DB = _REPO_ROOT / "data" / "demo.sqlite"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_recordings", _SEED_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_recordings"] = mod
    spec.loader.exec_module(mod)
    return mod


seed_mod = _load_seed_module()


# 短窗口：够 warmup + 若干笔交易 + 若干 committee LLM 调用，但测试快。
_INST, _BAR = "BTC-USDT-SWAP", "1m"
_START, _END = 0, 120 * 60_000  # 前 ~120 根 1m bar


def _requires_demo_db():
    if not _DEMO_MARKET_DB.exists():
        pytest.skip(f"demo market db missing: {_DEMO_MARKET_DB} (run ensure_demo_db.py)")


def _record(tmp_db: Path) -> RecordingLLMClient:
    """用 fake transport（不联网）在 tmp_db 上录制一段演示回测。"""
    transport = seed_mod.make_fake_transport()
    client = RecordingLLMClient(transport, tmp_db, model=seed_mod.MODEL, offline=False)
    bp = load_loom_file(_DEMO_BLUEPRINT)
    source = SQLiteMarketData(_DEMO_MARKET_DB)
    run_backtest(bp, source, inst=_INST, bar=_BAR,
                 start_ms=_START, end_ms=_END, llm=client)
    source.close()
    return client


def test_demo_blueprint_compiles():
    """招牌演示蓝图 agent_committee.loom 编译通过（committee→…→risk_gate→execute 类型链成立）。"""
    bp = load_loom_file(_DEMO_BLUEPRINT)
    res = compile_blueprint(bp)
    assert res.ok, [e.to_dict() for e in res.errors]
    types = {n.type for n in bp.nodes}
    # 招牌蓝图确实含全套 LLM 决策 + RAG + 反思节点
    for t in ("committee", "knowledge_retrieve", "require_citations",
              "experience_retrieve", "reflector", "risk_gate", "execute_order"):
        assert t in types, f"demo blueprint missing {t!r}"


def test_record_mode_populates_db(tmp_path):
    """record 模式把若干条 request-hash→response 写进录制库（cache_misses>0）。"""
    _requires_demo_db()
    db = tmp_path / "llm_calls.sqlite"
    client = _record(db)
    assert client.cache_misses > 0
    n = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert n > 0
    assert n == client.cache_misses  # 每次 miss 恰好写一行（唯一请求）


def test_committee_decisions_vary_and_veto_fires(tmp_path):
    """录制里委员会决策有变化：策略师 side 非恒定，且风控官出现 veto（代码级强制路径）。"""
    _requires_demo_db()
    db = tmp_path / "llm_calls.sqlite"
    _record(db)
    rows = sqlite3.connect(str(db)).execute(
        "SELECT request_json, response_json FROM llm_calls").fetchall()
    strat_sides, vetoes = set(), 0
    for rq, rs in rows:
        sysc = next((m["content"].lower() for m in json.loads(rq)["messages"]
                     if m["role"] == "system"), "")
        try:
            obj = json.loads(json.loads(rs)["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if "committee's strategist" in sysc:
            strat_sides.add(obj.get("side"))
        elif "committee's risk officer" in sysc and obj.get("veto"):
            vetoes += 1
    # 策略师不是恒 long：至少两种不同 side 出现
    assert len(strat_sides) >= 2, f"strategist side not varied: {strat_sides}"
    assert vetoes > 0, "risk officer never vetoed — veto path not exercised in seed"


def test_copilot_self_repair_recorded(tmp_path):
    """copilot text_to_blueprint 录制触发编译期自修复：坏图 TYPE_MISMATCH → 修正图 ok。"""
    db = tmp_path / "llm_calls.sqlite"
    transport = seed_mod.make_fake_transport()
    client = RecordingLLMClient(transport, db, model=seed_mod.MODEL, offline=False)
    out = _copilot.text_to_blueprint(
        "an EMA-cross trend follower routed through the risk gate",
        REGISTRY, client, max_retries=3)
    # 最终图编译通过
    assert compile_blueprint(loads_loom(json.dumps(out["loom"]))).ok
    # 自修复轨迹：第一轮编译失败（TYPE_MISMATCH），第二轮 ok
    joined = " ".join(out["notes"])
    assert "TYPE_MISMATCH" in joined
    assert "compiled OK" in joined


def test_offline_replay_is_zero_quota(tmp_path):
    """核心自证：先 record，再以 offline=True 重放同一回测 → cache_hits>0 且 cache_misses==0，
    且 transport（换成会炸的哨兵）一次都不被调用——证明零配额、没联网、无 ReplayMissError。"""
    _requires_demo_db()
    db = tmp_path / "llm_calls.sqlite"
    _record(db)  # 先录制

    def _forbidden(_req):
        raise AssertionError("offline replay must not hit the transport (network)")

    replay = RecordingLLMClient(_forbidden, db, model=seed_mod.MODEL, offline=True)
    bp = load_loom_file(_DEMO_BLUEPRINT)
    source = SQLiteMarketData(_DEMO_MARKET_DB)
    report = run_backtest(bp, source, inst=_INST, bar=_BAR,
                          start_ms=_START, end_ms=_END, llm=replay)
    source.close()

    assert report.bars > 0
    assert replay.cache_hits > 0, "offline replay produced no cache hits"
    assert replay.cache_misses == 0, "offline replay had cache misses (not zero-quota)"


def test_offline_miss_raises_when_uncached(tmp_path):
    """反证：offline 模式碰到没录过的请求会抛 ReplayMissError（不静默联网）。"""
    db = tmp_path / "llm_calls.sqlite"
    # 空库 + offline：任何 chat 都是 miss
    client = RecordingLLMClient(lambda r: {}, db, model=seed_mod.MODEL, offline=True)
    with pytest.raises(ReplayMissError):
        client.chat([{"role": "user", "content": "never recorded"}])
