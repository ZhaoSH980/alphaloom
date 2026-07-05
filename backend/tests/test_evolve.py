"""进化实验室测试（D4-T5）—— LLM 变异算子 + 编译守门 + 谱系树，全程确定性剧本，零配额。

覆盖锁定契约：
- 规模硬锁定：population≤4、generations≤3，超出 ValueError
- 变异 patch 应用深拷贝不污染父代；param_only 保险丝拒绝结构变异
- 编译守门 + fix_hint 回喂自修复（复用 copilot 模式）：ok / repaired / stillborn
- 硬护栏不可进化绕过：LLM 提议去掉 risk_gate → TYPE_MISMATCH → stillborn 如实入谱系
- 防过拟合泄漏：进化循环内任何个体不得碰 valid 窗（RecordingSource 断言：
  valid 窗查询只发生在终选、恰好一次、且是最后一次查询）
- 适应度：train 窗 return_pct 为主，num_trades==0 判 0 分（零交易=零证据）
- Genealogy to_dict JSON 安全 + winner generalization_gap

零配额自证：所有 LLM 都是 ScriptedMutationLLM（纯本地队列，无 socket），
剧本必须精确耗尽（多一次调用立刻 AssertionError）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import alphaloom.nodes  # noqa: F401  触发全部内置节点注册
from alphaloom.data.source import DataSource
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.evolve import Genealogy, evolve
from alphaloom.evolve.lab import (
    MAX_GENERATIONS,
    MAX_POPULATION,
    GenealogyNode,
    MutationRejected,
    apply_patch,
    fitness_of,
)
from alphaloom.graph.model import load_loom_file
from tests.fixtures.synth import gen_candles

_REPO = Path(__file__).resolve().parents[2]
_EMA_CROSS = _REPO / "blueprints" / "ema_cross.loom"

# 与 fake run_backtest 约定的窗口（monkeypatch 剧本测试用；毫秒任意但互不重叠）
TRAIN_W = (0, 999)
VALID_W = (2_000, 2_999)


# --------------------------------------------------------------------------- #
# 确定性剧本 LLM（纯本地队列——无 socket、无网络、零配额）
# --------------------------------------------------------------------------- #
class ScriptedMutationLLM:
    """按队列吐变异 patch 的确定性 fake LLM。

    - script 项为 dict（自动 json.dumps）或原始字符串（测非 JSON 回复路径）。
    - conversations 记录每次 chat 收到的完整 messages —— 测试据此断言
      fix_hint/拒绝原因真的被喂回了 LLM（自修复反馈环的自证）。
    - 队列耗尽再被调用 → 立刻 AssertionError：LLM 调用次数被剧本精确锁定。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.conversations: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature=0.2, **params):
        self.conversations.append([dict(m) for m in messages])
        assert self.script, "script exhausted - unexpected extra LLM call"
        item = self.script.pop(0)
        self.calls += 1
        content = item if isinstance(item, str) else json.dumps(item)
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class RecordingSource(DataSource):
    """包装真实 DataSource，记录每次 iter_candles 的查询窗口（泄漏测试探针）。"""

    def __init__(self, inner):
        self.inner = inner
        self.windows: list[tuple] = []

    def iter_candles(self, inst, bar, start_ms=None, end_ms=None):
        self.windows.append((start_ms, end_ms))
        return self.inner.iter_candles(inst, bar, start_ms, end_ms)


def _seed():
    return load_loom_file(_EMA_CROSS)


def _fake_run_backtest(bp, source, *, inst, bar, start_ms=None, end_ms=None, **kw):
    """确定性 fake：适应度 = ema_fast.period（valid 窗额外 -10 制造泛化差距）。

    只替换回测（适应度来源），编译守门仍是真 compile_blueprint —— 剧本测试
    锁的是进化循环结构，不是回测数学。
    """
    period = next(float(n.params["period"]) for n in bp.nodes if n.id == "ema_fast")
    ret = period - 10.0 if (start_ms, end_ms) == VALID_W else period
    return SimpleNamespace(
        run_id="fake", blueprint_id=bp.id, bars=10,
        summary={"net_pnl": ret, "return_pct": ret, "max_drawdown": 0.1,
                 "num_trades": 3, "win_rate": 0.5, "profit_factor": 1.2,
                 "halted": False, "halt_reason": None},
        certificate={}, equity_curve=[], fills=[])


@pytest.fixture
def fake_backtest(monkeypatch):
    monkeypatch.setattr("alphaloom.evolve.lab.run_backtest", _fake_run_backtest)


