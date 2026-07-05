# AlphaLoom D3 Implementation Plan — LLM 节点/委员会/Copilot/反思闭环

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Agent 成为主角——交付 LLM 节点栈（分析师/委员会/PA 决策树门控/RAG）、讯飞 Spark 录制回放层（`ALPHALOOM_OFFLINE=1` 零配额演示）、反思闭环+经验库、加速回放模式，以及 Copilot 元 Agent（Text-to-Blueprint 自然语言生成蓝图 + 编译错误自修复 + Text-to-Node 沙箱），达成"聊一句话→生成蓝图→编译反馈→Agent 自修复→一键回测"的招牌演示。

**Architecture:** 在 D1 图内核 + D2 服务/前端之上加 LLM 层：`alphaloom/llm/`（Spark client + RecordingLLMClient，端口自 Hindsight）；LLM 类节点通过 `RunContext.llm` 拿录制客户端（成本注解如实、离线回放确定性）；`alphaloom/copilot/`（NL→loom + diff + explain/optimize）；`alphaloom/sandbox/`（AST 白名单热注册自定义节点）；`alphaloom/memory/`（经验库按市场状态桶检索）。全程录制保证 `ALPHALOOM_OFFLINE=1` 断网零配额演示。

**Tech Stack:** 复用 D1/D2 全栈 + `openai` SDK（Spark OpenAI 兼容端点）+ `python-dotenv`（已随 uvicorn[standard]）；前端 Copilot 侧栏复用 D2 React/WS。

**执行约定（沿用 D1/D2 + D2 教训）：** 每任务实现者+单审查者两阶段审查；**LLM/WS/浏览器集成缺陷单元测试测不出，控制器必做 preview live 走查兜底**（D2 血泪教训：uvicorn WS 断层、React hook 依赖循环都是 live 才抓到）；React hook 依赖问题静态论证不可靠，必 live 复测计数；计划即权威，偏差先改计划；`.env`（讯飞 key）绝不入库；讯飞 429 限流耐心退避（次数充裕、瓶颈是繁忙），演示路径全走录制回放。

---

## 锁定契约（跨任务，改动即 sanctioned deviation）

**LLM 配置**（port Hindsight，前缀改 ALPHALOOM）：`llm/client.py::LLMConfig.from_env()` 读 repo 根 `.env` 的 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`；`ALPHALOOM_OFFLINE=1` 时用 OFFLINE_DEFAULTS（base_url `http://offline.invalid/v1`、key `offline-replay`、model **必须匹配录制库里的 model 串**否则 replay key miss）。讯飞 Spark 端点 OpenAI 兼容。

**录制回放**（port Hindsight recording.py）：`RecordingLLMClient(transport, db_path, model, offline)`——请求 canonical JSON（sort_keys）→ sha256 → SQLite `llm_calls` 表缓存；命中不走网络；`offline=True` 且 miss → `ReplayMissError`；`cache_hits/cache_misses` 计数（provenance，回答"为什么秒出"）。`temperature` 强制 float（int/float 必须哈希到同一 key）。db 默认 `data/llm_calls.sqlite`（gitignore？——**否**，录制库要入库供离线演示，见卫生节）。

**RunContext 扩展**：`RunContext.llm: RecordingLLMClient | None`（默认 None；LLM 节点在 None 时抛清晰错误"no LLM client bound; run via service or pass llm=")；`RunContext.audit: AuditLog`（每次 LLM/检索调用留痕，port Hindsight audit.py）。runner 的 `run_backtest` 加 `llm=None` 可选参数透传。

**LLM 节点成本注解纪律**（D1 Carryover 10）：所有 LLM 类节点 `CostAnnotation(llm_calls_per_bar>=1, deterministic=False, latency_class="llm")` 如实填写；新增注册期审计——`nodes/registry.py` 或测试断言"category 含 llm 的节点禁止 deterministic=True"（D1 Carryover 10 兑现）。

**信号扩展**：LLM 节点产出的 signal dict 在 D1 四值 side 基础上增加 `"rationale": str`（决策理由）、`"citations": [str]`（RAG 命中，强制引用）、`"confidence": float`（0-1）；RiskGate 透传这些字段不变。

