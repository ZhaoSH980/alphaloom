"""进化实验室 —— "Agent 即研究员"终极形态（D4-T5，spec §6）。

本质是遗传算法，**变异算子是 LLM**：

1. **Gen 0**：种子蓝图跑 train 窗回测 → 适应度。
2. **每代**：对存活个体轮转，LLM 读【蓝图 JSON + 回测报告摘要】提出**变异 patch**
   （JSON 形式：参数改动 / 节点增删 / 边重接，见下方 patch 格式）→ 应用变异
   （深拷贝，绝不污染父代）→ **编译守门**：编译失败把 CompileError.fix_hint
   喂回 LLM 重试 ≤ :data:`MAX_REPAIR_RETRIES` 次（复用 copilot text_to_blueprint
   的自修复模式）；仍失败则该变异**废弃并如实记录 stillborn** 进谱系树。
   编译过的孩子跑 train 窗回测 → 适应度。**回测执行期若炸**（编译不校验 param
   值——如 ``period="very fast"`` 编译过但 setup 的 ``int()`` 崩），该孩子记
   ``runtime_error``（错误摘要入谱系）、不进种群，**进化继续**——一个变异垃圾
   绝不炸掉整棵谱系（API 暴露后这是网络可达的 DoS 面，收容是硬性）。
   **种子蓝图跑炸仍 raise**：种子坏是调用方错误不是变异风险，不静默收容。
3. **选择**：存活个体 + 本代合法孩子按适应度排序，top-N 进下代（N=population，
   精英保留——父代不自动死亡，被更强的孩子挤出去才死）。
4. **终选**：最后一代最优个体跑 **valid 窗**（从未参与进化的数据）→ 最终成绩 +
   泛化差距。**防过拟合泄漏（测试锁定）**：进化循环内任何个体不得查询 valid 窗
   ——valid 窗查询只发生在终选、恰好一次；train/valid 窗口重叠直接 ValueError。

**变异 patch 格式**（LLM 输出契约，应用序 set_params → del_nodes → add_nodes →
del_edges → add_edges，任何一步非法即 :class:`MutationRejected` 喂回重试）::

    {"summary": "<一句话变异描述>",
     "set_params": {"<nodeId>": {"<param>": <value>}},
     "add_nodes":  [{"id": "...", "type": "<catalog type>", "params": {...}}],
     "del_nodes":  ["<nodeId>"],
     "add_edges":  [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}],
     "del_edges":  [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}]}

**适应度（简单、可解释）**：train 窗 ``return_pct`` 为主；``num_trades == 0``
一律判 0 分——零交易 = 零证据（T3 记分卡教义）：躺平蓝图的账面收益是空洞的，
判 0 保证它永远赢不了任何真实盈利个体（在全员亏损的种群里 0 分躺平者仍可能
居首——"什么都不做好过亏钱"，这是诚实结论不是 bug）。完整 composite 评分是
终选 scorecard 的事，进化循环内不重复。

**安全（硬护栏不可进化绕过，测试锁定）**：变异想移除 risk_gate？不用特判——
``execute_order`` 只吃 ``risk_stamped_signal``，全宇宙唯一产地是 RiskGate，
编译守门天然 TYPE_MISMATCH 拦截，fix_hint 把 LLM 按回 RiskGate；冥顽不灵则
stillborn 如实入谱系。类型系统是合规官，进化也逃不出去（与消融同款卖点）。

**规模硬锁定（契约）**：population ≤ 4、generations ≤ 3，超出 ValueError。
**降级保险丝**：``param_only=True`` 时变异提示只许 set_params，应用侧
:func:`apply_patch` 拒绝一切结构变异 patch（节点/边不变，只 params 差异）——
保证最小可用演示。

零配额：本模块不创建任何 LLM 客户端——``llm`` 由调用方注入（测试 = 确定性
剧本；演示 = 录制回放）。确定性种子蓝图（ema_cross 等）回测本身零 LLM，
唯一 LLM 消耗是变异算子（每孩子 ≤ 1 + MAX_REPAIR_RETRIES 次 chat）。
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field

from alphaloom.backtest.runner import run_backtest
from alphaloom.copilot.blueprint import _content, _errors_to_feedback, _extract_json
from alphaloom.copilot.prompts import _RISK_CONSTRAINT, build_node_catalog
from alphaloom.eval.scorecard import _json_safe
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import (
    BlueprintSpec,
    EdgeSpec,
    NodeSpec,
    _parse_ref,
    dumps_loom,
)

# 规模硬锁定（契约；扩规模是 D4 Carryover #2，不是调这两个常量的事）
MAX_POPULATION = 4
MAX_GENERATIONS = 3
MAX_MUTATIONS_PER_GEN = 2 * MAX_POPULATION   # LLM 预算护栏（每代变异提案上限）
MAX_REPAIR_RETRIES = 2                       # 编译失败 fix_hint 回喂重试上限

PATCH_KEYS = ("set_params", "add_nodes", "del_nodes", "add_edges", "del_edges")
STRUCTURAL_KEYS = ("add_nodes", "del_nodes", "add_edges", "del_edges")


class MutationRejected(ValueError):
    """变异 patch 在应用侧被拒（未知键 / 引用不存在的节点或边 / param_only 保险丝）。

    与编译失败同等待遇：拒绝原因作为反馈喂回 LLM 重试；重试耗尽 → stillborn。
    """


# --------------------------------------------------------------------------- #
# 变异 patch 应用（深拷贝——父代绝不被污染，测试锁定）
# --------------------------------------------------------------------------- #
def _ref(s):
    try:
        return _parse_ref(s)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MutationRejected(f"bad edge ref {s!r}: {exc}") from None


def _edge_str(e: EdgeSpec) -> tuple[str, str]:
    return (f"{e.src.node_id}.{e.src.port}", f"{e.dst.node_id}.{e.dst.port}")


def apply_patch(bp: BlueprintSpec, patch, *, param_only: bool = False) -> BlueprintSpec:
    """把变异 patch 应用到蓝图，返回**全新深拷贝**（``bp`` 纹丝不动）。

    应用序：set_params → del_nodes → add_nodes → del_edges → add_edges。
    ``param_only=True`` 是降级保险丝：任何非空结构键（add/del nodes/edges）
    直接 :class:`MutationRejected` ——图结构与父代逐位一致，只许 params 差异。
    """
    if not isinstance(patch, dict):
        raise MutationRejected("patch must be a single JSON object")
    unknown = set(patch) - set(PATCH_KEYS) - {"summary"}
    if unknown:
        raise MutationRejected(
            f"unknown patch key(s) {sorted(unknown)}; allowed keys: "
            f"{list(PATCH_KEYS) + ['summary']}")
    if param_only:
        offending = [k for k in STRUCTURAL_KEYS if patch.get(k)]
        if offending:
            raise MutationRejected(
                "param_only mode: structural mutation keys "
                f"({', '.join(offending)}) are not allowed; propose ONLY "
                "set_params changes (nodes and edges must stay identical)")

    nodes = [NodeSpec(n.id, n.type, copy.deepcopy(n.params)) for n in bp.nodes]
    edges = list(bp.edges)                      # EdgeSpec frozen → 共享安全

    # 1) set_params：整包合并进目标节点（值深拷贝，防 patch dict 被外部复用）
    set_params = patch.get("set_params") or {}
    if not isinstance(set_params, dict):
        raise MutationRejected("set_params must be an object {nodeId: {param: value}}")
    by_id = {n.id: i for i, n in enumerate(nodes)}
    for nid, upd in set_params.items():
        if nid not in by_id:
            raise MutationRejected(f"set_params targets unknown node {nid!r}")
        if not isinstance(upd, dict):
            raise MutationRejected(f"set_params[{nid!r}] must be an object")
        n = nodes[by_id[nid]]
        nodes[by_id[nid]] = NodeSpec(n.id, n.type,
                                     {**n.params, **copy.deepcopy(upd)})

    # 2) del_nodes：节点与触及它的边（悬空边）一并移除
    del_nodes = patch.get("del_nodes") or []
    victims = set()
    for nid in del_nodes:
        if not any(n.id == nid for n in nodes):
            raise MutationRejected(f"del_nodes targets unknown node {nid!r}")
        victims.add(nid)
    if victims:
        nodes = [n for n in nodes if n.id not in victims]
        edges = [e for e in edges
                 if e.src.node_id not in victims and e.dst.node_id not in victims]

    # 3) add_nodes：id 不得与现存节点冲突
    for spec in patch.get("add_nodes") or []:
        if not isinstance(spec, dict) or not spec.get("id") or not spec.get("type"):
            raise MutationRejected(
                f"add_nodes entries need 'id' and 'type': {spec!r}")
        if any(n.id == spec["id"] for n in nodes):
            raise MutationRejected(
                f"add_nodes id {spec['id']!r} collides with an existing node")
        nodes.append(NodeSpec(str(spec["id"]), str(spec["type"]),
                              copy.deepcopy(spec.get("params") or {})))

    # 4) del_edges：按 "from"/"to" 精确匹配现存边
    for spec in patch.get("del_edges") or []:
        if not isinstance(spec, dict) or "from" not in spec or "to" not in spec:
            raise MutationRejected(f"del_edges entries need 'from' and 'to': {spec!r}")
        key = (str(spec["from"]), str(spec["to"]))
        hit = next((e for e in edges if _edge_str(e) == key), None)
        if hit is None:
            raise MutationRejected(
                f"del_edges: no edge {key[0]} -> {key[1]} in the blueprint")
        edges.remove(hit)

    # 5) add_edges：解析 'node.port' 引用（端点是否存在/类型是否闭合交给编译器裁决）
    for spec in patch.get("add_edges") or []:
        if not isinstance(spec, dict) or "from" not in spec or "to" not in spec:
            raise MutationRejected(f"add_edges entries need 'from' and 'to': {spec!r}")
        edges.append(EdgeSpec(_ref(str(spec["from"])), _ref(str(spec["to"])),
                              bool(spec.get("feedback", False))))

    return BlueprintSpec(bp.id, bp.name, nodes, edges, copy.deepcopy(bp.meta))


# --------------------------------------------------------------------------- #
# 适应度（简单、可解释；完整 composite 是终选 scorecard 的事）
# --------------------------------------------------------------------------- #
def fitness_of(summary: dict) -> float:
    """适应度 = train 窗 ``return_pct``；``num_trades == 0`` 一律判 0 分。

    零交易 = 零证据（T3 教义）：躺平蓝图的账面收益空洞，判 0 使它永远赢不了
    任何真实盈利个体，防止进化收敛到"什么都不做"。负收益如实为负——在全员
    亏损的种群里躺平者（0 分）居首是诚实结论（不亏就是赢），不是 bug。
    """
    if float(summary.get("num_trades", 0) or 0) <= 0:
        return 0.0
    return float(summary.get("return_pct", 0.0) or 0.0)


# --------------------------------------------------------------------------- #
# 谱系树（React Flow 直接可渲染：nodes + parent_id 即树）
# --------------------------------------------------------------------------- #
@dataclass
class GenealogyNode:
    id: str
    gen: int
    parent_id: str | None
    mutation_summary: str          # LLM 的变异一句话描述（seed 为 "seed blueprint"）
    fitness: float | None          # train 窗适应度；stillborn/runtime_error = None（无有效回测）
    # "ok" | "repaired"（≥1 轮反馈后过编译）| "stillborn"（编译守门未过）
    # | "runtime_error"（编译过、但回测执行期炸了——如垃圾 param 在 setup 处 int() 崩）
    compile_status: str
    blueprint_json: dict | None    # loom dict（stillborn 记最后一次尝试产物，可能 None；
                                   # runtime_error 恒有——编译成功过才可能运行期炸）
    survived: bool = False         # 是否活到终局（最后一代选择后的存活集）
    error: str | None = None       # runtime_error 的错误消息摘要（其余状态为 None）

    def to_dict(self) -> dict:
        return _json_safe({
            "id": self.id, "gen": self.gen, "parent_id": self.parent_id,
            "mutation_summary": self.mutation_summary, "fitness": self.fitness,
            "compile_status": self.compile_status,
            "blueprint_json": self.blueprint_json, "survived": self.survived,
            "error": self.error,
        })


@dataclass
class Genealogy:
    nodes: list[GenealogyNode] = field(default_factory=list)
    winner: dict = field(default_factory=dict)
    param_only: bool = False
    population: int = 0
    generations: int = 0

    def to_dict(self) -> dict:
        return _json_safe({
            "nodes": [n.to_dict() for n in self.nodes],
            "winner": self.winner,
            "param_only": self.param_only,
            "population": self.population,
            "generations": self.generations,
        })


@dataclass
class _Individual:
    """进化循环内部个体：谱系节点 + 可运行蓝图 + train 窗报告摘要（变异提示用）。"""
    node: GenealogyNode
    bp: BlueprintSpec
    summary: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 变异提示词（复用 copilot 的节点目录 + 风控硬约束）
# --------------------------------------------------------------------------- #
_MUTATE_SYSTEM = """\
You are the MUTATION OPERATOR of an evolutionary trading-strategy lab. You are
given one parent .loom blueprint and its backtest summary. Propose ONE mutation
that plausibly improves the train-window return.

