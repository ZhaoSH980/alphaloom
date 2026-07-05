# AlphaLoom D1 Implementation Plan — 图核心/引擎/回测/预置蓝图

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 AlphaLoom 后端图核心：.loom 蓝图 schema、类型系统（风控盖章 + as-of 因果）、编译器（子图展开/环规则/成本证书）、事件驱动执行引擎（状态/断点钩子/全程录制）、PaperBroker、回测 runner + CLI、两张可跑通的预置蓝图、样本数据脚本。

**Architecture:** 纯 Python 3.13 后端包 `alphaloom`（本日零 Web 依赖）。蓝图 = JSON 数据类模型 → 编译器产出拓扑执行计划 + 成本证书 → 事件驱动引擎逐 bar 推进（数据引脚值带 as-of 时间戳，运行时守卫强制因果）→ PaperBroker 次 bar 开盘价撮合 → 回测报告 + SQLite 全程录制。安全性质由类型系统承载：`ExecuteOrder` 只接受 `risk_stamped_signal`，该类型仅 `RiskGate` 能产出。

**Tech Stack:** Python 3.13（stdlib 为主）、pytest + hypothesis、SQLite（录制与行情）、OKX v5 公共 REST（仅样本库脚本，测试不联网）。

**执行约定（沿用 Hindsight，见 memory `hindsight-workflow-conventions`）：**
- 每任务一个实现者子智能体 + 单审查者两阶段审查（spec 逐字节 diff 先行，质量对抗审查在后）；Important+ 发现由原实现者修复后复审。审查者派工提示词加一句："You ARE the reviewer, do the work yourself in this session."
- 计划即权威：实现偏差先改本计划文件（sanctioned deviation）再动代码；跨任务备忘写进文末 Carryover。
- 环境：Windows 11 + Git Bash 风格命令；venv 在 `backend/.venv`（`.venv/Scripts/python`）；Python 3.13；测试 `cd backend && .venv/Scripts/python -m pytest -q`；一切 CLI 入口先 `sys.stdout.reconfigure(encoding="utf-8")`（cp1252 陷阱）。
- 安全红线：`.env` 绝不入库；任务收尾禁止提交任何密钥形状字符串；样本库脚本只调 OKX **公共**端点（无鉴权）。

---

## 文件结构总览（本日新建）

```
alphaloom/                              # 仓库根（已存在 docs/）
├── .gitignore                          # Task 1
├── LICENSE                             # Task 1（MIT）
├── backend/
│   ├── pyproject.toml                  # Task 1
│   ├── alphaloom/
│   │   ├── __init__.py                 # Task 1
│   │   ├── graph/
│   │   │   ├── __init__.py             # Task 2
│   │   │   ├── types.py                # Task 2：PinType、Stamped、CostAnnotation
│   │   │   ├── model.py                # Task 2：PortRef/EdgeSpec/NodeSpec/BlueprintSpec + .loom IO
│   │   │   ├── errors.py               # Task 4：CompileError（结构化，LLM 可消费）
│   │   │   ├── compiler.py             # Task 4 结构/类型检查 → Task 5 子图/环 → Task 6 接成本
│   │   │   └── cost.py                 # Task 6：CostCertificate 聚合
│   │   ├── nodes/
│   │   │   ├── __init__.py             # Task 3（导入触发内置节点注册，本日先空注册）
│   │   │   ├── registry.py             # Task 3：@node 装饰器 + NodeDef + REGISTRY
│   │   │   ├── data.py                 # Task 11：CandleFeedNode
│   │   │   ├── indicators.py           # Task 11：Ema/Atr/Rsi（增量）
│   │   │   ├── gates.py                # Task 11：CrossSignal/ScenarioGate/RiskGate/KillSwitch
│   │   │   ├── sizing.py               # Task 11：PositionSizer
│   │   │   └── execution.py            # Task 11：ExecuteOrder（paper）
│   │   ├── runtime/
│   │   │   ├── __init__.py             # Task 7
│   │   │   ├── events.py               # Task 7：BarEvent
│   │   │   ├── context.py              # Task 7：SimClock/RunContext/CausalityError
│   │   │   ├── recorder.py             # Task 8：SQLite 全程录制
│   │   │   └── engine.py               # Task 8：波次调度/状态/反馈边/断点钩子/因果守卫
│   │   ├── brokers/
│   │   │   ├── __init__.py             # Task 9
│   │   │   ├── base.py                 # Task 9：Order/Fill/Broker 协议
│   │   │   └── paper.py                # Task 9：PaperBroker（次 bar 开盘撮合）
│   │   ├── data/
│   │   │   ├── __init__.py             # Task 10
│   │   │   ├── source.py               # Task 10：DataSource ABC（多市场抽象）
│   │   │   └── sqlite_source.py        # Task 10：SQLiteMarketData
│   │   ├── backtest/
│   │   │   ├── __init__.py             # Task 12
│   │   │   └── runner.py               # Task 12：run_backtest → BacktestReport
│   │   └── cli.py                      # Task 12：python -m alphaloom.cli run ...
│   └── tests/
│       ├── conftest.py                 # Task 1
│       ├── fixtures/
│       │   └── synth.py                # Task 10：确定性合成 K 线（测试绝不联网）
│       ├── test_smoke.py               # Task 1
│       ├── test_graph_model.py         # Task 2
│       ├── test_registry.py            # Task 3
│       ├── test_compiler_typecheck.py  # Task 4
│       ├── test_compiler_subgraph.py   # Task 5
│       ├── test_cost_certificate.py    # Task 6
│       ├── test_causality.py           # Task 7
│       ├── test_engine.py              # Task 8
│       ├── test_paper_broker.py        # Task 9
│       ├── test_data_source.py         # Task 10
│       ├── test_builtin_nodes.py       # Task 11
│       └── test_backtest_e2e.py        # Task 12
├── blueprints/
│   ├── ema_cross.loom                  # Task 12
│   └── breakout_scenario.loom          # Task 12
└── scripts/
    └── build_sample_db.py              # Task 13：OKX 公共 REST 拉 90 天 1m（可续传，非测试路径）
```

**跨任务锁定签名（所有任务必须一致，改动即 sanctioned deviation）：**
- `PinType(str, Enum)`：`EXEC/CANDLE/SERIES/SIGNAL/RISK_STAMPED_SIGNAL/BOOL`
- `Stamped(value: Any, as_of: int)`（as_of = 毫秒 epoch，与 OKX ts 一致）
- `CostAnnotation(llm_calls_per_bar=0, max_tokens_per_call=0, latency_class="fast", deterministic=True)`
- `CompileError(code, message, node_id=None, port=None, fix_hint=None)`；错误码集合：`UNKNOWN_NODE_TYPE / BAD_PORT_REF / DUP_NODE_ID / TYPE_MISMATCH / ILLEGAL_CYCLE / PARAM_INVALID`
- `compile_blueprint(bp: BlueprintSpec, *, bars_per_day=1440) -> CompileResult(ok, errors, order, bindings, certificate, nodes)`——`nodes` 为**展开后**的 `dict[node_id, NodeSpec]`（含子图展开产物 `sub/inner`），runner 用它实例化
- `InputBinding(dst_port, src_node, src_port, feedback: bool)`
- 节点类协议：`setup(self, params: dict) -> None`；`on_bar(self, ctx: RunContext, inputs: dict) -> dict`；实例属性 `self.state: dict`
- 信号 dict：`{"side": "long"|"short"|"flat"|"hold", "qty": float, "stop": float|None, "reason": str}`（`hold`=不动作，`flat`=平仓）；RiskGate 输出在其上增加 `"risk": {"checked": True, "blocked": bool, "checks": [...]}`
- K 线 dict：`{"ts": int, "open": float, "high": float, "low": float, "close": float, "volume": float}`（ts=bar 开始毫秒；bar 收盘时刻 = ts + bar_ms）

---

### Task 1: 仓库脚手架 + venv + pytest 冒烟

**Files:**
- Create: `.gitignore`, `LICENSE`, `backend/pyproject.toml`, `backend/alphaloom/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_smoke.py`

- [ ] **Step 1: 创建 venv 与目录**

```bash
cd F:/AIProjects/my_show/alphaloom
mkdir -p backend/alphaloom backend/tests blueprints scripts
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -U pip pytest hypothesis
```

- [ ] **Step 2: 写入 `.gitignore`**

```gitignore
backend/.venv/
__pycache__/
*.pyc
.pytest_cache/
.hypothesis/
data/sample.sqlite
runs/
.env
.env.*
```

- [ ] **Step 3: 写入 `LICENSE`（MIT，版权行 `Copyright (c) 2026 Zhao Chenghao`，标准 MIT 全文）与 `backend/pyproject.toml`**

```toml
[project]
name = "alphaloom"
version = "0.1.0"
description = "Agent-native quant trading platform - the graph IS the agent"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "hypothesis>=6"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["alphaloom*"]
```

- [ ] **Step 4: 写入 `backend/alphaloom/__init__.py`（`__version__ = "0.1.0"`）、空 `backend/tests/conftest.py`、冒烟测试**

```python
# backend/tests/test_smoke.py
import alphaloom

def test_version():
    assert alphaloom.__version__ == "0.1.0"
```

- [ ] **Step 5: editable 安装并跑测试**

```bash
cd backend && .venv/Scripts/python -m pip install -e . && .venv/Scripts/python -m pytest -q
```
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "chore: scaffold backend package, venv, pytest smoke"
```

---

### Task 2: 图数据模型 + .loom 序列化（graph/types.py, graph/model.py）

**Files:**
- Create: `backend/alphaloom/graph/__init__.py`（空）, `backend/alphaloom/graph/types.py`, `backend/alphaloom/graph/model.py`
- Test: `backend/tests/test_graph_model.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_graph_model.py
import json
import pytest
from alphaloom.graph.types import PinType, Stamped, CostAnnotation
from alphaloom.graph.model import (
    PortRef, EdgeSpec, NodeSpec, BlueprintSpec, loads_loom, dumps_loom,
)

LOOM = {
    "id": "bp1", "name": "demo",
    "nodes": [
        {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP"}},
        {"id": "ema1", "type": "ema", "params": {"period": 20}},
    ],
    "edges": [
        {"from": "feed.out", "to": "ema1.candle"},
        {"from": "ema1.value", "to": "feed.dummy", "feedback": True},
    ],
    "meta": {"author": "test"},
}

def test_roundtrip():
    bp = loads_loom(json.dumps(LOOM))
    assert bp.id == "bp1" and len(bp.nodes) == 2
    e0 = bp.edges[0]
    assert e0.src == PortRef("feed", "out") and e0.dst == PortRef("ema1", "candle")
    assert bp.edges[1].feedback is True and e0.feedback is False
    again = loads_loom(dumps_loom(bp))
    assert again == bp

def test_bad_port_ref_raises():
    bad = dict(LOOM, edges=[{"from": "no_dot", "to": "a.b"}])
    with pytest.raises(ValueError, match="port ref"):
        loads_loom(json.dumps(bad))

def test_stamped_and_cost_defaults():
    s = Stamped(42.0, as_of=1700000000000)
    assert s.value == 42.0 and s.as_of == 1700000000000
    c = CostAnnotation()
    assert c.llm_calls_per_bar == 0 and c.deterministic is True
    assert PinType.RISK_STAMPED_SIGNAL.value == "risk_stamped_signal"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_graph_model.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.graph）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/graph/types.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

class PinType(str, Enum):
    EXEC = "exec"
    CANDLE = "candle"
    SERIES = "series"
    SIGNAL = "signal"
    RISK_STAMPED_SIGNAL = "risk_stamped_signal"
    BOOL = "bool"

@dataclass(frozen=True)
class Stamped:
    """数据引脚上流动的值：value + as-of 毫秒时间戳（因果类型系统的载体）。"""
    value: Any
    as_of: int

@dataclass(frozen=True)
class CostAnnotation:
    llm_calls_per_bar: int = 0
    max_tokens_per_call: int = 0
    latency_class: str = "fast"   # fast | slow | llm
    deterministic: bool = True
```

```python
# backend/alphaloom/graph/model.py
from __future__ import annotations
import json
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PortRef:
    node_id: str
    port: str

def _parse_ref(s: str) -> PortRef:
    if s.count(".") != 1:
        raise ValueError(f"bad port ref {s!r}: expected 'node.port'")
    n, p = s.split(".")
    if not n or not p:
        raise ValueError(f"bad port ref {s!r}: empty segment")
    return PortRef(n, p)

@dataclass(frozen=True)
class EdgeSpec:
    src: PortRef
    dst: PortRef
    feedback: bool = False

@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str
    params: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.id, self.type))