**节点新增（D3 面板，category 决定 Studio 颜色）**：
| 节点 | category | 关键 |
|---|---|---|
| LLMAnalyst | decision | 人格+提示词 params，产 signal+rationale+confidence；cost llm_calls_per_bar=1 |
| Committee | decision | 扇出 N 角色（策略师/风控官/主席）→ 结构化 JSON 交接 → 表决；cost = 角色数 |
| PADecisionTree | decision | 确定性数值门控（不信 LLM 嘴），读 二元决策树规则；cost 全 0 deterministic |
| KnowledgeRetrieve | rag | BM25 检索自撰知识库，产 citations；cost 0 |
| RequireCitations | rag | 强制引用门：long/short 但 citations 空 → 降级 hold；**必须带可选输入 pin `citations: SERIES`**（T4 审查 Critical：无此 pin 时 kr.citations 画布上不可达、正向放行永不发生——pin 值非 None 时合流进 sig["citations"] 再判门）；cost 0（T4 sanctioned 新增，D4 升级编译期盖章类型）|
| ExperienceRetrieve | rag | 按市场状态桶检索经验；cost 0 |
| ExperienceWrite | reflection | 平仓后写经验（由 Reflector 驱动）；cost 0 |
| Reflector | reflection | 过程/结局分离打分（reasonable_but_wrong 分类学，port Hindsight）；cost 可选 LLM |

**Copilot 契约**：`copilot/blueprint.py::text_to_blueprint(nl, defs, llm) -> {loom, notes}`（NL→loom JSON，schema 校验+dagre 风格自动布局，编译失败则 LLM 读 CompileError 自修复重试 ≤3 次）；`explain(loom, llm) -> str`；`optimize(loom, report, llm) -> {loom, diff, notes}`。前端 diff 预览：新增/删除/改动节点与边高亮，用户点应用才落地。

**沙箱契约**（port Hindsight sandbox）：`sandbox/node_sandbox.py::compile_node_source(src) -> NodeDef | SandboxError`——AST 白名单（禁 import 白名单外、禁 exec/eval/open/__import__/文件 IO/网络），只允许纯计算 + 声明的 @node 装饰器；通过则热注册进 REGISTRY。降级保险丝：受限模板版（LLM 只填模板参数不写自由代码）。

**T7 红队发现的 Critical 修复（sanctioned）——沙箱节点禁止声明 `RISK_STAMPED_SIGNAL` 输出**：`compile_node_source` 检查 @node 装饰器的 outputs，任何沙箱/自定义节点若声明 `PinType.RISK_STAMPED_SIGNAL` 作输出 → SandboxError（reason=`forge_risk_stamp`）。否则不可信代码能伪造风控盖章、绕过"类型系统即合规官"（execute_order 只吃 RISK_STAMPED_SIGNAL，是信号→实盘单的唯一闸门；名字撞车 risk_gate 已被 registry 拦，但"伪造新盖章发射器"这条路原本开着）。盖章 provenance 保留给受信内置 RiskGate。**Important 顺带修**：非字面 range() 上界也套 MAX_RANGE 上限（堵运行期 DoS 的一半；完整 CPU/内存/超时限额仍 D4 Carryover 3）。

**REST/WS 新增**：`POST /api/copilot/blueprint`（NL→loom，SSE 或返回 diff）；`POST /api/copilot/explain`；`POST /api/copilot/optimize`；`POST /api/nodes/custom`（Text-to-Node 沙箱注册）；run 的 `mode` 参数加 `"replay"`（加速回放，走真实 LLM 或录制）。

---

## 文件结构总览（D3 新增）

```
alphaloom/backend/alphaloom/
├── llm/{client,recording,retry}.py   # T1: port Hindsight（前缀 ALPHALOOM）
├── sandbox/{audit,node_sandbox}.py   # T1 audit / T7 AST 白名单
├── nodes/{llm_nodes,pa_gate,rag_nodes,reflection}.py  # T2-T5 节点
├── knowledge/{corpus.py, data/*.md}  # T4: 自撰知识库 + BM25
├── memory/experience_store.py        # T5: 经验库按市场状态桶
├── copilot/{layout,blueprint,prompts}.py  # T6: Text-to-Blueprint
└── api/app.py                        # T8: copilot 端点 + run mode=replay
frontend/src/components/CopilotPanel.tsx + lib/copilot.ts  # T9
```

---

### Task 1: Spark LLM client + 录制回放 + 429 退避（port Hindsight）

