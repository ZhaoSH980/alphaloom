"""Copilot / custom-node API 端点 + run mode=replay + LLM 注入（AlphaLoom D3 Task 8）。

覆盖：
- POST /api/copilot/blueprint：NL → {loom, notes}，loom 可 compile；
- POST /api/copilot/explain：loom → 非空叙述；
- POST /api/copilot/optimize：loom(+report) → {loom, diff, notes}；
- POST /api/nodes/custom：合法源码沙箱注册后 /api/nodes 出现该 type；恶意源码 → 422 + reason；
- run mode=replay：带 LLM 节点的蓝图，服务注入的 RecordingLLMClient 让 LLM 节点跑通（不联网）；
- LLM 注入接缝：service 真拿到 llm；ALPHALOOM_OFFLINE 录制 miss → run failed（不崩服务）。

LLM 注入接缝（测试用）：create_app 接受预构建的 llm_client（RecordingLLMClient 包一个
fake transport，不联网）。fake transport 对 copilot 请求返回 loom JSON、对 analyst 请求
返回决策 JSON；RecordingLLMClient 录制进临时 sqlite，同 prompt 命中缓存。
"""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import alphaloom.nodes  # noqa: F401  触发全部内置节点注册
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.llm.recording import RecordingLLMClient
from alphaloom.nodes.registry import REGISTRY
from pathlib import Path
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]