Reply with ONLY a single JSON object - a MUTATION PATCH, NOT a full blueprint:
{
  "summary": "<one-line description of the mutation>",
  "set_params": {"<nodeId>": {"<param>": <value>}},
  "add_nodes": [{"id": "<newId>", "type": "<catalog type>", "params": {...}}],
  "del_nodes": ["<nodeId>"],
  "add_edges": [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}],
  "del_edges": [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}]
}
Omit any key you do not need ("summary" is always required). Patches are applied
in order: set_params, del_nodes, add_nodes, del_edges, add_edges. The mutated
blueprint must still compile; if it fails you will get the compiler errors (with
fix hints) - fix exactly those and reply with a corrected patch."""

_PARAM_ONLY_RULE = """\
PARAM-ONLY MODE: you may ONLY use "set_params" (plus "summary"). Any
add_nodes/del_nodes/add_edges/del_edges will be rejected - the graph structure
must stay identical to the parent; only parameter values may change."""


def _build_mutation_messages(loom: dict, summary: dict, fitness: float,
                             defs, *, param_only: bool) -> list[dict]:
    catalog = build_node_catalog(defs)
    system = f"{_MUTATE_SYSTEM}\n\n"
    if param_only:
        system += f"{_PARAM_ONLY_RULE}\n\n"
    system += f"NODE CATALOG:\n{catalog}\n\n{_RISK_CONSTRAINT}"
    user = json.dumps({"blueprint": loom, "backtest_summary": summary,
                       "train_fitness": fitness},
                      ensure_ascii=False, sort_keys=True)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


# --------------------------------------------------------------------------- #
# 单个孩子：LLM 提变异 → 应用 → 编译守门（fix_hint 回喂自修复 ≤2 次）
# --------------------------------------------------------------------------- #
def _mutate_child(parent: _Individual, child_id: str, gen: int, llm, defs,
                  *, param_only: bool, temperature: float):
    """返回 (GenealogyNode, BlueprintSpec | None)。蓝图为 None ⇔ stillborn。"""
    loom = json.loads(dumps_loom(parent.bp))
    messages = _build_mutation_messages(
        loom, parent.summary, parent.node.fitness or 0.0, defs,
        param_only=param_only)
    summary_note = "(no mutation proposed)"
    last_bp_json: dict | None = None

    for attempt in range(1 + MAX_REPAIR_RETRIES):
        response = llm.chat(messages, temperature=temperature)
        raw = _content(response)
        patch = _extract_json(raw)
        if patch is None:
            feedback = ("Your reply was not a single JSON object. Reply with "
                        "ONLY the mutation patch JSON.")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": feedback})
            continue
        summary_note = str(patch.get("summary") or "").strip() or "(no summary)"
        try:
            child_bp = apply_patch(parent.bp, patch, param_only=param_only)
        except MutationRejected as exc:
            feedback = (f"[PATCH_REJECTED] {exc}. Reply with ONLY a corrected "
                        "mutation patch JSON.")
            messages.append({"role": "assistant",
                             "content": json.dumps(patch, ensure_ascii=False)})
            messages.append({"role": "user", "content": feedback})
            continue
        child_bp = BlueprintSpec(f"{parent.bp.id.split('__', 1)[0]}__{child_id}",
                                 parent.bp.name, child_bp.nodes, child_bp.edges,
                                 child_bp.meta)
        compiled = compile_blueprint(child_bp)
        if compiled.ok:
            status = "ok" if attempt == 0 else "repaired"
            node = GenealogyNode(
                id=child_id, gen=gen, parent_id=parent.node.id,
                mutation_summary=summary_note, fitness=None,
                compile_status=status,
                blueprint_json=json.loads(dumps_loom(child_bp)))
            return node, child_bp
        # 编译守门失败：结构化错误（含 fix_hint）喂回 LLM 自修复
        last_bp_json = json.loads(dumps_loom(child_bp))
        feedback = _errors_to_feedback(compiled.errors)
        messages.append({"role": "assistant",
                         "content": json.dumps(patch, ensure_ascii=False)})
        messages.append({"role": "user", "content": (
            "The mutated blueprint failed to compile with these errors. Fix "
            "EXACTLY these and reply with ONLY a corrected mutation patch "
            f"JSON:\n{feedback}")})

    # 重试耗尽：该变异废弃，如实记 stillborn（谱系树不隐藏失败）
    node = GenealogyNode(
        id=child_id, gen=gen, parent_id=parent.node.id,
        mutation_summary=summary_note, fitness=None,
        compile_status="stillborn", blueprint_json=last_bp_json)
    return node, None


# --------------------------------------------------------------------------- #
# 窗口工具
# --------------------------------------------------------------------------- #
def _unpack_window(name: str, window):
    if (not isinstance(window, (tuple, list)) or len(window) != 2):
        raise ValueError(f"{name} must be a (start_ms, end_ms) pair, got {window!r}")
    return window[0], window[1]


def _windows_overlap(a, b) -> bool:
    lo_a = -math.inf if a[0] is None else a[0]
    hi_a = math.inf if a[1] is None else a[1]
    lo_b = -math.inf if b[0] is None else b[0]
    hi_b = math.inf if b[1] is None else b[1]
    return max(lo_a, lo_b) <= min(hi_a, hi_b)


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def evolve(seed_blueprint: BlueprintSpec, source, *, inst: str, bar: str,
           train_window, valid_window, llm, population: int = 4,
           generations: int = 3, mutations_per_gen: int | None = None,
           param_only: bool = False, initial_cash: float = 10_000.0,
           fee_rate: float = 0.0005, temperature: float = 0.3) -> Genealogy:
    """进化循环（模块 docstring 有全貌）。返回完整谱系树 :class:`Genealogy`。

    - ``train_window`` / ``valid_window``：``(start_ms, end_ms)``（None = 开区间端）。
      两窗重叠直接 ValueError；进化循环只查 train 窗，valid 窗仅终选一次。
    - ``population`` ≤ 4、``generations`` ≤ 3：规模硬锁定，超出 ValueError。
    - ``mutations_per_gen``：每代变异提案数，默认 = population，上限
      :data:`MAX_MUTATIONS_PER_GEN`（LLM 预算护栏）。
    - ``param_only``：降级保险丝——只许参数变异（见 :func:`apply_patch`）。
    - ``llm`` 只用作变异算子（＋透传给回测供 LLM 节点蓝图使用）；确定性种子
      蓝图（ema_cross 等）回测零 LLM。
    """
    if not isinstance(population, int) or not (1 <= population <= MAX_POPULATION):
        raise ValueError(
            f"population must be an int in [1, {MAX_POPULATION}] (scale lock), "
            f"got {population!r}")
    if not isinstance(generations, int) or not (1 <= generations <= MAX_GENERATIONS):
        raise ValueError(
            f"generations must be an int in [1, {MAX_GENERATIONS}] (scale lock), "
            f"got {generations!r}")
    if mutations_per_gen is None:
        mutations_per_gen = population
    if not isinstance(mutations_per_gen, int) or \
            not (1 <= mutations_per_gen <= MAX_MUTATIONS_PER_GEN):
        raise ValueError(
            f"mutations_per_gen must be an int in [1, {MAX_MUTATIONS_PER_GEN}] "
            f"(LLM budget lock), got {mutations_per_gen!r}")
    train_w = _unpack_window("train_window", train_window)
    valid_w = _unpack_window("valid_window", valid_window)
    if _windows_overlap(train_w, valid_w):
        raise ValueError(
            f"train_window {train_w} and valid_window {valid_w} overlap - the "
            "validation window must never be touched by the evolution loop")

    from alphaloom.nodes.registry import REGISTRY
    defs = REGISTRY

    def _run_train(bp: BlueprintSpec):
        return run_backtest(bp, source, inst=inst, bar=bar,
                            start_ms=train_w[0], end_ms=train_w[1],
                            initial_cash=initial_cash, fee_rate=fee_rate, llm=llm)

    # ---- Gen 0：种子 ----
    seed_report = _run_train(seed_blueprint)
    seed_node = GenealogyNode(
        id="g0_seed", gen=0, parent_id=None, mutation_summary="seed blueprint",
        fitness=fitness_of(seed_report.summary), compile_status="ok",
        blueprint_json=json.loads(dumps_loom(seed_blueprint)))
    seed = _Individual(node=seed_node, bp=seed_blueprint,
                       summary=dict(seed_report.summary))
    genealogy_nodes: list[GenealogyNode] = [seed_node]
    survivors: list[_Individual] = [seed]

    # ---- 每代：变异 → 编译守门 → train 回测 → 选择 ----
    for gen in range(1, generations + 1):
        children: list[_Individual] = []
        for k in range(mutations_per_gen):
            parent = survivors[k % len(survivors)]
            child_node, child_bp = _mutate_child(
                parent, f"g{gen}_c{k}", gen, llm, defs,
                param_only=param_only, temperature=temperature)
            genealogy_nodes.append(child_node)
            if child_bp is None:
                continue                      # stillborn：只入谱系，不进种群
            # 运行期错误收容（T5 审查遗留）：孩子已过编译守门，但回测执行期仍可能
            # 炸（垃圾 param 类型编译不校验值——如 period="very fast" 在 setup 的
            # int() 处 ValueError）。捕获后记 runtime_error、如实留错误摘要，进化
            # 继续（不让一个变异垃圾炸掉整棵谱系；网络可达后这就是 DoS 面）。
            try:
                report = _run_train(child_bp)
            except Exception as exc:          # noqa: BLE001 —— 变异沙盒的执行期兜底
                child_node.compile_status = "runtime_error"
                child_node.fitness = None
                child_node.error = f"{type(exc).__name__}: {exc}"[:500]
                continue                      # runtime_error：入谱系，不进种群
            child_node.fitness = fitness_of(report.summary)
            children.append(_Individual(node=child_node, bp=child_bp,
                                        summary=dict(report.summary)))
        pool = survivors + children
        pool.sort(key=lambda ind: ind.node.fitness, reverse=True)  # 稳定：平分保老
        survivors = pool[:population]

    for ind in survivors:
        ind.node.survived = True

    # ---- 终选：最后一代最优个体跑 valid 窗（进化循环从未触碰的数据）----
    best = max(survivors, key=lambda ind: ind.node.fitness)
    valid_report = run_backtest(best.bp, source, inst=inst, bar=bar,
                                start_ms=valid_w[0], end_ms=valid_w[1],
                                initial_cash=initial_cash, fee_rate=fee_rate,
                                llm=llm)
    valid_fitness = fitness_of(valid_report.summary)
    winner = {
        "id": best.node.id,
        "train_fitness": best.node.fitness,
        "valid_fitness": valid_fitness,
        "generalization_gap": round(best.node.fitness - valid_fitness, 6),
        "train_summary": dict(best.summary),
        "valid_summary": dict(valid_report.summary),
    }
    return Genealogy(nodes=genealogy_nodes, winner=winner, param_only=param_only,
                     population=population, generations=generations)