def _evolve(llm, **kw):
    defaults = dict(inst="TEST", bar="1m", train_window=TRAIN_W,
                    valid_window=VALID_W, llm=llm)
    defaults.update(kw)
    return evolve(_seed(), SQLiteMarketData(":memory:"), **defaults)


# =========================================================================== #
# 规模硬锁定（契约）
# =========================================================================== #
def test_scale_locks_are_hard():
    assert MAX_POPULATION == 4 and MAX_GENERATIONS == 3
    for bad in ({"population": 5}, {"population": 0}, {"generations": 4},
                {"generations": 0}, {"mutations_per_gen": 0},
                {"mutations_per_gen": 9}):
        with pytest.raises(ValueError):
            _evolve(ScriptedMutationLLM([]), **bad)


def test_overlapping_windows_rejected():
    with pytest.raises(ValueError, match="overlap"):
        _evolve(ScriptedMutationLLM([]), train_window=(0, 1000),
                valid_window=(500, 2000))
    with pytest.raises(ValueError, match="overlap"):
        _evolve(ScriptedMutationLLM([]), train_window=(0, None),
                valid_window=(5000, None))


# =========================================================================== #
# 适应度：train 窗 return_pct 为主；零交易 = 零证据 = 0 分
# =========================================================================== #
def test_fitness_return_pct_and_zero_trade_doctrine():
    assert fitness_of({"return_pct": 7.5, "num_trades": 3}) == 7.5
    assert fitness_of({"return_pct": -3.2, "num_trades": 2}) == -3.2
    # 躺平蓝图：账面 return_pct 再好看也判 0（防进化出零交易个体骗高分）
    assert fitness_of({"return_pct": 55.0, "num_trades": 0}) == 0.0
    assert fitness_of({}) == 0.0


# =========================================================================== #
# 变异 patch 应用：深拷贝、语义、param_only 保险丝
# =========================================================================== #
def test_apply_patch_set_params_deep_copy_no_parent_pollution():
    parent = _seed()
    child = apply_patch(parent, {"set_params": {"ema_fast": {"period": 40}}})
    c_fast = next(n for n in child.nodes if n.id == "ema_fast")
    p_fast = next(n for n in parent.nodes if n.id == "ema_fast")
    assert c_fast.params["period"] == 40
    assert p_fast.params["period"] == 12          # 父代不被污染
    # 改孩子的 params dict 也不能反向渗透
    c_fast.params["period"] = 999
    c_fast.params["injected"] = True
    assert p_fast.params == {"period": 12}
    # 未触及节点同样是独立副本
    c_risk = next(n for n in child.nodes if n.id == "risk")
    c_risk.params["max_qty"] = -1
    assert next(n for n in parent.nodes if n.id == "risk").params["max_qty"] == 100.0


def test_apply_patch_structural_semantics():
    parent = _seed()
    n_nodes, n_edges = len(parent.nodes), len(parent.edges)
    child = apply_patch(parent, {
        "del_nodes": ["kill"],
        "add_nodes": [{"id": "ema_mid", "type": "ema", "params": {"period": 20}}],
        "add_edges": [{"from": "feed.out", "to": "ema_mid.candle"}],
    })
    child_ids = {n.id for n in child.nodes}
    assert "kill" not in child_ids and "ema_mid" in child_ids
    # kill 的悬空边（feed.out -> kill.candle）一并移除；新边加入
    assert not any("kill" in (e.src.node_id, e.dst.node_id) for e in child.edges)
    assert any(e.src.node_id == "feed" and e.dst.node_id == "ema_mid"
               for e in child.edges)
    # 父代规模纹丝不动
    assert len(parent.nodes) == n_nodes and len(parent.edges) == n_edges

    # del_edges：按 "from"/"to" 精确匹配
    child2 = apply_patch(parent, {"del_edges": [{"from": "feed.out", "to": "kill.candle"}]})
    assert len(child2.edges) == n_edges - 1


def test_apply_patch_rejects_bad_patches():
    parent = _seed()
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"set_params": {"no_such_node": {"x": 1}}})
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"del_nodes": ["no_such_node"]})
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"add_nodes": [{"id": "risk", "type": "ema"}]})  # id 冲突
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"del_edges": [{"from": "feed.out", "to": "nope.in"}]})
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"add_edges": [{"from": "not-a-ref", "to": "exec.signal"}]})
    with pytest.raises(MutationRejected):
        apply_patch(parent, {"replace_graph": {}})   # 未知 patch 键
    with pytest.raises(MutationRejected):
        apply_patch(parent, ["not", "a", "dict"])