**Files:** Create `llm/__init__.py`(空)/`llm/client.py`/`llm/recording.py`/`llm/retry.py`/`sandbox/__init__.py`(空)/`sandbox/audit.py`；Modify `pyproject.toml`(+openai>=1.40)；Test `tests/test_llm_recording.py`

- [ ] Step 1 装依赖：`cd backend && .venv/Scripts/python -m pip install "openai>=1.40" && .venv/Scripts/python -m pip install -e .`
- [ ] Step 2 写测试（6 个）：offline 配置默认、record→replay 命中不走网络、offline miss 抛 ReplayMissError、temperature int/float 同 key、429 退避 [15,30] 第三次成功、audit 留痕。测试代码见下。
- [ ] Step 3 确认失败（ModuleNotFoundError: alphaloom.llm）
- [ ] Step 4 实现：client.py/recording.py/retry.py 逐字 port Hindsight `backend/hindsight/llm/*.py`（`HINDSIGHT_OFFLINE`→`ALPHALOOM_OFFLINE`，OFFLINE_DEFAULTS model=`spark-x1`，docstring §4.4→D3）；audit.py port Hindsight `sandbox/audit.py` 但字段 date→ts（`data_max_ts: int|None`）。
- [ ] Step 5 全量 98 passed（92+6）
- [ ] Step 6 commit `feat(llm): Spark client + record/replay layer + 429 backoff + audit (port Hindsight)`

测试文件 `tests/test_llm_recording.py`：

```python
import pytest
from alphaloom.llm.client import LLMConfig, OFFLINE_DEFAULTS
from alphaloom.llm.recording import RecordingLLMClient, ReplayMissError
from alphaloom.llm.retry import with_retry
from alphaloom.sandbox.audit import AuditLog

def _fake_transport(canned):
    calls = []
    def send(req):
        calls.append(req); return canned
    send.calls = calls
    return send

def test_offline_config_defaults(monkeypatch):
    monkeypatch.setenv("ALPHALOOM_OFFLINE", "1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    cfg = LLMConfig.from_env(dotenv_path=None)
    assert cfg.model == OFFLINE_DEFAULTS["LLM_MODEL"]
    assert cfg.base_url == OFFLINE_DEFAULTS["LLM_BASE_URL"]

def test_record_then_replay(tmp_path):
    canned = {"choices": [{"message": {"content": "hi"}}]}
    tr = _fake_transport(canned)
    c = RecordingLLMClient(tr, tmp_path / "llm.sqlite", model="m", offline=False)
    assert c.chat([{"role": "user", "content": "q"}]) == canned
    assert c.cache_misses == 1 and len(tr.calls) == 1
    assert c.chat([{"role": "user", "content": "q"}]) == canned
    assert c.cache_hits == 1 and len(tr.calls) == 1

def test_offline_miss_raises(tmp_path):
    c = RecordingLLMClient(_fake_transport({}), tmp_path / "llm.sqlite", model="m", offline=True)
    with pytest.raises(ReplayMissError):
        c.chat([{"role": "user", "content": "never"}])

def test_temperature_int_float_same_key(tmp_path):
    tr = _fake_transport({"ok": 1})
    c = RecordingLLMClient(tr, tmp_path / "llm.sqlite", model="m", offline=False)
    c.chat([{"role": "user", "content": "q"}], temperature=1)
    c.chat([{"role": "user", "content": "q"}], temperature=1.0)
    assert len(tr.calls) == 1 and c.cache_hits == 1

def test_retry_backoff_on_rate_limit():
    waits = []; attempts = [0]
    def flaky(req):
        attempts[0] += 1
        if attempts[0] < 3:
            raise RuntimeError("HTTP 429 code 11210 busy")
        return {"ok": 1}
    assert with_retry(flaky, sleep=waits.append)({"x": 1}) == {"ok": 1}
    assert waits == [15.0, 30.0]

def test_audit_log():
    log = AuditLog()
    log.record(tool="llm_chat", params={"node": "analyst"}, note="ok")
    assert log.as_dicts()[0]["tool"] == "llm_chat"
```

client.py（逐字，仅前缀/默认改）：