@dataclass
class BlueprintSpec:
    id: str
    name: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    meta: dict = field(default_factory=dict)

    def __eq__(self, other):
        return (isinstance(other, BlueprintSpec)
                and (self.id, self.name, self.nodes, self.edges, self.meta)
                == (other.id, other.name, other.nodes, other.edges, other.meta))

def loads_loom(text: str) -> BlueprintSpec:
    raw = json.loads(text)
    nodes = [NodeSpec(n["id"], n["type"], dict(n.get("params", {}))) for n in raw["nodes"]]
    edges = [EdgeSpec(_parse_ref(e["from"]), _parse_ref(e["to"]), bool(e.get("feedback", False)))
             for e in raw.get("edges", [])]
    return BlueprintSpec(raw["id"], raw.get("name", raw["id"]), nodes, edges, dict(raw.get("meta", {})))

def dumps_loom(bp: BlueprintSpec) -> str:
    return json.dumps({
        "id": bp.id, "name": bp.name,
        "nodes": [{"id": n.id, "type": n.type, "params": n.params} for n in bp.nodes],
        "edges": [{"from": f"{e.src.node_id}.{e.src.port}", "to": f"{e.dst.node_id}.{e.dst.port}",
                   **({"feedback": True} if e.feedback else {})} for e in bp.edges],
        "meta": bp.meta,
    }, ensure_ascii=False, indent=2)

def load_loom_file(path) -> BlueprintSpec:
    from pathlib import Path
    return loads_loom(Path(path).read_text(encoding="utf-8"))
```

并创建空 `backend/alphaloom/graph/__init__.py`。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_graph_model.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(graph): blueprint model, pin types, Stamped, .loom serialization"
```

---

### Task 3: 节点注册表 + @node 装饰器（nodes/registry.py）

**Files:**
- Create: `backend/alphaloom/nodes/__init__.py`, `backend/alphaloom/nodes/registry.py`
- Test: `backend/tests/test_registry.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_registry.py
import pytest
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node, get_node_def, create_instance, NodeDef, REGISTRY
from alphaloom.graph.model import NodeSpec

@node(type="t_add", category="test",
      inputs={"a": PinType.SERIES, "b": PinType.SERIES},
      outputs={"sum": PinType.SERIES},
      params={"scale": float},
      cost=CostAnnotation())
class AddNode:
    def setup(self, params):
        self.scale = params.get("scale", 1.0)
    def on_bar(self, ctx, inputs):
        return {"sum": (inputs["a"] + inputs["b"]) * self.scale}

def test_registered():
    d = get_node_def("t_add")
    assert isinstance(d, NodeDef) and d.cls is AddNode
    assert d.inputs["a"] is PinType.SERIES and d.outputs["sum"] is PinType.SERIES

def test_create_instance_runs_setup_and_state():
    inst = create_instance(NodeSpec("n1", "t_add", {"scale": 2.0}))
    assert inst.scale == 2.0 and inst.state == {}
    assert inst.on_bar(None, {"a": 1.0, "b": 2.0}) == {"sum": 6.0}

def test_unknown_type():
    with pytest.raises(KeyError):
        get_node_def("nope")

def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):
        node(type="t_add", category="test", inputs={}, outputs={})(AddNode)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_registry.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/nodes/registry.py
from __future__ import annotations
from dataclasses import dataclass, field
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.graph.model import NodeSpec

@dataclass(frozen=True)
class NodeDef:
    type: str
    category: str
    cls: type
    inputs: dict[str, PinType]
    outputs: dict[str, PinType]
    params: dict[str, type] = field(default_factory=dict)
    cost: CostAnnotation = CostAnnotation()

REGISTRY: dict[str, NodeDef] = {}

def node(*, type: str, category: str, inputs: dict, outputs: dict,
         params: dict | None = None, cost: CostAnnotation = CostAnnotation()):
    def deco(cls):
        if type in REGISTRY:
            raise ValueError(f"node type {type!r} already registered")
        REGISTRY[type] = NodeDef(type, category, cls, dict(inputs), dict(outputs),
                                 dict(params or {}), cost)
        cls.node_type = type
        return cls
    return deco

def get_node_def(t: str) -> NodeDef:
    return REGISTRY[t]

def create_instance(spec: NodeSpec):
    d = get_node_def(spec.type)
    inst = d.cls()
    inst.state = {}
    inst.node_id = spec.id
    inst.def_ = d
    inst.setup(dict(spec.params))
    return inst
```

`backend/alphaloom/nodes/__init__.py` 暂时只写：

```python
from alphaloom.nodes.registry import REGISTRY, node, get_node_def, create_instance  # noqa: F401
```

（Task 11 在此追加内置节点模块导入以触发注册。）

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_registry.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(nodes): @node decorator, NodeDef registry, instance factory"
```

---

### Task 4: 编译器·结构与类型检查 + 风控盖章规则（graph/errors.py, graph/compiler.py）

**Files:**
- Create: `backend/alphaloom/graph/errors.py`, `backend/alphaloom/graph/compiler.py`
- Test: `backend/tests/test_compiler_typecheck.py`

本任务只做：结构校验（未知类型/坏引用/重复 id/未知端口）、边类型相等检查、拓扑排序（暂不处理 feedback 与子图——Task 5）。**风控盖章规则不是特例代码，而是类型检查的自然结果**：测试必须证明"LLM/信号节点直连 ExecuteOrder 会得到 TYPE_MISMATCH，且 fix_hint 指向 RiskGate"。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_compiler_typecheck.py
import json
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

# 测试专用最小节点集（与内置节点解耦，前缀 tc_）
@node(type="tc_feed", category="test", inputs={}, outputs={"out": PinType.CANDLE})
class TcFeed:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_brain", category="test",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL})
class TcBrain:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_riskgate", category="test",
      inputs={"signal": PinType.SIGNAL}, outputs={"stamped": PinType.RISK_STAMPED_SIGNAL})
class TcRisk:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

@node(type="tc_exec", category="test",
      inputs={"signal": PinType.RISK_STAMPED_SIGNAL}, outputs={})
class TcExec:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

def _bp(edges):
    return loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "brain", "type": "tc_brain"},
            {"id": "risk", "type": "tc_riskgate"},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": edges,
    }))

GOOD = [
    {"from": "feed.out", "to": "brain.candle"},
    {"from": "brain.signal", "to": "risk.signal"},
    {"from": "risk.stamped", "to": "ex.signal"},
]

def test_good_graph_compiles_with_topo_order():
    r = compile_blueprint(_bp(GOOD))
    assert r.ok and r.errors == []
    assert r.order.index("feed") < r.order.index("brain") < r.order.index("risk") < r.order.index("ex")
    b = {x.dst_port: x for x in r.bindings["ex"]}
    assert b["signal"].src_node == "risk" and b["signal"].feedback is False

def test_bypassing_riskgate_is_type_error():
    bad = [
        {"from": "feed.out", "to": "brain.candle"},
        {"from": "brain.signal", "to": "ex.signal"},   # 直连下单 → 必须编译失败
    ]
    r = compile_blueprint(_bp(bad))
    assert not r.ok
    err = [e for e in r.errors if e.code == "TYPE_MISMATCH"][0]
    assert err.node_id == "ex" and err.port == "signal"
    assert "RiskGate" in err.fix_hint  # 报错为 LLM 消费设计

def test_unknown_node_and_bad_port():
    r = compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [{"id": "a", "type": "no_such"}],
        "edges": [{"from": "a.x", "to": "a.y"}],
    })))
    codes = {e.code for e in r.errors}
    assert "UNKNOWN_NODE_TYPE" in codes

def test_duplicate_node_id():
    r = compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [{"id": "a", "type": "tc_feed"}, {"id": "a", "type": "tc_feed"}],
        "edges": [],
    })))
    assert any(e.code == "DUP_NODE_ID" for e in r.errors)

def test_error_json_serializable():
    r = compile_blueprint(_bp([{"from": "brain.signal", "to": "ex.signal"}]))
    d = r.errors[0].to_dict()
    assert set(d) == {"code", "message", "node_id", "port", "fix_hint"}
    json.dumps(d)  # 不抛即通过
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_compiler_typecheck.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.graph.compiler）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/graph/errors.py
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class CompileError:
    code: str
    message: str
    node_id: str | None = None
    port: str | None = None
    fix_hint: str | None = None   # 面向 LLM 的修复提示（结构化反馈环境的一部分）

    def to_dict(self) -> dict:
        return asdict(self)
```