def test_apply_patch_param_only_fuse_rejects_structural():
    parent = _seed()
    # 保险丝：param_only 下任何结构变异键（非空）都拒绝
    for key, val in (("add_nodes", [{"id": "x", "type": "ema"}]),
                     ("del_nodes", ["kill"]),
                     ("add_edges", [{"from": "feed.out", "to": "kill.candle"}]),
                     ("del_edges", [{"from": "feed.out", "to": "kill.candle"}])):
        with pytest.raises(MutationRejected, match="param_only"):
            apply_patch(parent, {key: val}, param_only=True)
    # 纯参数变异照常通过；空结构键不算结构变异
    child = apply_patch(parent, {"set_params": {"ema_fast": {"period": 30}},
                                 "add_nodes": []}, param_only=True)
    assert next(n for n in child.nodes if n.id == "ema_fast").params["period"] == 30


# =========================================================================== #
# 谱系树结构：好/坏变异、选择、stillborn 记录（fake 回测 + 真编译守门）
# =========================================================================== #
def test_genealogy_selection_and_stillborn(fake_backtest):
    llm = ScriptedMutationLLM([
        # gen1 c0（父=seed）：好变异 period 40 → fitness 40
        {"summary": "widen fast ema to 40", "set_params": {"ema_fast": {"period": 40}}},
        # gen1 c1（父=seed）：坏变异 period 5 → fitness 5（低于 seed 的 12）
        {"summary": "shrink fast ema to 5", "set_params": {"ema_fast": {"period": 5}}},
        # gen2 c0（父=g1_c0，存活第一名）：非法节点类型，3 次尝试全废 → stillborn
        {"summary": "bolt on a magic node", "add_nodes": [{"id": "zz", "type": "no_such_type"}]},
        {"summary": "bolt on a magic node", "add_nodes": [{"id": "zz", "type": "no_such_type"}]},
        {"summary": "bolt on a magic node", "add_nodes": [{"id": "zz", "type": "no_such_type"}]},
        # gen2 c1（父=seed，存活第二名）：好变异 period 50 → fitness 50
        {"summary": "widen fast ema to 50", "set_params": {"ema_fast": {"period": 50}}},
    ])
    g = _evolve(llm, population=2, generations=2, mutations_per_gen=2)

    assert isinstance(g, Genealogy)
    assert llm.calls == 6 and not llm.script            # 剧本精确耗尽 = 零配额自证

    by_id = {n.id: n for n in g.nodes}
    assert set(by_id) == {"g0_seed", "g1_c0", "g1_c1", "g2_c0", "g2_c1"}

    # 代数与血缘
    assert by_id["g0_seed"].gen == 0 and by_id["g0_seed"].parent_id is None
    assert by_id["g1_c0"].parent_id == "g0_seed"
    assert by_id["g1_c1"].parent_id == "g0_seed"
    # gen1 选择后存活 = [g1_c0(40), g0_seed(12)] → gen2 父代轮转正确
    assert by_id["g2_c0"].parent_id == "g1_c0"
    assert by_id["g2_c1"].parent_id == "g0_seed"

    # 适应度如实
    assert by_id["g0_seed"].fitness == 12.0
    assert by_id["g1_c0"].fitness == 40.0
    assert by_id["g1_c1"].fitness == 5.0
    assert by_id["g2_c1"].fitness == 50.0

    # stillborn 如实入谱系：无适应度、无蓝图运行、编译状态记录在案
    still = by_id["g2_c0"]
    assert still.compile_status == "stillborn"
    assert still.fitness is None
    assert still.mutation_summary == "bolt on a magic node"
    assert still.survived is False

    # 编译失败反馈真的喂回了 LLM（自修复反馈环自证）
    retry_msgs = [c[-1]["content"] for c in llm.conversations[3:5]]
    assert all("UNKNOWN_NODE_TYPE" in m for m in retry_msgs)

    # 终选存活 = 最后一代 top-2
    assert {n.id for n in g.nodes if n.survived} == {"g2_c1", "g1_c0"}

    # winner：最后一代最优个体，valid 窗只在终选跑（fake：valid = train - 10）
    assert g.winner["id"] == "g2_c1"
    assert g.winner["train_fitness"] == 50.0
    assert g.winner["valid_fitness"] == 40.0
    assert g.winner["generalization_gap"] == pytest.approx(10.0)

    # 其余个体 compile_status = ok（一次过编译）
    assert all(by_id[i].compile_status == "ok"
               for i in ("g0_seed", "g1_c0", "g1_c1", "g2_c1"))