```python
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable
from dotenv import load_dotenv
from pydantic import BaseModel

Transport = Callable[[dict[str, Any]], dict[str, Any]]

OFFLINE_DEFAULTS = {
    "LLM_BASE_URL": "http://offline.invalid/v1",
    "LLM_API_KEY": "offline-replay",
    "LLM_MODEL": "spark-x1",
}

class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, dotenv_path: Path | None = None) -> "LLMConfig":
        load_dotenv(dotenv_path)
        if os.environ.get("ALPHALOOM_OFFLINE", "") == "1":
            return cls(base_url=os.environ.get("LLM_BASE_URL", OFFLINE_DEFAULTS["LLM_BASE_URL"]),
                       api_key=os.environ.get("LLM_API_KEY", OFFLINE_DEFAULTS["LLM_API_KEY"]),
                       model=os.environ.get("LLM_MODEL", OFFLINE_DEFAULTS["LLM_MODEL"]))
        return cls(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"],
                   model=os.environ["LLM_MODEL"])

def openai_transport(config: LLMConfig) -> Transport:
    from openai import OpenAI
    client = OpenAI(base_url=config.base_url, api_key=config.api_key)
    def send(request: dict[str, Any]) -> dict[str, Any]:
        return client.chat.completions.create(**request).model_dump()
    return send
```

recording.py / retry.py：逐字 port Hindsight（recording 把 HINDSIGHT_OFFLINE→ALPHALOOM_OFFLINE；retry 全文照抄，RATE_LIMIT_WAITS=(15,30,60) GENERIC_WAITS=(2,4,8)）。audit.py：字段 `tool/params/data_max_ts:int|None/note`，`record(tool,params,data_max_ts=None,note="")` + `as_dicts()`（pydantic BaseModel）。

---

### Task 2: LLM 节点基础设施 + LLMAnalyst 节点

**Files:** Modify `runtime/context.py`(RunContext +llm/audit)/`backtest/runner.py`(run_backtest +llm=)/`nodes/__init__.py`；Create `nodes/llm_nodes.py`；Test `tests/test_llm_nodes.py`

- [ ] Step 1 写测试（5 个）：analyst 产 signal+rationale+stop、坏 JSON 回退 hold、成本注解诚实(llm_calls>=1/deterministic False/latency llm)、无 llm 客户端抛 RuntimeError、**全 REGISTRY 审计 llm 节点禁 deterministic=True（D1 Carryover 10）**
- [ ] Step 2 确认失败（KeyError llm_analyst）
- [ ] Step 3 实现（见下）
- [ ] Step 4 全量 103 passed（98+5）
- [ ] Step 5 commit `feat(nodes): LLM node infra - RunContext.llm/audit, LLMAnalyst with offline replay + honest cost`

context.py RunContext 加（halted 之后）：`llm: Any = None` / `audit: Any = None`。
runner.py：`run_backtest(..., llm=None)`；构造 ctx 后 `ctx.llm = llm; ctx.audit = AuditLog()`（顶部 import）。默认 None → D1/D2 零改动。

nodes/llm_nodes.py：LLMAnalystNode（type=llm_analyst, category=decision, inputs candle+atr, outputs signal, params persona+atr_mult, cost llm_calls_per_bar=1 max_tokens=512 latency=llm deterministic=False）。on_bar：ctx.llm None → RuntimeError("no LLM client bound...")；调 ctx.llm.chat([sys,user], temperature=0.2)；ctx.audit.record；_extract_json 提取 {..}，坏 JSON 或 side 非法 → hold+rationale "parse failed"；合法 → signal 带 side/stop(close∓atr_mult*atr)/rationale/confidence/citations=[]。`nodes/__init__.py` 追加 `import alphaloom.nodes.llm_nodes  # noqa: F401,E402`。

测试与实现完整代码见 Task 2 正文（控制器写入正式计划时含 test_llm_nodes.py 6 断言 + LLMAnalystNode 全文）。

---

### Task 3: Committee 节点（多角色扇出+表决+主席合成）

**Files:** Modify `nodes/llm_nodes.py`（加 CommitteeNode）；Test `tests/test_committee.py`

角色链（锁定）：策略师（读 candle+atr → 提案 side+rationale+confidence）→ 风控官（读提案 → veto/收紧，产 concern）→ 主席（读两者 → 合成终案）。三次 LLM 调用（cost llm_calls_per_bar=3）。结构化 JSON 交接：每角色输出严格 JSON，下一角色输入含上一角色 JSON。风控官 veto（confidence 拉 0 + side→hold）时主席必须尊重。

