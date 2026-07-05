"""Eval/Evolve API 端点测试（D4-T6）—— /api/eval/* + /api/evolve。

全程零配额：LLM 蓝图用注入的 offline-safe scripted fake LLM（纯本地函数、无 socket，
`offline=True` 使配额守门放行）；确定性蓝图（ema_cross）回测本身零 LLM。

覆盖每端点 happy path + 错误路径：
- /api/eval/fidelity：从已完成 run 取 fills+candles 重放；run 不存在 404 / 未完成 409
- /api/eval/leaderboard：三基线 + 可选蓝图；含 LLM 蓝图但非 offline → 409 守门
- /api/eval/ablation：三臂图手术；含 LLM 蓝图非 offline → 409；offline 空录制 miss → 4xx 干净
- /api/evolve：谱系树；规模超限 → 422；含 LLM 蓝图非 offline → 409
- JSON 安全（inf 不泄漏）；蓝图不存在/不可编译 → 422
"""
import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alphaloom.nodes  # noqa: F401  触发全部内置节点注册
from alphaloom.api.app import create_app
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# offline-safe scripted fake LLM（纯本地——零 socket、零配额；offline=True 使守门放行）
# --------------------------------------------------------------------------- #
def _wrap(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _try_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


class OfflineCommitteeLLM:
    """委员会三角色的确定性 fake LLM（消融端点用）。offline=True → 配额守门放行。

    策略师追动能（close>_HOT 看涨）；风控官在过热区 veto；主席尊重 veto/否则跟随。
    role_calls 计数供零配额自证（other==0 = 全部本地路由，无请求可能触网）。
    """

    offline = True   # 配额守门：本地剧本 = 零配额 = 视同 offline 安全

    _HOT = 101.5

    def __init__(self):
        self.role_calls = {"strategist": 0, "risk": 0, "chair": 0, "other": 0}

    def chat(self, messages, tools=None, temperature=0.2, **params):
        system = next((str(m.get("content", "")) for m in messages
                       if m.get("role") == "system"), "").lower()
        user_objs = [o for o in (_try_json(m.get("content", "")) for m in messages
                                 if m.get("role") == "user") if isinstance(o, dict)]

        def _market():
            for o in user_objs:
                if isinstance(o.get("market"), dict):
                    return o["market"]
                if "close" in o:
                    return o
            return {}

        def _part(key):
            for o in user_objs:
                if isinstance(o.get(key), dict):
                    return o[key]
            return {}

        close = float(_market().get("close", 0.0) or 0.0)
        if "committee's strategist" in system:
            self.role_calls["strategist"] += 1
            if close > self._HOT:
                return _wrap(json.dumps({"side": "long", "rationale": "momentum",
                                         "confidence": 0.9}))
            return _wrap(json.dumps({"side": "hold", "rationale": "no edge",
                                     "confidence": 0.4}))
        if "committee's risk officer" in system:
            self.role_calls["risk"] += 1
            strat = _part("strategist")
            if strat.get("side") in ("long", "short") and close > self._HOT:
                return _wrap(json.dumps({"veto": True, "concern": "bubble risk",
                                         "confidence": 0.2}))
            return _wrap(json.dumps({"veto": False, "concern": "ok", "confidence": 0.7}))
        if "committee chair" in system:
            self.role_calls["chair"] += 1
            strat, risk = _part("strategist"), _part("risk_officer")
            if bool(risk.get("veto")):
                return _wrap(json.dumps({"side": "hold", "rationale": "defer to veto",
                                         "confidence": 0.1}))
            side = strat.get("side", "hold")
            conf = float(strat.get("confidence", 0.5) or 0.5)
            return _wrap(json.dumps({"side": side, "rationale": f"follow {side}",
                                     "confidence": round(conf * 0.9, 3)}))
        self.role_calls["other"] += 1
        return _wrap(json.dumps({"side": "hold", "rationale": "unknown",
                                 "confidence": 0.0}))


class OfflineMutationLLM:
    """进化变异算子的确定性 fake LLM（进化端点用）。offline=True → 守门放行。

    只做参数变异（ema_fast.period 微调）——零结构风险，编译恒过。剧本循环取用
    （evolve 每孩子 ≤1 chat）。calls 计数供零配额自证。
    """

    offline = True

    def __init__(self, periods=(8, 20, 10, 34)):
        self.periods = list(periods)
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.2, **params):
        self.calls += 1
        p = self.periods[(self.calls - 1) % len(self.periods)]
        return _wrap(json.dumps({"summary": f"tune fast ema to {p}",
                                 "set_params": {"ema_fast": {"period": p}}}))