# =========================================================================== #
# 硬护栏不可进化绕过（卖点锁定）：去 risk_gate 的变异 → TYPE_MISMATCH → stillborn
# =========================================================================== #
def test_risk_gate_removal_mutation_is_stillborn(fake_backtest):
    bad = {"summary": "remove the risk gate for more trades",
           "del_nodes": ["risk"],
           "add_edges": [{"from": "sizer.sized", "to": "exec.signal"}]}
    llm = ScriptedMutationLLM([bad, bad, bad])   # 冥顽不灵：3 次尝试全提同一危险变异
    g = _evolve(llm, population=1, generations=1, mutations_per_gen=1)

    assert llm.calls == 3 and not llm.script
    by_id = {n.id: n for n in g.nodes}
    still = by_id["g1_c0"]
    assert still.compile_status == "stillborn"
    assert still.fitness is None
    assert still.mutation_summary == "remove the risk gate for more trades"

    # 编译器裁决 + fix_hint 真喂回 LLM：类型系统是合规官，进化也逃不出去
    for conv in llm.conversations[1:]:
        feedback = conv[-1]["content"]
        assert "TYPE_MISMATCH" in feedback
        assert "RiskGate" in feedback

    # 危险变异全灭 → seed 独活成为 winner（进化如实退化为原地踏步）
    assert g.winner["id"] == "g0_seed"
    assert by_id["g0_seed"].survived is True


# =========================================================================== #
# 自修复路径：第一次编译失败、fix_hint 回喂后第二次合法 → compile_status="repaired"
# =========================================================================== #
def test_mutation_self_repair_records_repaired(fake_backtest):
    llm = ScriptedMutationLLM([
        {"summary": "try an unknown node", "add_nodes": [{"id": "zz", "type": "no_such_type"}]},
        {"summary": "fall back to a param tweak", "set_params": {"ema_fast": {"period": 40}}},
    ])
    g = _evolve(llm, population=1, generations=1, mutations_per_gen=1)

    assert llm.calls == 2 and not llm.script
    child = next(n for n in g.nodes if n.id == "g1_c0")
    assert child.compile_status == "repaired"
    assert child.fitness == 40.0
    assert child.mutation_summary == "fall back to a param tweak"
    # 第二次调用的最后一条消息就是第一轮的编译错误反馈
    assert "UNKNOWN_NODE_TYPE" in llm.conversations[1][-1]["content"]
    assert g.winner["id"] == "g1_c0"


def test_non_json_reply_consumes_retry_then_repairs(fake_backtest):
    llm = ScriptedMutationLLM([
        "I think we should probably widen the EMA a bit?",   # 非 JSON 回复
        {"summary": "widen ema", "set_params": {"ema_fast": {"period": 30}}},
    ])
    g = _evolve(llm, population=1, generations=1, mutations_per_gen=1)
    child = next(n for n in g.nodes if n.id == "g1_c0")
    assert child.compile_status == "repaired"
    assert "JSON" in llm.conversations[1][-1]["content"]


# =========================================================================== #
# param_only 降级保险丝：结构变异被拒喂回，param 变异照常；产物图结构与种子一致
# =========================================================================== #
def test_param_only_mode_rejects_structural_and_still_evolves(fake_backtest):
    llm = ScriptedMutationLLM([
        {"summary": "add a node", "add_nodes": [{"id": "x", "type": "ema", "params": {"period": 9}}]},
        {"summary": "param tweak", "set_params": {"ema_fast": {"period": 35}}},
    ])
    g = _evolve(llm, population=1, generations=1, mutations_per_gen=1, param_only=True)

    child = next(n for n in g.nodes if n.id == "g1_c0")
    assert child.compile_status == "repaired"   # 被拒 → 反馈 → 第二次合法
    assert child.fitness == 35.0
    # 拒绝原因真的喂回了 LLM
    assert "param_only" in llm.conversations[1][-1]["content"]
    # 结构保险丝兑现：图结构与种子完全一致（节点 id/type 与边集合不变，只 params 差异）
    seed_bp = _seed()
    assert [(n["id"], n["type"]) for n in child.blueprint_json["nodes"]] \
        == [(n.id, n.type) for n in seed_bp.nodes]
    assert {(e["from"], e["to"]) for e in child.blueprint_json["edges"]} \
        == {(f"{e.src.node_id}.{e.src.port}", f"{e.dst.node_id}.{e.dst.port}")
            for e in seed_bp.edges}
    # 系统提示里向 LLM 明示了 param-only 模式
    assert "set_params" in llm.conversations[0][0]["content"]