- [ ] Step 1 测试（4 个）：三角色顺序调用（mock llm 按角色返回不同 canned，验证 3 次 chat + 交接内容含上游 JSON）；风控官 veto → 终案 hold；成本 llm_calls_per_bar==3；输出 signal 含 committee_trace（三角色 JSON 列表，供前端展示）。
- [ ] Step 2 确认失败
- [ ] Step 3 实现 CommitteeNode（type=committee, category=decision, inputs candle+atr, outputs signal, params roles(默认三角色提示词可覆盖)+atr_mult, cost llm_calls_per_bar=3 deterministic False latency llm）。on_bar：依次 ctx.llm.chat 三角色，每次 audit.record；风控官 JSON 含 `veto:bool`；主席合成产 side/stop/rationale/confidence/citations=[]，附加 `committee_trace:[strategist,risk,chair]`。坏 JSON 任一角色 → hold。
- [ ] Step 4 全量 107 passed（103+4）
- [ ] Step 5 commit `feat(nodes): Committee node - strategist/risk-officer/chair fan-out with structured handoff + veto`

### Task 4: KnowledgeRetrieve（RAG）+ PADecisionTree（确定性门控）+ 自撰知识库

**Files:** Create `knowledge/__init__.py`/`knowledge/corpus.py`(BM25)/`knowledge/data/{grid,dca,price_action}.md`(自撰精要，中英对照)/`nodes/rag_nodes.py`(KnowledgeRetrieve)/`nodes/pa_gate.py`(PADecisionTree)；Modify `nodes/__init__.py`；Test `tests/test_rag_pa.py`

- [ ] Step 1 测试：BM25 检索命中相关文档（"martingale risk" → dca.md 段）；KnowledgeRetrieve 产 citations 进 signal（下游可查）；**强制引用**——LLMAnalyst 若连了 KnowledgeRetrieve，citations 非空才允许非 hold（约定在 risk_gate 或单独 test 锁）；PADecisionTree 纯确定性（同输入同输出、cost 全 0 deterministic True）数值门控（如 close>ema 且 atr>阈值才放行 long，否则 hold）——不调 LLM。
- [ ] Step 2 确认失败
- **T4 审查修订（sanctioned）**：`_tokenize` 增加 CJK 支持——中日韩字符连串按 2-gram 切分（约 6 行），使 "马丁格尔 爆仓"→dca、"网格 间距"→grid 可命中（语料中英对照但原正则 [a-z0-9]+ 使中文全灭，Studio 中文用户 RAG 断裂）。
- [ ] Step 3 实现：corpus.py 极简 BM25（分词+idf+bm25 打分，纯 stdlib，语料 3 个自撰 md 文件——**自己写精要不整包搬 PA_Agent 语料**，网格机制/DCA 马丁风险/Al Brooks 价格行为核心概念各一页中英对照）；KnowledgeRetrieveNode(type=knowledge_retrieve, category=rag, inputs query 可选/candle, outputs citations:SERIES 或专用 pin, cost 0)；PADecisionTreeNode(type=pa_decision_tree, category=decision, inputs candle+ema+atr+signal, outputs signal, cost 0 deterministic True)——数值门控收紧/否决上游信号。
- [ ] Step 4 全量绿（+~5）
- [ ] Step 5 commit `feat(nodes): KnowledgeRetrieve BM25 RAG + PADecisionTree deterministic gate + hand-written corpus`

### Task 5: 反思闭环——Reflector + 经验库

**Files:** Create `memory/__init__.py`/`memory/experience_store.py`/`nodes/reflection.py`(Reflector)/`nodes/rag_nodes.py`(+ExperienceRetrieve/ExperienceWrite)；Test `tests/test_reflection.py`

反思语义（port Hindsight reasonable_but_wrong 分类学）：平仓事件 → Reflector 用 **过程/结局分离**打分——过程好坏（决策是否基于合理信号）× 结局好坏（是否盈利）→ 四象限（reasonable_and_right / reasonable_but_wrong / lucky / bad_process）。经验库按**市场状态桶**（trend_up/trend_down/range，由 ema/atr 派生）索引，写 {桶, 配置摘要, 结局, 教训}；ExperienceRetrieve 按当前桶检索注入决策上下文。