```python
# backend/alphaloom/graph/compiler.py
from __future__ import annotations
from dataclasses import dataclass, field
from graphlib import TopologicalSorter, CycleError
from alphaloom.graph.model import BlueprintSpec
from alphaloom.graph.errors import CompileError
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import REGISTRY

@dataclass(frozen=True)
class InputBinding:
    dst_port: str
    src_node: str
    src_port: str
    feedback: bool

@dataclass
class CompileResult:
    ok: bool
    errors: list[CompileError]
    order: list[str] = field(default_factory=list)
    bindings: dict[str, list[InputBinding]] = field(default_factory=dict)
    certificate: object | None = None   # Task 6 填充
    nodes: dict = field(default_factory=dict)   # 展开后的 {node_id: NodeSpec}，runner 实例化用

_HINTS = {
    PinType.RISK_STAMPED_SIGNAL: (
        "This input only accepts risk_stamped_signal, which is produced solely by a "
        "RiskGate node. Route the signal through a RiskGate before this node."),
}

def compile_blueprint(bp: BlueprintSpec, *, bars_per_day: int = 1440) -> CompileResult:
    errors: list[CompileError] = []
    seen: set[str] = set()
    for n in bp.nodes:
        if n.id in seen:
            errors.append(CompileError("DUP_NODE_ID", f"duplicate node id {n.id!r}", node_id=n.id))
        seen.add(n.id)
        if n.type not in REGISTRY:
            errors.append(CompileError("UNKNOWN_NODE_TYPE", f"unknown node type {n.type!r}",
                                       node_id=n.id,
                                       fix_hint=f"Available types: {sorted(REGISTRY)[:20]}"))
    if errors:
        return CompileResult(False, errors)

    defs = {n.id: REGISTRY[n.type] for n in bp.nodes}
    bindings: dict[str, list[InputBinding]] = {n.id: [] for n in bp.nodes}
    for e in bp.edges:
        src_ok = e.src.node_id in defs and e.src.port in defs[e.src.node_id].outputs
        dst_ok = e.dst.node_id in defs and e.dst.port in defs[e.dst.node_id].inputs
        if not src_ok or not dst_ok:
            errors.append(CompileError(
                "BAD_PORT_REF",
                f"edge {e.src.node_id}.{e.src.port} -> {e.dst.node_id}.{e.dst.port} references unknown node/port",
                node_id=(e.src.node_id if not src_ok else e.dst.node_id)))
            continue
        t_out = defs[e.src.node_id].outputs[e.src.port]
        t_in = defs[e.dst.node_id].inputs[e.dst.port]
        if t_out is not t_in:
            errors.append(CompileError(
                "TYPE_MISMATCH",
                f"{e.dst.node_id}.{e.dst.port} expects {t_in.value}, got {t_out.value} "
                f"from {e.src.node_id}.{e.src.port}",
                node_id=e.dst.node_id, port=e.dst.port,
                fix_hint=_HINTS.get(t_in, f"Produce a {t_in.value} value upstream.")))
            continue
        bindings[e.dst.node_id].append(InputBinding(e.dst.port, e.src.node_id, e.src.port, e.feedback))
    if errors:
        return CompileResult(False, errors)

    deps = {n.id: {b.src_node for b in bindings[n.id] if not b.feedback} for n in bp.nodes}
    try:
        order = list(TopologicalSorter(deps).static_order())
    except CycleError as ce:
        return CompileResult(False, [CompileError(
            "ILLEGAL_CYCLE", f"cycle without feedback edge: {ce.args[1]}",
            fix_hint="Mark exactly the intentional back edge with \"feedback\": true; "
                     "feedback values are delivered on the NEXT bar.")])
    return CompileResult(True, [], order, bindings,
                         nodes={n.id: n for n in bp.nodes})
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_compiler_typecheck.py -q`
Expected: `5 passed`（其余测试文件不回归）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(compiler): structural+type checks, risk-stamp rule as type error, topo order"
```

---

### Task 5: 编译器·子图展开 + 反馈环规则（compiler.py 扩展）

**Files:**
- Modify: `backend/alphaloom/graph/compiler.py`
- Test: `backend/tests/test_compiler_subgraph.py`

子图约定：`NodeSpec.type == "subgraph"`，`params = {"blueprint": <内联 loom dict>, "inputs": {"外部入口": "内部节点.端口"}, "outputs": {"外部出口": "内部节点.端口"}}`。编译第一步展开：内部节点改名 `子图id/内部id`，外部指向子图端口的边重写到映射的内部端口；递归展开，深度上限 8。展开后统一走 Task 4 的检查。反馈环规则已在 Task 4 落地（TopologicalSorter 忽略 feedback 边），本任务补测试证明。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_compiler_subgraph.py
import json
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

import tests.test_compiler_typecheck  # noqa: F401  确保 tc_* 已注册

@node(type="tc_loop", category="test",
      inputs={"sig_in": PinType.SIGNAL}, outputs={"candle_out": PinType.CANDLE})
class TcLoop:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

INNER = {
    "id": "inner", "name": "inner",
    "nodes": [
        {"id": "brain", "type": "tc_brain"},
        {"id": "risk", "type": "tc_riskgate"},
    ],
    "edges": [{"from": "brain.signal", "to": "risk.signal"}],
}

def test_subgraph_expansion_and_typecheck():
    outer = {
        "id": "outer", "name": "outer",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "sub", "type": "subgraph", "params": {
                "blueprint": INNER,
                "inputs": {"candle_in": "brain.candle"},
                "outputs": {"stamped_out": "risk.stamped"},
            }},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "sub.candle_in"},
            {"from": "sub.stamped_out", "to": "ex.signal"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(outer)))
    assert r.ok, [e.to_dict() for e in r.errors]
    assert "sub/brain" in r.order and "sub/risk" in r.order
    assert r.order.index("feed") < r.order.index("sub/brain") < r.order.index("sub/risk") < r.order.index("ex")
    b = {x.dst_port: x for x in r.bindings["ex"]}
    assert b["signal"].src_node == "sub/risk"

def test_subgraph_cannot_bypass_risk_type():
    outer = {
        "id": "outer", "name": "outer",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "sub", "type": "subgraph", "params": {
                "blueprint": INNER,
                "inputs": {"candle_in": "brain.candle"},
                "outputs": {"raw_out": "brain.signal"},
            }},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "sub.candle_in"},
            {"from": "sub.raw_out", "to": "ex.signal"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(outer)))
    assert not r.ok and any(e.code == "TYPE_MISMATCH" for e in r.errors)

def test_feedback_cycle_legal_and_illegal():
    base = {
        "id": "c", "name": "c",
        "nodes": [{"id": "a", "type": "tc_brain"}, {"id": "b", "type": "tc_loop"}],
        "edges": [
            {"from": "a.signal", "to": "b.sig_in"},
            {"from": "b.candle_out", "to": "a.candle"},
        ],
    }
    r = compile_blueprint(loads_loom(json.dumps(base)))
    assert not r.ok and r.errors[0].code == "ILLEGAL_CYCLE"
    assert "feedback" in r.errors[0].fix_hint
    base["edges"][1]["feedback"] = True
    r2 = compile_blueprint(loads_loom(json.dumps(base)))
    assert r2.ok
    fb = [x for x in r2.bindings["a"] if x.dst_port == "candle"][0]
    assert fb.feedback is True

def test_nesting_depth_limit():
    bp = INNER
    for i in range(9):
        bp = {"id": f"w{i}", "name": f"w{i}",
              "nodes": [{"id": "s", "type": "subgraph",
                         "params": {"blueprint": bp, "inputs": {}, "outputs": {}}}],
              "edges": []}
    r = compile_blueprint(loads_loom(json.dumps(bp)))
    assert not r.ok and any(e.code == "PARAM_INVALID" for e in r.errors)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_compiler_subgraph.py -q`
Expected: FAIL（subgraph 类型被当作 UNKNOWN_NODE_TYPE）

- [ ] **Step 3: 在 `compiler.py` 增加展开逻辑**

```python
from alphaloom.graph.model import EdgeSpec, NodeSpec, PortRef, loads_loom

_MAX_DEPTH = 8

def _parse_ref_str(s: str) -> PortRef:
    n, p = s.split(".")
    return PortRef(n, p)

def _expand_subgraphs(bp, depth=0):
    if depth > _MAX_DEPTH:
        return None, [CompileError("PARAM_INVALID", f"subgraph nesting exceeds {_MAX_DEPTH}",
                                   fix_hint="Flatten your subgraph hierarchy.")]
    if not any(n.type == "subgraph" for n in bp.nodes):
        return bp, []
    import json as _json
    nodes, edges, errors = [], [], []
    in_map, out_map = {}, {}
    for n in bp.nodes:
        if n.type != "subgraph":
            nodes.append(n)
            continue
        try:
            inner = loads_loom(_json.dumps(n.params["blueprint"]))
        except Exception as exc:
            errors.append(CompileError("PARAM_INVALID", f"subgraph {n.id}: bad blueprint ({exc})",
                                       node_id=n.id))
            continue
        inner, sub_errs = _expand_subgraphs(inner, depth + 1)
        if sub_errs:
            errors.extend(sub_errs)
            continue
        pre = f"{n.id}/"
        nodes.extend(NodeSpec(pre + m.id, m.type, m.params) for m in inner.nodes)
        edges.extend(EdgeSpec(PortRef(pre + e.src.node_id, e.src.port),
                              PortRef(pre + e.dst.node_id, e.dst.port), e.feedback)
                     for e in inner.edges)
        for outer_port, ref in n.params.get("inputs", {}).items():
            r = _parse_ref_str(ref)
            in_map[PortRef(n.id, outer_port)] = PortRef(pre + r.node_id, r.port)
        for outer_port, ref in n.params.get("outputs", {}).items():
            r = _parse_ref_str(ref)
            out_map[PortRef(n.id, outer_port)] = PortRef(pre + r.node_id, r.port)
    if errors:
        return None, errors
    for e in bp.edges:
        edges.append(EdgeSpec(out_map.get(e.src, e.src), in_map.get(e.dst, e.dst), e.feedback))
    flat = BlueprintSpec(bp.id, bp.name, nodes, edges, bp.meta)
    return _expand_subgraphs(flat, depth + 1)
```

`compile_blueprint` 首行改为：

```python
def compile_blueprint(bp: BlueprintSpec, *, bars_per_day: int = 1440) -> CompileResult:
    bp2, exp_errors = _expand_subgraphs(bp)
    if exp_errors:
        return CompileResult(False, exp_errors)
    bp = bp2
    # ……以下原有检查不变
```

注意 `BlueprintSpec` 需从 model 导入（Task 4 已导入）。展开产生的扁平图上，嵌套 9 层触发 PARAM_INVALID（测试 4 依赖此语义）。

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(compiler): recursive subgraph expansion, feedback-cycle rule, depth limit"
```

---

### Task 6: 成本证书（graph/cost.py + 编译器集成）

**Files:**
- Create: `backend/alphaloom/graph/cost.py`
- Modify: `backend/alphaloom/graph/compiler.py`（成功路径填 certificate）
- Test: `backend/tests/test_cost_certificate.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_cost_certificate.py
import json
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint

import tests.test_compiler_typecheck  # noqa: F401

@node(type="tc_llm", category="test",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(llm_calls_per_bar=2, max_tokens_per_call=4000,
                          latency_class="llm", deterministic=False))
class TcLlm:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {}

def _compile(node_type):
    return compile_blueprint(loads_loom(json.dumps({
        "id": "t", "name": "t",
        "nodes": [
            {"id": "feed", "type": "tc_feed"},
            {"id": "brain", "type": node_type},
            {"id": "risk", "type": "tc_riskgate"},
            {"id": "ex", "type": "tc_exec"},
        ],
        "edges": [
            {"from": "feed.out", "to": "brain.candle"},
            {"from": "brain.signal", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "ex.signal"},
        ],
    })), bars_per_day=1440)

def test_deterministic_graph_certificate():
    c = _compile("tc_brain").certificate
    assert c.llm_calls_per_bar == 0 and c.daily_token_ceiling == 0
    assert c.worst_latency_class == "fast" and c.deterministic_ratio == 1.0