class LiveLikeLLM:
    """模拟"非 offline 的真实 LLM 客户端"——offline=False → 配额守门必须拒绝。

    chat 被调用即 AssertionError：守门若失灵放它进来跑 LLM 蓝图就会烧真配额，
    此断言正是"守门真的拦住了"的自证（端点该在调 chat 前就 409）。
    """

    offline = False

    def chat(self, *a, **k):
        raise AssertionError("live LLM must never be invoked by an eval endpoint "
                             "(quota gate should have returned 409 first)")


# --------------------------------------------------------------------------- #
# 蓝图夹具
# --------------------------------------------------------------------------- #
def _ema_loom():
    return json.loads((REPO / "blueprints" / "ema_cross.loom").read_text(encoding="utf-8"))


def _committee_loom():
    """镜像 headline 委员会结构的小蓝图（feed→atr→committee→sizer→risk→exec）。
    含 committee（LLM 节点）——消融端点的 no_risk_officer 臂目标。"""
    return {
        "id": "abl_api_v1", "name": "ablation-api-bp",
        "nodes": [
            {"id": "feed", "type": "candle_feed",
             "params": {"inst": "TEST", "bar": "1m"}},
            {"id": "atr", "type": "atr", "params": {"period": 14}},
            {"id": "committee", "type": "committee", "params": {"atr_mult": 2.0}},
            {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.05}},
            {"id": "risk", "type": "risk_gate",
             "params": {"max_qty": 100000.0, "require_stop": True}},
            {"id": "exec", "type": "execute_order", "params": {}},
        ],
        "edges": [
            {"from": "feed.out", "to": "atr.candle"},
            {"from": "feed.out", "to": "committee.candle"},
            {"from": "atr.value", "to": "committee.atr"},
            {"from": "committee.signal", "to": "sizer.signal"},
            {"from": "feed.out", "to": "sizer.candle"},
            {"from": "sizer.sized", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "exec.signal"},
        ],
        "meta": {},
    }


def _crash_candles():
    """横盘→冲过热区→暴跌→横盘：过热区风控官 veto（护栏正价值剧本）。"""
    def _bar(i, o, c, spread=1.0):
        return {"ts": i * 60_000, "open": round(o, 6),
                "high": round(max(o, c) + spread, 6),
                "low": round(min(o, c) - spread, 6),
                "close": round(c, 6), "volume": 1.0}
    closes = [100 + 0.2 * ((i % 3) - 1) for i in range(20)]
    closes += [103.0, 103.0]
    closes += [103.0 - 6.0 * k for k in range(1, 10)]
    closes += [49.0] * 15
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(_bar(i, prev, c))
        prev = c
    return out


# --------------------------------------------------------------------------- #
# App 夹具
# --------------------------------------------------------------------------- #
def _make_app(tmp_path, *, llm_client=None, candles=None, inst="BTC-USDT-SWAP"):
    db = SQLiteMarketData(tmp_path / "demo.sqlite")
    if candles is None:
        up = gen_candles(200, seed=11, trend=0.002)
        down = gen_candles(150, seed=12, trend=-0.002,
                           start_ts=up[-1]["ts"] + 60_000,
                           start_price=up[-1]["close"])
        candles = up + down
    db.insert_candles(inst, "1m", candles)
    db.close()
    app = create_app(
        db_path=tmp_path / "demo.sqlite", runs_db=tmp_path / "runs.sqlite",
        record_dir=tmp_path, blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=tmp_path / "user_bp", frontend_dist=tmp_path / "no_dist",
        llm_client=llm_client)
    return TestClient(app)


def _await_run(client, run_id, timeout_s=15):
    for _ in range(int(timeout_s / 0.1)):
        row = client.get(f"/api/runs/{run_id}").json()
        if row["status"] != "running":
            return row
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _complete_ema_run(client, inst="BTC-USDT-SWAP"):
    """跑一个 ema_cross run 到 completed，返回 run_id（fidelity 端点的输入）。"""
    r = client.post("/api/runs", json={"blueprint": _ema_loom(), "inst": inst,
                                       "bar": "1m", "playback_ms": 0})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    row = _await_run(client, run_id)
    assert row["status"] == "completed", row.get("error")
    return run_id