- [ ] Step 1 测试：experience_store 按桶写入+检索隔离；Reflector 四象限分类（好过程坏结局→reasonable_but_wrong，与 Hindsight 一致）；记忆开/关（有无 ExperienceRetrieve）signal 上下文差异可测；ExperienceWrite 幂等。
- [ ] Step 2 确认失败
- [ ] Step 3 实现：experience_store.py（SQLite 桶索引，data/experience.sqlite）；ReflectorNode(type=reflector, category=reflection, inputs signal+平仓 pnl, outputs verdict, cost 0 或可选 LLM)；ExperienceRetrieveNode/ExperienceWriteNode(category rag/reflection, cost 0)。市场状态桶派生纯函数（ema 斜率+atr）。
- [ ] Step 4 全量绿
- [ ] Step 5 commit `feat(memory): reflection loop - Reflector process/outcome scoring + experience store by regime bucket`

### Task 6: Copilot 后端——Text-to-Blueprint + Explain + Optimize

**Files:** Create `copilot/__init__.py`/`copilot/layout.py`(自动布局)/`copilot/prompts.py`/`copilot/blueprint.py`；Test `tests/test_copilot.py`

- [ ] Step 1 测试（mock llm 返回 loom JSON）：text_to_blueprint NL→合法 loom（compile ok）；**编译错误自修复**——首次 mock 返回绕风控的坏图（TYPE_MISMATCH），第二次 mock 读 CompileError 返回修正图 → 最终 ok（验证 ≤3 次重试 + fix_hint 进重试提示）；explain 产非空叙述；optimize 读 report 产 diff；自动布局给每节点 position（无重叠）。
- [ ] Step 2 确认失败
- [ ] Step 3 实现：prompts.py（系统提示含全 NodeDef 目录 + loom schema + "必须过 RiskGate" 约束）；blueprint.py text_to_blueprint(nl, defs, llm, compile_fn) 循环：LLM 生成 loom→compile→ok 则布局返回，失败则把 CompileError.fix_hint 塞回提示重试；layout.py 简单分层布局（按拓扑 order 分列）；explain/optimize 同构。
- [ ] Step 4 全量绿
- [ ] Step 5 commit `feat(copilot): text-to-blueprint with compile-error self-repair, explain, optimize`

### Task 7: Text-to-Node 沙箱（AST 白名单热注册）

**Files:** Create `sandbox/node_sandbox.py`/`sandbox/errors.py`；Test `tests/test_node_sandbox.py`

- [ ] Step 1 测试：合法纯计算节点源码 → compile_node_source 注册进 REGISTRY 可实例化；恶意源码全部 SandboxError——`import os`、`open(...)`、`__import__`、`exec/eval`、`while True`（可选循环上限）、访问 `__builtins__`、dunder 逃逸；注册后热可用于编译。
- [ ] Step 2 确认失败
- [ ] Step 3 实现：node_sandbox.py AST walk 白名单（允许的 AST 节点类型集合 + import 白名单仅 math/statistics + 禁 Attribute 访问 dunder + 禁 Call open/exec/eval/__import__）；通过则 `exec` 于受限 namespace（无 __builtins__ 或最小白名单）触发 @node 注册。降级保险丝：模板版 make_threshold_node(params) 只填参数。
- [ ] Step 4 全量绿
- [ ] Step 5 commit `feat(sandbox): text-to-node AST whitelist compiler with hot-registration`

### Task 8: Copilot/custom-node API 端点 + run mode=replay + LLM 绑定

**Files:** Modify `api/app.py`(copilot 端点+custom-node+服务注入 llm)/`api/service.py`(_worker 绑 llm)/`api/schemas.py`(RunIn +mode)；Test `tests/test_copilot_api.py`

- [ ] Step 1 测试（TestClient）：POST /api/copilot/blueprint NL→{loom,notes} 且 loom 可 compile；/explain /optimize；POST /api/nodes/custom 沙箱注册后 /api/nodes 出现；run mode=replay 走 LLM 节点（mock/录制）完成；服务从 env 构建 RecordingLLMClient（ALPHALOOM_OFFLINE=1 时 offline）注入 run。
- [ ] Step 2 确认失败
- [ ] Step 3 实现：app.py create_app 加 llm_db 参数构建 RecordingLLMClient（LLMConfig.from_env + openai_transport + with_retry，offline 跟 env）；RunService.start 透传 llm；copilot 端点调 copilot/blueprint.py；custom-node 调沙箱。**控制器 live 走查专项**：Copilot 生成图→编译自修复→回测必须 live 验证（D2 教训）。
- [ ] Step 4 全量绿
- [ ] Step 5 commit `feat(api): copilot + custom-node endpoints, LLM client injection, replay mode`