def test_llm_graph_certificate():
    c = _compile("tc_llm").certificate
    assert c.llm_calls_per_bar == 2
    assert c.daily_token_ceiling == 2 * 4000 * 1440
    assert c.worst_latency_class == "llm"
    assert 0 < c.deterministic_ratio < 1
    d = c.to_dict()
    json.dumps(d)
    assert d["llm_calls_per_bar"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_cost_certificate.py -q`
Expected: FAIL（certificate 为 None / 模块不存在）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/graph/cost.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from alphaloom.nodes.registry import NodeDef

_LATENCY_RANK = {"fast": 0, "slow": 1, "llm": 2}

@dataclass(frozen=True)
class CostCertificate:
    llm_calls_per_bar: int
    daily_token_ceiling: int
    worst_latency_class: str
    deterministic_ratio: float

    def to_dict(self) -> dict:
        return asdict(self)

def build_certificate(defs: list[NodeDef], bars_per_day: int) -> CostCertificate:
    calls = sum(d.cost.llm_calls_per_bar for d in defs)
    tokens = sum(d.cost.llm_calls_per_bar * d.cost.max_tokens_per_call for d in defs) * bars_per_day
    worst = max((d.cost.latency_class for d in defs),
                key=lambda c: _LATENCY_RANK[c], default="fast")
    det = (sum(1 for d in defs if d.cost.deterministic) / len(defs)) if defs else 1.0
    return CostCertificate(calls, tokens, worst, round(det, 4))
```

`compiler.py` 成功返回前加：

```python
from alphaloom.graph.cost import build_certificate
...
    cert = build_certificate([defs[nid] for nid in order], bars_per_day)
    return CompileResult(True, [], order, bindings, cert,
                         nodes={n.id: n for n in bp.nodes})
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(compiler): static cost certificate (llm calls, token ceiling, latency, determinism)"
```

---

### Task 7: 运行时基础·事件/时钟/因果守卫（runtime/events.py, runtime/context.py）

**Files:**
- Create: `backend/alphaloom/runtime/__init__.py`（空）, `backend/alphaloom/runtime/events.py`, `backend/alphaloom/runtime/context.py`
- Test: `backend/tests/test_causality.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_causality.py
import pytest
from alphaloom.graph.types import Stamped
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.context import SimClock, RunContext, CausalityError, check_stamped

CANDLE = {"ts": 60_000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}

def test_bar_event_close_ts():
    ev = BarEvent(candle=CANDLE, bar_ms=60_000)
    assert ev.ts_open == 60_000 and ev.ts_close == 120_000

def test_clock_monotonic():
    clk = SimClock()
    clk.advance(120_000)
    assert clk.now == 120_000
    with pytest.raises(ValueError):
        clk.advance(60_000)

def test_check_stamped_passes_and_blocks():
    check_stamped("n1", Stamped(1.0, as_of=120_000), now=120_000)
    check_stamped("n1", {"x": Stamped(1.0, 60_000)}, now=120_000)
    with pytest.raises(CausalityError, match="n1"):
        check_stamped("n1", Stamped(1.0, as_of=180_000), now=120_000)

def test_run_context_defaults():
    ctx = RunContext(clock=SimClock(), run_id="r1")
    assert ctx.halted is False and ctx.broker is None and ctx.current_event is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_causality.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.runtime）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/runtime/events.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class BarEvent:
    candle: dict
    bar_ms: int

    @property
    def ts_open(self) -> int:
        return int(self.candle["ts"])

    @property
    def ts_close(self) -> int:
        return int(self.candle["ts"]) + self.bar_ms
```

```python
# backend/alphaloom/runtime/context.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from alphaloom.graph.types import Stamped

class CausalityError(Exception):
    """节点试图产出/传播 as_of 晚于当前时钟的数据 —— 图感知了未来。"""

class SimClock:
    def __init__(self) -> None:
        self.now: int = 0

    def advance(self, ts: int) -> None:
        if ts < self.now:
            raise ValueError(f"clock cannot go backwards: {ts} < {self.now}")
        self.now = ts

def check_stamped(node_id: str, obj: Any, now: int) -> None:
    if isinstance(obj, Stamped):
        if obj.as_of > now:
            raise CausalityError(
                f"node {node_id!r} emitted data stamped as_of={obj.as_of} "
                f"but clock is {now}: graphs must not perceive the future")
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, Stamped) and v.as_of > now:
                raise CausalityError(
                    f"node {node_id!r} emitted nested future data (as_of={v.as_of} > now={now})")

@dataclass
class RunContext:
    clock: SimClock
    run_id: str
    broker: Any = None
    recorder: Any = None
    current_event: Any = None
    halted: bool = False
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_causality.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(runtime): bar events, monotonic clock, causality guard"
```

---

### Task 8: 执行引擎 + 全程录制（runtime/engine.py, runtime/recorder.py）

**Files:**
- Create: `backend/alphaloom/runtime/engine.py`, `backend/alphaloom/runtime/recorder.py`
- Test: `backend/tests/test_engine.py`

引擎语义（锁定）：
- `step(ev)`：`clock.advance(ev.ts_close)` → `ctx.current_event = ev` → 按 `compiled.order` 波次执行；每节点输入 = 非反馈边取本波上游输出、反馈边取**上一波**输出（首波为 None）。
- 输入解包：engine 把 `Stamped` 解包成裸值传给 `on_bar`；节点返回裸值由 engine 以 `Stamped(value, ev.ts_close)` 封装；节点若自己返回 `Stamped`（如 CandleFeed）则先过 `check_stamped` 再原样传播。
- 断点：`breakpoints: set[str]`，命中时调用 `on_pause(node_id, ev, inputs)`（回调制，D2 API 层接成 WS 暂停协议）。
- 录制：每节点每事件一行（inputs/outputs JSON，Stamped 编码为 `{"__stamped__": as_of, "value": ...}`）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_engine.py
import json
import pytest
from alphaloom.graph.types import PinType, Stamped
from alphaloom.nodes.registry import node, create_instance
from alphaloom.graph.model import loads_loom
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.context import SimClock, RunContext, CausalityError
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.recorder import Recorder

@node(type="te_src", category="test", inputs={}, outputs={"v": PinType.SERIES})
class TeSrc:
    def setup(self, params): self.i = 0
    def on_bar(self, ctx, inputs):
        self.i += 1
        return {"v": float(self.i)}

@node(type="te_double", category="test",
      inputs={"x": PinType.SERIES}, outputs={"y": PinType.SERIES})
class TeDouble:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs): return {"y": inputs["x"] * 2}

@node(type="te_echo_prev", category="test",
      inputs={"cur": PinType.SERIES, "prev": PinType.SERIES},
      outputs={"out": PinType.SERIES})
class TeEchoPrev:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs):
        return {"out": inputs["cur"] if inputs["prev"] is None else inputs["prev"]}

@node(type="te_evil", category="test", inputs={}, outputs={"v": PinType.SERIES})
class TeEvil:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs):
        return {"v": Stamped(99.0, as_of=ctx.clock.now + 999_999)}

def _mk(bp_json):
    bp = loads_loom(json.dumps(bp_json))
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.to_dict() for e in compiled.errors]
    instances = {n.id: create_instance(n) for n in bp.nodes}
    return compiled, instances

def _events(n):
    return [BarEvent({"ts": i * 60_000, "open": 1, "high": 1, "low": 1,
                      "close": 1, "volume": 1}, 60_000) for i in range(n)]

LINEAR = {"id": "l", "name": "l",
          "nodes": [{"id": "s", "type": "te_src"}, {"id": "d", "type": "te_double"}],
          "edges": [{"from": "s.v", "to": "d.x"}]}

def test_linear_dataflow(tmp_path):
    compiled, inst = _mk(LINEAR)
    rec = Recorder(tmp_path / "rec.sqlite")
    ctx = RunContext(clock=SimClock(), run_id="r1", recorder=rec)
    Engine(compiled, inst, ctx).run(_events(3))
    rows = rec.fetch("r1", node_id="d")
    outs = [json.loads(r["outputs_json"])["y"]["value"] for r in rows]
    assert outs == [2.0, 4.0, 6.0]

def test_feedback_edge_prev_wave():
    bp = {"id": "f", "name": "f",
          "nodes": [{"id": "s", "type": "te_src"}, {"id": "e", "type": "te_echo_prev"}],
          "edges": [{"from": "s.v", "to": "e.cur"},
                    {"from": "e.out", "to": "e.prev", "feedback": True}]}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r2"))
    seen = []
    eng.after_node = lambda nid, outs: seen.append(outs["out"].value) if nid == "e" else None
    eng.run(_events(3))
    assert seen == [1.0, 1.0, 2.0]

def test_breakpoint_callback():
    compiled, inst = _mk(LINEAR)
    hits = []
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r3"),
                 breakpoints={"d"},
                 on_pause=lambda nid, ev, inputs: hits.append((nid, inputs["x"])))
    eng.run(_events(2))
    assert hits == [("d", 1.0), ("d", 2.0)]

def test_causality_guard_kills_run():
    bp = {"id": "e", "name": "e", "nodes": [{"id": "bad", "type": "te_evil"}], "edges": []}
    compiled, inst = _mk(bp)
    eng = Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r4"))
    with pytest.raises(CausalityError):
        eng.run(_events(1))

def test_recorder_row_count(tmp_path):
    compiled, inst = _mk(LINEAR)
    rec = Recorder(tmp_path / "rec.sqlite")
    Engine(compiled, inst, RunContext(clock=SimClock(), run_id="r5", recorder=rec)).run(_events(4))
    assert len(rec.fetch("r5")) == 4 * 2
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_engine.py -q`
Expected: FAIL（ModuleNotFoundError: alphaloom.runtime.engine）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/runtime/recorder.py
from __future__ import annotations
import json
import sqlite3
from alphaloom.graph.types import Stamped

def _enc(o):
    if isinstance(o, Stamped):
        return {"__stamped__": o.as_of, "value": o.value}
    raise TypeError(f"not JSON serializable: {type(o)}")

def to_json(obj: dict) -> str:
    return json.dumps(obj, default=_enc, ensure_ascii=False)

class Recorder:
    def __init__(self, path):
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS node_io ("
            " run_id TEXT, event_idx INTEGER, ts INTEGER, node_id TEXT,"
            " inputs_json TEXT, outputs_json TEXT)")
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_io ON node_io(run_id, node_id, event_idx)")

    def record(self, run_id, event_idx, ts, node_id, inputs, outputs):
        self._db.execute("INSERT INTO node_io VALUES (?,?,?,?,?,?)",
                         (run_id, event_idx, ts, node_id, to_json(inputs), to_json(outputs)))
        self._db.commit()

    def fetch(self, run_id, node_id=None):
        q = "SELECT * FROM node_io WHERE run_id=?"
        args = [run_id]
        if node_id:
            q += " AND node_id=?"
            args.append(node_id)
        q += " ORDER BY event_idx"
        cur = self._db.execute(q, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self):
        self._db.close()
```

```python
# backend/alphaloom/runtime/engine.py
from __future__ import annotations
from alphaloom.graph.types import Stamped
from alphaloom.graph.compiler import CompileResult
from alphaloom.runtime.context import RunContext, check_stamped
from alphaloom.runtime.events import BarEvent

class Engine:
    def __init__(self, compiled: CompileResult, instances: dict, ctx: RunContext,
                 breakpoints: set[str] | None = None, on_pause=None):
        self.compiled = compiled
        self.instances = dict(instances)
        self.ctx = ctx
        self.breakpoints = breakpoints or set()
        self.on_pause = on_pause
        self.after_node = None            # 测试/调试钩子
        self._prev: dict[tuple[str, str], Stamped | None] = {}
        self._event_idx = 0

    def run(self, events) -> None:
        for ev in events:
            self.step(ev)

    def step(self, ev: BarEvent) -> None:
        self.ctx.clock.advance(ev.ts_close)
        self.ctx.current_event = ev
        wave: dict[tuple[str, str], Stamped] = {}
        for node_id in self.compiled.order:
            inst = self.instances[node_id]
            raw_inputs: dict = {}
            rec_inputs: dict = {}
            for b in self.compiled.bindings.get(node_id, []):
                key = (b.src_node, b.src_port)
                stamped = self._prev.get(key) if b.feedback else wave.get(key)
                rec_inputs[b.dst_port] = stamped
                raw_inputs[b.dst_port] = stamped.value if isinstance(stamped, Stamped) else stamped
            if node_id in self.breakpoints and self.on_pause:
                self.on_pause(node_id, ev, raw_inputs)
            outputs = inst.on_bar(self.ctx, raw_inputs) or {}
            stamped_outputs: dict[str, Stamped] = {}
            for port, val in outputs.items():
                s = val if isinstance(val, Stamped) else Stamped(val, self.ctx.clock.now)
                check_stamped(node_id, s, self.ctx.clock.now)
                stamped_outputs[port] = s
                wave[(node_id, port)] = s
            if self.after_node:
                self.after_node(node_id, stamped_outputs)
            if self.ctx.recorder:
                self.ctx.recorder.record(self.ctx.run_id, self._event_idx, ev.ts_close,
                                         node_id, rec_inputs, stamped_outputs)
        self._prev = wave
        self._event_idx += 1
```