# =========================================================================== #
# /api/eval/fidelity
# =========================================================================== #
def test_fidelity_happy_path_from_completed_run(tmp_path):
    client = _make_app(tmp_path)
    run_id = _complete_ema_run(client)
    r = client.post("/api/eval/fidelity", json={"run_id": run_id})
    assert r.status_code == 200, r.text
    body = r.json()
    # LadderReport.to_dict 形状：四档 + optimism_gap
    assert [lv["level"] for lv in body["levels"]] == ["L0", "L1", "L2", "L3"]
    assert "optimism_gap" in body
    # 单调性契约（测谎仪心脏）：net_pnl L0 ≥ L1 ≥ L2 ≥ L3
    pnl = {lv["level"]: lv["net_pnl"] for lv in body["levels"]}
    assert pnl["L0"] >= pnl["L1"] >= pnl["L2"] >= pnl["L3"] - 1e-6
    assert body["optimism_gap"] == pytest.approx(pnl["L0"] - pnl["L3"])
    # JSON 安全：无 Infinity 泄漏（profit_factor inf → None）
    assert "Infinity" not in json.dumps(body)


def test_fidelity_unknown_run_404(tmp_path):
    client = _make_app(tmp_path)
    r = client.post("/api/eval/fidelity", json={"run_id": "does-not-exist"})
    assert r.status_code == 404


def test_fidelity_unfinished_run_409(tmp_path):
    """未完成的 run（running）→ 409：没有可重放的成交序列。直接向 RunsStore 插入
    一条 running 记录以精确命中 409 路径（编译失败在 /api/runs 就 422 拿不到 run）。"""
    client = _make_app(tmp_path)
    store = client.app.state.store
    store.create("stuck-running", "ema_cross_v1", "{}",
                 json.dumps({"inst": "BTC-USDT-SWAP", "bar": "1m"}), 0)
    r = client.post("/api/eval/fidelity", json={"run_id": "stuck-running"})
    assert r.status_code == 409, r.text
    assert "completed" in r.text.lower() or "running" in r.text.lower()


# =========================================================================== #
# /api/eval/leaderboard
# =========================================================================== #
def test_leaderboard_three_baselines_plus_optional_blueprint(tmp_path):
    client = _make_app(tmp_path)
    body = {"inst": "BTC-USDT-SWAP", "bar": "1m",
            "blueprint": _ema_loom(), "blueprint_name": "my_ema"}
    r = client.post("/api/eval/leaderboard", json=body)
    assert r.status_code == 200, r.text
    board = r.json()
    names = {row["name"] for row in board["rows"]}
    assert {"baseline_buy_hold", "baseline_ema_default",
            "baseline_random", "my_ema"} <= names
    # 已按 return_pct 降序
    rets = [row["return_pct"] for row in board["rows"]]
    assert rets == sorted(rets, reverse=True)
    assert "Infinity" not in json.dumps(board)


def test_leaderboard_baselines_only_no_blueprint(tmp_path):
    client = _make_app(tmp_path)
    r = client.post("/api/eval/leaderboard",
                    json={"inst": "BTC-USDT-SWAP", "bar": "1m"})
    assert r.status_code == 200, r.text
    names = {row["name"] for row in r.json()["rows"]}
    assert names == {"baseline_buy_hold", "baseline_ema_default", "baseline_random"}


def test_leaderboard_with_valid_window_reports_generalization_gap(tmp_path):
    client = _make_app(tmp_path)
    body = {"inst": "BTC-USDT-SWAP", "bar": "1m",
            "start_ms": 0, "end_ms": 199 * 60_000,
            "valid_start_ms": 200 * 60_000, "valid_end_ms": None,
            "blueprint": _ema_loom(), "blueprint_name": "my_ema"}
    r = client.post("/api/eval/leaderboard", json=body)
    assert r.status_code == 200, r.text
    rows = {row["name"]: row for row in r.json()["rows"]}
    # 有验证窗 → generalization_gap 非 None、in_sample_only False
    assert rows["my_ema"]["generalization_gap"] is not None
    assert rows["my_ema"]["in_sample_only"] is False