# =========================================================================== #
# 真回测端到端（确定性种子蓝图，零 LLM 回测）+ 泄漏测试
# =========================================================================== #
@pytest.fixture(scope="module")
def real_evolution(tmp_path_factory):
    """一次真实 evolve 跑通，多测试共享：ema_cross param 变异，
    population=2 generations=2，真 run_backtest（唯一 LLM 是变异算子剧本）。"""
    tmp = tmp_path_factory.mktemp("evolve_real")
    db = SQLiteMarketData(tmp / "market.sqlite")
    up = gen_candles(220, seed=7, trend=0.003)
    down = gen_candles(230, seed=8, trend=-0.003,
                       start_ts=up[-1]["ts"] + 60_000, start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    src = RecordingSource(db)
    train_w = (0, 299 * 60_000)              # 前 300 根
    valid_w = (300 * 60_000, None)           # 后 150 根，进化循环绝不许碰
    llm = ScriptedMutationLLM([
        {"summary": "faster entry ema", "set_params": {"ema_fast": {"period": 8}}},
        {"summary": "slower exit ema", "set_params": {"ema_slow": {"period": 34}}},
        {"summary": "combo tweak", "set_params": {"ema_fast": {"period": 10}}},
        {"summary": "wider atr stop", "set_params": {"cross": {"atr_mult": 2.5}}},
    ])
    g = evolve(load_loom_file(_EMA_CROSS), src, inst="BTC-USDT-SWAP", bar="1m",
               train_window=train_w, valid_window=valid_w, llm=llm,
               population=2, generations=2)
    return SimpleNamespace(g=g, src=src, llm=llm, train_w=train_w, valid_w=valid_w)


def test_valid_window_leak_guard(real_evolution):
    """契约锁定：进化循环内任何个体不得查询 valid 窗；valid 窗查询恰好一次
    且是最后一次查询（终选）。"""
    windows = real_evolution.src.windows
    # 5 次 train（seed + 4 孩子）+ 1 次 valid 终选
    assert len(windows) == 6
    assert windows.count(real_evolution.valid_w) == 1
    assert windows[-1] == real_evolution.valid_w
    assert all(w == real_evolution.train_w for w in windows[:-1])


def test_real_evolution_genealogy_and_winner(real_evolution):
    g = real_evolution.g
    assert real_evolution.llm.calls == 4 and not real_evolution.llm.script
    assert len(g.nodes) == 5                            # seed + 4 孩子
    assert {n.gen for n in g.nodes} == {0, 1, 2}
    by_id = {n.id: n for n in g.nodes}
    # 所有个体都过了真编译 + 真回测：有适应度、状态 ok
    assert all(n.compile_status == "ok" and isinstance(n.fitness, float)
               for n in g.nodes)
    # 血缘闭合：每个非种子节点的 parent_id 指向谱系里真实存在的更早代节点
    for n in g.nodes:
        if n.parent_id is not None:
            assert n.parent_id in by_id
            assert by_id[n.parent_id].gen < n.gen
    # winner 是最后一代存活者中的适应度冠军，训练分与谱系一致
    w = g.winner
    assert w["id"] in by_id and by_id[w["id"]].survived
    assert w["train_fitness"] == by_id[w["id"]].fitness
    assert w["train_fitness"] == max(n.fitness for n in g.nodes if n.survived)
    assert isinstance(w["valid_fitness"], float)
    assert w["generalization_gap"] == pytest.approx(
        w["train_fitness"] - w["valid_fitness"])


def test_genealogy_to_dict_json_safe_react_flow_shape(real_evolution):
    d = real_evolution.g.to_dict()
    json.dumps(d)                                       # JSON 安全
    assert set(d) == {"nodes", "winner", "param_only", "population", "generations"}
    for nd in d["nodes"]:
        assert set(nd) >= {"id", "gen", "parent_id", "mutation_summary",
                           "fitness", "compile_status", "blueprint_json",
                           "survived"}
    # React Flow 可渲染：nodes + parent 关系即树；蓝图 JSON 内嵌可直接开画布
    live = [nd for nd in d["nodes"] if nd["compile_status"] != "stillborn"]
    assert all(isinstance(nd["blueprint_json"], dict)
               and "nodes" in nd["blueprint_json"] for nd in live)
    assert set(d["winner"]) >= {"id", "train_fitness", "valid_fitness",
                                "generalization_gap"}


def test_genealogy_node_dataclass_shape():
    n = GenealogyNode(id="g0_seed", gen=0, parent_id=None, mutation_summary="seed",
                      fitness=float("inf"), compile_status="ok", blueprint_json=None)
    d = n.to_dict()
    assert d["fitness"] is None                         # inf → JSON 安全清洗
    json.dumps(d)