- [ ] **Step 4: 运行确认通过（全量回归）**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(runtime): wave engine with feedback edges, breakpoints, causality guard, sqlite recording"
```

---

### Task 9: PaperBroker（brokers/base.py, brokers/paper.py）

**Files:**
- Create: `backend/alphaloom/brokers/__init__.py`（空）, `backend/alphaloom/brokers/base.py`, `backend/alphaloom/brokers/paper.py`
- Test: `backend/tests/test_paper_broker.py`

撮合语义（锁定，防未来函数）：bar t 波次中 `submit()` 的市价单在 **bar t+1 开盘价**成交；附带 stop 的持仓在每根 bar 开盘成交处理后检查（long: low<=stop → 以 stop 价离场；short: high>=stop）。手续费 `fee_rate × 名义额` 双边收取。权益曲线按收盘 mark-to-market。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_paper_broker.py
from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker

def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}

def test_market_fill_next_bar_open():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0))
    assert b.fills == []
    b.on_bar(_bar(60_000, 12, 13, 11, 12))
    assert len(b.fills) == 1 and b.fills[0].price == 12.0
    assert b.position().qty == 1.0

def test_stop_loss_triggers():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 11, 9, 10))
    b.submit(Order(side="buy", qty=1.0, stop=8.0))
    b.on_bar(_bar(60_000, 10, 11, 9, 10))
    b.on_bar(_bar(120_000, 9, 9.5, 7.5, 8.5))
    assert b.position().qty == 0.0
    exit_fill = b.fills[-1]
    assert exit_fill.side == "sell" and exit_fill.price == 8.0

def test_equity_and_summary():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    bars = [_bar(0, 10, 11, 9, 10), _bar(60_000, 10, 12, 10, 12),
            _bar(120_000, 12, 13, 11, 13), _bar(180_000, 13, 13, 12, 12)]
    b.on_bar(bars[0]); b.submit(Order(side="buy", qty=1.0))
    b.on_bar(bars[1])
    b.on_bar(bars[2]); b.submit(Order(side="sell", qty=1.0))
    b.on_bar(bars[3])
    assert b.equity() == 1000.0 + 3.0
    s = b.summary()
    assert s["num_trades"] == 1 and s["net_pnl"] == 3.0
    assert s["win_rate"] == 1.0 and s["max_drawdown"] >= 0.0
    assert len(b.equity_curve) == 4

def test_fee_applied():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.001)
    b.on_bar(_bar(0, 10, 10, 10, 10))
    b.submit(Order(side="buy", qty=2.0))
    b.on_bar(_bar(60_000, 10, 10, 10, 10))
    assert b.fills[0].fee == 2.0 * 10 * 0.001

def test_halted_broker_rejects():
    b = PaperBroker(initial_cash=1000.0)
    b.halt("kill switch")
    assert b.submit(Order(side="buy", qty=1.0)) is False

def test_reversal_resets_avg_price():
    b = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    b.on_bar(_bar(0, 10, 10, 10, 10))
    b.submit(Order(side="buy", qty=2.0))
    b.on_bar(_bar(60_000, 10, 10, 10, 10))      # 多 2 @10
    b.submit(Order(side="sell", qty=3.0))
    b.on_bar(_bar(120_000, 12, 12, 12, 12))     # 反手：平 2 开空 1 @12
    p = b.position()
    assert p.qty == -1.0 and p.avg_price == 12.0
    assert b.summary()["num_trades"] == 1        # 只有平掉的 2 手计一笔往返
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_paper_broker.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/brokers/base.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Order:
    side: str                  # "buy" | "sell"
    qty: float
    kind: str = "market"       # D1 仅 market
    stop: float | None = None  # 开仓单附带止损
    tag: str = ""

@dataclass(frozen=True)
class Fill:
    ts: int
    side: str
    qty: float
    price: float
    fee: float
    tag: str = ""

@dataclass
class Position:
    qty: float = 0.0           # 有符号：+多 -空
    avg_price: float = 0.0
    stop: float | None = None
```

```python
# backend/alphaloom/brokers/paper.py
from __future__ import annotations
from alphaloom.brokers.base import Order, Fill, Position

class PaperBroker:
    def __init__(self, initial_cash: float = 10_000.0, fee_rate: float = 0.0005):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self._pos = Position()
        self._pending: list[Order] = []
        self.fills: list[Fill] = []
        self.equity_curve: list[tuple[int, float]] = []
        self._round_trips: list[float] = []
        self._entry_cost = 0.0
        self._halted = False
        self._halt_reason = ""
        self._last_close = 0.0

    def submit(self, order: Order) -> bool:
        if self._halted:
            return False
        self._pending.append(order)
        return True

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason

    @property
    def halted(self) -> bool:
        return self._halted

    def position(self) -> Position:
        return self._pos

    def equity(self) -> float:
        return self.cash + self._pos.qty * self._last_close

    def on_bar(self, candle: dict) -> None:
        o = float(candle["open"])
        pending, self._pending = self._pending, []
        for od in pending:
            self._fill(int(candle["ts"]), od, o)
        p = self._pos
        if p.qty > 0 and p.stop is not None and float(candle["low"]) <= p.stop:
            self._fill(int(candle["ts"]), Order("sell", p.qty, tag="stop"), p.stop)
        elif p.qty < 0 and p.stop is not None and float(candle["high"]) >= p.stop:
            self._fill(int(candle["ts"]), Order("buy", -p.qty, tag="stop"), p.stop)
        self._last_close = float(candle["close"])
        self.equity_curve.append((int(candle["ts"]), self.equity()))

    def _fill(self, ts: int, od: Order, price: float) -> None:
        fee = od.qty * price * self.fee_rate
        signed = od.qty if od.side == "buy" else -od.qty
        p = self._pos
        closing = (p.qty > 0 > signed) or (p.qty < 0 < signed)
        crossed = closing and abs(signed) > abs(p.qty)   # 反手：平掉全部旧仓并反向开新仓
        if closing:
            closed_qty = min(abs(p.qty), abs(signed))
            pnl = (price - p.avg_price) * closed_qty * (1 if p.qty > 0 else -1)
            self._round_trips.append(pnl - fee - self._entry_cost)
            self._entry_cost = 0.0
        else:
            self._entry_cost += fee
        new_qty = p.qty + signed
        if not closing and (p.qty == 0 or abs(new_qty) > abs(p.qty)):
            total = p.avg_price * abs(p.qty) + price * abs(signed)
            p.avg_price = total / (abs(p.qty) + abs(signed))
        if crossed:
            p.avg_price = price          # 反手剩余部分按本次成交价计新仓成本
        if new_qty == 0:
            p.avg_price = 0.0
            p.stop = None
        elif od.stop is not None:
            p.stop = od.stop
        p.qty = new_qty
        self.cash -= signed * price + fee
        self.fills.append(Fill(ts, od.side, od.qty, price, fee, od.tag))

    def summary(self) -> dict:
        eq = [e for _, e in self.equity_curve] or [self.initial_cash]
        peak, max_dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0.0)
        wins = [x for x in self._round_trips if x > 0]
        losses = [-x for x in self._round_trips if x < 0]
        return {
            "net_pnl": round(self.equity() - self.initial_cash, 8),
            "return_pct": round((self.equity() / self.initial_cash - 1) * 100, 4),
            "max_drawdown": round(max_dd, 6),
            "num_trades": len(self._round_trips),
            "win_rate": round(len(wins) / len(self._round_trips), 4) if self._round_trips else 0.0,
            "profit_factor": round(sum(wins) / sum(losses), 4) if losses else (float("inf") if wins else 0.0),
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_paper_broker.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(brokers): paper broker with next-bar-open fills, attached stops, round-trip stats"
```

---

### Task 10: 数据源抽象 + SQLite 行情 + 合成夹具（data/source.py, data/sqlite_source.py, tests/fixtures/synth.py）

**Files:**
- Create: `backend/alphaloom/data/__init__.py`（空）, `backend/alphaloom/data/source.py`, `backend/alphaloom/data/sqlite_source.py`, `backend/tests/fixtures/__init__.py`（空）, `backend/tests/fixtures/synth.py`
- Test: `backend/tests/test_data_source.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_data_source.py
from alphaloom.data.source import bar_to_ms
from alphaloom.data.sqlite_source import SQLiteMarketData
from tests.fixtures.synth import gen_candles

def test_bar_to_ms():
    assert bar_to_ms("1m") == 60_000
    assert bar_to_ms("15m") == 900_000
    assert bar_to_ms("1H") == 3_600_000

def test_synth_deterministic():
    a = gen_candles(50, seed=7, trend=0.001)
    b = gen_candles(50, seed=7, trend=0.001)
    assert a == b and len(a) == 50
    for c in a:
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]
    assert a[1]["ts"] - a[0]["ts"] == 60_000

def test_sqlite_roundtrip(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    candles = gen_candles(100, seed=1)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles)
    db.insert_candles("BTC-USDT-SWAP", "1m", candles[:10])  # 重复插入幂等
    got = list(db.iter_candles("BTC-USDT-SWAP", "1m",
                               candles[10]["ts"], candles[19]["ts"]))
    assert len(got) == 10 and got[0]["ts"] == candles[10]["ts"]
    assert [c["ts"] for c in got] == sorted(c["ts"] for c in got)
    assert db.bounds("BTC-USDT-SWAP", "1m") == (candles[0]["ts"], candles[-1]["ts"])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_data_source.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/alphaloom/data/source.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterator

_BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
           "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}

def bar_to_ms(bar: str) -> int:
    return _BAR_MS[bar]

class DataSource(ABC):
    """多市场行情抽象：D1 只有 SQLite 实现；D4 加 OKX 实时。"""

    @abstractmethod
    def iter_candles(self, inst: str, bar: str,
                     start_ms: int | None = None,
                     end_ms: int | None = None) -> Iterator[dict]: ...
```

```python
# backend/alphaloom/data/sqlite_source.py
from __future__ import annotations
import sqlite3
from typing import Iterator
from alphaloom.data.source import DataSource

class SQLiteMarketData(DataSource):
    def __init__(self, path):
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS candles ("
            " inst TEXT, bar TEXT, ts INTEGER,"
            " open REAL, high REAL, low REAL, close REAL, volume REAL,"
            " PRIMARY KEY (inst, bar, ts))")

    def insert_candles(self, inst: str, bar: str, candles: list[dict]) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [(inst, bar, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"])
             for c in candles])
        self._db.commit()

    def iter_candles(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
        q = "SELECT ts, open, high, low, close, volume FROM candles WHERE inst=? AND bar=?"
        args: list = [inst, bar]
        if start_ms is not None:
            q += " AND ts>=?"; args.append(start_ms)
        if end_ms is not None:
            q += " AND ts<=?"; args.append(end_ms)
        q += " ORDER BY ts"
        for ts, o, h, l, c, v in self._db.execute(q, args):
            yield {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}

    def bounds(self, inst: str, bar: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT MIN(ts), MAX(ts) FROM candles WHERE inst=? AND bar=?",
            (inst, bar)).fetchone()
        return None if row[0] is None else (row[0], row[1])
```