def test_leaderboard_llm_blueprint_without_offline_gated_409(tmp_path):
    """含 LLM 节点的蓝图 + 非 offline 客户端 → 409 守门（不烧真配额）。"""
    client = _make_app(tmp_path, llm_client=LiveLikeLLM(), inst="TEST",
                       candles=_crash_candles())
    body = {"inst": "TEST", "bar": "1m", "blueprint": _committee_loom(),
            "blueprint_name": "committee_bp"}
    r = client.post("/api/eval/leaderboard", json=body)
    assert r.status_code == 409, r.text
    assert "offline" in r.text.lower() or "llm" in r.text.lower()


def test_leaderboard_bad_blueprint_422(tmp_path):
    client = _make_app(tmp_path)
    r = client.post("/api/eval/leaderboard",
                    json={"inst": "BTC-USDT-SWAP", "bar": "1m",
                          "blueprint": {"garbage": True}})
    assert r.status_code == 422, r.text


# =========================================================================== #
# /api/eval/ablation
# =========================================================================== #
def test_ablation_happy_path_offline_scripted_llm(tmp_path):
    llm = OfflineCommitteeLLM()
    client = _make_app(tmp_path, llm_client=llm, inst="TEST",
                       candles=_crash_candles())
    body = {"blueprint": _committee_loom(), "inst": "TEST", "bar": "1m",
            "start_ms": 0, "end_ms": None}
    r = client.post("/api/eval/ablation", json=body)
    assert r.status_code == 200, r.text
    rep = r.json()
    # full + no_risk_officer 两臂（无 RAG → no_rag 臂应被端点安全跳过或不含）
    arms = {a["arm"] for a in rep["arms"]}
    assert "full" in arms and "no_risk_officer" in arms
    assert rep["guardrail_value"] is not None
    assert "Infinity" not in json.dumps(rep)
    # 零配额自证：所有 LLM 请求本地路由
    assert llm.role_calls["other"] == 0
    assert llm.role_calls["strategist"] > 0


def test_ablation_llm_blueprint_without_offline_gated_409(tmp_path):
    client = _make_app(tmp_path, llm_client=LiveLikeLLM(), inst="TEST",
                       candles=_crash_candles())
    body = {"blueprint": _committee_loom(), "inst": "TEST", "bar": "1m"}
    r = client.post("/api/eval/ablation", json=body)
    assert r.status_code == 409, r.text


def test_ablation_offline_replay_miss_is_clean_4xx_not_500(tmp_path):
    """offline 真实 RecordingLLMClient + 空录制库 → 消融臂首个委员会调用即
    ReplayMissError；端点须干净 4xx（带解释），不是 500 栈。"""
    from alphaloom.llm.recording import RecordingLLMClient

    def _junk_transport(request):
        return _wrap("{}")  # 不该被调用（offline）

    llm = RecordingLLMClient(_junk_transport, tmp_path / f"empty_{uuid.uuid4().hex}.sqlite",
                             model="test-model", offline=True)
    client = _make_app(tmp_path, llm_client=llm, inst="TEST",
                       candles=_crash_candles())
    body = {"blueprint": _committee_loom(), "inst": "TEST", "bar": "1m"}
    r = client.post("/api/eval/ablation", json=body)
    assert r.status_code in (409, 422), r.text     # 干净 4xx
    assert r.status_code < 500
    assert "replay" in r.text.lower() or "record" in r.text.lower() or "miss" in r.text.lower()


def test_ablation_no_committee_blueprint_422(tmp_path):
    """无 committee 节点的蓝图 → 消融无对象 → 4xx（arm_blueprint ValueError）。"""
    client = _make_app(tmp_path)
    r = client.post("/api/eval/ablation",
                    json={"blueprint": _ema_loom(), "inst": "BTC-USDT-SWAP",
                          "bar": "1m"})
    assert r.status_code in (409, 422), r.text
    assert r.status_code < 500


