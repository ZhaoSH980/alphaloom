# backend/tests/test_api_lifespan.py
"""D4 Carryover: FastAPI on_event → lifespan 迁移 + RunsStore 连接 finalizer。

锁定三件事：
1. create_app 不再注册 @app.on_event（消除 269 测试里的 on_event DeprecationWarning）。
2. lifespan 挂在 app 上（FastAPI(lifespan=...)），startup 兜底抓 event loop、
   shutdown 关闭 RunsStore 连接（无 ResourceWarning 泄漏）。
3. D2-T4 死锁修复未被破坏：ws_run handler 内仍在连接期抓 running loop
   （TestClient 每连接独立 loop，不能只靠 startup 抓的 loop）。
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alphaloom.nodes  # noqa: F401
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]


def _make_app(tmp_path):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(30, seed=5, trend=0.001))
    return create_app(
        db_path=tmp_path / "demo.sqlite", runs_db=tmp_path / "runs.sqlite",
        record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "ubp", frontend_dist=tmp_path / "nd")


def test_create_app_registers_no_on_event_deprecation(tmp_path):
    """create_app 不得触发 FastAPI on_event DeprecationWarning（已迁 lifespan）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _make_app(tmp_path)
    on_event_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "on_event is deprecated" in str(w.message)
    ]
    assert on_event_warnings == [], (
        f"create_app still emits on_event DeprecationWarning: "
        f"{[str(w.message) for w in on_event_warnings]}")


def test_app_has_lifespan_not_on_event_startup(tmp_path):
    """迁移证据：router 不再有 on_startup handler；lifespan_context 已设置。"""
    app = _make_app(tmp_path)
    # on_event('startup') 会往 router.on_startup 追加 handler；迁移后应为空。
    assert not app.router.on_startup, (
        f"expected no on_startup handlers after lifespan migration, "
        f"got {app.router.on_startup}")
    # lifespan_context 是 FastAPI 存放 lifespan 的地方（默认也非 None，但我们
    # 的自定义 lifespan 必须被采用——通过 startup 抓 loop + shutdown 关 store 验证）。
    assert app.router.lifespan_context is not None


def test_lifespan_grabs_loop_and_closes_store(tmp_path):
    """lifespan startup 兜底抓 loop；shutdown 关闭 RunsStore（finalizer，无泄漏）。

    用 TestClient 作 context manager 触发 lifespan startup+shutdown。
    """
    app = _make_app(tmp_path)
    store = app.state.store
    assert store is not None
    with TestClient(app) as client:
        # startup 已跑：loop 兜底被抓（非 None）
        assert app.state.loop is not None
        r = client.get("/api/nodes")
        assert r.status_code == 200
    # 退出 with → lifespan shutdown 跑 → store 连接已关闭。
    # 关闭后的 sqlite 连接再操作会抛 ProgrammingError（证明确实关了）。
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        store.get("anything")


def test_ws_still_works_after_lifespan_migration(tmp_path):
    """D2-T4 死锁修复自证：WS 仍能流事件到 done（ws_run 内抓 running loop 未被删）。"""
    import json
    app = _make_app(tmp_path)
    client = TestClient(app)
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={"blueprint": loom, "inst": "BTC-USDT-SWAP",
                                       "bar": "1m", "playback_ms": 0, "ws_wait_ms": 3000})
    run_id = r.json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        seen_done = False
        for _ in range(500):
            ev = ws.receive_json()
            if ev["type"] == "done":
                seen_done = True
                assert ev["report"]["bars"] == 30
                break
        assert seen_done, "WS never reached 'done' — loop grab in ws_run may be broken"