```python
# backend/tests/fixtures/synth.py
"""确定性合成 K 线 —— 测试与离线演示专用，绝不联网。"""
from __future__ import annotations
import random

def gen_candles(n: int, *, start_ts: int = 0, bar_ms: int = 60_000,
                seed: int = 42, trend: float = 0.0, start_price: float = 100.0,
                vol: float = 0.004) -> list[dict]:
    rng = random.Random(seed)
    out, close = [], start_price
    for i in range(n):
        o = close
        drift = trend + rng.gauss(0, vol)
        c = max(0.01, o * (1 + drift))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        out.append({"ts": start_ts + i * bar_ms, "open": round(o, 6),
                    "high": round(hi, 6), "low": round(lo, 6),
                    "close": round(c, 6), "volume": round(abs(rng.gauss(10, 3)), 3)})
        close = c
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_data_source.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(data): DataSource ABC, sqlite market store, deterministic synth fixtures"
```

---

### Task 11: 内置节点六件套（nodes/data.py, indicators.py, gates.py, sizing.py, execution.py）

**Files:**
- Create: `backend/alphaloom/nodes/data.py`, `backend/alphaloom/nodes/indicators.py`, `backend/alphaloom/nodes/gates.py`, `backend/alphaloom/nodes/sizing.py`, `backend/alphaloom/nodes/execution.py`
- Modify: `backend/alphaloom/nodes/__init__.py`（追加导入触发注册）
- Test: `backend/tests/test_builtin_nodes.py`

**信号语义（锁定）**：`side ∈ {"long","short","flat","hold"}`——`hold`=不动作，`flat`=平仓。gates 产出带 `stop` 的信号；sizer 按风险额定量；risk_gate 盖章或拦截（拦截时输出 `hold` + blocked 原因）；execute_order 按目标仓位与当前仓位的差额下单。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_builtin_nodes.py
import math
import pytest
from hypothesis import given, strategies as st
import alphaloom.nodes  # 触发内置节点注册
from alphaloom.graph.model import NodeSpec
from alphaloom.graph.types import Stamped
from alphaloom.nodes.registry import create_instance, get_node_def
from alphaloom.runtime.context import SimClock, RunContext
from alphaloom.runtime.events import BarEvent
from alphaloom.brokers.paper import PaperBroker
from alphaloom.brokers.base import Order
from tests.fixtures.synth import gen_candles

def _ctx(broker=None):
    ctx = RunContext(clock=SimClock(), run_id="t")
    ctx.broker = broker
    return ctx

def _feed_ev(ctx, candle, bar_ms=60_000):
    ev = BarEvent(candle, bar_ms)
    ctx.clock.advance(ev.ts_close)
    ctx.current_event = ev
    return ev

# ---- CandleFeed ----
def test_candle_feed_stamps_close_time():
    ctx = _ctx()
    feed = create_instance(NodeSpec("f", "candle_feed", {"inst": "X", "bar": "1m"}))
    c = {"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    _feed_ev(ctx, c)
    out = feed.on_bar(ctx, {})
    assert isinstance(out["out"], Stamped)
    assert out["out"].as_of == 60_000 and out["out"].value == c

# ---- EMA 增量 == 批量（hypothesis 性质测试）----
def _batch_ema(closes, period):
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema

@given(st.lists(st.floats(min_value=1, max_value=1000, allow_nan=False), min_size=2, max_size=200),
       st.integers(min_value=2, max_value=50))
def test_ema_incremental_matches_batch(closes, period):
    ctx = _ctx()
    ema = create_instance(NodeSpec("e", "ema", {"period": period}))
    last = None
    for i, c in enumerate(closes):
        candle = {"ts": i * 60_000, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        last = ema.on_bar(ctx, {"candle": candle})["value"]
    assert last == pytest.approx(_batch_ema(closes, period), rel=1e-9)

# ---- ATR 基本性质 ----
def test_atr_positive_and_warms_up():
    ctx = _ctx()
    atr = create_instance(NodeSpec("a", "atr", {"period": 3}))
    vals = []
    for c in gen_candles(20, seed=3):
        vals.append(atr.on_bar(ctx, {"candle": c})["value"])
    assert all(v is None for v in vals[:3]) and all(v > 0 for v in vals[3:])

# ---- CrossSignal ----
def test_cross_signal_long_and_short():
    ctx = _ctx()
    cross = create_instance(NodeSpec("c", "cross_signal", {"atr_mult": 2.0}))
    candle = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
    s1 = cross.on_bar(ctx, {"fast": 1.0, "slow": 2.0, "candle": candle, "atr": 1.0})
    assert s1["signal"]["side"] == "hold"          # 首 bar 只记状态
    s2 = cross.on_bar(ctx, {"fast": 3.0, "slow": 2.5, "candle": candle, "atr": 1.0})
    sig = s2["signal"]
    assert sig["side"] == "long" and sig["stop"] == pytest.approx(100 - 2.0 * 1.0)
    s3 = cross.on_bar(ctx, {"fast": 1.0, "slow": 2.0, "candle": candle, "atr": 1.0})
    assert s3["signal"]["side"] == "short"
    assert s3["signal"]["stop"] == pytest.approx(100 + 2.0 * 1.0)

# ---- ScenarioGate 突破状态机 ----
def test_scenario_gate_breakout_and_cooldown():
    ctx = _ctx()
    g = create_instance(NodeSpec("g", "scenario_gate",
                                 {"lookback": 3, "cooldown": 2, "atr_mult": 1.0}))
    def bar(i, hi, lo, close):
        return {"ts": i * 60_000, "open": close, "high": hi, "low": lo,
                "close": close, "volume": 1}
    sides = []
    seq = [bar(0, 10, 9, 9.5), bar(1, 10, 9, 9.6), bar(2, 10, 9, 9.4),
           bar(3, 12, 10, 11.5),   # close 11.5 > max(前3根 high)=10 → long
           bar(4, 13, 11, 12.5),   # cooldown
           bar(5, 14, 12, 13.5),   # cooldown
           bar(6, 15, 13, 14.5)]   # 可再触发
    for c in seq:
        sides.append(g.on_bar(ctx, {"candle": c, "atr": 0.5})["signal"]["side"])
    assert sides[3] == "long" and sides[4] == "hold" and sides[5] == "hold"
    assert sides[6] == "long"

# ---- PositionSizer ----
def test_position_sizer_risk_math():
    broker = PaperBroker(initial_cash=10_000.0)
    broker.on_bar({"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    ctx = _ctx(broker)
    sizer = create_instance(NodeSpec("s", "position_sizer", {"risk_pct": 0.02}))
    sig = {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "t"}
    candle = {"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1}
    out = sizer.on_bar(ctx, {"signal": sig, "candle": candle})["sized"]
    assert out["qty"] == pytest.approx(10_000 * 0.02 / 5.0)   # 风险额/止损距离
    hold = sizer.on_bar(ctx, {"signal": {"side": "hold", "qty": 0, "stop": None,
                                         "reason": ""}, "candle": candle})["sized"]
    assert hold["side"] == "hold" and hold["qty"] == 0

# ---- RiskGate ----
def test_risk_gate_stamps_and_blocks():
    ctx = _ctx()
    gate = create_instance(NodeSpec("r", "risk_gate", {"max_qty": 5.0, "require_stop": True}))
    ok = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 2.0, "stop": 95.0, "reason": "x"}})
    assert ok["stamped"]["risk"]["checked"] is True and ok["blocked"] is False
    no_stop = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 2.0, "stop": None, "reason": "x"}})
    assert no_stop["blocked"] is True and no_stop["stamped"]["side"] == "hold"
    assert any("stop" in c for c in no_stop["stamped"]["risk"]["checks"])
    too_big = gate.on_bar(ctx, {"signal": {"side": "long", "qty": 99.0, "stop": 95.0, "reason": "x"}})
    assert too_big["blocked"] is True

def test_risk_gate_is_sole_stamper():
    d = get_node_def("risk_gate")
    from alphaloom.graph.types import PinType
    from alphaloom.nodes.registry import REGISTRY
    stampers = [t for t, dd in REGISTRY.items()
                if PinType.RISK_STAMPED_SIGNAL in dd.outputs.values()
                and dd.category != "test"]
    assert stampers == ["risk_gate"]