# =========================================================================== #
# /api/evolve
# =========================================================================== #
def test_evolve_happy_path_param_only(tmp_path):
    """确定性 ema_cross 种子 + offline 变异算子：谱系树 + winner。回测零 LLM，
    唯一 LLM 是变异算子（offline scripted，零配额）。"""
    llm = OfflineMutationLLM()
    client = _make_app(tmp_path)
    client.app.state.llm = llm   # 注入进化用变异算子（offline-safe）
    body = {"blueprint": _ema_loom(), "inst": "BTC-USDT-SWAP", "bar": "1m",
            "train_start_ms": 0, "train_end_ms": 199 * 60_000,
            "valid_start_ms": 200 * 60_000, "valid_end_ms": None,
            "population": 2, "generations": 2, "param_only": True}
    r = client.post("/api/evolve", json=body)
    assert r.status_code == 200, r.text
    g = r.json()
    assert set(g) >= {"nodes", "winner", "param_only", "population", "generations"}
    assert g["param_only"] is True
    assert any(n["id"] == "g0_seed" for n in g["nodes"])
    assert g["winner"]["id"] in {n["id"] for n in g["nodes"] if n["survived"]}
    assert "Infinity" not in json.dumps(g)
    assert llm.calls > 0


def test_evolve_scale_over_limit_422(tmp_path):
    client = _make_app(tmp_path)
    client.app.state.llm = OfflineMutationLLM()
    base = {"blueprint": _ema_loom(), "inst": "BTC-USDT-SWAP", "bar": "1m",
            "train_start_ms": 0, "train_end_ms": 199 * 60_000,
            "valid_start_ms": 200 * 60_000, "valid_end_ms": None}
    for over in ({"population": 5}, {"generations": 4}, {"population": 0}):
        r = client.post("/api/evolve", json={**base, **over})
        assert r.status_code == 422, (over, r.text)


def test_evolve_overlapping_windows_422(tmp_path):
    client = _make_app(tmp_path)
    client.app.state.llm = OfflineMutationLLM()
    body = {"blueprint": _ema_loom(), "inst": "BTC-USDT-SWAP", "bar": "1m",
            "train_start_ms": 0, "train_end_ms": 199 * 60_000,
            "valid_start_ms": 100 * 60_000, "valid_end_ms": None,   # 与 train 重叠
            "population": 2, "generations": 1}
    r = client.post("/api/evolve", json=body)
    assert r.status_code == 422, r.text


def test_evolve_llm_blueprint_without_offline_gated_409(tmp_path):
    """含 LLM 节点的种子蓝图 + 非 offline 变异算子 → 409（回测臂会烧真配额）。"""
    client = _make_app(tmp_path, llm_client=LiveLikeLLM(), inst="TEST",
                       candles=_crash_candles())
    body = {"blueprint": _committee_loom(), "inst": "TEST", "bar": "1m",
            "train_start_ms": 0, "train_end_ms": 30 * 60_000,
            "valid_start_ms": 31 * 60_000, "valid_end_ms": None,
            "population": 1, "generations": 1}
    r = client.post("/api/evolve", json=body)
    assert r.status_code == 409, r.text


def test_evolve_bad_blueprint_422(tmp_path):
    client = _make_app(tmp_path)
    client.app.state.llm = OfflineMutationLLM()
    r = client.post("/api/evolve",
                    json={"blueprint": {"nope": 1}, "inst": "BTC-USDT-SWAP",
                          "bar": "1m", "train_start_ms": 0, "train_end_ms": 100,
                          "valid_start_ms": 200, "valid_end_ms": 300})
    assert r.status_code == 422, r.text


# =========================================================================== #
# /api/eval/scorecard —— 前端把已算好的证据碎片拼成权威综合分（唯一评分实现）
# =========================================================================== #
def test_scorecard_endpoint_aggregates_evidence(tmp_path):
    """记分卡端点：前端把 run 报告 + 保真度阶梯拼进来 → 权威 Scorecard。评分数学
    只在后端有一份实现（tanh 压缩 / 权重 / 缺证据保守分），前端绝不重实现以防漂移。"""
    client = _make_app(tmp_path)
    run_id = _complete_ema_run(client)
    report = client.get(f"/api/runs/{run_id}").json()["report"]
    ladder = client.post("/api/eval/fidelity", json={"run_id": run_id}).json()
    r = client.post("/api/eval/scorecard", json={
        "train_report": {"summary": report["summary"],
                         "certificate": report["certificate"]},
        "ladder": ladder})
    assert r.status_code == 200, r.text
    card = r.json()
    assert 0.0 <= card["composite"] <= 100.0
    assert set(card["components"]) == {"valid_performance", "generalization",
                                       "fidelity", "determinism"}
    assert card["evidence_coverage"]["fidelity_ladder"] is True
    assert card["evidence_coverage"]["valid_window"] is False   # 未给 valid
    assert card["fidelity"] == ladder                           # 原证据随卡携带
    assert "Infinity" not in json.dumps(card)