### Task 9: Copilot 前端侧栏（Text-to-Blueprint + diff 预览）

**Files:** Create `frontend/src/components/CopilotPanel.tsx`/`frontend/src/lib/copilot.ts`；Modify `Studio.tsx`(挂 Copilot 侧栏 + diff 应用)；验收=build+tsc+vitest 绿 + 控制器 live 走查
- [ ] 聊天输入→POST /api/copilot/blueprint→画布 diff 预览（新增绿/删除红/改动黄高亮）→应用按钮落地→一键回测。explain/optimize 按钮。committee_trace/citations/rationale 在节点 hover 或 Inspector 展示。
- [ ] commit `feat(studio): copilot sidebar - text-to-blueprint chat, diff preview, apply`

### Task 10: 加速回放模式前端 + 反思/记忆可视化

**Files:** Modify `Terminal.tsx`/`Studio.tsx`（replay 模式选择器 + committee_trace/citations/reflection verdict 展示 + 记忆开关对比）；验收=build+走查
- [ ] Run 配置加 mode（backtest/replay）；Terminal 展示 committee 三角色轨迹、RAG 引用徽章、Reflector 四象限、记忆开/关对比。
- [ ] commit `feat(frontend): replay mode selector, committee trace, citations badges, reflection verdicts`

### Task 11: D3 集成走查 + 录制种子 + d3-complete

**Files:** Create `scripts/seed_recordings.py`(录制种子演示运行，入库 llm_calls.sqlite)/`docs/demo-recordings/`；Modify README 骨架
- [ ] 用真实讯飞 Spark 跑 2-3 个演示（LLMAnalyst/Committee 各一蓝图 + 一次 Copilot 生成）录制进 llm_calls.sqlite（**入库**供离线回放）；ALPHALOOM_OFFLINE=1 全量离线回放验证零配额；控制器 live 走查全链（聊天生成→自修复→回测→反思→Terminal）；后端全量 + 前端绿；密钥扫描 0；tag d3-complete。
- [ ] commit `feat(app): recorded demo seeds, offline replay verified, D3 walkthrough` + tag d3-complete

## D3 Carryover（D4 输入）
1. 委员会消融/进化实验室/保真度阶梯/记分卡是 D4；D3 只交付单蓝图的 LLM 决策+反思。
2. 录制库 llm_calls.sqlite 入库供离线演示——**Task 11 硬前置：.gitignore 现有 `data/*.sqlite` 会挡住它，必须加 `!data/llm_calls.sqlite` 例外否则离线回放全 miss**（T1 审查实测 git check-ignore 确认被挡）；注意体积（种子控制在少量演示运行）；真实 key 演示前用户自填 .env。
3. 沙箱 AST 白名单是最小可用版；D4 若开放用户自定义节点市场需加资源限额（CPU/内存/超时）。
4. 强制引用（citations 非空才允许交易）在 D3 是软约定+测试；D4 可升级为编译期类型（RAG 盖章类型，类比 RiskStampedSignal）。
8. **EOD 强平不被反思**（T5 审查确认）：runner.py 收盘强平的 `broker.on_bar` 在最后一次 `engine.step` 之后，故每次回测最后一笔（EOD 合成平仓）不入经验库——运行中所有正常平仓均已反思且引擎级证明，仅这一笔缺席；D4 反思可视化时补（EOD 后补一步 reflector，或在报告路径反思 EOD 平仓）。
6. citations 合流：signal 自带 + pin 若携同一条 citation 会输出重复（真实蓝图不触发——LLM 节点自带 citations 恒空、pin 唯一来源；前端引用徽章可选 `dict.fromkeys` 去重，D4）。
7. CJK 2-gram 改变了 BM25 全局统计量使英文绝对分值变化（排序不变、当前无 score 阈值依赖）——D4 若加 score 门限逻辑需重标定。
5. `_extract_json` 字符级平衡扫描不识别 JSON 字符串值内的孤立花括号（fail-safe 回退 hold，被 system prompt "No prose outside JSON" 覆盖）——T2/T3 复用它，若想硬化改 `json.JSONDecoder().raw_decode()` 从 `{` 逐位尝试（D4）。