# 一个合法蓝图：feed → ema×2 → cross → sizer → risk_gate → execute（下单过风控）。
_GOOD_LOOM = {
    "id": "gen_api_v1",
    "name": "generated-api",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
        {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
        {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
        {"id": "atr", "type": "atr", "params": {"period": 14}},
        {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
        {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
        {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": True}},
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
        {"from": "sizer.sized", "to": "risk.signal"},
        {"from": "risk.stamped", "to": "exec.signal"},
    ],
    "meta": {},
}

# 带 LLM 节点的蓝图：llm_analyst 产 signal → sizer → risk_gate → execute。
_LLM_LOOM = {
    "id": "llm_run_v1",
    "name": "llm-run",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
        {"id": "atr", "type": "atr", "params": {"period": 14}},
        {"id": "analyst", "type": "llm_analyst", "params": {"persona": "trend", "atr_mult": 2.0}},
        {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
        {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": True}},
        {"id": "exec", "type": "execute_order", "params": {}},
    ],
    "edges": [
        {"from": "feed.out", "to": "atr.candle"},
        {"from": "feed.out", "to": "analyst.candle"},
        {"from": "atr.value", "to": "analyst.atr"},
        {"from": "analyst.signal", "to": "sizer.signal"},
        {"from": "feed.out", "to": "sizer.candle"},
        {"from": "sizer.sized", "to": "risk.signal"},
        {"from": "risk.stamped", "to": "exec.signal"},
    ],
    "meta": {},
}


class _FakeTransport:
    """按请求内容路由 canned 响应，模拟 OpenAI 兼容端点（不联网）。

    - copilot blueprint/optimize 请求（system 提示含 loom schema）→ 返回 loom JSON；
    - copilot explain 请求 → 返回叙述文本；
    - llm_analyst 请求 → 返回决策 JSON。
    calls 记录每次真实"网络"请求（RecordingLLMClient 缓存命中时不到这里）。
    """

    def __init__(self, loom=None):
        self.calls = []
        self._loom = loom or _GOOD_LOOM

    def __call__(self, request):
        self.calls.append(request)
        system = ""
        for m in request.get("messages", []):
            if m.get("role") == "system":
                system = m.get("content", "")
                break
        if "translate" in system.lower() and "NODE CATALOG" in system:
            content = json.dumps(self._loom)          # text_to_blueprint
        elif "improve a trading strategy" in system:
            content = json.dumps(self._loom)          # optimize
        elif "tutor" in system.lower():
            content = "This blueprint follows signals and gates every order through risk."
        else:
            # llm_analyst / committee 决策
            content = json.dumps(
                {"side": "hold", "rationale": "flat market", "confidence": 0.5})
        return {"choices": [{"message": {"content": content}}]}


def _make_client(tmp_path, *, loom=None, offline=False, transport=None):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    up = gen_candles(120, seed=11, trend=0.002)
    db.insert_candles("BTC-USDT-SWAP", "1m", up)
    tr = transport if transport is not None else _FakeTransport(loom=loom)
    llm_client = RecordingLLMClient(
        tr, tmp_path / f"llm_{uuid.uuid4().hex}.sqlite", model="test-model", offline=offline)
    app = create_app(
        db_path=tmp_path / "demo.sqlite",
        runs_db=tmp_path / "runs.sqlite",
        record_dir=tmp_path,
        blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "user_bp",
        frontend_dist=tmp_path / "no_dist",
        llm_client=llm_client,
    )
    return TestClient(app), llm_client, tr


# --------------------------------------------------------------------------- #
# D2 API 零回归：create_app 不传 llm 参数仍能构造（现有 test_api_rest 调用形态）
# --------------------------------------------------------------------------- #
def test_create_app_without_llm_args_still_works(tmp_path):
    """D2 接缝守卫：不传 llm_client/llm_db 时 create_app 仍能构造（旧测试调用形态）。"""
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(30, seed=1))
    app = create_app(
        db_path=tmp_path / "demo.sqlite", runs_db=tmp_path / "runs.sqlite",
        record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "user_bp", frontend_dist=tmp_path / "no_dist")
    c = TestClient(app)
    assert c.get("/api/nodes").status_code == 200


# --------------------------------------------------------------------------- #
# Copilot 端点
# --------------------------------------------------------------------------- #
def test_copilot_blueprint_endpoint_returns_compilable_loom(tmp_path):
    client, _llm, _tr = _make_client(tmp_path)
    r = client.post("/api/copilot/blueprint",
                    json={"nl": "EMA cross trend follow routed through the risk gate"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "loom" in body and "notes" in body
    # 返回的 loom 经 /api/compile 复核真能编译过
    c2 = client.post("/api/compile", json={"blueprint": body["loom"], "bar": "1m"})
    assert c2.status_code == 200 and c2.json()["ok"] is True, c2.text
    # 自动布局进 meta.positions（前端读）
    assert body["loom"]["meta"]["positions"]


def test_copilot_explain_endpoint(tmp_path):
    client, _llm, _tr = _make_client(tmp_path)
    r = client.post("/api/copilot/explain", json={"blueprint": _GOOD_LOOM})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["explanation"], str) and body["explanation"].strip()


def test_copilot_optimize_endpoint_returns_diff(tmp_path):
    # optimize 变异图：atr period 14→20
    mutated = json.loads(json.dumps(_GOOD_LOOM))
    for n in mutated["nodes"]:
        if n["id"] == "atr":
            n["params"]["period"] = 20
    client, _llm, _tr = _make_client(tmp_path, loom=mutated)
    r = client.post("/api/copilot/optimize",
                    json={"blueprint": _GOOD_LOOM,
                          "report": {"summary": {"num_trades": 3, "win_rate": 0.33}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "loom" in body and "diff" in body and "notes" in body
    changed = {c["id"] for c in body["diff"].get("changed", [])}
    assert "atr" in changed


def test_copilot_optimize_without_report(tmp_path):
    """report 可选：不传 report 也能优化（默认空 report）。"""
    client, _llm, _tr = _make_client(tmp_path)
    r = client.post("/api/copilot/optimize", json={"blueprint": _GOOD_LOOM})
    assert r.status_code == 200, r.text
    assert "loom" in r.json() and "diff" in r.json()


def test_copilot_blueprint_generation_failure_maps_to_422(tmp_path):
    """LLM 始终产不出可编译图（transport 恒返回非 JSON）→ 422，不崩服务。"""
    class _JunkTransport:
        def __init__(self):
            self.calls = []

        def __call__(self, request):
            self.calls.append(request)
            return {"choices": [{"message": {"content": "sorry, I cannot"}}]}

    client, _llm, _tr = _make_client(tmp_path, transport=_JunkTransport())
    r = client.post("/api/copilot/blueprint", json={"nl": "nonsense"})
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# custom-node 沙箱端点
# --------------------------------------------------------------------------- #
_LEGIT_SOURCE_TMPL = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{tp}", category="indicator",
      inputs={{"candle": PinType.CANDLE}}, outputs={{"value": PinType.SERIES}},
      params={{"factor": float}},
      cost=CostAnnotation(deterministic=True))
class CustomNode:
    def setup(self, params):
        self.factor = float(params.get("factor", 2.0))
    def on_bar(self, ctx, inputs):
        return {{"value": float(inputs["candle"]["close"]) * self.factor}}
'''


def test_custom_node_registers_and_appears_in_nodes(tmp_path):
    # 唯一 type（避免全局 REGISTRY 跨测试污染）
    tp = f"custom_api_{uuid.uuid4().hex[:8]}"
    client, _llm, _tr = _make_client(tmp_path)
    try:
        r = client.post("/api/nodes/custom",
                        json={"source": _LEGIT_SOURCE_TMPL.format(tp=tp)})
        assert r.status_code == 200, r.text
        assert r.json()["type"] == tp
        # /api/nodes 现在含该 type
        types = {n["type"] for n in client.get("/api/nodes").json()}
        assert tp in types
    finally:
        REGISTRY.pop(tp, None)   # 清理全局副作用


def test_custom_node_malicious_source_returns_422(tmp_path):
    client, _llm, _tr = _make_client(tmp_path)
    r = client.post("/api/nodes/custom", json={"source": "import os\n"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "import_denied"


def test_custom_node_bad_pin_type_returns_422_and_nodes_stays_healthy(tmp_path):
    """T8 审查 carryover #9②：畸形节点（outputs 值是 list 而非真 PinType）此前能
    注册成功，随后让 GET /api/nodes 对所有后续调用者 500（进程级 REGISTRY 持久
    污染，网络可达跨用户 DoS）。修复后：POST 本身 422 reason=bad_pin_type，且
    紧随其后的 GET /api/nodes 仍 200（未被畸形节点污染）。"""
    tp = f"bad_pin_{uuid.uuid4().hex[:8]}"
    src = f'''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{tp}", category="indicator",
      inputs={{"candle": PinType.CANDLE}}, outputs={{"v": [PinType.SERIES]}},
      cost=CostAnnotation(deterministic=True))
class BadPinNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {{"v": 1.0}}
'''
    client, _llm, _tr = _make_client(tmp_path)
    try:
        r = client.post("/api/nodes/custom", json={"source": src})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["reason"] == "bad_pin_type"
        assert tp not in REGISTRY   # 畸形节点绝不注册

        # 复现点：紧随其后的 GET /api/nodes 必须仍 200（不被畸形节点污染成 500）
        r2 = client.get("/api/nodes")
        assert r2.status_code == 200, r2.text
        types = {n["type"] for n in r2.json()}
        assert tp not in types
    finally:
        REGISTRY.pop(tp, None)


def test_custom_node_forge_risk_stamp_returns_422(tmp_path):
    """声明 RISK_STAMPED_SIGNAL 输出的沙箱节点 → 422 reason=forge_risk_stamp。"""
    tp = f"fake_gate_{uuid.uuid4().hex[:8]}"
    src = f'''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{tp}", category="decision",
      inputs={{"signal": PinType.SIGNAL}},
      outputs={{"stamped": PinType.RISK_STAMPED_SIGNAL}},
      cost=CostAnnotation(deterministic=True))
class FakeGate:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {{"stamped": dict(inputs["signal"])}}
'''
    client, _llm, _tr = _make_client(tmp_path)
    try:
        r = client.post("/api/nodes/custom", json={"source": src})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["reason"] == "forge_risk_stamp"
        assert tp not in REGISTRY   # 伪造盖章绝不注册
    finally:
        REGISTRY.pop(tp, None)


# --------------------------------------------------------------------------- #
# run mode=replay：带 LLM 节点的蓝图，服务注入的 llm 让它跑通（不联网）
# --------------------------------------------------------------------------- #
def _await_run(client, run_id, timeout_s=15):
    for _ in range(int(timeout_s / 0.1)):
        row = client.get(f"/api/runs/{run_id}").json()
        if row["status"] != "running":
            return row
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_run_replay_mode_with_llm_node_completes(tmp_path):
    """LLM 注入接缝自证：service 从 create_app 拿到 RecordingLLMClient，
    _worker 绑 llm 到 run，llm_analyst 节点跑通（无 llm 会 RuntimeError → failed）。"""
    client, llm_client, tr = _make_client(tmp_path, loom=_GOOD_LOOM)
    r = client.post("/api/runs", json={
        "blueprint": _LLM_LOOM, "inst": "BTC-USDT-SWAP", "bar": "1m",
        "mode": "replay", "playback_ms": 0})
    assert r.status_code == 200, r.text
    row = _await_run(client, r.json()["run_id"])
    assert row["status"] == "completed", row.get("error")
    assert row["report"]["bars"] == 120
    # 服务真的调了注入的 llm（analyst 每根 bar 一次；缓存命中后 transport 只被打一次）
    assert llm_client.cache_hits + llm_client.cache_misses >= 1
    assert len(tr.calls) >= 1


def test_run_backtest_mode_default_also_binds_llm(tmp_path):
    """默认 mode=backtest 同样绑 llm（replay 语义 D3 先等同 backtest 但绑 llm）。"""
    client, _llm, _tr = _make_client(tmp_path, loom=_GOOD_LOOM)
    r = client.post("/api/runs", json={
        "blueprint": _LLM_LOOM, "inst": "BTC-USDT-SWAP", "bar": "1m",
        "playback_ms": 0})   # 不传 mode → 默认 backtest
    assert r.status_code == 200, r.text
    row = _await_run(client, r.json()["run_id"])
    assert row["status"] == "completed", row.get("error")


def test_run_offline_replay_miss_fails_run_not_service(tmp_path):
    """离线安全：ALPHALOOM_OFFLINE 语义（offline=True）录制 miss → run failed，
    服务不崩（ReplayMissError 被 engine 崩溃契约兜住，其他端点仍活）。"""
    # offline=True 的 RecordingLLMClient：空录制库 → 首个 analyst 调用即 miss
    client, _llm, _tr = _make_client(tmp_path, offline=True)
    r = client.post("/api/runs", json={
        "blueprint": _LLM_LOOM, "inst": "BTC-USDT-SWAP", "bar": "1m",
        "mode": "replay", "playback_ms": 0})
    assert r.status_code == 200, r.text
    row = _await_run(client, r.json()["run_id"])
    assert row["status"] == "failed"
    # 服务仍活：其他端点正常响应
    assert client.get("/api/nodes").status_code == 200


def test_non_llm_blueprint_runs_regardless_of_llm(tmp_path):
    """无 LLM 节点的蓝图（ema_cross）在 replay 模式下照常完成（不因 llm 存在而变化）。"""
    client, _llm, _tr = _make_client(tmp_path)
    loom = json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))
    r = client.post("/api/runs", json={
        "blueprint": loom, "inst": "BTC-USDT-SWAP", "bar": "1m",
        "mode": "replay", "playback_ms": 0})
    assert r.status_code == 200, r.text
    row = _await_run(client, r.json()["run_id"])
    assert row["status"] == "completed", row.get("error")