def test_scorecard_endpoint_missing_train_report_422(tmp_path):
    client = _make_app(tmp_path)
    r = client.post("/api/eval/scorecard", json={"ladder": {"levels": []}})
    assert r.status_code == 422, r.text


# =========================================================================== #
# 端点不因缺 LLM 客户端而崩（确定性蓝图零 LLM 照跑）
# =========================================================================== #
def test_deterministic_endpoints_work_without_llm_client(tmp_path):
    """无注入 llm（app.state.llm=None）：确定性蓝图的 fidelity/leaderboard 照跑。"""
    client = _make_app(tmp_path)            # 不注入 llm_client
    assert client.app.state.llm is None
    run_id = _complete_ema_run(client)
    assert client.post("/api/eval/fidelity", json={"run_id": run_id}).status_code == 200
    assert client.post("/api/eval/leaderboard",
                       json={"inst": "BTC-USDT-SWAP", "bar": "1m"}).status_code == 200


# =========================================================================== #
# C1（Critical）端到端：沙箱自定义节点绕过 LLM 配额守门（网络可达刷爆真配额）
#
# 攻击链：/api/nodes/custom 注册一个声称 llm_calls_per_bar=0 但 on_bar 偷调
# ctx.llm.chat 的沙箱节点 → 放进类型合法蓝图打 /api/eval/leaderboard（非 offline
# 客户端）。修复前：证书报 0 → 守门放行 → 真 LLM 被调、返回 200（刷爆配额）。
# 修复后（两条防御）：#1 运行期剥离沙箱节点的 ctx.llm（偷调即 raise）；#2 守门不
# 信任沙箱节点自证——含沙箱节点的蓝图非 offline 即 409（chat 根本没机会被调）。
# =========================================================================== #
class _SpyLLM:
    """记录被调次数的假 LLM（offline=False）——守门/剥离失灵时 calls>0 即自证绕过。"""
    offline = False

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.2, **params):
        self.calls += 1
        return {"choices": [{"message": {"content":
                json.dumps({"side": "long", "rationale": "x", "confidence": 0.9})}}]}


# 偷调 LLM 的恶意沙箱节点源码：cost 谎报 0，on_bar 却 ctx.llm.chat（AST 禁 .format，
# 故用 dict 直构 messages）。输出 SIGNAL 以能连进类型合法蓝图（thief→sizer→risk→exec）。
_THIEF_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{tp}", category="decision",
      inputs={{"candle": PinType.CANDLE}}, outputs={{"signal": PinType.SIGNAL}},
      cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True))
class ThiefNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        ctx.llm.chat([{{"role": "user", "content": "burn quota"}}])
        return {{"signal": {{"side": "long", "qty": 0.0, "stop": None, "reason": "stolen"}}}}
'''

# 纯净（不偷调）的沙箱节点：仅用于验证守门 #2 兜底——即便节点无害，含沙箱节点
# 的蓝图也不受信（证书信任根在沙箱节点在场时失效，深度防御拒绝）。
_PURE_SANDBOX_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="{tp}", category="indicator",
      inputs={{"candle": PinType.CANDLE}}, outputs={{"value": PinType.SERIES}},
      cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True))
class PureNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {{"value": float(inputs["candle"]["close"]) * 2.0}}
'''