# ---- ExecuteOrder ----
def test_execute_order_delta_and_reversal():
    broker = PaperBroker(initial_cash=10_000.0, fee_rate=0.0)
    broker.on_bar({"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    ctx = _ctx(broker)
    ex = create_instance(NodeSpec("x", "execute_order", {}))
    stamped = {"side": "long", "qty": 2.0, "stop": 95.0, "reason": "t",
               "risk": {"checked": True, "blocked": False, "checks": []}}
    assert ex.on_bar(ctx, {"signal": stamped})["submitted"] is True
    broker.on_bar({"ts": 60_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    assert broker.position().qty == 2.0
    rev = dict(stamped, side="short", qty=1.0)
    ex.on_bar(ctx, {"signal": rev})
    broker.on_bar({"ts": 120_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    assert broker.position().qty == -1.0          # 2.0 多 → 1.0 空，一次性下 3.0 卖单
    hold = dict(stamped, side="hold")
    assert ex.on_bar(ctx, {"signal": hold})["submitted"] is False

# ---- KillSwitch ----
def test_kill_switch_halts_broker():
    broker = PaperBroker(initial_cash=1000.0, fee_rate=0.0)
    ctx = _ctx(broker)
    ks = create_instance(NodeSpec("k", "kill_switch", {"max_drawdown_pct": 0.10}))
    bars = [
        {"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"ts": 60_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"ts": 120_000, "open": 100, "high": 100, "low": 70, "close": 70, "volume": 1},
    ]
    broker.on_bar(bars[0])
    out = ks.on_bar(ctx, {"candle": bars[0]})        # 建立 peak=1000
    assert out["halted"] is False
    broker.submit(Order(side="buy", qty=5.0))
    broker.on_bar(bars[1])                           # 成交 5 @100，equity 仍 1000
    assert ks.on_bar(ctx, {"candle": bars[1]})["halted"] is False
    broker.on_bar(bars[2])                           # close 70 → equity 850，回撤 15%
    out = ks.on_bar(ctx, {"candle": bars[2]})
    assert out["halted"] is True and broker.halted is True
    assert "drawdown" in broker.summary()["halt_reason"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_builtin_nodes.py -q`
Expected: FAIL（KeyError: 'candle_feed' 等）

- [ ] **Step 3: 实现五个模块**

```python
# backend/alphaloom/nodes/data.py
from __future__ import annotations
from alphaloom.graph.types import PinType, Stamped
from alphaloom.nodes.registry import node

@node(type="candle_feed", category="data", inputs={},
      outputs={"out": PinType.CANDLE}, params={"inst": str, "bar": str})
class CandleFeedNode:
    def setup(self, params):
        self.inst = params.get("inst", "")
        self.bar = params.get("bar", "1m")
    def on_bar(self, ctx, inputs):
        ev = ctx.current_event
        return {"out": Stamped(ev.candle, ev.ts_close)}
```

```python
# backend/alphaloom/nodes/indicators.py
from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

@node(type="ema", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class EmaNode:
    def setup(self, params):
        self.k = 2 / (int(params["period"]) + 1)
        self.ema = None
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        self.ema = c if self.ema is None else c * self.k + self.ema * (1 - self.k)
        return {"value": self.ema}

@node(type="atr", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class AtrNode:
    def setup(self, params):
        self.period = int(params["period"])
        self.prev_close = None
        self.atr = None
        self.warm = []
    def on_bar(self, ctx, inputs):
        c = inputs["candle"]
        h, l = float(c["high"]), float(c["low"])
        if self.prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
        self.prev_close = float(c["close"])
        if self.atr is None:
            self.warm.append(tr)
            if len(self.warm) <= self.period:
                return {"value": None if len(self.warm) <= self.period else self.atr}
        if self.atr is None:
            self.atr = sum(self.warm) / len(self.warm)
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / self.period
        return {"value": self.atr}

@node(type="rsi", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"period": int})
class RsiNode:
    def setup(self, params):
        self.period = int(params["period"])
        self.prev = None
        self.avg_gain = None
        self.avg_loss = None
        self.warm_g, self.warm_l = [], []
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        if self.prev is None:
            self.prev = c
            return {"value": None}
        chg = c - self.prev
        self.prev = c
        g, l = max(chg, 0.0), max(-chg, 0.0)
        if self.avg_gain is None:
            self.warm_g.append(g); self.warm_l.append(l)
            if len(self.warm_g) < self.period:
                return {"value": None}
            self.avg_gain = sum(self.warm_g) / self.period
            self.avg_loss = sum(self.warm_l) / self.period
        else:
            self.avg_gain = (self.avg_gain * (self.period - 1) + g) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + l) / self.period
        if self.avg_loss == 0:
            return {"value": 100.0}
        rs = self.avg_gain / self.avg_loss
        return {"value": 100 - 100 / (1 + rs)}
```

注意 AtrNode 预热逻辑实现者需自查一遍边界（warm 满 period 根后首个 ATR = TR 简单均值，之后 Wilder 平滑；预热期内输出 None——测试 `test_atr_positive_and_warms_up` 锁定语义为前 period 根输出 None）。上面骨架中 warm 分支有意留了一处冗余判断，实现时化简为清晰版本即可（sanctioned deviation 允许等价重写，测试为准）。

```python
# backend/alphaloom/nodes/gates.py
from __future__ import annotations
from collections import deque
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

def _sig(side="hold", qty=0.0, stop=None, reason=""):
    return {"side": side, "qty": qty, "stop": stop, "reason": reason}

@node(type="cross_signal", category="decision",
      inputs={"fast": PinType.SERIES, "slow": PinType.SERIES,
              "candle": PinType.CANDLE, "atr": PinType.SERIES},
      outputs={"signal": PinType.SIGNAL}, params={"atr_mult": float})
class CrossSignalNode:
    def setup(self, params):
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.prev_diff = None
    def on_bar(self, ctx, inputs):
        fast, slow, atr = inputs["fast"], inputs["slow"], inputs["atr"]
        candle = inputs["candle"]
        if fast is None or slow is None or atr is None:
            return {"signal": _sig()}
        diff = fast - slow
        prev = self.prev_diff
        self.prev_diff = diff
        if prev is None:
            return {"signal": _sig()}
        close = float(candle["close"])
        if prev <= 0 < diff:
            return {"signal": _sig("long", 0.0, close - self.atr_mult * atr, "ema cross up")}
        if prev >= 0 > diff:
            return {"signal": _sig("short", 0.0, close + self.atr_mult * atr, "ema cross down")}
        return {"signal": _sig()}

@node(type="scenario_gate", category="decision",
      inputs={"candle": PinType.CANDLE, "atr": PinType.SERIES},
      outputs={"signal": PinType.SIGNAL},
      params={"lookback": int, "cooldown": int, "atr_mult": float})
class ScenarioGateNode:
    """突破场景状态机（Trade-Tools 血统）：waiting → triggered → cooldown → waiting"""
    def setup(self, params):
        self.lookback = int(params.get("lookback", 20))
        self.cooldown = int(params.get("cooldown", 10))
        self.atr_mult = float(params.get("atr_mult", 2.0))
        self.highs = deque(maxlen=self.lookback)
        self.lows = deque(maxlen=self.lookback)
        self.cool = 0
        self.state["phase"] = "waiting"
    def on_bar(self, ctx, inputs):
        c, atr = inputs["candle"], inputs["atr"]
        close = float(c["close"])
        sig = _sig()
        if self.cool > 0:
            self.cool -= 1
            self.state["phase"] = "cooldown"
        elif len(self.highs) == self.lookback and atr:
            if close > max(self.highs):
                sig = _sig("long", 0.0, close - self.atr_mult * atr, "breakout up")
                self.cool = self.cooldown
                self.state["phase"] = "triggered"
            elif close < min(self.lows):
                sig = _sig("short", 0.0, close + self.atr_mult * atr, "breakout down")
                self.cool = self.cooldown
                self.state["phase"] = "triggered"
            else:
                self.state["phase"] = "waiting"
        self.highs.append(float(c["high"]))
        self.lows.append(float(c["low"]))
        return {"signal": sig}

@node(type="risk_gate", category="risk",
      inputs={"signal": PinType.SIGNAL},
      outputs={"stamped": PinType.RISK_STAMPED_SIGNAL, "blocked": PinType.BOOL},
      params={"max_qty": float, "require_stop": bool})
class RiskGateNode:
    """全宇宙唯一能产出 risk_stamped_signal 的内置节点 —— 类型系统即合规官。"""
    def setup(self, params):
        self.max_qty = float(params.get("max_qty", 10.0))
        self.require_stop = bool(params.get("require_stop", True))
    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        checks: list[str] = []
        if sig["side"] in ("long", "short"):
            if self.require_stop and sig.get("stop") is None:
                checks.append("missing stop: attach a stop-loss to every entry signal")
            if sig.get("qty", 0) > self.max_qty:
                checks.append(f"qty {sig['qty']} exceeds max_qty {self.max_qty}")
        blocked = bool(checks)
        if blocked:
            sig = _sig("hold", reason="blocked by risk gate")
        sig["risk"] = {"checked": True, "blocked": blocked, "checks": checks}
        return {"stamped": sig, "blocked": blocked}

@node(type="kill_switch", category="risk",
      inputs={"candle": PinType.CANDLE}, outputs={"halted": PinType.BOOL},
      params={"max_drawdown_pct": float})
class KillSwitchNode:
    def setup(self, params):
        self.max_dd = float(params.get("max_drawdown_pct", 0.2))
        self.peak = None
    def on_bar(self, ctx, inputs):
        broker = ctx.broker
        if broker is None:
            return {"halted": False}
        eq = broker.equity()
        self.peak = eq if self.peak is None else max(self.peak, eq)
        dd = (self.peak - eq) / self.peak if self.peak > 0 else 0.0
        if dd >= self.max_dd and not broker.halted:
            broker.halt(f"kill switch: drawdown {dd:.1%} >= {self.max_dd:.1%}")
        return {"halted": broker.halted}
```

```python
# backend/alphaloom/nodes/sizing.py
from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node

@node(type="position_sizer", category="risk",
      inputs={"signal": PinType.SIGNAL, "candle": PinType.CANDLE},
      outputs={"sized": PinType.SIGNAL}, params={"risk_pct": float})
class PositionSizerNode:
    def setup(self, params):
        self.risk_pct = float(params.get("risk_pct", 0.02))
    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        if sig["side"] not in ("long", "short") or sig.get("stop") is None:
            return {"sized": sig}
        close = float(inputs["candle"]["close"])
        dist = abs(close - float(sig["stop"]))
        if dist <= 0:
            return {"sized": dict(sig, side="hold", reason="zero stop distance")}
        equity = ctx.broker.equity() if ctx.broker else 10_000.0
        sig["qty"] = equity * self.risk_pct / dist
        return {"sized": sig}
```

```python
# backend/alphaloom/nodes/execution.py
from __future__ import annotations
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import node
from alphaloom.brokers.base import Order

@node(type="execute_order", category="execution",
      inputs={"signal": PinType.RISK_STAMPED_SIGNAL},
      outputs={"submitted": PinType.BOOL})
class ExecuteOrderNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        sig = inputs["signal"]
        broker = ctx.broker
        if sig is None or broker is None or broker.halted or sig["side"] == "hold":
            return {"submitted": False}
        cur = broker.position().qty
        target = {"long": sig["qty"], "short": -sig["qty"], "flat": 0.0}[sig["side"]]
        delta = target - cur
        if abs(delta) < 1e-12:
            return {"submitted": False}
        ok = broker.submit(Order(side="buy" if delta > 0 else "sell",
                                 qty=abs(delta), stop=sig.get("stop"),
                                 tag=sig.get("reason", "")))
        return {"submitted": bool(ok)}
```

`backend/alphaloom/nodes/__init__.py` 末尾追加：

```python
import alphaloom.nodes.data        # noqa: F401,E402
import alphaloom.nodes.indicators  # noqa: F401,E402
import alphaloom.nodes.gates       # noqa: F401,E402
import alphaloom.nodes.sizing      # noqa: F401,E402
import alphaloom.nodes.execution   # noqa: F401,E402
```

- [ ] **Step 4: 运行确认通过（全量回归）**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 全绿（hypothesis 用例较慢属正常）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(nodes): builtin palette - feed, ema/atr/rsi, cross/scenario gates, risk gate, sizer, kill switch, execute"
```

---

### Task 12: 回测 runner + CLI + 两张预置蓝图 + e2e（backtest/runner.py, cli.py, blueprints/*.loom）

**Files:**
- Create: `backend/alphaloom/backtest/__init__.py`（空）, `backend/alphaloom/backtest/runner.py`, `backend/alphaloom/cli.py`, `blueprints/ema_cross.loom`, `blueprints/breakout_scenario.loom`
- Test: `backend/tests/test_backtest_e2e.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_backtest_e2e.py
import json
from pathlib import Path
import alphaloom.nodes  # noqa: F401
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import load_loom_file
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.backtest.runner import run_backtest
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]   # alphaloom 仓库根

def _db(tmp_path, n=600):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    up = gen_candles(n // 2, seed=11, trend=0.002)
    down = gen_candles(n // 2, seed=12, trend=-0.002,
                       start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    return db

def test_preset_blueprints_compile():
    for name in ("ema_cross.loom", "breakout_scenario.loom"):
        bp = load_loom_file(REPO / "blueprints" / name)
        r = compile_blueprint(bp)
        assert r.ok, (name, [e.to_dict() for e in r.errors])
        assert r.certificate.deterministic_ratio == 1.0

def test_ema_cross_end_to_end(tmp_path):
    db = _db(tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    report = run_backtest(bp, db, inst="BTC-USDT-SWAP", bar="1m",
                          record_dir=tmp_path)
    assert report.bars == 600
    assert report.summary["num_trades"] >= 1
    assert report.certificate["deterministic_ratio"] == 1.0
    assert Path(report.recording_path).exists()
    assert len(report.equity_curve) == 600

def test_breakout_end_to_end(tmp_path):
    db = _db(tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "breakout_scenario.loom")
    report = run_backtest(bp, db, inst="BTC-USDT-SWAP", bar="1m")
    assert report.bars == 600 and "net_pnl" in report.summary

def test_cli_run_and_compile(tmp_path, capsys):
    from alphaloom.cli import main
    db = _db(tmp_path)  # noqa: F841  路径在 tmp_path/m.sqlite
    rc = main(["compile", str(REPO / "blueprints" / "ema_cross.loom")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "certificate" in out
    rc2 = main(["run", str(REPO / "blueprints" / "ema_cross.loom"),
                "--db", str(tmp_path / "m.sqlite"),
                "--inst", "BTC-USDT-SWAP", "--bar", "1m"])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and "summary" in out2 and out2["summary"]["num_trades"] >= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_backtest_e2e.py -q`
Expected: FAIL（blueprints 不存在 / runner 不存在）

- [ ] **Step 3: 实现 runner、CLI、两张蓝图**

```python
# backend/alphaloom/backtest/runner.py
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import alphaloom.nodes  # noqa: F401  触发注册
from alphaloom.brokers.paper import PaperBroker
from alphaloom.data.source import DataSource, bar_to_ms
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import BlueprintSpec
from alphaloom.nodes.registry import create_instance
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.recorder import Recorder

class CompileFailed(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} compile error(s)")

@dataclass
class BacktestReport:
    run_id: str
    blueprint_id: str
    bars: int
    summary: dict
    certificate: dict
    equity_curve: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    recording_path: str | None = None

def run_backtest(bp: BlueprintSpec, source: DataSource, *, inst: str, bar: str,
                 start_ms: int | None = None, end_ms: int | None = None,
                 initial_cash: float = 10_000.0, fee_rate: float = 0.0005,
                 record_dir=None) -> BacktestReport:
    compiled = compile_blueprint(bp)
    if not compiled.ok:
        raise CompileFailed(compiled.errors)
    run_id = uuid.uuid4().hex[:12]
    broker = PaperBroker(initial_cash=initial_cash, fee_rate=fee_rate)
    recorder = None
    rec_path = None
    if record_dir is not None:
        rec_path = str(Path(record_dir) / f"run_{run_id}.sqlite")
        recorder = Recorder(rec_path)
    ctx = RunContext(clock=SimClock(), run_id=run_id, broker=broker, recorder=recorder)
    instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
    engine = Engine(compiled, instances, ctx)
    bar_ms = bar_to_ms(bar)
    bars = 0
    for candle in source.iter_candles(inst, bar, start_ms, end_ms):
        broker.on_bar(candle)              # 先撮合上一根的挂单/止损并 mark
        engine.step(BarEvent(candle, bar_ms))
        bars += 1
    if recorder:
        recorder.close()
    return BacktestReport(
        run_id=run_id, blueprint_id=bp.id, bars=bars,
        summary=broker.summary(), certificate=compiled.certificate.to_dict(),
        equity_curve=broker.equity_curve,
        fills=[f.__dict__ for f in broker.fills],
        recording_path=rec_path)
```

```python
# backend/alphaloom/cli.py
from __future__ import annotations
import argparse
import json
import sys

def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # cp1252 陷阱
    p = argparse.ArgumentParser(prog="alphaloom")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("compile", help="compile a .loom blueprint")
    pc.add_argument("blueprint")
    pr = sub.add_parser("run", help="backtest a .loom blueprint")
    pr.add_argument("blueprint")
    pr.add_argument("--db", required=True)
    pr.add_argument("--inst", required=True)
    pr.add_argument("--bar", default="1m")
    pr.add_argument("--start", type=int, default=None)
    pr.add_argument("--end", type=int, default=None)
    pr.add_argument("--cash", type=float, default=10_000.0)
    args = p.parse_args(argv)

    import alphaloom.nodes  # noqa: F401
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.graph.model import load_loom_file

    bp = load_loom_file(args.blueprint)
    if args.cmd == "compile":
        r = compile_blueprint(bp)
        print(json.dumps({
            "ok": r.ok,
            "errors": [e.to_dict() for e in r.errors],
            "certificate": r.certificate.to_dict() if r.certificate else None,
            "order": r.order,
        }, ensure_ascii=False, indent=2))
        return 0 if r.ok else 1

    from alphaloom.backtest.runner import run_backtest, CompileFailed
    from alphaloom.data.sqlite_source import SQLiteMarketData
    try:
        report = run_backtest(bp, SQLiteMarketData(args.db), inst=args.inst,
                              bar=args.bar, start_ms=args.start, end_ms=args.end,
                              initial_cash=args.cash)
    except CompileFailed as cf:
        print(json.dumps({"ok": False,
                          "errors": [e.to_dict() for e in cf.errors]},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "run_id": report.run_id, "bars": report.bars,
                      "certificate": report.certificate,
                      "summary": report.summary}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

`blueprints/ema_cross.loom`（完整内容）：

```json
{
  "id": "ema_cross_v1",
  "name": "EMA Cross Trend Follow",
  "nodes": [
    {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
    {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
    {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
    {"id": "atr", "type": "atr", "params": {"period": 14}},
    {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
    {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
    {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": true}},
    {"id": "exec", "type": "execute_order", "params": {}},
    {"id": "kill", "type": "kill_switch", "params": {"max_drawdown_pct": 0.25}}
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
    {"from": "feed.out", "to": "kill.candle"}
  ],
  "meta": {"preset": true, "description_zh": "EMA 金叉死叉趋势跟随（止损=ATR×2，风险额 2%）"}
}
```

`blueprints/breakout_scenario.loom`（完整内容）：

```json
{
  "id": "breakout_scenario_v1",
  "name": "Breakout Scenario Machine",
  "nodes": [
    {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
    {"id": "atr", "type": "atr", "params": {"period": 14}},
    {"id": "scenario", "type": "scenario_gate", "params": {"lookback": 30, "cooldown": 10, "atr_mult": 2.5}},
    {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.01}},
    {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100.0, "require_stop": true}},
    {"id": "exec", "type": "execute_order", "params": {}},
    {"id": "kill", "type": "kill_switch", "params": {"max_drawdown_pct": 0.2}}
  ],
  "edges": [
    {"from": "feed.out", "to": "atr.candle"},
    {"from": "feed.out", "to": "scenario.candle"},
    {"from": "atr.value", "to": "scenario.atr"},
    {"from": "scenario.signal", "to": "sizer.signal"},
    {"from": "feed.out", "to": "sizer.candle"},
    {"from": "sizer.sized", "to": "risk.signal"},
    {"from": "risk.stamped", "to": "exec.signal"},
    {"from": "feed.out", "to": "kill.candle"}
  ],
  "meta": {"preset": true, "description_zh": "30 根区间突破 + 冷却期状态机（Trade-Tools 场景机血统）"}
}
```

- [ ] **Step 4: 运行确认通过（全量回归）**

Run: `cd backend && .venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(backtest): runner + CLI + two preset blueprints, end-to-end green"
```

---

### Task 13: 样本库脚本 + D1 收尾

**Files:**
- Create: `scripts/build_sample_db.py`
- Modify: 无

- [ ] **Step 1: 实现下载脚本（无测试——联网脚本不进测试路径，人工冒烟）**

```python
# scripts/build_sample_db.py
"""从 OKX 公共 REST 拉取 1m K 线到 data/sample.sqlite（可断点续传）。

用法:  backend/.venv/Scripts/python scripts/build_sample_db.py --days 90 \
           --inst BTC-USDT-SWAP ETH-USDT-SWAP
仅公共端点、无鉴权；限流退避；测试/CI 绝不调用本脚本。
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from alphaloom.data.sqlite_source import SQLiteMarketData  # noqa: E402

BASE = "https://www.okx.com/api/v5/market/history-candles"
UA = {"User-Agent": "alphaloom-sample-builder/0.1"}

def fetch(inst: str, before_ms: int | None, bar: str = "1m") -> list[list]:
    q = {"instId": inst, "bar": bar, "limit": "100"}
    if before_ms is not None:
        q["after"] = str(before_ms)     # OKX: after=ts 返回更旧的数据
    url = BASE + "?" + urllib.parse.urlencode(q)
    for attempt in range(8):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=15) as r:
                body = json.loads(r.read().decode("utf-8"))
            if body.get("code") == "0":
                return body["data"]
            time.sleep(2 ** attempt)     # 限流/繁忙：指数退避
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"OKX fetch failed for {inst} before={before_ms}")

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--inst", nargs="+", default=["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
    ap.add_argument("--out", default="data/sample.sqlite")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteMarketData(args.out)
    cutoff = int(time.time() * 1000) - args.days * 86_400_000
    for inst in args.inst:
        bounds = db.bounds(inst, "1m")
        before = bounds[0] if bounds else None   # 续传：从已有最旧处继续往回拉
        total = 0
        while True:
            rows = fetch(inst, before)
            if not rows:
                break
            candles = [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                        "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                       for r in rows]
            db.insert_candles(inst, "1m", candles)
            total += len(candles)
            before = min(c["ts"] for c in candles)
            print(f"{inst}: {total} bars, oldest={before}")
            if before <= cutoff:
                break
            time.sleep(0.25)                      # 温和限速
    print("done")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 人工冒烟（可选，若网络可用）**

Run: `backend/.venv/Scripts/python scripts/build_sample_db.py --days 2 --inst BTC-USDT-SWAP`
Expected: 打印进度并生成 `data/sample.sqlite`（约 2880 根/合约）。正式 90 天拉取放在 D1 收尾后台执行。

- [ ] **Step 3: D1 全量验收**

```bash
cd backend && .venv/Scripts/python -m pytest -q          # 全绿
cd .. && git log -p --all | grep -ciE "sk-[A-Za-z0-9]{16,}|apikey.{0,4}[:=]" || true   # 期望 0
```

- [ ] **Step 4: Commit + tag**

```bash
git add -A && git commit -m "feat(scripts): resumable OKX public candle downloader for sample db"
git tag d1-complete
```

---

## Carryover（跨任务备忘，D2+ 待办）

1. **CompileResult.nodes**：编译结果必须携带**展开后**的 `nodes: dict[str, NodeSpec]`（子图展开产生 `sub/inner` 形态的新节点，runner 用它实例化）。Task 4 定义、Task 5 展开后填充、Task 12 消费。
2. 信号 side 语义为四值：`long/short/flat/hold`（hold=不动作，flat=平仓）——文首"锁定签名"以 Task 11 的四值定义为准。
3. Engine 断点目前是同步回调（on_pause）；D2 API 层把它接成 WS 暂停/单步协议（挂起 asyncio Event）。
4. Recorder 每事件 commit 一次，大回测慢——D2 若性能不足改批量 commit（每 N 事件），语义不变。
5. AtrNode 预热分支按测试语义化简重写（前 period 根输出 None）。
6. PaperBroker 单仓单品种；组合多品种是 future-work，不进 D2-D4。
7. okx_algorithnm 引擎（tick/L2 成交模型）D4 保真度阶梯时移植为 `alphaloom/backtest/fills/`，黄金测试对照其 runs/ 产物。
8. `data/sample.sqlite` 已 gitignore；演示前需人工跑一次 build_sample_db.py（90 天约 26 万根/合约，20-40 分钟）。
9. D2 前端将复用 Hindsight 设计系统；Studio 画布节点分类颜色 = registry category（data/indicator/decision/risk/execution）。
10. LLM 节点（D3）的 CostAnnotation 必须如实填写（llm_calls_per_bar≥1, deterministic=False），成本证书的可信度靠注解纪律维持；D3 加"注解审计"测试（LLM 类节点禁止声明 deterministic=True）。
11. 因果类型系统 D1 交付**运行时守卫**（check_stamped + 恶意节点测试）；spec §3.1 提到的"编译器对窗口类操作做静态越界检查"排 D3（节点声明 lookback 元数据后编译器才有静态分析素材）——对外表述统一为"runtime-enforced, compiler-assisted"，不要吹成纯编译期。