def _thief_blueprint(tp):
    """类型合法蓝图：feed → <thief:SIGNAL> → sizer → risk_gate → exec（过风控）。"""
    return {
        "id": "thief_bp_v1", "name": "thief-bp",
        "nodes": [
            {"id": "feed", "type": "candle_feed",
             "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
            {"id": "thief", "type": tp, "params": {}},
            {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
            {"id": "risk", "type": "risk_gate",
             "params": {"max_qty": 100.0, "require_stop": True}},
            {"id": "exec", "type": "execute_order", "params": {}},
        ],
        "edges": [
            {"from": "feed.out", "to": "thief.candle"},
            {"from": "thief.signal", "to": "sizer.signal"},
            {"from": "feed.out", "to": "sizer.candle"},
            {"from": "sizer.sized", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "exec.signal"},
        ],
        "meta": {},
    }


def test_c1_sandbox_node_quota_bypass_is_blocked_end_to_end(tmp_path):
    """C1 端到端 RED→GREEN：注册偷调 LLM 的沙箱节点 → 打 leaderboard（非 offline
    SpyLLM）→ 必须 409 拒绝，且 SpyLLM.chat 从未被调（calls==0）。修复前此路返回
    200 且 calls>0（真配额被刷）。"""
    from alphaloom.nodes.registry import REGISTRY
    spy = _SpyLLM()
    client = _make_app(tmp_path, llm_client=spy)
    tp = f"thief_{uuid.uuid4().hex[:8]}"
    try:
        reg = client.post("/api/nodes/custom",
                          json={"source": _THIEF_SOURCE.format(tp=tp)})
        assert reg.status_code == 200, reg.text     # 沙箱允许它注册（普通属性访问不拦）
        body = {"inst": "BTC-USDT-SWAP", "bar": "1m",
                "blueprint": _thief_blueprint(tp), "blueprint_name": "thief"}
        r = client.post("/api/eval/leaderboard", json=body)
        assert r.status_code == 409, r.text          # 守门 #2：含沙箱节点即拒（非 offline）
        assert spy.calls == 0                          # 真 LLM 绝对没被偷调
    finally:
        REGISTRY.pop(tp, None)


def test_c1_pure_sandbox_node_also_distrusted_by_gate(tmp_path):
    """守门 #2 兜底：即便沙箱节点无害（不偷调），含它的蓝图也不受信——非 offline
    即 409（证书信任根在沙箱节点在场时失效）。"""
    from alphaloom.nodes.registry import REGISTRY
    spy = _SpyLLM()
    client = _make_app(tmp_path, llm_client=spy)
    tp = f"pure_{uuid.uuid4().hex[:8]}"
    try:
        reg = client.post("/api/nodes/custom",
                          json={"source": _PURE_SANDBOX_SOURCE.format(tp=tp)})
        assert reg.status_code == 200, reg.text
        # 蓝图：feed → pure(SERIES) 悬空 + 正常 ema_cross 主链（pure 节点在场即触发守门）
        bp = _ema_loom()
        bp["nodes"].append({"id": "pure", "type": tp, "params": {}})
        bp["edges"].append({"from": "feed.out", "to": "pure.candle"})
        r = client.post("/api/eval/leaderboard",
                        json={"inst": "BTC-USDT-SWAP", "bar": "1m",
                              "blueprint": bp, "blueprint_name": "with_pure"})
        assert r.status_code == 409, r.text
        assert "sandbox" in r.text.lower()
    finally:
        REGISTRY.pop(tp, None)


def test_c1_sandbox_node_blueprint_allowed_when_offline(tmp_path):
    """守门只拦非 offline：含沙箱节点的蓝图在 offline 客户端下放行（零配额安全），
    且运行期剥离仍生效（偷调节点即便放行也调不到真 llm——纵深防御）。"""
    from alphaloom.nodes.registry import REGISTRY

    class _OfflineSpy(_SpyLLM):
        offline = True

    spy = _OfflineSpy()
    client = _make_app(tmp_path, llm_client=spy)
    tp = f"pure2_{uuid.uuid4().hex[:8]}"
    try:
        client.post("/api/nodes/custom",
                    json={"source": _PURE_SANDBOX_SOURCE.format(tp=tp)})
        bp = _ema_loom()
        bp["nodes"].append({"id": "pure", "type": tp, "params": {}})
        bp["edges"].append({"from": "feed.out", "to": "pure.candle"})
        r = client.post("/api/eval/leaderboard",
                        json={"inst": "BTC-USDT-SWAP", "bar": "1m",
                              "blueprint": bp, "blueprint_name": "with_pure"})
        assert r.status_code == 200, r.text          # offline → 放行
        assert spy.calls == 0                          # 纯节点不调 llm
    finally:
        REGISTRY.pop(tp, None)
