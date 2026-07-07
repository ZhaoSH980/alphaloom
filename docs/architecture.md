# AlphaLoom 设计与实现文档

> 本文档系统性描述 AlphaLoom 当前代码库（`main` 分支）的架构设计与关键实现机制，覆盖从蓝图 DSL、图编译器、运行时引擎、内置节点体系、自定义节点沙箱，到回测/实时纸上交易执行路径、LLM 集成、数据持久化、评估与进化实验室、API 层、前端架构与部署运维的完整链路。内容基于对源码的实际阅读整理而成，重点解释"设计是什么、为什么这样设计、模块之间如何协作"，而非逐行代码翻译；涉及安全相关机制（如沙箱、风控盖章类型）之处，描述的是其设计意图与工作原理，不代表当前实现已被审计为无缺陷——如需了解已知边界，请参阅 `docs/future-work.md`。
>
> **执行边界说明：** 本文中的"Live"/"实时"/"实盘"均指实时行情驱动的纸上交易路径：`LiveSession` 轮询 OKX 公开 K 线，策略图按 live cadence 前进，但成交仍由本地 `PaperBroker` 模拟。当前代码没有 OKX demo 账户下单适配器，也不会向真实交易所提交委托。
>
> 覆盖范围：`backend/alphaloom/*`、`frontend/src/*`、`scripts/*`、`.github/workflows/*`。

## 目录

- [1. 项目概览与总体架构](#1-项目概览与总体架构)
- [2. 蓝图 DSL 与图编译器](#2-蓝图-dsl-与图编译器)
- [3. 运行时引擎与执行模型](#3-运行时引擎与执行模型)
- [4. 内置节点体系](#4-内置节点体系)
- [5. 自定义节点沙箱](#5-自定义节点沙箱)
- [6. 回测引擎与模拟经纪商](#6-回测引擎与模拟经纪商)
- [7. 实时纸上交易会话](#7-实时纸上交易会话)
- [8. LLM 集成、录制回放与 Copilot](#8-llm-集成录制回放与-copilot)
- [9. 数据与持久化层](#9-数据与持久化层)
- [10. 评估实验室与策略进化](#10-评估实验室与策略进化)
- [11. REST/WebSocket API 层](#11-restwebsocket-api-层)
- [12. 前端架构](#12-前端架构)
- [13. 部署、运维与 CI](#13-部署运维与-ci)

---

## 1. 项目概览与总体架构

### 1.1 AlphaLoom 是什么

AlphaLoom 是一个"Agent-native"的量化交易研究工作台：它把一个 LLM 交易想法从"prompt 里的一段叙事"变成一个**可编译、可类型检查、可增量实时运行、可完整回放、可被证伪评估**的交易 Agent 运行时协议。核心手段是把策略拓扑表达为一张可编辑的 `.loom` 蓝图（节点 + 类型化连线），由图编译器在运行前静态验证出"合法下单路径"和"成本证书"，再由运行时引擎按 bar-by-bar 的方式驱动这张图，在回测引擎/纸上经纪商、轮询 OKX 公开行情的 Live Session、LLM 分析/反思/委员会节点、AST 沙箱自定义节点等子系统之间协同工作，最终由 FastAPI 后端和 React/TypeScript 前端把编译证书、执行轨迹、评估结果暴露给使用者（`backend/pyproject.toml:4` 中项目自述为 *"Agent-native quant trading platform - the graph IS the agent"*）。项目定位是**研究/演示系统**，不是投资建议也不是 alpha 收益承诺（`README.md:45`），其"卖点"是把 LLM trading demo 里通常被隐藏的部分——策略结构、风控、成本、证据——都变成图上可检查的一等公民（`README.md:47-59`）。

### 1.2 核心心智模型

理解 AlphaLoom 的关键在于三个互相支撑的设计支点：

**（1）图即 Agent。** `.loom` 蓝图不是文档，而是唯一的策略事实来源：一组带类型输入/输出引脚的节点（`PinType`：`EXEC`/`CANDLE`/`SERIES`/`SIGNAL`/`RISK_STAMPED_SIGNAL`/`BOOL`，定义于 `backend/alphaloom/graph/types.py:6-12`）通过具名端口互相连线。编译器（`graph/compiler.py`）把这张图静态编译为拓扑执行序（`CompileResult.order`）、端口绑定表（`bindings`）、成本证书（`certificate`）与合法下单路径（`order`），任何图结构或类型错误都在编译期被拒绝，而不是运行时才暴露。

**（2）按 bar 驱动的数据流执行。** `Engine.step(ev: BarEvent)`（`backend/alphaloom/runtime/engine.py:89-129`）是运行时的心跳：每来一根新 K 线，先推进 `SimClock`（`ctx.clock.advance(ev.ts_close)`），再按编译期定好的拓扑序 `self.compiled.order` 依次调用每个节点实例的 `on_bar(ctx, inputs)`，把本轮所有节点的输出汇入一个 `wave: dict[(node_id, port), Stamped]` 字典；下一个节点的输入直接从 `wave`（或反馈边情形下从上一轮的 `self._prev`）里取值。这个"逐 bar 全图前向传播一次"的模型，与 LangGraph 风格的显式状态图执行非常接近，只是这里的驱动信号是市场时间序列上的每一根 K 线，而非对话轮次。

**（3）因果性保证：图看不到未来。** 每个节点的输出都被包装为 `Stamped(value, as_of)`（`graph/types.py:14-17`），`as_of` 是该值在时间轴上生效的毫秒时间戳。`Engine` 在采纳任何节点输出前调用 `check_stamped(node_id, s, self.ctx.clock.now)`（`runtime/context.py:18-28`），一旦发现某个输出的 `as_of` 晚于当前时钟 `now`，立即抛出 `CausalityError`（"graphs must not perceive the future"）。这不是运行时的可选校验，而是每一步都强制执行的不变量——它把"策略不能用未来数据"从人工审查的编码规范，变成了引擎层面机械保证的契约。这也是为什么回测引擎坚持 next-bar-open 成交、不做 look-ahead 读取（README 中反复强调）：因果性保证与撮合规则是同一设计哲学在两处的体现。

三者合起来的心智模型是：**一张类型化的图，在编译期被证明"只有合法信号才能走到下单"，在运行期被逐 bar 推进，且每一步都无法窥见未来。**

### 1.3 高层架构

系统在物理上分为前端单页应用、FastAPI 后端（REST + WebSocket）、图编译器、运行时引擎，以及运行时引擎驱动的两条并列执行路径（历史回测 / 实时纸上交易），二者共享同一套节点实现、经纪商抽象、LLM 客户端和 SQLite 持久化层。

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         浏览器：React/TypeScript SPA                       │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────┐            │
│  │  Studio  │  │ Live Desk │  │  Terminal  │  │   Eval Lab   │            │
│  │ 蓝图编辑  │  │ 实时看盘   │  │  轨迹回放   │  │ 评估/进化    │            │
│  │ +Copilot │  │ +sidecar  │  │ +节点 I/O  │  │ +排行/消融   │            │
│  └────┬─────┘  └─────┬─────┘  └─────┬──────┘  └──────┬───────┘            │
│       └──────────────┴──────HTTP/JSON + WebSocket─────┘                   │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    │  /api/*  (REST)   /ws/runs/{id}, /ws/live/{id} (WS)
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     FastAPI 后端  (alphaloom.api.app:create_app)           │
│  compile / blueprints / market / runs / live / copilot / eval / evolve /   │
│  nodes/custom 等路由；RunsStore（元数据）+ 事件回放缓冲（内存 20k 上限）    │
└───────┬───────────────────────────────────┬──────────────────────────────┘
        │                                   │
        ▼                                   ▼
┌───────────────────┐              ┌────────────────────┐
│   图编译器          │─ 编译 ─────▶│   运行时引擎 Engine  │
│ graph/compiler.py  │  CompileResult │ runtime/engine.py │
│ 类型检查/拓扑序/     │  (order,      │  bar-by-bar 驱动、  │
│ 成本证书/合法下单路径│   bindings,   │  因果性校验、        │
└───────────────────┘   certificate) │  沙箱受限 ctx        │
                                     └──────────┬─────────┘
                                                │ Engine.step(BarEvent)
                        ┌───────────────────────┴────────────────────────┐
                        ▼                                                ▼
              ┌───────────────────┐                          ┌────────────────────────┐
              │   回测运行器        │                          │   实时会话 LiveSession   │
              │ backtest/runner.py │                          │   api/live.py           │
              │ next-bar-open 成交、│                          │   轮询 OKX 公开行情 API、 │
              │ 附加止损、EOD 结算   │                          │   sidecar LLM 分析卡片   │
              └─────────┬──────────┘                          └───────────┬─────────────┘
                        │                                                 │
                        ▼                                                 ▼
              ┌────────────────────┐                         ┌────────────────────────┐
              │   PaperBroker       │                         │  同一 PaperBroker /     │
              │ brokers/paper.py    │◀────────共用────────────│  Engine / RunContext    │
              └────────────────────┘                         └────────────────────────┘
                        │                                                 │
                        └───────────────────┬─────────────────────────────┘
                                             ▼
                      ┌───────────────────────────────────────────┐
                      │     共享基础设施层                          │
                      │  · LLM 客户端 (llm/client.py, recording.py) │
                      │    —— offline 录制回放 / live 真实端点       │
                      │  · SQLite：demo.sqlite（行情）/              │
                      │    runs.sqlite（run 元数据）/                │
                      │    llm_calls.sqlite（LLM 调用录制）/         │
                      │    每次 run 独立的 recording（node I/O）     │
                      │  · 沙箱 AST 编译器 (sandbox/node_sandbox.py) │
                      │  · 反思记忆 (memory/experience_store.py)     │
                      └───────────────────────────────────────────┘
```

数据流方向：前端通过 REST 提交蓝图 JSON 与运行参数（发起编译 / 回测 / 实时纸上交易会话 / Copilot 请求 / 评估任务），后端先经图编译器验证并生成执行计划，再交给运行时引擎实例化节点并逐 bar 驱动；回测场景下由 `backtest/runner.py` 批量喂入历史 K 线，实时场景下由 `LiveSession` 轮询 OKX 得到新 K 线后逐根喂入同一个 `Engine`/`RunContext`（两条路径共用同一套节点实现与 `PaperBroker`）；执行过程中的每一步事件（进度、节点 I/O、成交、告警）通过内存队列推给 WebSocket 连接，同时落盘到 run 专属的 recording SQLite 用于日后在 Terminal 页面回放；LLM 相关节点通过统一的 LLM 客户端接口发起调用，该客户端在离线模式下从 `llm_calls.sqlite` 按请求哈希回放录制响应，在实时模式下才真正联网调用 OpenAI 兼容端点。

### 1.4 技术栈

| 层次 | 技术选型 | 说明 |
|---|---|---|
| 后端语言/运行时 | Python ≥ 3.12（`backend/pyproject.toml:7`） | `requires-python = ">=3.12"` |
| Web 框架 | FastAPI ≥ 0.115 + Uvicorn（standard）≥ 0.30 | 单进程同时提供 REST、WebSocket 与前端静态资源（SPA fallback，见 `serve.py`/`api/app.py`） |
| LLM 客户端 | `openai` ≥ 1.40 SDK（OpenAI 兼容协议） | 通过 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` 对接任意 OpenAI 兼容端点（含讯飞 Spark `astron-code-latest`） |
| 配置 | `python-dotenv` ≥ 1.0 | `.env` 承载 LLM 端点配置 |
| 数据持久化 | 内置 `sqlite3`（标准库，无 ORM） | 行情库 `demo.sqlite`、run 元数据库 `runs.sqlite`、LLM 调用录制库 `llm_calls.sqlite`、每 run 独立的 node I/O recording |
| 测试（后端） | pytest ≥ 8、hypothesis ≥ 6（基于性质测试）、httpx ≥ 0.27（ASGI 测试客户端） | `dev` 可选依赖组 |
| 前端构建 | Vite ^5.4 + TypeScript ~5.6（`tsc -b` 严格构建）+ React ^18.3 | `npm run build` 输出到 `frontend/dist`，由后端 `create_app(frontend_dist=...)` 直接托管 |
| 前端图编辑 | `@xyflow/react` ^12 | Studio 页面的可视化节点图编辑器（拖拽连线） |
| 前端图表 | `lightweight-charts` ^4.2 | K 线/权益曲线渲染（CandleChart、EquityChart 等组件） |
| 前端样式 | Tailwind CSS ^3.4 + 自定义字体（Chakra Petch / IBM Plex Sans / JetBrains Mono） | 深色 HUD 风格设计系统 |
| 前端测试 | Vitest ^2 + jsdom ^25 | `npm run test`，覆盖 lib/、components/、pages/ |
| 交易所对接 | OKX 公开行情 REST API（`https://www.okx.com/api/v5/market/candles`） | `okx_candle_fetcher`（`backend/alphaloom/api/live.py:62`），无需 API Key 的公开候选数据轮询 |

后端以可编辑安装形式发布（`pip install -e .[dev]`），包名 `alphaloom`，构建系统为 `setuptools`（`[tool.setuptools.packages.find] include = ["alphaloom*"]`）。项目提供一键启动脚本 `START_ALPHALOOM.cmd`，默认以 `ALPHALOOM_OFFLINE=1` 的离线模式运行——录制好的市场数据与 LLM 调用零网络、零 LLM quota 地回放，这也是 `docs/evaluation-methodology.md` 反复强调的"诚实评估"品牌的基础设施前提。

### 1.5 目录结构地图

#### `backend/alphaloom/*`

| 目录/文件 | 一句话职责 |
|---|---|
| `serve.py` | 单进程生产入口：`uvicorn alphaloom.serve:app` 使用的默认 `create_app(...)` 装配（数据库路径、蓝图目录、前端 dist 目录） |
| `cli.py` | 命令行入口 `alphaloom compile` / `alphaloom run`，脱离 HTTP 直接编译或回测一个 `.loom` 文件 |
| `graph/` | 蓝图 DSL 模型（`model.py`）、类型系统（`types.py`）、编译器（`compiler.py`）、成本核算（`cost.py`）、编译错误类型（`errors.py`）——第 2 章主题 |
| `runtime/` | 执行引擎（`engine.py`）、运行上下文与因果时钟（`context.py`）、bar 事件定义（`events.py`）、node I/O 录制器（`recorder.py`)——第 3 章主题 |
| `nodes/` | 内置节点实现：数据/指标（`data.py`/`indicators.py`）、执行/风控门（`execution.py`/`gates.py`/`pa_gate.py`）、仓位 sizing（`sizing.py`）、LLM 与 RAG 节点（`llm_nodes.py`/`rag_nodes.py`）、反思（`reflection.py`）、全局节点注册表（`registry.py`）——第 4 章主题 |
| `sandbox/` | Text-to-Node 自定义节点的 AST 白名单沙箱编译器（`node_sandbox.py`）、沙箱专用错误类型（`errors.py`）、审计日志（`audit.py`）——第 5 章主题 |
| `backtest/` | 回测运行器 `runner.py`：编译 → 实例化 → 逐 bar 驱动 → 生成报告 —— 第 6 章主题 |
| `brokers/` | 经纪商抽象（`base.py`）与纸上经纪商实现（`paper.py`，含 next-bar-open 撮合、止损、EOD 结算）——第 6 章主题 |
| `llm/` | LLM 客户端配置与 OpenAI 兼容传输（`client.py`）、录制/回放客户端（`recording.py`）、429 退避重试（`retry.py`)——第 8 章主题 |
| `memory/` | 反思经验存储（`experience_store.py`)——第 8 章主题 |
| `knowledge/` | BM25 检索语料（`corpus.py`），供 RAG 节点引用检查——第 4/8 章主题 |
| `copilot/` | 自然语言到蓝图（`blueprint.py`）、Copilot 自动布局（`layout.py`）、提示词模板（`prompts.py`)——第 8 章主题 |
| `eval/` | 评估实验室各面板的后端计算：保真度阶梯（`fidelity.py`）、记分卡（`scorecard.py`）、排行榜（`leaderboard.py`）、消融（`ablation.py`）、demo 坐标常量（`demo_coords.py`)——第 10 章主题 |
| `evolve/` | 蓝图进化实验室：LLM 变异算子 + 编译守门 + 谱系树（`lab.py`)——第 10 章主题 |
| `data/` | 市场数据源抽象（`source.py`）与 SQLite 实现（`sqlite_source.py`)——第 9 章主题 |
| `api/` | FastAPI 应用装配（`app.py`）、实时会话服务（`live.py`）、run 元数据存储（`runs_store.py`）、请求/响应模型（`schemas.py`）、安全序列化（`serialize.py`）、run 服务编排（`service.py`)——第 11 章主题 |

#### `frontend/src/*`

| 目录/文件 | 一句话职责 |
|---|---|
| `App.tsx` | 顶层路由（基于 `location.hash` 的四页签切换）与全局运行模式（offline/live/none）切换控制 |
| `main.tsx` | Vite/React 应用挂载入口 |
| `pages/Studio.tsx` | 蓝图工坊：可视化图编辑、编译证书面板、Copilot 面板 |
| `pages/LiveDesk.tsx` | 实时看盘台：左侧蓝图、中间 K 线、右侧门控/反思/LLM sidecar 分析 |
| `pages/Terminal.tsx` | 交易终端：run 选择器、trace 浏览、节点 I/O、委员会/反思证据回放 |
| `pages/Eval.tsx` | 评估实验室：保真度阶梯、记分卡、排行榜、消融、进化谱系 |
| `components/` | 可复用 UI 组件：K 线图/权益曲线（`CandleChart`/`EquityChart`）、节点卡片与门控视图（`NodeCard`/`GateView`）、成交表/汇总卡（`TradesTable`/`SummaryCards`）、回测实验面板（`BacktestLab`）、Copilot 面板（`CopilotPanel`）、各评估面板（`FidelityLadder`/`LeaderboardTable`/`AblationTable`/`GenealogyTree`/`ScorecardPanel`）等 |
| `lib/api.ts` | REST 客户端封装（compile/runs/live/copilot/eval 等端点调用） |
| `lib/ws.ts` | WebSocket 连接与事件流封装 |
| `lib/loom.ts` | `.loom` 蓝图 JSON 的前端侧类型与操作 |
| `lib/backtestConfig.ts` / `liveDesk.ts` / `demoDefaults.ts` | 回测参数、实时看盘状态、demo 默认值等业务逻辑 |
| `lib/eval.ts` / `insights.ts` | 评估面板数据处理与派生洞察 |
| `lib/copilot.ts` | Copilot 交互状态机（生成/解释/优化/diff 应用） |
| `lib/i18n.ts` | 中英文双语文案 |
| `styles/` | 全局样式与 Tailwind 配置相关资源 |

### 1.6 后续章节路线图

| 章节 | 内容概述 |
|---|---|
| 2. 蓝图 DSL 与图编译器 | `.loom` JSON 格式、节点/端口/类型系统、编译器如何做类型检查、拓扑排序、成本证书与合法下单路径推导。 |
| 3. 运行时引擎与执行模型 | `Engine`/`RunContext`/`BarEvent` 的协作细节、wave 式逐 bar 执行、因果性校验、断点与事件录制机制。 |
| 4. 内置节点体系 | 数据/指标、风控门、仓位 sizing、执行、LLM 分析/委员会、RAG、反思等内置节点族的职责与实现方式。 |
| 5. 自定义节点沙箱 | Text-to-Node 的 AST 白名单编译流程、受限运行时上下文、沙箱节点与受信内置节点的能力边界。 |
| 6. 回测引擎与模拟经纪商 | `backtest/runner.py` 的编译-实例化-驱动流程、`PaperBroker` 的撮合规则（next-bar-open、止损、EOD 结算）。 |
| 7. 实时纸上交易会话 | `LiveSession` 如何轮询 OKX 行情、驱动同一运行上下文实现增量前进、sidecar LLM 分析卡片的产出与记录。 |
| 8. LLM 集成、录制回放与 Copilot | LLM 客户端抽象、offline 录制回放机制、Copilot 自然语言生成/解释/优化蓝图的工作流。 |
| 9. 数据与持久化层 | `DataSource` 抽象、SQLite 行情/运行元数据/LLM 调用/节点 I/O 各库的表结构与读写路径。 |
| 10. 评估实验室与策略进化 | 保真度阶梯、记分卡、基线排行榜、委员会消融与进化实验室的计算逻辑与诚实边界。 |
| 11. REST/WebSocket API 层 | FastAPI 路由全貌、请求/响应模型、WebSocket 事件推送与重放协议。 |
| 12. 前端架构 | React/TypeScript 页面与组件组织、状态管理方式、图编辑器与图表库的集成方式。 |
| 13. 部署、运维与 CI | 一键启动脚本、离线/实时/无 LLM 三种运行模式的配置矩阵、CI 流水线与测试策略。 |

---

## 2. 蓝图 DSL 与图编译器

AlphaLoom 把一条交易策略表达为一张有向图：数据从 `candle_feed` 流出，经过指标、决策、风控、执行等节点逐级加工，最终落到 broker。这张图既可以在前端画布上以节点连线的方式可视化编辑，也可以直接写成一个 `.loom` JSON 文件。承载这一切的核心模块是 `backend/alphaloom/graph/` 下的四个文件：`model.py`（数据模型与序列化）、`types.py`（引脚类型系统）、`compiler.py`（静态编译/校验）、`cost.py`（成本证书）、`errors.py`（结构化编译错误）。

### 2.1 `.loom` 文件格式与 `BlueprintSpec` 数据模型

`backend/alphaloom/graph/model.py` 定义了蓝图的内存表示，是一组冻结（`frozen=True`）的 dataclass，刻意保持"薄"——不含任何校验或类型检查逻辑，那些工作全部下放给 compiler：

```python
@dataclass(frozen=True)
class PortRef:
    node_id: str
    port: str

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

@dataclass
class BlueprintSpec:
    id: str
    name: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    meta: dict = field(default_factory=dict)
```

（`backend/alphaloom/graph/model.py:5-44`）

四个字段各司其职：

- **`id` / `name`**：蓝图的标识与展示名，供前端列表、`/api/compile` 请求体等引用。
- **`nodes`**：`NodeSpec` 列表，每个节点有全局唯一 `id`（图内命名空间）、`type`（对应 `nodes/registry.py` 里 `REGISTRY` 注册的节点类型字符串，例如 `"ema"`、`"risk_gate"`）、以及 `params` 字典（节点的构造参数，如 `{"period": 12}`）。
- **`edges`**：`EdgeSpec` 列表，每条边由 `src`/`dst` 两个 `PortRef("node_id", "port")` 组成，外加一个 `feedback: bool` 标记——这是图里唯一允许出现"环"的机制（下文 2.3 节详述）。
- **`meta`**：自由格式的元数据字典，不参与编译，用于承载展示层信息（下面会看到它常被用来存一份 `gateProtocol` —— 面向前端 UI 的"叙事协议"，说明每个阶段在讲故事线上处于什么位置，但这与编译器语义无关）。

`BlueprintSpec` 的边用字符串形式 `"node.port"` 编码（而不是嵌套 JSON 对象），`_parse_ref()`（`model.py:10-16`）负责把它切分成 `PortRef`，规则很简单：必须恰好有一个 `.`，且两侧都非空，否则直接抛 `ValueError`。这个从字符串到 `PortRef` 的解析同时也在 `compiler.py` 里为子图端口映射复用了一份（`_parse_ref_str`，`compiler.py:34-40`），保持两处解析行为一致。

#### 一个真实 `.loom` 片段：`blueprints/ema_cross.loom`

```json
{
  "id": "ema_cross_v1",
  "name": "EMA Cross Trend Follow",
  "nodes": [
    { "id": "feed",  "type": "candle_feed", "params": { "inst": "BTC-USDT-SWAP", "bar": "1m" } },
    { "id": "ema_fast", "type": "ema", "params": { "period": 12 } },
    { "id": "ema_slow", "type": "ema", "params": { "period": 26 } },
    { "id": "atr",   "type": "atr",  "params": { "period": 14 } },
    { "id": "cross", "type": "cross_signal", "params": { "atr_mult": 2 } },
    { "id": "sizer", "type": "position_sizer", "params": { "risk_pct": 0.02 } },
    { "id": "risk",  "type": "risk_gate", "params": { "max_qty": 100, "require_stop": true } },
    { "id": "exec",  "type": "execute_order", "params": {} },
    { "id": "kill",  "type": "kill_switch", "params": { "max_drawdown_pct": 0.25 } }
  ],
  "edges": [
    { "from": "feed.out",      "to": "ema_fast.candle" },
    { "from": "feed.out",      "to": "ema_slow.candle" },
    { "from": "feed.out",      "to": "atr.candle" },
    { "from": "ema_fast.value","to": "cross.fast" },
    { "from": "ema_slow.value","to": "cross.slow" },
    { "from": "feed.out",      "to": "cross.candle" },
    { "from": "atr.value",     "to": "cross.atr" },
    { "from": "cross.signal",  "to": "sizer.signal" },
    { "from": "feed.out",      "to": "sizer.candle" },
    { "from": "sizer.sized",   "to": "risk.signal" },
    { "from": "risk.stamped",  "to": "exec.signal" },
    { "from": "feed.out",      "to": "kill.candle" }
  ],
  "meta": {
    "preset": true,
    "description_zh": "EMA 金叉死叉趋势跟随（止损=ATR×2，风险额 2%）",
    "gateProtocol": { "...": "面向前端叙事 UI 的阶段/证据说明，不参与编译" }
  }
}
```

这条蓝图的信号链路是：`candle_feed` 产出 K 线 → `ema`（快/慢两条）与 `atr` 计算指标 → `cross_signal` 依据快慢线交叉和 ATR 产出裸 `signal` → `position_sizer` 按 `risk_pct` 换算仓位（`sizer.sized`）→ `risk_gate` 对信号做风控检查并"盖章"为 `risk_stamped_signal`（`risk.stamped`）→ `execute_order` 才能真正下单。`kill_switch` 则独立挂在 `feed.out` 上，监控回撤、按需熔断 broker，它不在信号主链路上，是一条并行的护栏。`meta.gateProtocol` 里的 `invariant` 字段用自然语言写明了这条蓝图的设计不变式："模型或策略的原始输出不能直接执行，必须先经过仓位计算和 RiskGate 盖章"——这正是编译器在类型层面强制的规则（见 2.2、2.3 节）。

`blueprints/agent_committee.loom` 结构相似但把 `cross_signal` 换成了 LLM 驱动的 `committee` 节点，并插入了 `knowledge_retrieve`（RAG 检索）→ `require_citations`（引用门控）→ `experience_retrieve` / `reflector` / `experience_write`（反思闭环）等节点，构成一条"LLM 决策 + 证据门控 + 反思写库"的更复杂链路，但落到执行侧的收口方式完全一样：`sizer → risk_gate → execute_order`。这说明 DSL 和编译器对"这是不是 LLM 节点"是无感的——无论决策来自确定性指标交叉还是 LLM 委员会，类型系统只关心信号有没有被 `risk_gate` 盖章。

### 2.2 PinType 引脚类型系统

`backend/alphaloom/graph/types.py` 定义了图上数据流动的类型系统：

```python
class PinType(str, Enum):
    EXEC = "exec"
    CANDLE = "candle"
    SERIES = "series"
    SIGNAL = "signal"
    RISK_STAMPED_SIGNAL = "risk_stamped_signal"
    BOOL = "bool"
```

（`backend/alphaloom/graph/types.py:6-12`）

| PinType | 值 | 代表的数据 | 典型产出节点 | 典型消费节点 |
|---|---|---|---|---|
| `EXEC` | `"exec"` | 执行时序/控制流引脚（预留的控制流类型） | — | — |
| `CANDLE` | `"candle"` | 一根 K 线（OHLCV 字典） | `candle_feed` | `ema`、`atr`、`cross_signal`、`position_sizer`、`kill_switch` 等几乎所有需要行情的节点 |
| `SERIES` | `"series"` | 单个数值型时间序列点（指标输出，如 EMA/ATR 的当前值） | `ema`、`atr` | `cross_signal`（fast/slow/atr 输入）、`llm_analyst`、`committee` 等 |
| `SIGNAL` | `"signal"` | 未经风控的裸交易意图（`{"side","qty","stop","reason",...}`） | `cross_signal`、`scenario_gate`、`llm_analyst`、`committee`、`position_sizer`（`sized` 输出仍是 `SIGNAL`） | `position_sizer`、`require_citations`、`risk_gate` |
| `RISK_STAMPED_SIGNAL` | `"risk_stamped_signal"` | **已通过风控检查、盖了"合规章"的信号** | **仅 `risk_gate`（`RiskGateNode.stamped` 输出）** | `execute_order` |
| `BOOL` | `"bool"` | 布尔标志（如熔断状态、是否被风控拦截） | `risk_gate`（`blocked`）、`kill_switch`（`halted`） | 供前端展示/下游条件判断 |

数据在引脚间以 `Stamped` 值的形式流动：

```python
@dataclass(frozen=True)
class Stamped:
    """数据引脚上流动的值：value + as-of 毫秒时间戳（因果类型系统的载体）。"""
    value: Any
    as_of: int
```

`as_of` 时间戳是"因果类型系统"的载体——保证节点在处理某一根 bar 时，看到的每个输入都标注了自己"生效于哪个时刻"，为回测引擎/运行时对齐时序、避免用到未来数据提供基础设施（该职责主要在 runtime/引擎侧落地，`types.py` 只定义载体形状）。

#### `RISK_STAMPED_SIGNAL`：把类型系统当作合规官

`RISK_STAMPED_SIGNAL` 是这套类型系统里最关键的设计。它不是一个"更严格的 SIGNAL"，而是一个**只有一个受信节点能够产出**的类型：`backend/alphaloom/nodes/gates.py` 中的 `RiskGateNode`：

```python
@node(type="risk_gate", category="risk",
      inputs={"signal": PinType.SIGNAL},
      outputs={"stamped": PinType.RISK_STAMPED_SIGNAL, "blocked": PinType.BOOL},
      params={"max_qty": float, "require_stop": bool})
class RiskGateNode:
    """全宇宙唯一能产出 risk_stamped_signal 的内置节点 —— 类型系统即合规官。"""
```

（`backend/alphaloom/nodes/gates.py:71-76`）

`RiskGateNode.on_bar` 会检查 `side` 是否合法、`qty` 是否为非负有限数且不超过 `max_qty`、`require_stop=True` 时是否附带止损价，把检查结果汇总进 `sig["risk"] = {"checked": True, "blocked": bool, "checks": [...]}`；一旦有任何检查未通过，输出信号会被强制重写为 `hold`。只有走完这套检查、被 `RiskGateNode` 亲自实例化、输出到 `stamped` 端口的值，其 `PinType` 才是 `RISK_STAMPED_SIGNAL`。

而下游唯一接受这个类型的节点是 `execute_order`：

```python
@node(type="execute_order", category="execution",
      inputs={"signal": PinType.RISK_STAMPED_SIGNAL},
      outputs={"submitted": PinType.BOOL})
class ExecuteOrderNode:
```

（`backend/alphaloom/nodes/execution.py:6-8`）

因此，任何试图把裸 `SIGNAL`（例如策略节点、LLM 节点、甚至仓位计算节点的输出）直接接到 `execute_order.signal` 上的蓝图，都会在编译期被 2.3 节的类型检查拒绝——这不是运行时才发现的逻辑错误，而是图还没跑起来、`compile_blueprint()` 静态分析边的两端类型时就报错。测试用例把这个不变式写死为回归测试：

```python
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
```

（`backend/tests/test_compiler_typecheck.py:56-65`）

这就是"类型系统即合规官"的含义：策略作者（无论人类还是 LLM Copilot）**结构上不可能**画出一张"决策直接下单、绕过风控"的图并让它通过编译——不是靠代码审查或者运行时断言去拦截，而是靠类型系统本身让这种连线不可表达为合法图。`ema_cross.loom` 和 `agent_committee.loom` 的 `meta.gateProtocol.invariant` 字段都用同一句话显式记录了这个设计意图："Raw model or strategy output cannot enter execution until it passes position sizing and RiskGate stamping."（模型或策略的原始输出不能直接执行，必须先经过仓位计算和 RiskGate 盖章）。子图（subgraph）机制同样受此约束——`test_subgraph_cannot_bypass_risk_type` 验证了即使把 `risk_gate` 藏在子图内部、试图从子图内部"偷"出未盖章的 `brain.signal` 作为子图输出端口，编译器展开子图后仍会在扁平化的边上发现 `TYPE_MISMATCH`（`backend/tests/test_compiler_subgraph.py:48-66`）。

### 2.3 `compile_blueprint()`：静态编译与校验

编译器入口 `compile_blueprint()`（`backend/alphaloom/graph/compiler.py:91-176`）是一个纯函数：吃一个 `BlueprintSpec`，吐一个 `CompileResult`，不做任何 IO 或副作用。它的职责按顺序分为五个阶段：

**阶段 0：子图展开（`_expand_subgraphs`，`compiler.py:42-89`）**。如果图里存在 `type == "subgraph"` 的节点，就把它 `params.blueprint` 里内联的子蓝图（可以是内嵌 JSON，也可能是字符串再 `loads_loom`）递归展开：子图内部所有节点 id 加上 `"{子图节点id}/"` 前缀拍平进外层节点列表，子图内部的边同理拍平，再通过 `params.inputs` / `params.outputs`（形如 `{"外部端口名": "innerNode.port"}`）把外层连到子图的边重新映射到子图内部节点的真实端口上。这个过程带最大嵌套深度保护 `_MAX_DEPTH = 8`，超限返回 `PARAM_INVALID` 编译错误而不是无限递归；任何端口映射格式错误（非字符串、没有单个 `.`、类型不对）也统一收敛为 `PARAM_INVALID` 错误而不是裸抛异常（`test_garbage_port_mappings_become_compile_errors` 验证了这一点，`test_compiler_subgraph.py:96-105`）。展开结束后，剩余四个阶段都在这张"扁平图"上工作，子图对它们完全透明。

**阶段 1：节点级校验**。遍历 `bp.nodes`，检查节点 id 是否重复（`DUP_NODE_ID`）、`type` 是否在 `nodes/registry.py` 的进程级 `REGISTRY` 中注册过（`UNKNOWN_NODE_TYPE`，并把可用类型列表的前 20 个作为 `fix_hint` 提示）。这一阶段的错误会直接短路——因为后续阶段依赖 `REGISTRY[type]` 查节点定义，节点类型不合法就没法继续。

**阶段 2：连线校验（wiring/type validation）**。对每条边 `EdgeSpec`：
1. **端口存在性**（`BAD_PORT_REF`）：`src.port` 必须在源节点的 `outputs` 声明里，`dst.port` 必须在目标节点的 `inputs` 声明里；
2. **输入唯一性**（`DUP_INPUT`）：每个输入端口只能被一条边接线，重复接线报错（`taken: set[tuple[str,str]]` 去重）；
3. **类型匹配**（`TYPE_MISMATCH`）：源端口的 `PinType` 与目标端口声明的 `PinType` 必须完全相等（`t_out is not t_in`，因为 `PinType` 是 `Enum` 单例比较）；不匹配时错误信息形如 `"{node}.{port} expects {期望类型}, got {实际类型} from {src}"`，并附带 `fix_hint`——如果期望类型恰好是 `RISK_STAMPED_SIGNAL`，`fix_hint` 会用 `_HINTS` 字典（`compiler.py:26-30`）给出针对性提示："This input only accepts risk_stamped_signal, which is produced solely by a RiskGate node. Route the signal through a RiskGate before this node."；

之后再补一轮**必填输入检查**（`MISSING_INPUT`）：每个节点声明的非 `optional_inputs` 的输入端口必须被某条边接上，否则报错。

以上任何阶段产生错误，`compile_blueprint()` 立即返回 `CompileResult(False, errors)`，errors 里可以同时包含多条不同类型的错误（不是遇到第一个就退出，而是尽量收集完同类阶段内的全部问题，方便一次性反馈给前端或 LLM Copilot）。

**阶段 3：拓扑排序与合法环检测**。把每个节点的"非 feedback 依赖"收集成 `deps: dict[node_id, set[node_id]]`，交给标准库 `graphlib.TopologicalSorter`。如果排序遇到环（`CycleError`），说明图里存在没有标记 `feedback: true` 的循环依赖，返回 `ILLEGAL_CYCLE` 错误，`fix_hint` 提示"用 `feedback: true` 标记有意为之的反馈边；反馈值会在下一根 bar 交付"。这正是 `EdgeSpec.feedback` 字段存在的原因：图本质上必须是 DAG（当前 bar 内数据只能单向流动），但策略常常需要"上一根 bar 的输出影响当前 bar"的反馈结构（比如状态机式节点回读自己历史输出）。把这类边标记为 `feedback=True` 后，它们不计入拓扑依赖集合，图退化为合法 DAG，但在真正的运行时里，这条边传递的值是延迟一根 bar 交付的（`test_feedback_cycle_legal_and_illegal` 用一对 `tc_brain`/`tc_loop` 节点互相反馈验证了标记前报 `ILLEGAL_CYCLE`、标记后编译通过且 `InputBinding.feedback is True`，见 `test_compiler_subgraph.py:68-84`）。

拓扑排序本身也刻意做了确定性处理：

```python
while ts.is_active():                 # 排序波次 Kahn：规范拓扑序
    ready = sorted(ts.get_ready())    # 跨进程/跨声明序确定（录制回放依赖）
    order.extend(ready)
    ts.done(*ready)
```

（`compiler.py:170-173`）每一波"就绪节点"按 id 字符串排序后再展开，保证同一张图不论节点在 `.loom` 文件里声明的先后顺序如何，只要图结构相同就产出完全相同的 `order`。这对录制回放（LLM 调用录制/重放机制）至关重要——运行顺序必须是图结构的函数，不能是文件书写顺序的函数。`test_canonical_order_is_declaration_invariant`（`test_compiler_typecheck.py:106-119`）直接验证了这一点：把节点声明顺序和边列表顺序整个倒过来，编译出的 `order` 仍然相同。

**阶段 4：成本证书构建**。调用 `build_certificate()`（下节详述），把结果装进 `CompileResult`。

#### `CompileResult` 与 `CompileError`

```python
@dataclass
class CompileResult:
    ok: bool
    errors: list[CompileError]
    order: list[str] = field(default_factory=list)
    bindings: dict[str, list[InputBinding]] = field(default_factory=dict)
    certificate: object | None = None
    nodes: dict = field(default_factory=dict)
```

`ok=False` 时只有 `errors` 有意义；`ok=True` 时，`order` 是拍平后节点的合法执行顺序（字符串 id 列表，如 `agent_committee_v1` 编译后实际得到 `['feed', 'atr', 'ema', 'kb', 'kill', 'committee', 'xp', 'cite_gate', 'reflector', 'sizer', 'risk', 'xp_write', 'exec']`），`bindings[node_id]` 是该节点每个输入端口对应的 `InputBinding(dst_port, src_node, src_port, feedback)` 列表（供 runtime 按 order 逐节点取值执行），`nodes` 是拍平后 `{node_id: NodeSpec}` 映射供 runtime 实例化，`certificate` 是成本证书。

`CompileError`（`backend/alphaloom/graph/errors.py`）是所有编译期问题的统一载体：

```python
@dataclass(frozen=True)
class CompileError:
    code: str
    message: str
    node_id: str | None = None
    port: str | None = None
    fix_hint: str | None = None   # 面向 LLM 的修复提示（结构化反馈环境的一部分）
```

`code` 是本节前面出现过的机器可读错误码集合：`DUP_NODE_ID`、`UNKNOWN_NODE_TYPE`、`BAD_PORT_REF`、`DUP_INPUT`、`TYPE_MISMATCH`、`MISSING_INPUT`、`ILLEGAL_CYCLE`、`PARAM_INVALID`。`to_dict()` 返回可 JSON 序列化的字典（字段固定为 `{code, message, node_id, port, fix_hint}`，`test_error_json_serializable` 验证了这一点），这套结构直接暴露给前端和 LLM Copilot 消费——`fix_hint` 字段的存在本身就是设计取向的体现：编译器的错误不只是给人看的诊断信息，更是"结构化反馈环境"的一部分，让 LLM 生成/修改蓝图时能拿到可执行的修复建议（例如 `RiskGate` 相关提示直接点名该往哪个节点类型接线）。

### 2.4 `CostAnnotation` / `CostCertificate`：成本证书系统

每个注册节点类型除了声明输入输出端口，还声明一份**静态成本注解**（`CostAnnotation`，定义在 `types.py`）：

```python
@dataclass(frozen=True)
class CostAnnotation:
    llm_calls_per_bar: int = 0
    max_tokens_per_call: int = 0
    latency_class: str = "fast"   # fast | slow | llm
    deterministic: bool = True
```

| 字段 | 含义 |
|---|---|
| `llm_calls_per_bar` | 该节点每处理一根 bar 会发起多少次 LLM 调用（默认 0，纯计算节点不改这个默认值） |
| `max_tokens_per_call` | 单次 LLM 调用的 token 数上界（用于估算成本，不是精确计费） |
| `latency_class` | 节点的延迟档位：`"fast"`（纯计算）/`"slow"`（重计算或 IO，如检索）/`"llm"`（依赖网络 LLM 调用），三档有序：`_LATENCY_RANK = {"fast": 0, "slow": 1, "llm": 2}` |
| `deterministic` | 该节点是否是确定性的：同输入是否总产出同输出。调用 LLM 的节点必须诚实标 `False`（不确定性来自模型采样/网络），而检索、四象限分类、数值风控等节点即便涉及"查库"也标 `True`，因为给定同一 query/bucket 会产出同一结果 |

以内置节点为例：`llm_analyst` 声明 `CostAnnotation(llm_calls_per_bar=1, max_tokens_per_call=512, latency_class="llm", deterministic=False)`（`backend/alphaloom/nodes/llm_nodes.py:60-65`，注释明写"诚实：调 LLM 就不是确定性"）；三角色委员会节点 `committee` 声明 `llm_calls_per_bar=3`（策略师/风控官/主席各一次）、`max_tokens_per_call=512`，且代码注释特别强调**这是编译期静态上界，不随运行时参数收窄**——即便传入 `skip_risk_officer=True` 消融掉风控官角色、运行时只调 2 次，成本注解仍然按最坏情况声明为 3 次（`llm_nodes.py:175-192`：*"cost 注解维持 3 次/bar 的静态上界（成本证书是编译期注解，不随参数收窄；只许高估不许低估）"*）。相对地，RAG 检索节点（`knowledge_retrieve`、`experience_retrieve`、`experience_write` 等，`backend/alphaloom/nodes/rag_nodes.py`）以及 `require_citations`、`reflector` 等纯逻辑节点全部声明 `llm_calls_per_bar=0, deterministic=True`——检索/打分/写库都是确定性副作用，不触发 LLM。

`build_certificate()`（`backend/alphaloom/graph/cost.py:17-23`）负责把编译后**每个节点**的 `CostAnnotation` 汇总/滚动成**整张蓝图级别**的一份 `CostCertificate`：

```python
@dataclass(frozen=True)
class CostCertificate:
    llm_calls_per_bar: int
    daily_token_ceiling: int
    worst_latency_class: str
    deterministic_ratio: float

def build_certificate(defs: list[NodeDef], bars_per_day: int) -> CostCertificate:
    calls = sum(d.cost.llm_calls_per_bar for d in defs)
    tokens = sum(d.cost.llm_calls_per_bar * d.cost.max_tokens_per_call for d in defs) * bars_per_day
    worst = max((d.cost.latency_class for d in defs),
                key=lambda c: _LATENCY_RANK[c], default="fast")
    det = (sum(1 for d in defs if d.cost.deterministic) / len(defs)) if defs else 1.0
    return CostCertificate(calls, tokens, worst, round(det, 4))
```

四个滚动规则分别是：

- **`llm_calls_per_bar`**：对全部节点的 `llm_calls_per_bar` 直接求和（同一根 bar 内所有节点都会跑一次，所以是简单加总）；
- **`daily_token_ceiling`**：`Σ(每节点 llm_calls_per_bar × max_tokens_per_call) × bars_per_day`——先算出"每根 bar 消耗的 token 上界"，再乘以一天的 bar 数（`compile_blueprint(bp, bars_per_day=...)` 的可选参数，默认 `1440`，对应 1 分钟 K 线一天的根数），得到"一天最多可能消耗多少 token"的静态上界；
- **`worst_latency_class`**：全图中出现的最差延迟档位（按 `fast < slow < llm` 排序取最大），任何一个节点是 `llm` 档，整张蓝图的延迟档位就是 `llm`；
- **`deterministic_ratio`**：全图中"确定性节点"占比（`deterministic=True` 的节点数 / 总节点数），四位小数四舍五入；`1.0` 代表全图完全确定性可复现，越低代表越多节点的行为依赖不可控的外部调用。

以真实蓝图为例，对 `blueprints/agent_committee.loom` 执行 `compile_blueprint(bp, bars_per_day=1440)`，得到的证书是：

```python
CostCertificate(
    llm_calls_per_bar=3,
    daily_token_ceiling=2211840,   # = 3 * 512 * 1440
    worst_latency_class='llm',
    deterministic_ratio=0.9231,    # 12/13 个节点确定性，仅 committee 非确定性
)
```

13 个节点里只有 `committee` 一个节点是非确定性/LLM 节点（其余 `feed/ema/atr/kb/kill/xp/cite_gate/reflector/sizer/risk/xp_write/exec` 全部 `deterministic=True`），所以 `deterministic_ratio = 12/13 ≈ 0.9231`；`committee` 贡献了全部 `llm_calls_per_bar=3` 和 `daily_token_ceiling = 3 × 512 × 1440 = 2,211,840`。而 `ema_cross.loom` 全部由确定性技术指标节点组成，若编译会得到 `llm_calls_per_bar=0, daily_token_ceiling=0, worst_latency_class='fast', deterministic_ratio=1.0`。

`CostCertificate.to_dict()`（基于 `dataclasses.asdict`）把证书转成普通 dict，字段名与上表一致，可直接 `json.dumps`；这份 JSON 就是 `CompileResult.certificate` 最终经 API 暴露给前端/调用方的形态，测试用例里也确认了它的 JSON 形状：

```python
d = c.to_dict()
json.dumps(d)
assert d["llm_calls_per_bar"] == 2
```

（`backend/tests/test_cost_certificate.py:44-46`）

需要强调的是，`registry.py` 的模块注释里专门指出：沙箱来源（`sandboxed=True`）的自定义节点的成本证书是"自证"的——热注册的自定义节点理论上可以在 `@node(cost=...)` 里谎报 `llm_calls_per_bar=0` 而运行期偷偷调用 LLM；证书系统本身只负责如实滚动**已声明**的成本，这一构造把"谁来为沙箱节点的证书兜底"的问题留给了运行时引擎层（给沙箱节点一个剥离 `.llm`/`.audit` 的受限 `ctx` 视图），而不是编译器/证书系统本身。

### 2.5 序列化：`dumps_loom` / `loads_loom`

`.loom` 文件本质是普通 JSON，`model.py` 提供了一对纯函数完成 `BlueprintSpec` 与文本之间的转换，不涉及任何编译期语义：

```python
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

`loads_loom` 对 `nodes[].params`、`edges[].feedback`、顶层 `meta` 都提供默认值（分别是 `{}`、`False`、`{}`），因此一个最小合法 `.loom` 只需要 `id`、`nodes[].id`、`nodes[].type`；`name` 缺省时回退为 `id`。序列化侧 `dumps_loom` 是其逆操作：端口重新拼回 `"node.port"` 字符串，且只有 `feedback=True` 时才写出 `"feedback": true` 键（`False` 时省略，保持文件简洁），并用 `ensure_ascii=False` 保留中文字符（蓝图的 `meta.description_zh` 等字段），`indent=2` 保证人类可读、便于 git diff。测试用 `test_roundtrip` 验证了往返一致性：`loads_loom(dumps_loom(bp)) == bp`（依赖 `BlueprintSpec.__eq__` 的自定义实现，逐字段比较 `id/name/nodes/edges/meta`，`backend/tests/test_graph_model.py:21-28`）。`load_loom_file` 是磁盘文件的便捷封装，是 `blueprints/*.loom` 预置蓝图和后端加载真实策略文件的入口。

这一层刻意保持"零校验"：格式错误（比如 `"from"` 字段没有 `.`）会在 `_parse_ref` 里直接抛 `ValueError`（`test_bad_port_ref_raises` 验证），但节点类型是否存在、端口类型是否匹配、图是否有环等语义问题一律交给下游的 `compile_blueprint()` 处理并转换成结构化的 `CompileError`——这是模块划分上的明确分层：`model.py` 只管"这是不是一个语法合法的图"，`compiler.py` 才回答"这是不是一个语义合法、可以安全执行的图"。

---

## 3. 运行时引擎与执行模型

运行时引擎（`backend/alphaloom/runtime/`）是 AlphaLoom 图编译产物（`CompileResult`）在真实时间序列上被"演算"出来的地方：`Engine` 把编译期算好的节点拓扑序，逐 bar、逐节点地驱动一遍，同时用一套时间戳类型系统（`Stamped` / `SimClock` / `check_stamped`）在运行期强制"图不能感知未来"，用一层受限 ctx 视图（`_RestrictedContext`）保证沙箱自定义节点即便被热注册进同一进程，也无法绕过成本证书偷用 LLM 或篡改审计轨迹。`Recorder` 则把每个节点在每根 bar 上的输入/输出原样落盘，供回放、断点调试与前端时间轴检查使用。这一层是回测引擎（`backtest/runner.py`）与实盘 paper-trading 会话（`api/live.py` 的 `_worker`）共用的核心执行内核——两者只是把不同的 `DataSource`/`broker`/`llm` 组装进同一个 `RunContext` 后交给同一个 `Engine`。

### 3.1 Engine：按拓扑序驱动一根 bar

`Engine`（`backend/alphaloom/runtime/engine.py:70-129`）的构造函数接收编译结果、节点实例字典、一个 `RunContext`，以及可选的断点集合与暂停回调：

```python
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
        self._dead = False
        self._restricted_ctx = _RestrictedContext(ctx)
```

`compiled.order` 是编译器（`graph/compiler.py`）用 `graphlib.TopologicalSorter` 对非 feedback 依赖做拓扑排序后得到的节点执行顺序（`compiler.py:160-174`：`deps = {n.id: {b.src_node for b in bindings[n.id] if not b.feedback}}`）；`compiled.bindings` 是 `dict[node_id, list[InputBinding]]`，每条 `InputBinding(dst_port, src_node, src_port, feedback)` 描述"这个节点的某个输入端口，接的是哪个源节点的哪个输出端口，是否走 feedback（上一拍）通道"。

对外驱动接口很薄：`run(events)` 就是对每个事件调用一次 `step(ev)`；`step` 包一层"毒化契约"（见 3.1.1），真正的单步逻辑在 `_step_inner`：

```python
def _step_inner(self, ev: BarEvent) -> None:
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
        node_ctx = self._restricted_ctx if getattr(inst, "sandboxed", False) else self.ctx
        outputs = inst.on_bar(node_ctx, raw_inputs) or {}
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

单步（一根 bar）的控制流可以概括为：

1. **推进时钟**：`ctx.clock.advance(ev.ts_close)` —— 时钟只能前进，见 3.2。
2. **建一个空的 `wave`**：`wave: dict[(node_id, port), Stamped]` 是"本拍内已经产出的所有节点输出"，按拓扑序填充，同一拍内下游节点能立刻看到上游节点本拍的输出。
3. **按 `compiled.order` 依次跑每个节点**：对每个节点，先按它的 `InputBinding` 列表组装输入——
   - 若绑定标了 `feedback=True`，从 `self._prev`（上一拍收工时的 `wave` 快照）取值，这是"接受上一拍产出"的显式反馈边，用来打破/避免同拍环依赖（`test_feedback_edge_prev_wave` 验证了这一点：`prev` 端口在第 1 拍拿到 `None`，从第 2 拍起拿到上一拍的 `s.v`）；
   - 否则从本拍 `wave` 里取同拍上游的输出。
   - 同时维护两份输入视图：`rec_inputs`（保留 `Stamped` 包装，供 Recorder 落盘）与 `raw_inputs`（拆包成裸值，喂给节点 `on_bar`——节点作者写业务逻辑时不必自己处理 `Stamped`）。
4. **断点回调**：若 `node_id` 在 `breakpoints` 集合里且注册了 `on_pause`，先同步调用 `on_pause(node_id, ev, raw_inputs)`，用于调试器在节点执行前暂停并检视即将喂给它的输入（`test_breakpoint_callback` 验证了回调按节点、按拍触发且能拿到当拍输入值）。
5. **选择 ctx 视图并调用 `on_bar`**：`inst.sandboxed` 为真则传入 `self._restricted_ctx`（沙箱节点），否则传入真实的 `self.ctx`（内置受信节点）。调用约定固定为 `inst.on_bar(node_ctx, raw_inputs) -> dict | None`。
6. **给输出打时间戳并做因果检查**：节点如果直接返回裸值，引擎用 `Stamped(val, self.ctx.clock.now)` 自动补上"此刻"的时间戳；节点也可以自己构造 `Stamped(value, as_of=...)`（例如声明某个值实际来自更早的数据源）。无论哪种，都要过 `check_stamped()`（3.3 节详述）——这是反向看未来的最后一道闸门。写入结果的同时更新 `wave`，供本拍下游节点消费。
7. **调试钩子与录制**：`self.after_node`（测试/实盘均用它做"最新输出快照"）先于 `Recorder.record` 被调用；`Recorder` 只有在 `ctx.recorder` 非空时才落盘（回测/实盘都会传，但单元测试里常常不传）。
8. **收尾**：本拍 `wave` 整体赋给 `self._prev`（下一拍的 feedback 源），`_event_idx` 自增，作为 Recorder 里每行的拍序号。

`test_linear_dataflow` 印证了最基础的数据流：`te_src` 每拍自增并输出 `v`，`te_double` 把 `x*2` 输出为 `y`，三拍下来 Recorder 记录到的 `d.y` 序列是 `[2.0, 4.0, 6.0]`——即普通（非 feedback）边在同一拍内就能被下游看到并处理完。

#### 3.1.1 崩溃即中毒：EngineDead 契约

```python
def step(self, ev: BarEvent) -> None:
    if self._dead:
        raise EngineDead("engine crashed earlier; discard this instance (crash contract)")
    try:
        self._step_inner(ev)
    except Exception:
        self._dead = True
        raise
```

任何一次 `_step_inner` 抛异常（不管是节点代码本身的 bug、`CausalityError`，还是沙箱越权的 `SandboxEscapeError`），`Engine` 都会把自己标记为 `_dead = True` 并把异常原样上抛。此后任何再调用 `step()` 都会立刻收到 `EngineDead`，而不是"看似正常地"从崩溃点继续跑下去。这是一个显式的"毒化契约"（代码注释里称为 *Carryover 14①*）：引擎实例一旦经历过未处理异常，其内部状态（`self._prev`、各节点实例的内部 `state`）已经处于不确定/半更新状态，唯一安全的处理方式是废弃整个 `Engine` 实例，而不是尝试"跳过这一拍继续跑"掩盖问题。`test_causality_guard_kills_run` 验证的正是"因果性违规会让 `run()` 整体抛出 `CausalityError`"这一后果。

### 3.2 RunContext 与 SimClock：节点能看到什么、"现在"是什么

`RunContext`（`context.py:30-40`）是节点执行时唯一的"外部世界"入口，是一个轻量 dataclass：

```python
@dataclass
class RunContext:
    clock: SimClock
    run_id: str
    broker: Any = None
    recorder: Any = None
    current_event: Any = None
    halted: bool = False
    llm: Any = None      # RecordingLLMClient | None：LLM 节点在 None 时抛清晰错误
    audit: Any = None    # AuditLog：每次 LLM/检索调用留痕（provenance）
```

各字段的角色：

| 字段 | 类型 | 用途 |
|---|---|---|
| `clock` | `SimClock` | 节点读取"现在几点"（`ctx.clock.now`，毫秒时间戳），用于自己构造 `Stamped` 输出 |
| `run_id` | `str` | 本次运行/会话的唯一标识，Recorder 按它分区存储，回放/前端按它检索 |
| `broker` | `PaperBroker \| None` | 下单/查仓位/查权益的接口，交易类节点通过它提交 `Order` |
| `recorder` | `Recorder \| None` | 引擎自己用来落盘每拍每节点 I/O；节点一般不直接碰它 |
| `current_event` | `BarEvent \| None` | 引擎在 `_step_inner` 开头写入的"这一拍的原始 K 线事件"，节点可读取 `candle`/`ts_open`/`ts_close` |
| `halted` | `bool` | 熔断/暂停标志位（例如风控节点触发后置位，供上层轮询） |
| `llm` | `RecordingLLMClient \| None` | LLM 分析/反思/copilot 节点调用大模型的唯一句柄；未配置时为 `None`，节点侧应显式判空并抛出清晰错误而不是静默跳过 |
| `audit` | `AuditLog \| None` | 每次 LLM 调用、检索访问都在这里留痕（provenance 审计轨迹） |

`RunContext` 本身不做任何校验或副作用——它只是一个"胶囊"，把 `broker`、`recorder`、`llm`、`audit` 这些跑一次图所需的可变外部资源打包传递。回测（`backtest/runner.py:59-61`）与实盘会话（`api/live.py:300-303`）分别构造自己的 `RunContext`：两者结构完全一致，区别只在于 `broker`（都用 `PaperBroker`，只是数据源不同）、`recorder` 落盘路径、以及 `llm`（是否真的接了 LLM 客户端）。这也是"回测与实盘共用同一套运行时内核"的直接体现——`Engine` 和节点代码完全不知道自己是在回放历史数据还是在追实时行情。

`SimClock`（`context.py:9-16`）是故意做得极简的时间源：

```python
class SimClock:
    def __init__(self) -> None:
        self.now: int = 0

    def advance(self, ts: int) -> None:
        if ts < self.now:
            raise ValueError(f"clock cannot go backwards: {ts} < {self.now}")
        self.now = ts
```

它只有一个不变量：**时钟单调不减**。`Engine._step_inner` 在每拍开头用 `ev.ts_close`（bar 的收盘时刻）推进时钟，任何试图把时钟往回拨的调用都会立刻抛 `ValueError`（`test_clock_monotonic` 验证）。之所以用"bar 收盘时刻"作为"现在"，是和 `BarEvent`（3.5 节）、回测撮合时序契约（`backtest/runner.py` 文档字符串："每根 bar 先 broker.on_bar（撮合上一根挂单/止损）再 engine.step（本根决策）——次 bar 开盘成交语义的另一半"）配套设计的：节点在"收盘时刻"做决策，是因为这一刻这根 K 线的所有数据（open/high/low/close/volume）才算完整可见，任何提前用到本拍 close 之前不该已知的信息都是一种时序泄漏；而委托真正的成交要等到下一根 bar 的 `broker.on_bar` 里才发生，从而避免"看着本根收盘价直接在本根内成交"的看未来问题。

### 3.3 Stamped / check_stamped：反向看穿未来的因果性闸门

AlphaLoom 图上流动的每一个数据值，理论上都应该带一个"这个值代表的是哪个时刻的信息"的标记，而不只是"值本身"。这就是 `Stamped`（`graph/types.py:14-18`）：

```python
@dataclass(frozen=True)
class Stamped:
    """数据引脚上流动的值：value + as-of 毫秒时间戳（因果类型系统的载体）。"""
    value: Any
    as_of: int
```

它是一个不可变的 `(value, as_of)` 二元组，`as_of` 是毫秒时间戳，含义是"这个 value 所反映的信息最迟在这个时刻已知/已发生"。`Engine` 在把节点的原始返回值包装成 `wave` 里的条目时，若节点没有主动构造 `Stamped`，就用当前时钟 `self.ctx.clock.now` 给它盖戳（`engine.py:119`：`s = val if isinstance(val, Stamped) else Stamped(val, self.ctx.clock.now)`）——即"没特别声明来源时刻的数据，默认视为诞生于此刻"。但节点也可以显式构造带有**任意** `as_of` 的 `Stamped`（比如某些延迟到达的链上数据、或刻意声明来自更早时刻的缓存值），这正是需要一道运行期校验的原因：一个（恶意或有 bug 的）节点完全可能构造一个 `as_of` 晚于当前时钟的 `Stamped`，等价于让下游节点提前"看见"了未来才会发生的信息。

`check_stamped()`（`context.py:18-28`）就是这道闸门：

```python
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
```

规则很直白：`Stamped.as_of` 绝不允许大于当前 `SimClock.now`；同时也检查了嵌套在 dict 里的 `Stamped`（节点某些输出端口本身是复合结构，里面套着带时间戳的子值）。`Engine._step_inner` 对**每一个节点的每一个输出端口**都调这个检查（`engine.py:120`：`check_stamped(node_id, s, self.ctx.clock.now)`），检查点设在"输出产生的那一刻"，而不是等到下游节点消费的时候——这样违规节点自己的身份（`node_id`）会直接出现在异常信息里，定位精确到"是谁在说谎"，而不是"某个下游节点收到了脏数据"。

`test_causality_guard_kills_run` 用一个刻意作恶的测试节点 `TeEvil` 演示了这条规则的强制力：

```python
@node(type="te_evil", category="test", inputs={}, outputs={"v": PinType.SERIES})
class TeEvil:
    def setup(self, params): pass
    def on_bar(self, ctx, inputs):
        return {"v": Stamped(99.0, as_of=ctx.clock.now + 999_999)}
```

这个节点每拍都声称自己的输出"来自未来"（`now + 999_999` 毫秒之后）。`Engine.run` 跑到它就会在 `check_stamped` 处抛出 `CausalityError`，并按 3.1.1 节所述的毒化契约让整个 `Engine` 实例作废。`test_check_stamped_passes_and_blocks`（`test_causality.py:19-23`）进一步把这条规则的边界条件钉死：`as_of == now` 放行，`as_of` 在 dict 里嵌套也会被查，`as_of > now` 无论深不深嵌套都会被拒绝并在异常信息里带上触发节点的 `node_id`。

把这条机制放回全局视角：这是 AlphaLoom **反前视偏差（anti-look-ahead-bias）的核心保证**。图编译期的拓扑排序只保证"数据依赖顺序正确"，但不能保证某个节点不会在运行时凭空造出一个属于未来的时间戳去欺骗下游（例如一个特征工程节点错误地把"未来 N 根 K 线的收盘价"当特征喂给策略节点，这在量化回测里是最常见也最隐蔽的一类 bug/作弊）。`Stamped` 让"时间戳"成为跨节点传递的一等公民而不是约定俗成的口头契约，`check_stamped` 则让这个契约变成运行时会主动崩溃的硬保证——图"看见未来"这件事，在 AlphaLoom 里不是一个需要靠代码审查才能发现的隐患，而是一跑就炸的运行时错误。

### 3.4 `_RestrictedContext`：沙箱节点的受限 ctx 视图

AlphaLoom 允许用户通过 `/api/nodes/custom` 提交源码，经 AST 沙箱编译（第 4 节详述）后热注册进全局 `REGISTRY`，并标记 `sandboxed=True`（`nodes/registry.py:62-67` 的 `mark_sandboxed`）。`create_instance()`（`registry.py:69-77`）会把这个标记原样搬到节点实例上：`inst.sandboxed = bool(d.sandboxed)`。`Engine` 在真正调用 `on_bar` 之前，就是靠这个实例属性来决定"给它一个什么样的 ctx"：

```python
node_ctx = self._restricted_ctx if getattr(inst, "sandboxed", False) else self.ctx
```

为什么需要这一层区分：沙箱节点在注册时会声明一份"成本证书"（`CostAnnotation`，见第 4/6 节），其中包括 `llm_calls_per_bar`——它自己承诺每拍最多调用几次 LLM。但这只是节点作者的自我声明，运行时并不能仅凭这份声明就信任节点不会在 `on_bar` 里"说一套做一套"：一个声明 `llm_calls_per_bar=0`（即声称自己是纯确定性计算）的沙箱节点，如果仍然拿到了真实 `RunContext`，理论上完全可以在 `on_bar` 内部直接调 `ctx.llm.chat(...)`，绕开成本证书去消耗真实的 LLM 配额；同理，它也可能试图写 `ctx.audit`，抹掉或伪造自己的调用留痕。引擎侧的应对方式是"运行期物理隔离"而不是"寄望于沙箱节点自律"：给沙箱节点的 `on_bar` 传入的不是真正的 `RunContext`，而是一个剥离了 `.llm`、`.audit`、`.broker` 的**受限视图** `_RestrictedContext`。

`_RestrictedContext`（`engine.py:34-68`）的实现要点：

```python
_SANDBOX_DENIED_CTX_ATTRS = frozenset({"llm", "audit", "broker"})
_CTX_BACKING: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

class _RestrictedContext:
    def __init__(self, ctx: RunContext) -> None:
        _CTX_BACKING[self] = ctx

    def __getattr__(self, name):
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(
                f"sandboxed node may not access ctx.{name}: effectful runtime "
                "capabilities are stripped from sandbox nodes (declared-deterministic "
                "must be truly deterministic and cannot bypass gates)")
        ctx = _CTX_BACKING.get(self)
        if ctx is None:
            raise AttributeError(name)
        return getattr(ctx, name)

    def __setattr__(self, name, value):
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(f"sandboxed node may not set ctx.{name}")
        ctx = _CTX_BACKING.get(self)
        if ctx is None:
            raise AttributeError(name)
        setattr(ctx, name, value)
```

几个设计细节值得展开：

- **`clock` / `current_event` / `run_id` / `recorder` / `halted` 等"纯计算所需"字段照常可用**——`_RestrictedContext` 对未被拒绝的属性名一律通过 `__getattr__` 委托给背后真正的 `ctx`，所以沙箱节点仍然能读时钟、读当前 K 线事件、读 run_id，唯独摸不到 `.llm`、`.audit`、`.broker`。也就是说这不是"沙箱节点拿不到 ctx"，而是"拿到一个功能对等但砍掉效果性（effectful）能力的 ctx"。
- **真实 `ctx` 不挂在受限视图的实例属性上，而是存在模块级 `WeakKeyDictionary`（`_CTX_BACKING`）里**——`_RestrictedContext` 没有声明 `__slots__`，实例 `__dict__` 全程为空，意味着这个对象本身的属性图上根本不存在一条能追溯回真实 `RunContext` 的路径（不存在类似 `self._ctx = ctx` 这种可以被反射/内省找到的引用）。这是为了配合沙箱侧的 AST 白名单规则（沙箱代码本就禁止访问单下划线属性），双重保险防止"沙箱节点从私有槽位反向掏回真 ctx"这类逃逸手法。`WeakKeyDictionary` 的键是弱引用，受限视图对象被 GC 后映射项自动清理，不会泄漏。
- **`SandboxEscapeError`**（`engine.py:13-20`）是这层拦截命中时抛出的专用异常类型，其 docstring 直白地写明了设计动机："沙箱自定义节点可谎报 cost 证书 `llm_calls_per_bar=0` 却在 `on_bar` 里偷调 `ctx.llm.chat` 刷爆真实配额……根治：运行期给沙箱节点一个剥离 LLM 句柄的受限 ctx 视图，任何 `.llm`/`.audit` 访问即抛此错——'沙箱节点声称确定性'由此成为真确定性。" 一旦抛出，按 3.1.1 节的毒化契约，整个 `Engine` 实例同样会被标记为 `_dead` 并中止当前 run。
- **`_restricted_ctx` 在 `Engine.__init__` 里构造一次、全 run 复用**（`engine.py:83`），而不是每拍每节点现造一个——因为它本身无状态（除了 `_CTX_BACKING` 里那一条弱引用），复用没有隔离性代价，只是省一次分配。

需要强调的是，`_RestrictedContext` 拦截的是**沙箱节点对 ctx 高权能力的访问路径**，而不是重新审查沙箱代码本身的安全性（那是 AST 白名单编译器的职责，见第 4 节）；这里的设计前提是"即便沙箱代码本身被认为是安全的纯计算代码，运行时仍然不应该把可以产生真实副作用（花钱调 LLM、下真实委托、篡改审计记录）的句柄递给它"，是纵深防御的一层，而非唯一防线。

### 3.5 BarEvent 与 events 模块

`events.py` 目前只定义了一个类型，`BarEvent`（`events.py:4-15`）：

```python
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

它是驱动 `Engine.step()` 的最小事件单元：一根 K 线（`candle`，形如 `{"ts", "open", "high", "low", "close", "volume"}` 的 dict，来自 `DataSource`/`SQLiteMarketData` 等数据源）加上这根 K 线的周期长度 `bar_ms`（毫秒，由 `data/source.py` 的 `bar_to_ms("1m")` 之类的周期字符串换算而来）。两个只读 property 把"开盘时刻"和"收盘时刻"都算出来：`ts_open` 就是 K 线自带的起始时间戳，`ts_close` 是起始时间戳加上周期长度——`Engine._step_inner` 用 `ts_close` 去推进 `SimClock`（3.2 节），因为节点是在这根 K 线"收盘"、数据完整之后才做决策的。`test_bar_event_close_ts`（`test_causality.py:8-10`）直接验证了这层算术：`ts=60_000, bar_ms=60_000` 时 `ts_open=60_000, ts_close=120_000`。

`Engine.run(events)` 接受的就是一串 `BarEvent` 的可迭代对象；回测侧（`backtest/runner.py:70-74`）是在遍历 `source.iter_candles(...)` 拿到的每根历史 K 线上现造一个 `BarEvent(candle, bar_ms)` 直接喂给 `engine.step()`，实盘侧（`api/live.py`）则是在轮询 OKX 拿到新收盘 K 线后做同样的事——`BarEvent` 是回测与实盘共用的统一事件形状，节点代码完全不关心这根 K 线到底是历史回放出来的还是刚从交易所轮询回来的。

### 3.6 Recorder：按拍记录每个节点的输入输出

`Recorder`（`runtime/recorder.py`）把整场 run 中，每一拍、每一个节点收到的输入与产出的输出，原样序列化进一张 SQLite 表，供事后回放、前端时间轴渲染、以及断点调试器按拍检视中间状态使用。表结构：

```sql
CREATE TABLE IF NOT EXISTS node_io (
  run_id TEXT, event_idx INTEGER, ts INTEGER, node_id TEXT,
  inputs_json TEXT, outputs_json TEXT)
CREATE INDEX IF NOT EXISTS idx_node_io ON node_io(run_id, node_id, event_idx)
```

写入接口 `record(run_id, event_idx, ts, node_id, inputs, outputs)` 直接对应 `Engine._step_inner` 每处理完一个节点后的调用：`event_idx` 是引擎自增的拍序号，`ts` 是这一拍的收盘时间戳（`ev.ts_close`），`inputs`/`outputs` 是保留 `Stamped` 包装的字典（即前面提到的 `rec_inputs` 与 `stamped_outputs`）。序列化用自定义 JSON 编码器 `_enc`，把 `Stamped(value, as_of)` 编码成 `{"__stamped__": as_of, "value": value}`：

```python
def _enc(o):
    if isinstance(o, Stamped):
        return {"__stamped__": o.as_of, "value": o.value}
    raise TypeError(f"not JSON serializable: {type(o)}")
```

对应地，`from_json` 提供了反向的 `object_hook`，见到形状恰好是 `{"__stamped__", "value"}` 两个键的 dict 就还原成 `Stamped` 实例——这样落盘/读回是严格互逆的，时间戳信息不会在持久化过程中丢失，回放时依然能重建出每个值"as of 何时"的因果信息。

`fetch(run_id, node_id=None)` 是读路径：按 `run_id` 过滤（可选再按 `node_id` 过滤），按 `event_idx, rowid` 排序返回，取到的是原始行字典（`{run_id, event_idx, ts, node_id, inputs_json, outputs_json}` 列名到值的映射），调用方（前端 API、测试）自己决定要不要用 `from_json` 反解。数据库连接开启了 `PRAGMA journal_mode=WAL` 与 `synchronous=NORMAL`——即优化写入吞吐、允许读写并发（这对"实盘会话一边持续写入、前端一边轮询同一个 SQLite 文件读最新状态"的使用模式很关键），`flush()`/`close()` 提供显式的落盘/关闭时机。

`test_linear_dataflow` 与 `test_recorder_row_count` 印证了 Recorder 与 Engine 的耦合方式：跑 3 拍两节点的图会产出 `3*2=6` 行（`test_linear_dataflow` 里对 `node_id="d"` 过滤后拿到 3 行，逐行取 `outputs_json` 里 `y` 端口的 `value` 得到 `[2.0, 4.0, 6.0]`）；跑 4 拍同图则是 `4*2=8` 行（`test_recorder_row_count`）——即"每拍 × 每节点"各产出恰好一行记录，`Recorder` 不做任何采样或合并，是逐帧、逐节点的完整轨迹留存。这份完整轨迹正是"回放/时间旅行调试"的数据基础：给定 `run_id`，任何节点在任意一拍的输入输出都可以被精确重建，而不需要重新跑一遍整场回测。

---

**与相邻子系统的连接关系小结**：`Engine` 消费的 `CompileResult`/`instances` 来自图编译子系统（`graph/compiler.py` + `nodes/registry.create_instance`）；`RunContext.broker` 对接 `PaperBroker`（回测/实盘撮合引擎）；`RunContext.llm`/`.audit` 对接 LLM 分析节点与 `sandbox/audit.py` 的 `AuditLog`；`inst.sandboxed` 标记来自沙箱编译器 `sandbox/node_sandbox.py` 对自定义节点的热注册流程；`Recorder` 落盘的 SQLite 文件被 `api/app.py`、`api/live.py` 的 HTTP 接口读回，驱动前端的回放与实时状态展示。运行时引擎本身不关心这些邻居各自的实现细节，只通过 `RunContext` 这一个胶囊对象和 `Stamped`/`check_stamped` 这一套因果性契约与它们打交道。

---

## 4. 内置节点体系

### 4.1 REGISTRY：进程级节点类型总账

AlphaLoom 的每一种节点类型（`candle_feed`、`ema`、`risk_gate` ……）都是一份**声明式元数据**加一个**行为类**的组合，二者通过 `@node(...)` 装饰器绑定后存入一个进程级全局字典 `REGISTRY`（`backend/alphaloom/nodes/registry.py:39`）。这是图编译器（compiler.py）、成本证书构建器（cost.py）、HTTP `GET /api/nodes` 端点、以及沙箱热注册机制共同依赖的唯一事实来源。

`NodeDef` 是登记在 `REGISTRY` 里的值类型（`registry.py:21-35`），一个冻结 dataclass：

| 字段 | 类型 | 含义 |
|---|---|---|
| `type` | `str` | 节点类型名，蓝图 JSON 里 `"type"` 字段引用的字符串 |
| `category` | `str` | 分类标签（`data`/`indicator`/`decision`/`risk`/`execution`/`rag`/`reflection` 等），供前端节点面板分组、供测试按类别筛选（如 `test_risk_gate_is_sole_stamper` 用它排除 `category == "test"` 的探针节点） |
| `cls` | `type` | 实际承载 `setup`/`on_bar` 的行为类 |
| `inputs` / `outputs` | `dict[str, PinType]` | 输入/输出引脚名到引脚类型的映射——编译器做端口存在性和类型匹配检查全靠这两个字典 |
| `params` | `dict[str, type]` | 蓝图 `params` 字段的声明式类型提示（供 `/api/nodes` 展示、供前端表单渲染），**不是**运行期强校验网关——真正的取值发生在各节点 `setup()` 内部（如 `float(params.get("risk_pct", 0.02))`），类型不对会在那里抛异常 |
| `cost` | `CostAnnotation` | 节点自证的成本/延迟/确定性画像，供 `build_certificate()` 汇总（见 §4.2 与第 3 章编译器部分） |
| `optional_inputs` | `frozenset[str]` | 允许不接线的输入端口集合（见 §4.3） |
| `sandboxed` | `bool` | 来源标记：`False` = 内置受信节点（本章讨论对象），`True` = 经 `/api/nodes/custom` 沙箱编译热注册的节点。引擎据此在运行期给沙箱节点一个剥离 `.llm`/`.audit`/`.broker` 的 `_RestrictedContext`（`runtime/engine.py:34-68`），而内置节点始终拿到完整 `RunContext` |

`CostAnnotation`（`graph/types.py:20-25`）本身是四个字段的冻结 dataclass：`llm_calls_per_bar: int`、`max_tokens_per_call: int`、`latency_class: str`（`fast`/`slow`/`llm`）、`deterministic: bool`。绝大多数内置节点用默认值 `CostAnnotation()`（即全零、`fast`、`deterministic=True`），只有会调用 LLM 的节点（`committee`、`llm_analyst`，定义在本节读取范围之外的 `llm_nodes.py`）显式声明非零 `llm_calls_per_bar` 与 `deterministic=False`。

#### `@node` 装饰器的注册流程

```python
def node(*, type: str, category: str, inputs: dict, outputs: dict,
         params: dict | None = None, cost: CostAnnotation = CostAnnotation(),
         optional_inputs=None, sandboxed: bool = False):
    def deco(cls):
        if type in REGISTRY:
            raise ValueError(f"node type {type!r} already registered")
        optional = frozenset(optional_inputs or ())
        unknown = optional - set(inputs)
        if unknown:
            raise ValueError(f"optional inputs not declared for {type!r}: {sorted(unknown)}")
        REGISTRY[type] = NodeDef(type, category, cls, dict(inputs), dict(outputs),
                                 dict(params or {}), cost, optional, sandboxed=sandboxed)
        cls.node_type = type
        return cls
    return deco
```
（`registry.py:41-57`）

三条硬约束在装饰时刻（即 import 期）就地检查：

1. **类型名唯一**——重复注册直接 `ValueError`，不会静默覆盖。这一行为被 `test_duplicate_registration_rejected` 和沙箱路径的 `test_registry_is_process_global_single_user` 共同锁定。
2. **`optional_inputs` 必须是 `inputs` 的子集**——声明一个不存在的输入端口为"可选"是配置错误，立即报错，不会拖到编译期才发现。
3. 装饰器把 `node_type` 类属性直接写回 `cls`，供调试/反射时从实例反查类型名。

由于 `REGISTRY` 是**模块级可变全局字典**而非每次 `create_app()` 的实例状态，内置节点在 `import alphaloom.nodes`（`nodes/__init__.py:1-11`，依次 import `data`/`indicators`/`gates`/`sizing`/`execution`/`llm_nodes`/`rag_nodes`/`pa_gate`/`reflection` 各模块触发其顶层 `@node` 装饰）时一次性登记，进程存活期间常驻；自定义节点通过 `POST /api/nodes/custom` 走沙箱编译后用同一个 `@node` 装饰器热注册进同一张表（`sandbox/node_sandbox.py` 编译成功后调用 `mark_sandboxed()` 回填来源标记，`registry.py:62-67`）。模块顶部的 docstring 明确把这一点记录为**当前单用户本地部署的既定语义**（"AlphaLoom 当前定位是单用户本地/演示部署，此语义可接受且已被测试锁定"），并把多用户场景下引入 session 命名空间列为后续 Carryover 事项——这是一处刻意的设计取舍而非疏漏。

#### `create_instance()`：从 NodeSpec 到可运行实例

```python
def create_instance(spec: NodeSpec):
    d = get_node_def(spec.type)
    inst = d.cls()
    inst.state = {}
    inst.node_id = spec.id
    inst.def_ = d
    inst.sandboxed = bool(d.sandboxed)
    inst.setup(dict(spec.params))
    return inst
```
（`registry.py:69-77`）

`NodeSpec`（`graph/model.py:25-31`）是蓝图里单个节点的声明式表示，只有三个字段：`id`（图内唯一实例 id）、`type`（对应 `REGISTRY` 键）、`params`（该实例的参数字典）。`create_instance` 的实例化流程是：

1. 按 `spec.type` 查表拿到 `NodeDef`；
2. 用 `cls()`（无参构造）实例化行为类——因此所有内置节点类都不能有必填的 `__init__` 参数，状态初始化统一推迟到 `setup()`；
3. 挂上三个运行期元信息：`state`（空字典，节点自己往里放跨 bar 需要保留的状态，例如 `ScenarioGateNode.setup` 里的 `self.state["phase"] = "waiting"`）、`node_id`（用于回放/调试时定位）、`def_`（回指其 `NodeDef`，可用来反查 cost/inputs/outputs）；
4. 计算并挂上 `sandboxed` 标志——引擎 `Engine._step_inner` 就是靠这个实例属性（而非重新查表）决定该给这个节点真实 `ctx` 还是 `_RestrictedContext`；
5. 调用 `inst.setup(dict(spec.params))` 完成节点自身的初始化（把参数字典拷贝一份传入，避免节点意外持有并篡改蓝图原始 `params` 对象）。

之后运行时只调用实例的 `on_bar(ctx, inputs) -> dict[str, Any]` 方法——这是所有内置节点必须实现的核心接口，`ctx` 是 `RunContext`（或沙箱节点的受限视图），`inputs` 是按输入引脚名索引的已就绪值字典，返回值是按输出引脚名索引的字典。引擎在 `runtime/engine.py:116-122` 拿到 `on_bar` 的返回值后统一包一层 `Stamped`（若节点没有自己打时间戳）并做因果时间校验（`check_stamped`），因此节点作者通常无需关心 `Stamped` 包装，直接返回裸值即可（`candle_feed` 是例外，见下）。

### 4.2 内置节点分类目录

以下按 `category` 分组列出本节覆盖的六个模块（`data.py`、`indicators.py`、`gates.py`、`sizing.py`、`execution.py`、`pa_gate.py`）注册的全部节点类型。（`nodes/__init__.py` 还导入了 `llm_nodes.py`、`rag_nodes.py`、`reflection.py`，注册了 `committee`/`llm_analyst`（决策类，调 LLM）、`knowledge_retrieve`/`experience_retrieve`/`require_citations`（RAG 类）、`reflector`/`experience_write`（反思类）——这些属于 LLM/RAG/反思子系统，在别处章节详述，此处仅在需要对照时提及。）

#### 数据源（category = "data"）

| 类型 | 类 | 输入 | 输出 | 参数 | 说明 |
|---|---|---|---|---|---|
| `candle_feed` | `CandleFeedNode` | 无 | `out: CANDLE` | `inst: str`, `bar: str` | 图的起点，无输入引脚。`on_bar` 直接从 `ctx.current_event.candle` 取当前 bar，**浅拷贝**成新 dict 后包成 `Stamped(dict(ev.candle), ev.ts_close)` 输出（`data.py:11-14`），注释明确解释浅拷贝原因："防止下游节点原地篡改污染同波其他节点"——因为同一个 `CandleFeedNode` 的输出会被同一 wave 内多个下游节点（`ema_fast`/`ema_slow`/`atr`/`cross`/`sizer`/`kill` 等）共享读取，若不拷贝，任一节点原地改了 dict 会污染其余订阅者。这是本节读到的节点中唯一显式手工构造 `Stamped` 的节点——因为它的 `as_of` 必须是 bar 的收盘时间 `ev.ts_close` 而非引擎默认的 `ctx.clock.now`（二者此刻数值相同，但显式打点是因果时间戳设计的一部分）。 |

#### 技术指标（category = "indicator"）

| 类型 | 类 | 输入 | 输出 | 参数 | 算法要点 |
|---|---|---|---|---|---|
| `ema` | `EmaNode` | `candle: CANDLE` | `value: SERIES` | `period: int` | 标准指数移动平均，`k = 2/(period+1)`，首个 bar 直接取 close 作为初值，此后 `ema = c*k + ema*(1-k)`（`indicators.py:9-15`）。经 hypothesis 性质测试验证增量计算与批量公式在 `rel=1e-9` 精度内一致（`test_ema_incremental_matches_batch`）。 |
| `atr` | `AtrNode` | `candle: CANDLE` | `value: SERIES` | `period: int` | 真实波幅的 Wilder 平滑：`tr = max(h-l, |h-prev_close|, |l-prev_close|)`，warmup 期用简单平均凑够 `period` 根后转入递推平滑 `atr = (atr*(period-1)+tr)/period`。**warmup 未完成时返回 `{"value": None}`**（`indicators.py:34-41`）——这是全图里"None 代表未就绪"约定的源头之一，下游节点（如 `cross_signal`、`pa_decision_tree`）都要显式处理 `atr is None` 的情况。 |
| `rsi` | `RsiNode` | `candle: CANDLE` | `value: SERIES` | `period: int` | 相对强弱指数，同样是"简单平均 warmup → Wilder 平滑"两阶段结构，用独立的 `avg_gain`/`avg_loss` 序列，`avg_loss == 0` 时直接返回 100 避免除零（`indicators.py:70-71`）。 |

#### 决策/门控（category = "decision"，本节范围内为纯确定性节点）

| 类型 | 类 | 输入 | 输出 | 参数 | 行为 |
|---|---|---|---|---|---|
| `cross_signal` | `CrossSignalNode` | `fast/slow/atr: SERIES`, `candle: CANDLE` | `signal: SIGNAL` | `atr_mult: float` | 双均线金叉/死叉：记录上一 bar 的 `fast-slow` 差值符号，符号翻转（`prev<=0<diff` 或 `prev>=0>diff`）即产出 `long`/`short` 信号，止损 = `close ∓ atr_mult*atr`；任一输入为 `None`（指标未 warmup）或首个有效 bar（无 `prev_diff` 基线）时输出 `_sig()` 即 `{"side":"hold","qty":0.0,"stop":None,"reason":""}`（`gates.py:18-33`）。 |
| `scenario_gate` | `ScenarioGateNode` | `candle: CANDLE`, `atr: SERIES` | `signal: SIGNAL` | `lookback: int`, `cooldown: int`, `atr_mult: float` | 显式状态机，三态 `waiting → triggered → cooldown → waiting`，状态存在 `self.state["phase"]` 里（供调试/前端可视化读取）。用 `deque(maxlen=lookback)` 滚动窗口记录最近 N 根的高低点，收盘价突破窗口最高/最低即触发信号并进入 `cooldown` 计数，`cooldown` 消耗完才允许再次触发（`gates.py:39-69`）。测试 `test_scenario_gate_breakout_and_cooldown` 验证了触发→冷却→再触发的完整循环。 |
| `pa_decision_tree` | `PADecisionTreeNode` | `candle: CANDLE`, `ema/atr: SERIES`（**可选**）, `signal: SIGNAL` | `signal: SIGNAL` | `min_atr: float` | 见 §4.4 单独详述——它是本模块的"守门"节点，只收紧不放宽上游信号。 |

#### 仓位管理与风控（category = "risk"）

| 类型 | 类 | 输入 | 输出 | 参数 | 行为 |
|---|---|---|---|---|---|
| `position_sizer` | `PositionSizerNode` | `signal: SIGNAL`, `candle: CANDLE` | `sized: SIGNAL` | `risk_pct: float` | 把方向性信号转成带具体仓位的信号：只在 `side in ("long","short")` 且带 `stop` 时计算，`qty = equity * risk_pct / |close-stop|`；止损距离为零或权益非正时**降级为 hold**（`sizing.py:16-21`），体现"宁可不做单也不做出无穷大仓位"的保守设计。`equity` 优先取 `ctx.broker.equity()`，无 broker（如单测环境）时兜底用 `10_000.0`。 |
| `risk_gate` | `RiskGateNode` | `signal: SIGNAL` | `stamped: RISK_STAMPED_SIGNAL`, `blocked: BOOL` | `max_qty: float`, `require_stop: bool` | **全宇宙唯一能产出 `RISK_STAMPED_SIGNAL` 类型值的内置节点**——见 §4.5 单独详述。 |
| `kill_switch` | `KillSwitchNode` | `candle: CANDLE` | `halted: BOOL` | `max_drawdown_pct: float` | 组合级熔断：追踪权益历史峰值 `self.peak`，当前回撤 `dd = (peak-eq)/peak` 达到阈值且 broker 尚未停机时调用 `broker.halt(reason)`（`gates.py:117-118`）。无 broker 时直接返回 `halted=False`——这个节点直接读写 `ctx.broker`，是运行时状态的"副作用型"节点而非纯函数节点。 |

#### 执行下单（category = "execution"）

| 类型 | 类 | 输入 | 输出 | 参数 | 行为 |
|---|---|---|---|---|---|
| `execute_order` | `ExecuteOrderNode` | `signal: RISK_STAMPED_SIGNAL` | `submitted: BOOL` | 无 | **唯一接受 `RISK_STAMPED_SIGNAL` 类型输入的内置节点**，与 `risk_gate` 形成类型系统上的强制配对：`side=="hold"`、`broker is None`、`broker.halted` 三种情况直接跳过（`execution.py:15-16`）；否则算出目标仓位 `{"long":qty,"short":-qty,"flat":0.0}[side]` 与当前持仓 `broker.position().qty` 的差值 `delta`，只在 `|delta| >= 1e-12` 时才提交 `Order`（`brokers/base.py` 定义），方向由 `delta` 符号决定（`buy`/`sell`），把信号的 `stop`/`reason` 透传进 `Order.stop`/`Order.tag`。测试 `test_execute_order_delta_and_reversal` 验证了"多头 2.0 手直接反手到空头 1.0 手"这种一次性下 3.0 手卖单的差量下单逻辑。 |

### 4.3 `optional_inputs` 机制

绝大多数内置节点的所有输入端口都是**强制连线**的：图编译器在 `graph/compiler.py:146-156` 会遍历每个节点定义的 `inputs`，对每个不在 `optional_inputs` 集合里、且未被任何边连接的端口，产出 `MISSING_INPUT` 编译错误，直接让编译失败（`fix_hint`: "Connect this input to an upstream output of the same pin type."）。这保证了一张编译通过的蓝图里，非可选输入在运行时**必然**能在 `raw_inputs` 里查到对应键。

`optional_inputs` 就是为这条规则开的例外口子：声明为可选的输入端口即使没有连线也能通过编译。运行时行为对应到引擎的取值逻辑（`runtime/engine.py:104-110`）——`raw_inputs` 字典只由 `self.compiled.bindings.get(node_id, [])` 里实际存在的绑定填充，没有连线的可选端口根本不会出现在这个 dict 里。因此节点实现里必须用 `inputs.get("port_name")`（而不是 `inputs["port_name"]`）读取可选输入，未连线时得到 `None`。

本节读取范围内，唯一使用这一机制的是 `pa_decision_tree`：

```python
@node(
    type="pa_decision_tree", category="decision",
    inputs={"candle": PinType.CANDLE, "ema": PinType.SERIES,
            "atr": PinType.SERIES, "signal": PinType.SIGNAL},
    outputs={"signal": PinType.SIGNAL},
    params={"min_atr": float},
    optional_inputs={"ema", "atr"},
    cost=CostAnnotation(llm_calls_per_bar=0, max_tokens_per_call=0,
                        latency_class="fast", deterministic=True),
)
class PADecisionTreeNode:
    ...
    def on_bar(self, ctx, inputs):
        ...
        ema = inputs.get("ema")
        atr = inputs.get("atr")
```
（`pa_gate.py:20-38, 61-62`）

`ema`/`atr` 被标记可选，是因为这个节点设计上既可以接在完整的 EMA/ATR 指标链之后做趋势过滤，也可以在没有这两路上下文时只做"透传上游 signal"的最简配置——`optional_inputs` 让同一个节点类型适配两种蓝图拓扑，而不必为"要不要接 ema/atr"派生出两个节点类型。装饰器时刻的自检（`unknown = optional - set(inputs)`）确保了 `optional_inputs={"ema","atr"}` 里的每个名字都确实出现在 `inputs` 字典里，防止拼写错误声明了一个不存在的端口为可选。

跨模块看，同样的模式还出现在 `rag_nodes.py`（`knowledge_retrieve` 的 `query`、`require_citations` 的 `citations`）和 `reflection.py`（`reflector` 的 `ema`/`atr`），都是"该输入能显著增强判断质量，但没有它节点仍能安全降级运行"的场景。

### 4.4 RiskGate：唯一的风险盖章权威

`risk_gate`（`RiskGateNode`，`gates.py:71-101`）在 AlphaLoom 的类型系统里被设计成一个**强制关卡**：它是全图中唯一输出 `PinType.RISK_STAMPED_SIGNAL` 的内置节点，而 `execute_order` 又是唯一接受这个引脚类型作为输入的内置节点。类文档字符串直接点明设计意图："全宇宙唯一能产出 risk_stamped_signal 的内置节点 —— 类型系统即合规官"。这意味着：任何策略蓝图，无论上游是纯规则门控（`cross_signal`/`scenario_gate`/`pa_decision_tree`）还是 LLM 决策节点（`committee`/`llm_analyst`），只要想让信号真正下单，图编译器的引脚类型检查就会强制要求信号先流经一个 `risk_gate` 节点——用类型系统而非运行时断言来保证"风控不可绕过"，是设计层面的核心不变量之一（`ema_cross.loom` 的 `gateProtocol.invariant` 字段把它写成人类可读的断言："Raw model or strategy output cannot enter execution until it passes position sizing and RiskGate stamping."）。

`RiskGateNode.on_bar` 的检查逻辑（参数 `max_qty: float`、`require_stop: bool`）：

```python
def on_bar(self, ctx, inputs):
    sig = dict(inputs["signal"])
    checks: list[str] = []
    side = sig.get("side")
    if side not in ("long", "short", "flat", "hold"):
        checks.append(f"unknown side {side!r}: must be one of long/short/flat/hold")
    elif side in ("long", "short"):
        qty = sig.get("qty", 0.0)
        stop = sig.get("stop")
        if not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty < 0:
            checks.append(f"qty {qty!r} must be a finite non-negative number")
        elif qty > self.max_qty:
            checks.append(f"qty {qty} exceeds max_qty {self.max_qty}")
        if self.require_stop and stop is None:
            checks.append("missing stop: attach a stop-loss to every entry signal")
        elif stop is not None and (not isinstance(stop, (int, float)) or not math.isfinite(stop)):
            checks.append(f"stop {stop!r} must be a finite number")
    blocked = bool(checks)
    if blocked:
        sig = _sig("hold", reason="blocked by risk gate")
    sig["risk"] = {"checked": True, "blocked": blocked, "checks": checks}
    return {"stamped": sig, "blocked": blocked}
```

四类检查依次是：side 合法性（必须是 `long`/`short`/`flat`/`hold` 之一）、qty 有限非负且不超 `max_qty`、止损存在性（`require_stop=True` 时缺止损即拦截）、止损数值本身有限。**任何一条不通过，输出信号即被强制降级为 `_sig("hold", reason="blocked by risk gate")`**——RiskGate 只会让信号变得更保守，绝不会放大或改写一个本来合规的信号的方向/数量。无论放行还是拦截，输出都会带上一份 `sig["risk"] = {"checked": True, "blocked": bool, "checks": [...]}` 的盖章元数据，这份 `risk` 字段就是"已过 RiskGate"的可追溯凭证，写入 `stamped` 输出（`PinType.RISK_STAMPED_SIGNAL`）供下游/回放/审计读取；`blocked` 同时作为独立的 `BOOL` 输出供图里其他节点（如前端可视化、告警节点）订阅。

`test_risk_gate_is_sole_stamper`（`tests/test_builtin_nodes.py:130-137`）用反射方式在 `REGISTRY` 里扫描所有输出里含 `PinType.RISK_STAMPED_SIGNAL` 的节点类型（排除 `category=="test"` 的测试探针），断言结果集合严格等于 `["risk_gate"]`——这把"RiskGate 是唯一盖章者"这一设计不变量锁定为一条可执行的回归测试，而不仅仅是注释里的约定。

### 4.5 一个完整蓝图里的节点协作

`blueprints/ema_cross.loom` 展示了本节这些节点如何用引脚类型串成一张完整策略图（节选自 `nodes`/`edges`）：

```json
{
  "nodes": [
    {"id": "feed",  "type": "candle_feed",   "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
    {"id": "ema_fast", "type": "ema",        "params": {"period": 12}},
    {"id": "ema_slow", "type": "ema",        "params": {"period": 26}},
    {"id": "atr",   "type": "atr",           "params": {"period": 14}},
    {"id": "cross", "type": "cross_signal",  "params": {"atr_mult": 2}},
    {"id": "sizer", "type": "position_sizer","params": {"risk_pct": 0.02}},
    {"id": "risk",  "type": "risk_gate",     "params": {"max_qty": 100, "require_stop": true}},
    {"id": "exec",  "type": "execute_order", "params": {}},
    {"id": "kill",  "type": "kill_switch",   "params": {"max_drawdown_pct": 0.25}}
  ],
  "edges": [
    {"from": "feed.out", "to": "ema_fast.candle"},
    {"from": "feed.out", "to": "ema_slow.candle"},
    {"from": "feed.out", "to": "atr.candle"},
    {"from": "ema_fast.value", "to": "cross.fast"},
    {"from": "ema_slow.value", "to": "cross.slow"},
    {"from": "feed.out",  "to": "cross.candle"},
    {"from": "atr.value", "to": "cross.atr"},
    {"from": "cross.signal", "to": "sizer.signal"},
    {"from": "feed.out",  "to": "sizer.candle"},
    {"from": "sizer.sized", "to": "risk.signal"},
    {"from": "risk.stamped", "to": "exec.signal"},
    {"from": "feed.out", "to": "kill.candle"}
  ]
}
```

链路是 `candle_feed → {ema×2, atr} → cross_signal → position_sizer → risk_gate → execute_order`，外挂一条 `candle_feed → kill_switch` 的独立熔断支路。这条链天然按 `category` 排布：`data → indicator → decision → risk(sizing) → risk(gate) → execution`，正是图编译器拓扑排序（`TopologicalSorter`，第 3 章详述）依据边的依赖关系算出的执行序，而非人为写死的类别顺序。

对应地，`GET /api/nodes` 端点（`api/app.py:149-161`）把 `REGISTRY` 里每个非 `test` 类别节点序列化成前端节点面板消费的 JSON：

```json
{
  "type": "risk_gate",
  "category": "risk",
  "inputs": {"signal": "signal"},
  "outputs": {"stamped": "risk_stamped_signal", "blocked": "bool"},
  "params": {"max_qty": "float", "require_stop": "bool"},
  "cost": {"llm_calls_per_bar": 0, "max_tokens_per_call": 0,
           "latency_class": "fast", "deterministic": true}
}
```

`PinType.value`（`"signal"`、`"risk_stamped_signal"`、`"bool"` 等字符串）而非 Python 枚举名被序列化出去，这是前端 TypeScript 侧渲染引脚颜色/连线校验时匹配的实际字符串值（`PinType` 定义在 `graph/types.py:6-12`：`EXEC`/`CANDLE`/`SERIES`/`SIGNAL`/`RISK_STAMPED_SIGNAL`/`BOOL`）。

### 4.6 新增内置节点类型的模式

一个贡献者要新增一种内置节点类型，遵循的是本节六个文件里反复出现的同一套样板：

1. **选定模块**：按语义归入既有文件（`data.py`/`indicators.py`/`gates.py`/`sizing.py`/`execution.py`/`pa_gate.py`）或新开一个模块；若新开模块，须在 `nodes/__init__.py` 里加一行 `import alphaloom.nodes.<new_module>  # noqa: F401,E402`——`__init__.py` 目前的每一行 import 都只为了触发模块顶层的 `@node` 装饰器执行副作用，导入本身不使用任何具名符号，因此 `noqa: F401` 是必要的（未被使用的 import 警告本就是预期行为，不代表死代码）。
2. **声明引脚类型**：用 `PinType` 枚举值精确描述 `inputs`/`outputs` 字典。这一步的选择直接决定了图编译器的类型检查行为——例如故意选用 `PinType.RISK_STAMPED_SIGNAL` 作为某输出类型，会让这个节点自动被 `test_risk_gate_is_sole_stamper` 一类的守卫测试捕获（新增第二个"盖章者"节点若不希望被视为设计违规，需要同步更新该测试的预期集合并有明确理由）。
3. **声明 `params` 类型提示字典**：仅用于 `/api/nodes` 展示和前端表单渲染，真正的取值/转换/默认值仍在 `setup(self, params)` 里手写（本节六个文件里清一色是 `float(params.get("k", default))` / `int(params["period"])` 这种防御式写法，容忍蓝图缺省参数并给出合理默认值）。
4. **按需声明 `optional_inputs`**：只在"该输入缺失时节点仍能给出有意义降级行为"的场景使用（模式参考 `pa_decision_tree` 对 `ema`/`atr` 的处理：`inputs.get(...)` 取值，`None` 时执行保守分支），并确保节点的 `on_bar` 里对应位置用 `.get()` 而非 `[]` 取值，否则未连线时会抛 `KeyError`。
5. **如实声明 `cost: CostAnnotation`**：纯数值/规则节点保持默认全零、`deterministic=True`；若节点内部会调用 LLM 或外部服务，必须如实标注 `llm_calls_per_bar`/`max_tokens_per_call`/`latency_class="llm"`/`deterministic=False`，因为编译期的成本证书（`build_certificate`，`graph/cost.py:17-23`）是把所有节点的 `cost` 字段直接求和/取最坏值汇总给用户看的（`calls = sum(...)`、`tokens = sum(...) * bars_per_day`、`worst = max(..., key=latency_rank)`），谎报会让证书失真。
6. **实现 `setup(self, params)` 与 `on_bar(self, ctx, inputs)`**：`setup` 做一次性初始化（含 `self.state[...]` 里需要暴露给调试/前端的状态位，参考 `ScenarioGateNode` 的 `phase`），`on_bar` 是每个 bar 调用一次的纯粹计算入口，返回值字典的键必须与声明的 `outputs` 完全对应。返回裸值即可，引擎会自动包 `Stamped`；只有像 `candle_feed` 那样需要自定义 `as_of` 时间戳的节点才需要手工构造 `Stamped`。
7. **写单元测试**：跟随 `tests/test_builtin_nodes.py` 的模式——用 `create_instance(NodeSpec(id, type, params))` 直接实例化节点（不经过完整编译/引擎），手工调用 `on_bar(ctx, inputs)` 断言输出；`ctx` 可以用测试文件里的 `_ctx(broker=None)` 辅助函数构造一个最小 `RunContext`。涉及增量算法（如 EMA/ATR/RSI 这类递推指标）的节点，建议参考 `test_ema_incremental_matches_batch` 用 `hypothesis` 做"增量计算结果 == 批量公式结果"的性质测试，而不仅是若干个手算样例。

这套模式的核心约束是：`@node` 装饰器在 import 期就会执行类型名唯一性检查和 `optional_inputs` 合法性检查，因此新节点类型命名冲突或声明错误会在应用启动阶段（而非某次编译或运行时）就报错暴露，不会流入生产路径。

---

## 5. 自定义节点沙箱

### 5.1 目标：让"任意人写的节点代码"安全地热注册进运行时

AlphaLoom 允许终端用户（或 LLM copilot）通过 `POST /api/nodes/custom` 提交一段 Python 源码，系统在服务端把它编译、校验后**热注册**进进程级节点目录 `REGISTRY`，随后这个新节点类型即可像内置节点一样被拖进画布、连线、参与 `/api/compile` 编译与回测。这条路径统称 "Text-to-Node"：核心模块是 `backend/alphaloom/sandbox/node_sandbox.py`，其模块 docstring 把安全模型概括为一句话——"宁可保守拒绝也不漏"（node_sandbox.py:3）。

设计前提写在 `backend/alphaloom/nodes/registry.py` 的模块 docstring 里：`REGISTRY: dict[str, NodeDef]` 是**进程级全局单例**（registry.py:39），内置节点在 import 期注册，自定义节点在沙箱编译通过后注入同一张表，因此对所有请求/所有用户可见。这在文档中被明确记录为"单用户本地/演示部署"的既定语义（registry.py:3-14），多租户隔离被列为未来的 D4 Carryover 事项，而不是本节要解决的问题——本节只关心"这段外来代码在执行时能做什么"。

沙箱要保证的收敛点很朴素：允许的自定义节点只能是**纯计算**——读输入 dict、做数值/字符串运算、维护自身状态、返回输出 dict；不允许它触碰文件系统、网络、进程、其他节点的运行时能力（LLM 句柄、broker、审计钩子），也不允许它伪造属于内置合规节点的"证书"语义。

### 5.2 编译流水线总览

`compile_node_source(src: str) -> NodeDef | SandboxError`（node_sandbox.py:534）是唯一入口，签名上就体现了设计取向：**失败一律返回值，不抛异常**（除内部 `_SandboxViolation` 会被就地捕获转换）。这样调用方（HTTP handler、之后可能的 copilot 自动修复循环）可以把结果当结构化数据处理，而不必到处包 `try/except`。整个流程分七步：

```
parse (ast.parse)
  → AST 白名单校验 (_Validator)
    → 受限 exec（安全 __builtins__ + 白名单 import 钩子）
      → REGISTRY 快照 diff，定位新注册的 type
        → 接缝防线①：拒绝伪造 RISK_STAMPED_SIGNAL
          → 接缝防线②：拒绝畸形 PinType
            → mark_sandboxed(t)：打上不受信来源标记
```

任何一步出错都会把本次 exec 期间对 `REGISTRY` 造成的新增项**回滚**（`_rollback`，node_sandbox.py:630-632），保证"拒绝"是干净的、不留污染。

#### 5.2.1 解析与语法错误

```python
try:
    tree = ast.parse(src, mode="exec")
except SyntaxError as exc:
    return SandboxError(f"syntax error: {exc}", reason="syntax_error",
                        lineno=getattr(exc, "lineno", None))
```
（node_sandbox.py:540-544）语法错误本身就是一类 `SandboxError`，`reason` 字段是给前端/LLM 用的机器可读错误码，`lineno` 定位到源码具体行——这个"结构化拒绝理由"的模式贯穿整个模块。

#### 5.2.2 AST 白名单校验（`_Validator`）

这是沙箱的核心防线，由 `ast.NodeVisitor` 子类 `_Validator` 实现（node_sandbox.py:187-523）。校验分五个维度：

**(1) AST 节点类型白名单。** `_ALLOWED_NODES` 元组（node_sandbox.py:107-130）枚举了允许出现的全部 AST 节点类型：模块/类/函数声明、`Import`/`ImportFrom`、常规语句（`Return`/`Assign`/`AugAssign`/`AnnAssign`/`If`/`For`/`Break`/`Continue`）、表达式（`Call`/`Attribute`/`Subscript`/`BinOp`/比较/布尔运算）、以及受控的推导式（`ListComp`/`SetComp`/`DictComp`/`GeneratorExp`）。`_Validator.generic_visit` 覆写为"白名单外一律拒"：

```python
def generic_visit(self, node_obj: ast.AST) -> None:
    t = type(node_obj)
    if t in _DENY_REASONS:
        msg, reason = _DENY_REASONS[t]
        _reject(node_obj, msg, reason)
    if not isinstance(node_obj, _ALLOWED_NODES):
        _reject(node_obj, f"AST node {t.__name__} is not allowed in sandboxed node source",
                "ast_denied")
    super().generic_visit(node_obj)
```
（node_sandbox.py:368-381）

其中一部分节点类型有专门的拒绝理由表 `_DENY_REASONS`（node_sandbox.py:133-149），覆盖 `While`（无界循环/`while True` 逃逸）、`Lambda`（默认参数逃逸载体）、`With`/`Try`/`Raise`（吞掉逃逸错误的手段）、`Global`/`Nonlocal`、`Yield`/`Await`/`Async*` 全家桶等——这些不是简单地"不在白名单里"，而是被显式点名，报错信息里直接写明白"为什么危险"，便于 LLM copilot 根据 `reason` 做针对性修复。

**(2) dunder / 私有属性禁访问。** `visit_Attribute` 拦下两类属性访问（node_sandbox.py:404-432）：

- 任何以 `__` 开头的属性名（`_is_dunder`，node_sandbox.py:166-174）——保守到"只要以 `__` 开头就拒"，一次性堵死 `__class__`/`__globals__`/`__bases__`/`__mro__`/`__subclasses__`/`__builtins__`/`__code__`/`__dict__` 这整条经典 Python 沙箱逃逸链；
- 任何单下划线开头的属性名（如 `ctx._ctx`、`obj._internal`），理由是"经内部/私有属性反向取回受剥夺对象"——这条规则的存在直接对应第 5.3 节要讲的 `_RestrictedContext` 设计（见下）。

此外 `FORBIDDEN_ATTRS = {"format", "format_map", "mro", "__subclasshook__"}`（node_sandbox.py:72-75）按**名字**（而非 dunder 前缀）单独拦：因为 `"{0.__class__}".format(x)` 这种格式串逃逸，dunder 藏在字符串字面量里，AST 层面根本看不见有 `__class__` 这个属性访问，必须靠禁用 `.format`/`.format_map` 方法本身来堵。测试文件里 `str_format_escape`、`fstring_dunder` 等用例专门验证了这条（test_node_sandbox.py:155-159）。

**(3) 危险名字禁引用。** `visit_Name` 检查任何 `Name` 节点（无论 Load/Store/Del）是否落在 `FORBIDDEN_NAMES` 集合里（node_sandbox.py:60-68、435-448），包含 `open`/`exec`/`eval`/`compile`/`__import__`/`getattr`/`setattr`/`globals`/`locals`/`vars`/`dir`/`type`/`object`/`super` 等。设计注释特别指出这条规则的价值：因为 `getattr` 这个名字本身被禁止引用，`getattr(x, "__" + "globals__")` 这种试图用字符串拼接绕过"字面量 dunder 检测"的把戏会在名字检查这一层就失效（node_sandbox.py:12-14）。测试用例 `getattr_string_concat` 验证了这一点（test_node_sandbox.py:140）。

**(4) import 白名单。** `ALLOWED_IMPORTS` 只放行四项：`math`、`statistics`（纯计算 stdlib）、`alphaloom.graph.types`（供源码引用 `PinType`/`CostAnnotation`）、`alphaloom.sandbox.node_sandbox`（供源码引用 `node` 装饰器自身）（node_sandbox.py:48-53）。`visit_Import`/`visit_ImportFrom` 在 AST 层拒绝白名单外的模块、相对导入、以及通配符 `import *`（node_sandbox.py:384-401）。这一限制在受限 exec 阶段还有运行时兜底（见下文 `_guarded_import`），构成双重防线。

**(5) 循环上限。** 这是本模块里静态分析最复杂的部分：
- `While` 全面禁止（连带杜绝 `while True` 无界循环）；
- `for` 循环仅允许对字面量/参数迭代，`range(...)` 的字面上界受 `MAX_RANGE = 100_000` 限制（node_sandbox.py:511-523 `_check_range`）；
- 为防止简单的"变量间接"绕过（例如 `n = 500000; range(n)`），`_Validator` 维护一张 `_const_ints: dict[str, int]` 常量表，对形如 `n = <整数字面量>` 的绑定做静态跟踪，并支持在跟踪到的常量上做**算术传播**——`BinOp`（`+`/`-`/`*`/`//`/`%`/`**`/`<<`/`>>`）与一元 `+`/`-` 都能在常量表上求值，因此 `n=50000; m=n*3; range(m)` 这类间接写法也能被算出 `m=150000` 并超限拒绝（node_sandbox.py:198-263，注释标注这是 "D4 Carryover 3/9①：算术传播绕过加固"）；
- `AugAssign`（`n += 500000`）同样会更新常量表，通过构造等价 `BinOp` 复用同一套求值逻辑（node_sandbox.py:348-366，注释同样标注 "D4 Carryover"）；
- 任何算不出确切值的情况（参数、函数调用结果、未知量参与运算）一律**保守放行**而非报错——因为运行时已有受限 `__builtins__` 兜底，且这类值在实践中常见于合法代码（如 `range(len(x))`），不应误伤；
- 除了单层循环的字面上界，还有**嵌套循环工作量**估算：`_push_loop_work` 会把外层已知循环次数与当前层相乘，超过 `MAX_LOOP_WORK = 1_000_000` 即拒绝（node_sandbox.py:301-316），推导式（`ListComp`/`SetComp`/`DictComp`/`GeneratorExp`）也复用同一套 `_check_comprehension_work` 逻辑对多重 `for` 子句做乘积估算（node_sandbox.py:481-509）；
- 序列重复（`"x" * 10**9` 这类 `BinOp` 的 `Mult`）单独有 `_check_sequence_repeat`，对字面量序列长度与重复次数的乘积套 `MAX_SEQUENCE_REPEAT = 1_000_000` 上限（node_sandbox.py:318-341）。

这一整套"静态常量传播 + 保守放行未知量"的设计体现了沙箱一贯的取舍：**能证明安全的就精确算，算不出来的就依赖运行时兜底而不是拒绝合法代码**，测试文件里一组 `test_*_range_capped` / `test_small_*_allowed` 用例对称验证了"该拦的拦住、不该拦的放行"（test_node_sandbox.py:321-475）。

#### 5.2.3 受限 exec：安全 `__builtins__` 子集 + 白名单 import 钩子

AST 校验通过后，进入真正的 `exec`：

```python
sandbox_globals: dict[str, Any] = {
    "__builtins__": _make_safe_builtins(),
    "__name__": "alphaloom_sandbox_node",
}
code = compile(tree, filename="<sandbox_node>", mode="exec")
exec(code, sandbox_globals)  # noqa: S102 — 已 AST 白名单 + 受限 builtins
```
（node_sandbox.py:554-561）

`_make_safe_builtins()`（node_sandbox.py:635-653）构造的 `__builtins__` 字典只包含 `SAFE_BUILTINS`——一份纯计算安全内建的枚举（`abs`/`min`/`max`/`sum`/`len`/`round`/`pow`/`divmod`/类型构造器/`sorted`/`enumerate`/`zip`/`map`/`filter`/`range`/`all`/`any`/`print`/`isinstance`，以及 `True`/`False`/`None` 和几个异常类型，node_sandbox.py:87-99），完全不含 `open`/`exec`/`eval`/`__import__` 等。测试 `test_builtins_not_accessible_at_runtime` 明确验证"即便 AST 通过，运行时也拿不到危险 builtin"（test_node_sandbox.py:211-228）——这是刻意的双重防线：**AST 层禁止引用名字，运行时层再确保就算引用到了也没有真危险对象可用**。

`__import__` 被替换成一个白名单钩子 `_guarded_import`（node_sandbox.py:639-647），拒绝相对导入和非白名单模块——这是对 AST 层 import 检查的运行时复现，防止任何未被 AST 校验覆盖的动态 import 路径。此外还显式放行了 `__build_class__`（node_sandbox.py:652），因为 Python 的 `class` 语句在字节码层面依赖它来构造类对象；由于类体本身已经过 AST 白名单校验，这个内建本身不构成额外逃逸面。

任何 exec 期异常——包括 `_guarded_import` 抛出的 `SandboxError`、`_SandboxViolation`（理论上不会到这里，但作为防御性捕获），或源码本身的运行时错误——都会触发 `_rollback` 并把错误转成 `SandboxError` 返回（node_sandbox.py:562-571），确保失败路径不泄露内部异常细节、也不留脏状态。

#### 5.2.4 REGISTRY diff：识别新注册的节点类型

沙箱不直接调用某个"注册函数"再取返回值，而是让源码里的 `@node` 装饰器（`node = _register_node`，复用 `registry.node`，node_sandbox.py:41）在 exec 期间自然地把类注册进全局 `REGISTRY`，然后靠前后快照做差集来定位：

```python
before = set(REGISTRY)
...
new_types = set(REGISTRY) - before
if not new_types:
    return SandboxError("source did not register any @node; declare exactly one @node class",
                        reason="no_node")
if len(new_types) > 1:
    _rollback(before)
    return SandboxError(f"source registered multiple node types {sorted(new_types)}; "
                        f"declare exactly one @node class", reason="multiple_nodes")
```
（node_sandbox.py:552、574-586）

这一设计约束了每次提交"恰好注册一个节点类型"，符合 Text-to-Node 的产品心智模型（一次提交=一个新节点）。若源码干净但没用 `@node` 装饰任何类，也视为失败而非静默成功。

#### 5.2.5 两道"接缝防线"（seam defenses）

AST 白名单校验的是"代码写了什么"，但 `NodeDef` 的语义正确性（`inputs`/`outputs` 的值是否合法）是装饰器本身不做强校验的地方——这里补两道针对 `NodeDef` 本身的事后检查，模块注释称之为"接缝防线"：

**防线①：禁止伪造 `RISK_STAMPED_SIGNAL`。** `PinType` 枚举（`backend/alphaloom/graph/types.py:6-12`）包含 `EXEC`/`CANDLE`/`SERIES`/`SIGNAL`/`RISK_STAMPED_SIGNAL`/`BOOL`，其中 `RISK_STAMPED_SIGNAL` 在整个类型系统里的含义是"已经过内置 `RiskGate` 盖章合规的信号"——下游节点（如执行下单）信任这个类型本身作为一种 provenance 证明。若允许沙箱节点声明这个输出类型，它就能在 `on_bar` 里手工塞一个 `{"checked": True, "blocked": False, "checks": []}` 的假盖章，让一个未经真实风控检查的巨量仓位信号伪装成合规信号往下游传：

```python
if PinType.RISK_STAMPED_SIGNAL in ndef.outputs.values():
    _rollback(before)
    return SandboxError(
        f"node {t!r} declares a {PinType.RISK_STAMPED_SIGNAL.value} output; "
        f"this risk-stamp type is reserved for the trusted built-in RiskGate "
        f"and may not be declared by sandboxed nodes (stamp provenance forgery)",
        reason="forge_risk_stamp")
```
（node_sandbox.py:595-602）

这条检查故意放在"拿到 `NodeDef` 之后"而不是纯 AST 层匹配——注释解释是"从 AST 花式写法绕过的角度，事后查 `NodeDef.outputs.values()` 比 AST 层匹配更可靠"（node_sandbox.py:592-594），即：不管源码里用什么方式（关键字参数、拼接字符串键名等）声明 `outputs`，最终落到 `NodeDef.outputs` 里的实际值才是权威判据。测试 `test_sandbox_node_cannot_forge_risk_stamp` 与 `test_forged_stamp_node_never_reaches_compile_blueprint` 验证了拒绝发生在编译期，使得下游蓝图引用这个伪造节点时只会得到 `UNKNOWN_NODE_TYPE`（test_node_sandbox.py:254-268）。

**防线②：拒绝畸形 `PinType` 值。** `@node` 装饰器本身不校验 `inputs`/`outputs` 字典的值是否真的是 `PinType` 枚举成员；一个畸形节点（比如 `outputs={"v": [PinType.SERIES]}` 传了个 list，或 `outputs={"v": "series"}` 传了个裸字符串）此前能注册成功。问题在于 `GET /api/nodes`（app.py 里 `{k: v.value for ...}`）和 `/api/compile`（compiler.py 里的 `t_out.value`）都会对该类型的每个引脚值取 `.value`，非 `PinType` 实例上没有这个属性会抛 `AttributeError` → 500；而 `REGISTRY` 是进程级全局状态，一旦注册就持久污染，此后**所有调用者**（含其他用户）访问这两个端点都会 500（注释标注这是 "T8 审查 carryover #9②"，node_sandbox.py:604-611）。修复是逐一校验：

```python
for _port_name, _pin in {**ndef.inputs, **ndef.outputs}.items():
    if not isinstance(_pin, PinType):
        _rollback(before)
        return SandboxError(..., reason="bad_pin_type")
```
（node_sandbox.py:612-621）

这两道防线共享同一个模式：**AST 白名单管好"怎么写代码"，接缝防线管好"注册结果的语义/类型是否合法"**——两者互补，缺一都会留下一类问题。

### 5.3 `mark_sandboxed()`：来源标记如何改变运行时行为

`compile_node_source` 成功路径的最后一步是 `mark_sandboxed(t)`（node_sandbox.py:626），对应 `registry.py` 里的：

```python
def mark_sandboxed(t: str) -> None:
    d = REGISTRY.get(t)
    if d is not None and not d.sandboxed:
        REGISTRY[t] = replace(d, sandboxed=True)
```
（registry.py:62-67）

`NodeDef` 是一个 `frozen` dataclass，新增字段 `sandboxed: bool = False`（registry.py:21-35），字段注释直接点明这个标记的两个消费点：

> "守门层据此不信任沙箱节点的成本证书自证……且运行期给沙箱节点一个剥离 `.llm`/`.audit` 的受限 ctx 视图"

**消费点①——运行时受限 ctx（`engine.py`）。** `create_instance`（registry.py:69-77）在实例化节点时把 `inst.sandboxed = bool(d.sandboxed)` 写到实例上；`Engine._step_inner`（engine.py:113-116）据此为每个节点选择 ctx 视图：

```python
node_ctx = self._restricted_ctx if getattr(inst, "sandboxed", False) else self.ctx
outputs = inst.on_bar(node_ctx, raw_inputs) or {}
```

`_RestrictedContext`（engine.py:34-68）是这里的关键类型：它把真正的 `RunContext` 存在**类外的模块级 `WeakKeyDictionary`**（`_CTX_BACKING`）里而不是实例属性上，自身 `__dict__` 保持空、没有任何指向真 ctx 的 slot；`__getattr__`/`__setattr__` 拦截所有属性访问，对 `_SANDBOX_DENIED_CTX_ATTRS = frozenset({"llm", "audit", "broker"})` 中的名字直接抛 `SandboxEscapeError`，其余属性（`clock`/`current_event`/`run_id`/`recorder`/`halted` 等合法只读计算所需的能力）透明委托给真 ctx。

这个"真 ctx 不在受限视图对象图上"的设计与前面提到的 AST 层"禁止单下划线私有属性访问"形成一套组合防御：注释里称之为 "I1 红队实锤"——即便理论上受限视图曾经把真 ctx 存在类似 `self._ctx` 的私有 slot 上，AST 层的私有属性拦截也会让沙箱代码写不出 `ctx._ctx.llm.chat()` 这样的访问；而现在的实现更进一步，直接让真 ctx 在对象图层面就不可达，两层防御互相独立、互为冗余（engine.py:26-42）。

`SandboxEscapeError` 在 `app.py` 里被注册为专门的异常处理器，转成干净的 422 而非裸 500：

```python
@app.exception_handler(_SandboxEscapeError)
async def _sandbox_escape_handler(_request, exc):
    return JSONResponse(status_code=422, content={
        "error": "sandbox_escape",
        "message": f"a sandboxed node attempted a forbidden capability: {exc}. "
                   "Sandbox nodes are stripped of the LLM handle; this blueprint cannot be evaluated."})
```
（app.py:97-106）

**消费点②——不信任沙箱节点的成本证书自证（`app.py`）。** 编译期的静态成本证书（`CostAnnotation.llm_calls_per_bar` 等）是"节点自己声明"的——沙箱节点完全可以在 `@node(...)` 装饰器参数里声明 `cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True)`，然后在 `on_bar` 里"偷调" `ctx.llm`。虽然运行时受限 ctx 已经从根本上堵死了这种偷调（会抛 `SandboxEscapeError`），`app.py` 仍然额外做了一层深度防御：

```python
def _has_sandbox_node(compiled) -> bool:
    for spec in getattr(compiled, "nodes", {}).values():
        d = REGISTRY.get(getattr(spec, "type", None))
        if d is not None and getattr(d, "sandboxed", False):
            return True
    return False

def _needs_llm(compiled) -> bool:
    cert = getattr(compiled, "certificate", None)
    if cert is not None and getattr(cert, "llm_calls_per_bar", 0) > 0:
        return True
    return _has_sandbox_node(compiled)   # 证书信任根在沙箱节点在场时失效
```
（app.py:473-489）

也就是说：只要蓝图里含有任何 `sandboxed=True` 的节点，评估/进化端点就会把它当成"可能需要 LLM"对待，进而要求当前 LLM 客户端必须是 `offline`（零配额录制回放）才放行，否则 409（`_guard_llm_blueprint`，app.py:505 起）。这条规则不依赖沙箱节点实际有没有调用 LLM——只要来源是不受信的，证书自证就整体失效，体现"来源标记"是贯穿多个子系统的统一信任边界，而不只是单点开关。

### 5.4 降级路径：`make_threshold_node` 模板

除了自由源码沙箱，模块还提供了一条风险更低的备选路径——`make_threshold_node`（node_sandbox.py:669-736），专门给 LLM copilot 生成"简单阈值判断"节点时用。它**不接受任何自由代码**，只接受几个强类型参数：

```python
def make_threshold_node(*, type: str, indicator: str, op: str,
                         threshold: float, side: str) -> NodeDef | SandboxError:
```

`op` 必须是 `_TEMPLATE_OPS = {"lt", "le", "gt", "ge"}` 之一，`side` 必须是 `"long"` 或 `"short"`，`threshold` 必须能转成 `float`，`type` 必须是非空字符串且未被占用——任何一项不满足就返回 `SandboxError(reason="template_param")`（node_sandbox.py:682-702）。校验通过后，函数内部用固定的 `@node` 装饰器**代码模板**（而非 `exec` 用户输入）声明一个 `ThresholdTemplateNode`：输入 `value`（`PinType.SERIES`）与 `candle`（`PinType.CANDLE`），输出 `signal`（`PinType.SIGNAL`），`cost=CostAnnotation(llm_calls_per_bar=0, ..., deterministic=True)`，`on_bar` 逻辑就是把 `value` 与 `threshold` 用 `cmp` 比较后产出 `long`/`short`/`hold` 三态信号。

这条路径的设计动机很明确：**当 LLM 生成的需求本质上只是"参数化阈值比较"时，走模板比走自由源码沙箱风险更低**——因为模板的代码本身是硬编码在 `node_sandbox.py` 里、由维护者审查过的，LLM 只填入数值型参数，不存在"生成的源码需要过 AST 审查"这一整个攻击面。测试 `test_make_threshold_node_template` 与 `test_make_threshold_node_rejects_bad_params` 验证了合法/非法参数两侧的行为（test_node_sandbox.py:504-529）。这也是模块 docstring 里"降级保险丝"一词的含义：自由源码沙箱是主路径，模板是给简单场景准备的更保守替代（node_sandbox.py:656-658）。

### 5.5 迭代加固的演进脉络

`node_sandbox.py` 的注释里保留了大量指向历史加固轮次的标记（如 "D3"、"D4 Carryover"、"T7 红队"、"T8 审查"、"I1 红队实锤"、"C1 修复"），这些对应到 git 历史上一串清晰可辨的提交序列，体现了这个模块是通过多轮"实现 → 红队复核 → 定点修补"的方式收敛出来的，而非一次性设计完成：

| 提交（节选） | 内容 |
|---|---|
| `ba4f0cc` feat(sandbox) | 首次落地：AST 白名单编译器 + 热注册（Text-to-Node 主体） |
| `fd37b55` fix(sandbox) | T7 红队复核后：禁止伪造 `RISK_STAMPED_SIGNAL`（防线①）+ 变量绑定 range 上限加固 |
| `8429e00` fix(sandbox) | T8 复核：校验自定义节点 pin 值必须是真 `PinType`（防线②，堵住"网络可达的目录端点 DoS"这一类问题） |
| `4ac9d73` fix(hardening) | D4 Carryover 批次：算术传播绕过（`n*3`/`n<<10` 等）与 `AugAssign`（`n += x`）绕过 range 上限的加固 |
| `bcff3ad` fix(sandbox+api) | 从沙箱节点剥离 `.llm` 访问（`_RestrictedContext` 诞生）+ 在配额守门层不再信任沙箱节点的零 LLM 自证 |
| `1c189c6` fix(sandbox) | I1 红队复核：堵住"经私有 slot（如 `ctx._ctx`）反向取回被剥夺的真 ctx"的逃逸——即 AST 层新增单下划线属性拦截 + `_RestrictedContext` 改为把真 ctx 存到对象图之外的 `WeakKeyDictionary` |

这条演进路径反映出的设计原则是：**每一轮加固都对应一个具体被复核出的逃逸/绕过场景，修复后立刻把对应的对抗性用例固化进 `test_node_sandbox.py`**（该文件现有 37 个测试函数，参数化的 `MALICIOUS` 字典本身就覆盖了三十余种经典 Python 沙箱逃逸手法：`import os`/`eval`/`getattr` 拼接/`__class__.__bases__.__subclasses__()`/lambda 默认参数逃逸/推导式里调用禁用函数/`str.format` 格式串逃逸等，test_node_sandbox.py:117-160），使得"这一类问题不会在未来的重构里悄悄回归"。这种"红队复核 → 定点修补 → 回归用例固化"的循环，也是为什么模块 docstring 会强调"安全模型（沙箱契约锁定）"这个措辞——契约一旦被某轮复核收紧，后续改动必须在不破坏已有测试的前提下进行。

---

至此，自定义节点从"一段文本"到"REGISTRY 里可被画布拖拽使用的节点类型"的完整链路是：`POST /api/nodes/custom` 接收源码 → `compile_node_source` 完成 AST 白名单校验与受限 exec → 通过后的 `NodeDef` 被打上 `sandboxed=True` → 运行时 `Engine` 据此发放受限 ctx、API 层据此对成本证书保持怀疑。这套机制把"信任边界"从"一次性输入校验"延伸成了贯穿编译期与运行期的持续标记，是本项目里安全设计与产品功能（Text-to-Node、LLM copilot 生成节点）结合最紧密的一块。

---

**本节引用的关键文件路径**（供后续章节交叉引用）：
- `backend/alphaloom/sandbox/node_sandbox.py`——AST 白名单编译器主体、`compile_node_source`、`make_threshold_node`
- `backend/alphaloom/sandbox/errors.py`——`SandboxError` 类型
- `backend/alphaloom/sandbox/audit.py`——`AuditEntry`/`AuditLog`（沙箱数据/LLM 访问审计记录的数据结构）
- `backend/alphaloom/nodes/registry.py`——`REGISTRY`、`NodeDef`、`mark_sandboxed`、`create_instance`
- `backend/alphaloom/runtime/engine.py`——`_RestrictedContext`、`SandboxEscapeError`、`Engine._step_inner` 中的 ctx 选择逻辑
- `backend/alphaloom/api/app.py`——`POST /api/nodes/custom` 端点、`_sandbox_escape_handler`、`_has_sandbox_node`/`_needs_llm`/`_guard_llm_blueprint`
- `backend/alphaloom/api/schemas.py`——`CustomNodeIn`（`{"source": str}`）
- `backend/tests/test_node_sandbox.py`——37 个测试，覆盖合法注册、恶意源码矩阵、循环上限、盖章伪造防线、畸形 PinType、受限 ctx 逃逸对抗用例

---

## 6. 回测引擎与模拟经纪商

### 6.1 概览：数据 → 编译图 → 逐 bar 驱动

回测子系统由三部分协同构成：`backend/alphaloom/backtest/runner.py` 中的 `run_backtest()` 作为**驱动主循环**，负责把历史 K 线一根根喂给已编译的节点图；`backend/alphaloom/brokers/paper.py` 的 `PaperBroker` 作为**模拟撮合与账本**，负责订单撮合、持仓、权益曲线与统计口径；`backend/alphaloom/brokers/base.py` 定义三个共享数据结构 `Order` / `Fill` / `Position`，作为经纪商接口的最小公共契约。三者通过 `RunContext`（`alphaloom.runtime.context`）和 `Engine`（`alphaloom.runtime.engine`）与图运行时相连，向上再由 `RunService`（`backend/alphaloom/api/service.py`）包装成一次异步的 HTTP/WS 任务，落盘到 `RunsStore` 和 `Recorder`。

这一层刻意保持"确定性优先"：数据源、经纪商、图求值三者都是纯函数式地按 bar 推进，没有真实网络 I/O（对比第 7 节的实盘 `LiveSession`），因此同一份 `.loom` 蓝图 + 同一份历史数据必然得到逐比特一致的回测报告——这也是 `CostCertificate.deterministic_ratio` 和 `test_preset_blueprints_compile` 里断言 `deterministic_ratio == 1.0` 的意义所在（详见 6.5）。

### 6.2 `run_backtest()`：主循环与时序契约

`run_backtest()`（`backend/alphaloom/backtest/runner.py:35-99`）签名：

```python
def run_backtest(bp: BlueprintSpec, source: DataSource, *, inst: str, bar: str,
                 start_ms: int | None = None, end_ms: int | None = None,
                 initial_cash: float = 10_000.0, fee_rate: float = 0.0005,
                 record_dir=None, run_id: str | None = None, breakpoints=None,
                 on_pause=None, on_bar=None, llm=None, should_stop=None) -> BacktestReport
```

驱动步骤（对应源码 `runner.py:48-99`）：

1. **编译蓝图**：`compiled = compile_blueprint(bp, bars_per_day=86_400_000 // bar_ms)`。`bars_per_day` 用于成本证书里"日 token 上限"的估算（见 6.5），因此不同 `bar` 周期（如 `1m` vs `1H`）编译出的 `daily_token_ceiling` 不同。若 `compiled.ok` 为假，立刻 `raise CompileFailed(compiled.errors)`，回测在真正跑数据之前就短路失败（见 6.4）。
2. **构造运行期对象**：新建 `PaperBroker(initial_cash, fee_rate)`；若传入 `record_dir`，同时创建 `Recorder(f"run_{run_id}.sqlite")` 用于逐节点 I/O 落盘（trace 回放，见 6.5 与第 9 节 Recorder 相关内容）；组装 `RunContext(clock=SimClock(), run_id=run_id, broker=broker, recorder=recorder)`，并挂上 `ctx.llm`（默认 `None`）与 `ctx.audit = AuditLog()`。
3. **实例化节点**：`instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}`，再构造 `Engine(compiled, instances, ctx, breakpoints=..., on_pause=on_pause)`。`breakpoints="all"` 时以 `set(compiled.order)`（图的全部节点）作为断点集合传给 `Engine`，实际是否暂停由外层 `BreakBridge`（`api/service.py`）按用户断点集合过滤——引擎本身只知道"全断点"，暂停语义在桥接层实现。
4. **主循环**（核心时序契约，见注释 `runner.py:40-41`）：

```python
for candle in source.iter_candles(inst, bar, start_ms, end_ms):
    if should_stop is not None and should_stop():
        break
    broker.on_bar(candle)              # 先撮合上一根的挂单/止损并 mark
    engine.step(BarEvent(candle, bar_ms))
    ...
```

   **每根 bar 先 `broker.on_bar()` 再 `engine.step()`**：`broker.on_bar(candle)` 用**本根开盘价**撮合上一根 bar 决策提交的挂单（"次 bar 开盘成交"语义的执行端），随后才让 `engine.step()` 跑本根的策略节点、可能产生新的 `submit()`。这样保证策略在决策时看到的是"已经收盘确定"的当前 bar，而实际成交永远发生在下一根开盘，避免用未来价格撮合当前决策（look-ahead bias）。
5. **停止条件**：`should_stop`（由 `BreakBridge.stopped()` 提供）在每根 bar 开始前检查，命中则 `break`，循环在 bar 边界上干净退出——`test_backtest_can_stop_on_bar_boundary` 验证了跑够 10 根后 `report.bars == 10`。
6. **进度回调 `on_bar`**：每根 bar 处理完调用一次，payload 形如：

```python
{"idx": bars - 1, "ts": candle["ts"], "close": candle["close"],
 "equity": broker.equity(), "active": compiled.order,
 "fills": [f.__dict__ for f in broker.fills[fills_seen:]]}
```

   `fills` 字段只切出"本 bar 新增的成交"（`broker.fills[fills_seen:]`），供 `RunService` 通过 WebSocket 推送逐 bar 进度（`api/service.py` 里的 `on_bar_event` 把它包成 `{"type": "bar", **payload}` 送进 `sink`）。
7. **收盘强平（EOD close）**：数据耗尽后，如果仍有未平仓位且经纪商未熔断（`abs(broker.position().qty) > 1e-12 and not broker.halted`），用最后一根收盘价强制submit 一笔反向市价单平仓，再手工 `on_bar()` 一次一个虚拟的、OHLC 全等于收盘价、成交量为 0 的"结算 bar"来撮合它：

```python
px = float(last_candle["close"])
qty = broker.position().qty
broker.submit(Order(side="sell" if qty > 0 else "buy", qty=abs(qty), tag="eod_close"))
broker.on_bar({"ts": int(last_candle["ts"]) + bar_ms, "open": px, "high": px,
               "low": px, "close": px, "volume": 0.0})
del broker.equity_curve[bars:]     # 结算 bar 不入权益曲线（长度=数据根数）
```

   源码注释明确交代了设计动机：这是"回测惯例"——如果残仓一直拖到数据末尾都不结算，策略的 `num_trades` / `win_rate` 等统计口径会失真（`# Task 12 实测发现，sanctioned`，即这是经过验证后有意为之的行为，非临时补丁）。强平后立刻 `del broker.equity_curve[bars:]` 把这根虚拟结算 bar 造成的权益点截掉，保证 `len(equity_curve) == bars`（与真实历史 bar 数一致），`test_ema_cross_end_to_end` 里 `assert len(report.equity_curve) == 600` 印证了这一点。
8. **收尾**：`finally` 块里若有 `recorder` 则 `recorder.close()`，保证 SQLite trace 文件正确落盘/关闭（即便循环中途异常）。

### 6.3 `BacktestReport`：一次回测的最终产物

```python
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
```

- `bars`：真正跑过的 K 线根数（可能因 `should_stop` 提前小于数据总量）。
- `summary`：`broker.summary()` 的结果，见 6.3.1。
- `certificate`：`compiled.certificate.to_dict()`，即编译期算出的 `CostCertificate`（见 6.5），与回测执行本身无关，但一并附在报告里方便前端/评测同时展示"策略跑得怎样"和"策略静态成本画像"。
- `equity_curve` / `fills`：分别是 `broker.equity_curve`（`(ts, equity)` 元组列表）和 `[f.__dict__ for f in broker.fills]`（`Fill` dataclass 展平成 dict 的列表）。
- `recording_path`：若启用了 Recorder，则是落盘的 `run_{run_id}.sqlite` 路径；否则为 `None`。

#### 6.3.1 `summary()` 的统计口径

`PaperBroker.summary()`（`paper.py:106-123`）：

| 字段 | 计算方式 | 说明 |
|---|---|---|
| `net_pnl` | `equity() - initial_cash`，四舍五入到 8 位小数 | 总盈亏（现金+持仓市值 相对初始资金） |
| `return_pct` | `(equity()/initial_cash - 1) * 100`，4 位小数 | 收益率百分比 |
| `max_drawdown` | 遍历 `equity_curve` 逐点算 `(peak - v)/peak` 的最大值 | 基于权益曲线的最大回撤 |
| `num_trades` | `len(self._round_trips)` | 完整"往返"（开仓到平仓）笔数，不是成交(fill)笔数 |
| `win_rate` | 盈利往返数 / 总往返数 | 无往返时记 0.0 |
| `profit_factor` | 盈利之和 / 亏损之和绝对值 | 无亏损但有盈利时记 `inf`，无往返记 0.0 |
| `halted` / `halt_reason` | `broker._halted` / `broker._halt_reason` | 是否被熔断（如 `kill_switch` 节点触发）及原因 |

注意 `num_trades` 统计的是**平仓事件**（`_round_trips` 列表增长的次数），而非 `Fill` 记录数——一次开仓和一次平仓各是一条 `Fill`，但只有平仓的净盈亏才计入 `_round_trips`。这一区分在 6.3.3 的反手场景里尤其重要。

### 6.4 `Order` / `Fill` / `Position`：经纪商的最小公共数据模型

`backend/alphaloom/brokers/base.py` 定义了三个 frozen dataclass（`Position` 除外，因为它需要原地可变）：

```python
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

设计要点：

- `Order` 目前只支持 `kind="market"`（源码注释 `# D1 仅 market`，表明这是分阶段交付里第一阶段的范围收敛，限价单等留给后续版本）。`stop` 字段允许下单时**附带止损价**，由经纪商在后续每根 bar 自动检查触发（见 6.3.2），而不需要策略图自己再画一条止损分支。`tag` 是自由文本标记（如 `"stop"`、`"eod_close"`），用于回放/前端区分成交来源。
- `Fill` 是不可变的成交回执，`fee` 是该笔成交扣除的手续费金额（已经是货币单位，不是费率）。
- `Position.qty` 用**有符号数**表示方向（正数=多头，负数=空头），这使得后续"反手"逻辑可以统一用加法处理，不需要区分多空写两套分支。

`base.py` 本身不定义抽象基类/接口（没有 `Broker(ABC)`），`PaperBroker` 是目前唯一实现。它提供了 `Order`/`Fill`/`Position` 这套词汇表，未来若增加 OKX demo 或其它真实经纪商适配器，也应复用这套语义；当前第 7 节的 Live 会话仍然使用 `PaperBroker`，不做交易所账户下单。

### 6.5 `PaperBroker`：撮合、计费与账本

#### 6.5.1 生命周期状态

```python
def __init__(self, initial_cash=10_000.0, fee_rate=0.0005):
    self.cash = initial_cash
    self.initial_cash = initial_cash
    self.fee_rate = fee_rate
    self._pos = Position()
    self._pending: list[Order] = []
    self.fills: list[Fill] = []
    self.equity_curve: list[tuple[int, float]] = []
    self._round_trips: list[float] = []
    self.closed_trades: list[dict] = []
    self._entry_cost = 0.0
    self._halted = False
    self._halt_reason = ""
    self._last_close = 0.0
```

- `_pending`：当前 bar 内被 `submit()` 提交、尚未撮合的挂单队列，在下一次 `on_bar()` 开头统一清空撮合。
- `_entry_cost`：跟踪当前持仓的"累计已付开仓手续费"，用于平仓时把手续费按比例计入净盈亏（见 6.3.2）。
- `closed_trades`：专为 D3 反思闭环（ReflectorNode）开的接缝——源码注释写明"每笔往返平掉后追加 `{ts, pnl, entry_side}`，供 ReflectorNode 读 `ctx.broker.closed_trades` 拿最近平仓 pnl（不占决策引脚）"，即这是一条绕开图里类型化引脚、直接从 broker 读取最近交易结果的旁路通道，供 LLM 反思节点在下一根 bar 生成"上一笔交易表现如何"的上下文。
- `_halted` / `_halt_reason`：熔断标志，由 `halt(reason)` 设置（典型调用方是 `kill_switch` 节点），一旦置位：`submit()` 直接拒绝新单（返回 `False`），且 `halt()` 本身会清空 `_pending` 挂单队列，但**不平掉已有持仓**——源码注释"熔断=冻结：清挂单、拒新单、持仓保留现场"，即熔断是冻结当前状态而非强制清仓，保留现场便于事后复盘触发原因。

#### 6.5.2 `submit()` 与 `on_bar()`：次 bar 开盘成交

```python
def submit(self, order: Order) -> bool:
    if order.qty <= 0:
        return False
    if self._halted:
        return False
    self._pending.append(order)
    return True
```

`submit()` 只做合法性检查（数量为正、未熔断）并入队，不在提交时刻就撮合。真正的撮合发生在 **下一次** `on_bar()`：

```python
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
```

三件事按顺序发生：

1. **挂单撮合**：把上一根积累的 `_pending` 队列全部按**本根开盘价 `o`** 成交（市价单语义）。这正是 6.2 节强调的"次 bar 开盘成交"：策略在 bar N 收盘后决策提交订单，订单在 bar N+1 的开盘价成交，避免用同一根 K 线的收盘价既做决策依据又做成交价格。
2. **止损检查**：多头持仓（`p.qty > 0`）若本根 `low` 触及 `p.stop`，以**止损价本身**（而非最低价）成交一笔反向市价单平仓；空头对称检查 `high` 是否触及止损。止损检查在挂单撮合**之后**执行，因此同一根 bar 内一笔新开仓的止损不会在同一根 bar 触发（除非该 bar 极端波动且新止损价恰好被同根 bar 覆盖，取决于测试用例设计，见 `test_stop_loss_triggers`：止损单独占一根 bar 才触发）。
3. **收盘价 mark 与权益曲线**：记录 `_last_close` 供 `equity()` 计算浮动市值，并把 `(ts, equity())` 追加进 `equity_curve`——这一步无条件执行（不管本根有没有成交），因此 `len(equity_curve) == bars` 恒成立（EOD 强平那次例外调用会被 `runner.py` 显式截断，见 6.2 第 7 步）。

`equity()` 的定义很直白：

```python
def equity(self) -> float:
    return self.cash + self._pos.qty * self._last_close
```

现金 + 持仓按最近收盘价折算的浮动市值（有符号 `qty` 天然处理了多空两个方向）。

#### 6.5.3 `_fill()`：单笔撮合的核心状态机

`_fill(ts, od, price)`（`paper.py:62-104`）是整个 broker 里最稠密的一段逻辑，处理四类场景：纯开仓、纯加仓、部分平仓、反手（平仓量超过现有持仓，一笔成交内完成平旧仓+开新仓方向）。

```python
def _fill(self, ts: int, od: Order, price: float) -> None:
    fee = od.qty * price * self.fee_rate
    signed = od.qty if od.side == "buy" else -od.qty
    p = self._pos
    closing = (p.qty > 0 > signed) or (p.qty < 0 < signed)
    crossed = closing and abs(signed) > abs(p.qty)   # 反手：平掉全部旧仓并反向开新仓
    new_qty = p.qty + signed
    ...
```

- **`fee`**：`qty × price × fee_rate`——按**本笔成交名义金额**线性计费，与方向无关。
- **`closing`**：本次成交方向与当前持仓方向相反（多头收到卖单，或空头收到买单），即"在减少或反转仓位"。
- **`crossed`**（反手）：`closing` 为真且新成交量的绝对值**超过**现有持仓量——也就是一笔单子不仅平光旧仓，还多出方向相反的新开仓部分。

**平仓分支**（`closing=True`）里，手续费与已付开仓成本都需要**按比例拆分**成"归属于被平掉那部分"和"归属于剩余/新开部分"：

```python
closed_qty = min(abs(p.qty), abs(signed))
close_fee = fee * (closed_qty / abs(signed)) if signed else 0.0
open_fee = fee - close_fee
entry_cost_closed = (self._entry_cost * (closed_qty / abs(p.qty)) if p.qty else 0.0)
entry_cost_remaining = max(0.0, self._entry_cost - entry_cost_closed)
pnl = (price - p.avg_price) * closed_qty * (1 if p.qty > 0 else -1)
net_pnl = pnl - close_fee - entry_cost_closed
self._round_trips.append(net_pnl)
self.closed_trades.append(
    {"ts": ts, "pnl": net_pnl, "entry_side": "long" if p.qty > 0 else "short"})
```

- `close_fee` / `open_fee`：把本笔总手续费 `fee` 按"平仓量 / 本次成交总量"拆成两半——只有 `close_fee` 才计入这笔往返的净盈亏，`open_fee`（反手时才非零）留给新开仓位继续累积。
- `pnl`：`(成交价 - 持仓均价) × 平仓量 × 方向符号`，是该笔平仓的毛盈亏。
- `net_pnl = pnl - close_fee - entry_cost_closed`：毛盈亏扣除"本次平仓手续费"和"当初开这部分仓位摊销的开仓手续费"，得到真正记入 `_round_trips` 与 `closed_trades` 的净盈亏。这正是 `test_partial_close_allocates_entry_and_exit_fees_proportionally` 验证的行为：以 `fee_rate=0.01` 开 10 手 @100、平 4 手 @110 时，`closed_trades[-1]["pnl"] == pytest.approx(31.6)`（毛利 `(110-100)*4=40`，减去开仓摊销费 `4.0` 和平仓费 `4.4`）。
- `entry_cost` 的三路归宿：反手（`crossed`）时剩余 `_entry_cost` 归零、改记为反手新开部分的 `open_fee`；恰好平光（`new_qty == 0`）时清零；否则（部分平仓）保留 `entry_cost_remaining` 供后续继续平仓时摊销。

**均价更新**（非平仓，即新增同方向仓位，或首次开仓）：

```python
if not closing and (p.qty == 0 or abs(new_qty) > abs(p.qty)):
    total = p.avg_price * abs(p.qty) + price * abs(signed)
    p.avg_price = total / (abs(p.qty) + abs(signed))
```

标准的加权平均成本法：新均价 = (原均价×原量 + 本次成交价×本次量) / 总量。

**反手场景的特殊处理**（`test_reversal_resets_avg_price` 与源码注释直接对应）：

```python
if crossed:
    p.avg_price = price          # 反手剩余部分按本次成交价计新仓成本
    p.stop = od.stop             # 反手=新仓位：不继承旧方向止损（Task 11 TDD 发现的真 bug）
if new_qty == 0:
    p.avg_price = 0.0
    p.stop = None
elif od.stop is not None:
    p.stop = od.stop
p.qty = new_qty
self.cash -= signed * price + fee
self.fills.append(Fill(ts, od.side, od.qty, price, fee, od.tag))
```

反手（一笔卖单把多头 2 手直接打成空头 1 手，如 `test_reversal_resets_avg_price`：买 2 @10 后卖 3 @12）时：

- 新持仓的 `avg_price` **不是**继续用加权公式，而是直接取本次成交价 `price`——因为反手之后的仓位方向已经变了，"旧均价"对新方向没有意义，必须以反手那一刻的成交价作为新仓位的建仓成本。
- 新持仓的 `stop` 也**不继承**旧方向的止损单，而是采用本次成交单 `od` 自带的 `stop`（大概率是 `None`，除非策略在反手单上显式指定了新止损）。源码注释直白地写明这是"Task 11 TDD 发现的真 bug"——即最初实现里反手后错误地继承了旧方向止损价，会导致新仓位在错误的价位止损（旧的多头止损价对空头仓位毫无意义甚至方向反了），后来通过测试驱动开发发现并修复。
- `num_trades` 只计"被平掉"的那部分为一笔往返：`test_reversal_resets_avg_price` 里买 2 手、卖 3 手（2 手平仓 + 1 手反手开空），`summary()["num_trades"] == 1`，反手新开的 1 手空头仓位本身不算一笔已完成的往返（它还没平仓）。

最后，无论哪个分支，都统一执行：

- `self.cash -= signed * price + fee`：现金按有符号成交量结算价款并扣除手续费（买入减少现金、卖出增加现金，手续费无论方向都是现金流出）。
- `self.fills.append(Fill(...))`：无条件追加一条成交回执（含止损触发和 EOD 强平在内，所有走 `_fill()` 的路径都会留下 `Fill` 记录）。

#### 6.5.4 止损单与挂单的存续期

`Order.stop` 只在**开仓/加仓**类成交里被写入 `Position.stop`（`elif od.stop is not None: p.stop = od.stop`），并在每根 `on_bar()` 开头检查触发。止损触发时用 `Order(side=反向, qty=p.qty, tag="stop")` 以**止损价**（不是当根最低/最高价）成交——`test_stop_loss_triggers` 验证了这一点：止损价 8.0 被触发时，即使当根 `low=7.5`，`exit_fill.price == 8.0`，模拟的是"止损单以止损价成交"的理想化假设，不模拟滑点。

### 6.6 `CompileFailed` 与运行期错误处理

`CompileFailed`（`runner.py:19-22`）是唯一由 `run_backtest()` 主动抛出的领域异常：

```python
class CompileFailed(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} compile error(s)")
```

`errors` 是 `compile_blueprint()` 返回的 `list[CompileError]`，`CompileError`（`graph/errors.py`）本身是：

```python
@dataclass(frozen=True)
class CompileError:
    code: str
    message: str
    node_id: str | None = None
    port: str | None = None
    fix_hint: str | None = None   # 面向 LLM 的修复提示
```

`fix_hint` 字段的注释表明这是"结构化反馈环境的一部分"——编译错误不只是给人看的报错文案，还设计成可以喂回 LLM（例如 copilot 节点自动修图）的结构化提示。

在 `run_backtest()` 层面，`CompileFailed` 在**进入主循环之前**就可能抛出（`compiled.ok` 为假时），因此编译失败的回测连一根 K 线都不会跑，`bars=0` 意义上的部分结果根本不会生成。而循环体本身没有 `try/except` 包裹节点执行——一旦 `engine.step()` 内部抛出未捕获异常（比如自定义节点沙箱代码出错、broker 状态非法等），会直接从 `run_backtest()` 冒泡出去，是 `finally` 块负责保证 `recorder.close()` 一定执行，异常本身继续向上传播给调用方处理（见 6.7）。

### 6.7 `RunService`：从回测调用到 run 生命周期与持久化

`run_backtest()` 本身是同步阻塞函数，异步化、状态跟踪、持久化都在 `backend/alphaloom/api/service.py` 的 `RunService` 里完成，它是 HTTP 层（`POST /api/runs`）与回测引擎之间的适配器。

#### 6.7.1 启动与线程模型

```python
def start(self, bp, params, sink, run_id=None) -> str:
    run_id = run_id or uuid.uuid4().hex[:12]
    bridge = BreakBridge(params.get("breakpoints", []), sink)
    with self._lock:
        self._prune_threads()
        if len(self._threads) >= self.max_active_runs:
            raise RuntimeError("too many active runs; wait for one to finish")
        ...
        t = threading.Thread(target=self._worker, args=(run_id, bp, params, sink, bridge, llm_snapshot), daemon=True)
        self.store.create(run_id, bp.id, dumps_loom(bp), json.dumps(params), int(time.time() * 1000))
        self._bridges[run_id] = bridge
        self._threads[run_id] = t
    t.start()
    return run_id
```

每个 run 在独立的 daemon 线程里跑（`_worker`），`max_active_runs`（默认 4）限制同时活跃的回测线程数，超限时 `start()` 直接抛 `RuntimeError`，HTTP 层（`api/app.py` 的 `run_start`）把它转成 `429`。`RunsStore.create()` 在**启动线程之前**同步写入一条 `status='running'` 的记录，保证 `run_id` 一返回给调用方，`GET /api/runs/{run_id}` 立刻就能查到"running"状态（不存在竞态窗口）。

#### 6.7.2 `_worker`：状态机转换

```python
def _worker(self, run_id, bp, params, sink, bridge, llm):
    sink = _safe_sink(sink)
    sink({"type": "status", "status": "running"})
    source = None
    try:
        source = SQLiteMarketData(self.db_path)
        ...
        report = run_backtest(bp, source, inst=params["inst"], bar=params["bar"],
                              start_ms=params.get("start_ms"), end_ms=params.get("end_ms"),
                              initial_cash=params.get("cash", 10_000.0),
                              fee_rate=params.get("fee_rate", 0.0005),
                              record_dir=self.record_dir, run_id=run_id,
                              breakpoints="all" if want_break else None,
                              on_pause=bridge.on_pause if want_break else None,
                              on_bar=on_bar_event, llm=llm, should_stop=bridge.stopped)
        status = "halted" if report.summary.get("halted") else "completed"
        payload = {"run_id": report.run_id, "blueprint_id": report.blueprint_id,
                   "bars": report.bars, "summary": sanitize(report.summary),
                   "certificate": report.certificate,
                   "equity_curve": report.equity_curve, "fills": report.fills}
        self.store.set_status(run_id, status, report_json=json.dumps(payload),
                              recording_path=report.recording_path)
        sink({"type": "done", "report": payload})
    except CompileFailed as cf:
        self.store.set_status(run_id, "failed", error=json.dumps([e.to_dict() for e in cf.errors]))
        sink({"type": "error", "message": "compile failed"})
    except Exception as exc:  # Engine 崩溃契约：任何异常 → failed，实例弃用
        self.store.set_status(run_id, "failed", error=str(exc))
        sink({"type": "error", "message": str(exc)})
    finally:
        if source is not None:
            try: source.close()
            except Exception: pass
        with self._lock:
            self._bridges.pop(run_id, None)
            self._threads.pop(run_id, None)
```

状态迁移路径：

| 触发条件 | 落地状态 | 说明 |
|---|---|---|
| `RunsStore.create()` 调用时 | `running`（初始值，硬编码在 SQL 里） | run 一开始注册即为 running |
| `run_backtest()` 正常返回且 `summary["halted"]` 为假 | `completed` | 常规跑完 |
| `run_backtest()` 正常返回但 `summary["halted"]` 为真 | `halted` | 策略图内 `kill_switch` 触发熔断，回测本身没有异常，但业务语义上是提前终止 |
| 抛出 `CompileFailed` | `failed`，`error` 字段写入 `[e.to_dict() for e in cf.errors]` 的 JSON | 编译期错误列表整体保留 |
| 抛出其它任意 `Exception` | `failed`，`error` 字段写入 `str(exc)` | 源码注释"Engine 崩溃契约：任何异常 → failed，实例弃用"——即引擎/节点抛出的任何未预期异常都统一归入失败，不再重试或恢复这个 run 实例 |

无论走哪条路径，`finally` 块都保证 `SQLiteMarketData` 连接关闭、`BreakBridge` 和线程句柄从 `RunService` 内部字典里摘除（避免线程泄漏和断点桥悬挂）。同时通过 `sink` 参数把状态变化实时推送到 WebSocket 客户端（`{"type": "status"}` / `{"type": "bar"}` / `{"type": "done"}` / `{"type": "error"}` 四种事件），`_safe_sink` 包装保证 sink（即 WS 推送）本身的异常绝不影响 run 的执行结果。

#### 6.7.3 持久化：`RunsStore` 与 `Recorder` 的分工

两者职责完全不同、各自独立的 SQLite 文件：

- **`RunsStore`**（`backend/alphaloom/api/runs_store.py`）：run **级别**的元数据与终态结果索引表，单表 `runs`：

```sql
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, blueprint_id TEXT, blueprint_json TEXT,
  params_json TEXT, status TEXT, report_json TEXT, error TEXT,
  recording_path TEXT, created_ms INTEGER)
```

  `create()` 插入时状态硬编码为 `'running'`；`set_status()` 用 `COALESCE(?, report_json)` 这种写法保证"只更新传入的非 None 字段，其余保持原值"（例如失败时只传 `error` 不传 `report_json`，不会把已有报告字段冲掉）。连接以 `check_same_thread=False` 加显式 `threading.Lock()` 序列化访问，源码注释"D2 单进程足够"，即这是面向单进程部署场景的简化方案。`report_json` 存的正是 6.7.2 里 `_worker` 组装的 `payload`（`run_id`/`blueprint_id`/`bars`/`summary`/`certificate`/`equity_curve`/`fills`），`GET /api/runs/{run_id}` 直接读出并反序列化为 `report` 字段返回。

- **`Recorder`**（`backend/alphaloom/runtime/recorder.py`）：**bar 级、节点级**的输入输出全量 trace，独立的 `run_{run_id}.sqlite` 文件（路径由 `runner.py` 里 `record_dir/f"run_{run_id}.sqlite"` 决定），单表 `node_io`：

```sql
CREATE TABLE IF NOT EXISTS node_io (
  run_id TEXT, event_idx INTEGER, ts INTEGER, node_id TEXT,
  inputs_json TEXT, outputs_json TEXT)
CREATE INDEX IF NOT EXISTS idx_node_io ON node_io(run_id, node_id, event_idx)
```

  `record()` 由 `Engine` 在每根 bar 对每个节点求值后调用（不在 `runner.py`/`paper.py` 里直接触碰），`to_json()` 对 `Stamped` 类型值做了特殊编码（`{"__stamped__": as_of, "value": ...}`），保证类型化引脚值可逆序列化。`fetch(run_id, node_id=None)` 按 `event_idx, rowid` 排序取回，供 `GET /api/runs/{run_id}/trace` 端点做逐节点回放调试——这是与 `RunsStore` 的 `report_json`（只存最终汇总）完全互补的另一条数据通路：一个答"这次回测最终赚了多少钱"，一个答"某个节点在第 137 根 bar 的输入输出具体是什么"。

`RunService` 把二者粘合：`_worker` 里 `run_backtest(..., record_dir=self.record_dir, run_id=run_id, ...)` 让 `runner.py` 内部创建 `Recorder` 并传给 `RunContext`（`ctx.recorder`），回测结束后再把 `report.recording_path` 写回 `RunsStore.set_status(..., recording_path=...)`，使得 `runs` 表里的一条记录能索引到对应的 trace 文件路径。

### 6.8 与相邻子系统的接口关系

- **上游（图编译/运行时）**：`run_backtest()` 依赖 `compile_blueprint()`（第 4/5 节涉及的图编译器）产出的 `CompiledGraph`（含 `nodes`、`order`、`certificate`），以及 `Engine.step(BarEvent)` 逐 bar 推进节点求值；`PaperBroker` 通过 `RunContext.broker` 被节点访问（例如 `execute_order` 节点调用 `ctx.broker.submit()`，`kill_switch` 节点调用 `ctx.broker.halt()`，`ReflectorNode` 读 `ctx.broker.closed_trades`）。
- **下游（数据源）**：`source: DataSource` 是一个抽象接口（`data/source.py`），`iter_candles(inst, bar, start_ms, end_ms)` 产出字典形式的 K 线（`{"ts","open","high","low","close","volume"}`）；回测场景下具体实现是 `SQLiteMarketData`（历史数据），实盘场景下（第 7 节）是轮询 OKX 的等价实现，两者共享同一个 `run_backtest()`/`PaperBroker` 撮合逻辑，只是"喂 K 线"的来源不同——这也是为什么源码注释称 D1 只有 SQLite、"D4 加 OKX 实时"，回测与实盘在撮合层是同一套代码路径。
- **上层服务（`RunService`/HTTP/WS）**：`RunService._worker` 是唯一调用 `run_backtest()` 的生产路径，把同步调用包装成后台线程 + 状态机 + 双通道持久化（`RunsStore` 存终态摘要、`Recorder` 存逐 bar trace），并通过 `sink` 回调把 `on_bar`/`on_pause` 事件转成 WebSocket 推送，供前端渲染实时权益曲线和断点调试面板。
- **测试证据**：`backend/tests/test_backtest_e2e.py` 用真实预置蓝图（`blueprints/ema_cross.loom`、`blueprints/breakout_scenario.loom`）跑通"编译 → 600 根 K 线回测 → summary 含 `num_trades`/`net_pnl` → trace 文件存在"的完整链路；`backend/tests/test_paper_broker.py` 用最小 4 字段 K 线（`{"ts","open","high","low","close","volume"}`）针对性覆盖了次 bar 开盘成交、止损触发、手续费计提、熔断拒单、反手均价重置、部分平仓费用分摊等具体数值场景，是本节所有数值行为描述的直接依据。

---

## 7. 实时纸上交易会话

### 7.1 定位与设计意图

Live Desk 子系统（`backend/alphaloom/api/live.py`）实现的是一种"纸上实盘"（paper-live）模式：它不断从真实交易所 OKX 拉取最新 K 线，把新收到的每一根 bar 依次喂给**与回测完全相同**的已编译图（compiled graph）引擎，让策略在真实行情节奏下增量运行，同时把成交全部路由到 `PaperBroker`（纸上撮合，不接触真实资金）。这与 D2/D3 阶段的离线回测（参见 `alphaloom/backtest/runner.py` 和 `RunService`）共享同一套编译产物（`CompileResult`）、同一套节点实例（`create_instance`）、同一套 `Engine.step()` 驱动方式，唯一的区别是事件源：回测从 SQLite 里预先落好的历史 K 线批量 `run()`，Live 会话则是"轮询 → 拿到新 bar → `engine.step()` 一次"的增量节奏，且这些新 bar 会被写回同一份行情 SQLite（`data/source.py` 的注释写明"多市场行情抽象：D1 只有 SQLite 实现；D4 加 OKX 实时"），因此实盘抓到的数据本身也成为可复用的历史素材。

设计上刻意让 Live 和 Backtest 复用同一条编译/执行链路，这样"图在回测里怎么表现，在实盘纸上会话里就应该怎么表现"——不存在两套语义、两套节点实现。Live 会话在架构里被当成"engine 的另一种喂料方式"，而不是另一套独立的执行引擎。

### 7.2 LiveParams：可配置旋钮

`LiveParams`（`backend/alphaloom/api/live.py:90-134`）是驱动一次 Live 会话的全部参数集合，通过 `from_dict` 从 HTTP 请求体（`LiveStartIn`，`backend/alphaloom/api/schemas.py:36-48`）构造，构造时即做边界钳制（clamp），避免异常输入把线程跑飞：

| 字段 | 默认值 | 钳制范围 | 含义 |
|---|---|---|---|
| `inst` | 必填 | — | 交易对，如 `BTC-USDT` |
| `bar` | `"1m"` | 需在 `_BARS` 白名单内（app.py 校验） | K 线周期，决定 `bar_to_ms` 换算 |
| `cash` | `10_000.0` | — | `PaperBroker` 初始资金 |
| `fee_rate` | `0.0005` | — | 纸上撮合手续费率 |
| `poll_ms` | `5_000` | `max(250, …)` | 轮询 OKX 的最小间隔 |
| `analysis` | `True` | — | 是否启用 LLM 分析旁路（sidecar） |
| `analysis_every` | `1` | `max(1, …)`，schema 侧 `≤100` | 每隔多少根 bar 触发一次 LLM 分析 |
| `context_bars` | `30` | `[1, 120]` | 喂给分析旁路的最近 K 线窗口长度 |
| `max_bars` | `None`（不限） | `None` 或 `≥1`，schema 侧 `≤10_000` | 达到多少根 bar 后自动结束会话 |
| `fetch_limit` | `5` | `[1, 100]` | 每次向 OKX 请求的 candle 条数上限 |
| `ws_wait_ms` | `0` | `[0, 30_000]` | 首根 bar 处理前的人为等待（便于前端来得及订阅 WS 再收到第一条事件） |

`to_dict()`（第 120-134 行）额外附带 `"mode": "live"`，用于和回测的 `params_json` 区分。这套参数经由 FastAPI 的 `LiveStartIn` Pydantic 模型（`schemas.py:36-48`）在网络边界上二次约束（例如 `poll_ms` 用 `Field(ge=250, le=300_000)`），`LiveParams.from_dict` 内部再做一次防御性 clamp，双重保险。

### 7.3 LiveService 的会话生命周期

#### 7.3.1 `start()`：线程创建与并发上限

```python
def start(self, bp: BlueprintSpec, params: dict, sink,
          session_id: str | None = None) -> str:
    live_params = LiveParams.from_dict(params)
    session_id = session_id or uuid.uuid4().hex[:12]
    state = LiveSessionState(session_id=session_id)
    with self._lock:
        self._prune()
        if len(self._sessions) >= self.max_active_sessions:
            raise RuntimeError("too many active live sessions; stop one first")
        llm_snapshot = self.llm
        self.store.create(session_id, bp.id, dumps_loom(bp),
                          json.dumps(live_params.to_dict()),
                          int(time.time() * 1000))
        thread = threading.Thread(
            target=self._worker,
            args=(session_id, bp, live_params, sink, state.stop, llm_snapshot),
            daemon=True,
        )
        state.thread = thread
        self._sessions[session_id] = state
    thread.start()
    return session_id
```
（`live.py:249-270`）

关键点：

- **一个会话一个后台线程**（`daemon=True`），线程主体是 `_worker`，通过 `threading.Event`（`state.stop`）实现协作式停止。
- **`max_active_sessions` 并发上限**（构造函数默认 `3`，`live.py:226,232`）：`start()` 前先调用 `_prune()` 清掉已经结束（`thread.is_alive()` 为假）的会话记录，再检查是否已达上限；超限直接抛 `RuntimeError`，FastAPI 层（`app.py:334-337`）把它转成 HTTP `429`。这防止无限制地拉起轮询 OKX 的线程。
- **`session_id` 与 run_id 复用同一命名空间**：`LiveService` 持有的 `self.store` 就是 `RunsStore`（`backend/alphaloom/api/runs_store.py`），与普通回测 `RunService` 共用同一张 `runs` 表——`store.create(session_id, bp.id, dumps_loom(bp), json.dumps(live_params.to_dict()), …)` 直接把 Live 会话当作一条 `status='running'` 的 run 记录写入。因此 `/api/runs/{id}` 等既有查询接口天然也能查到 Live 会话（`report_json` 结构与回测报告一致，见 7.3.3）。
- **`llm_snapshot = self.llm`**：在持锁窗口内把当前 `self.llm`（可通过 `set_llm()` 热切换）拍一份快照传给 worker 线程，避免运行期间外部切换 LLM 客户端造成同一会话前后使用不同模型。

#### 7.3.2 `_worker` 轮询主循环

`_worker`（`live.py:282-435`）是会话的全部执行逻辑所在，大致分为三段：初始化、轮询循环、收尾。

**初始化**：

1. 通过 `compile_blueprint(bp, bars_per_day=86_400_000 // bar_ms)` 编译蓝图，编译失败直接抛 `CompileFailed`（外层 `except CompileFailed` 分支会把错误码结构化写入 `store.set_status(..., "failed", error=...)` 并发一条 `{"type": "error", ...}` 事件）。
2. 打开与回测同源的行情库 `SQLiteMarketData(self.db_path)`（新抓到的 K 线也会写回这里，见下）。
3. 为该会话单独建一个 SQLite 录制文件 `record_dir/live_{session_id}.sqlite`，同时供 `Recorder`（节点级 trace 记录，和回测共用同一 `Recorder` 类）和 `LiveAnalysisStore`（LLM 分析记录）使用——两者写同一个文件、不同的表。
4. 构造 `PaperBroker(initial_cash=params.cash, fee_rate=params.fee_rate)`、`RunContext(clock=SimClock(), run_id=session_id, broker=broker, recorder=recorder)`，把快照下来的 `llm` 挂到 `ctx.llm`、并附加 `ctx.audit = AuditLog()`（沙箱节点审计日志，和回测一致）。
5. `instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}` 构造节点实例，`engine = Engine(compiled, instances, ctx)`。
6. 挂一个 `after_node` 钩子把每个节点最新一次输出（`Stamped` 解包后 `sanitize()` 过）缓存进 `latest_outputs: dict[node_id -> dict]`——这份缓存正是 LLM 分析旁路读取"当前各节点状态"的数据源。

**轮询循环**（`while not stop.is_set()`）：

```python
candles = self.candle_fetcher(params.inst, params.bar, last_ts, params.fetch_limit)
```

- 拉取失败：计数 `fetch_errors`，用指数退避（`min(poll_ms * 2**(n-1), 30_000)`）重试并广播 `{"type":"status","status":"retrying",...}`；超过 5 次连续失败则直接向上抛出，触发外层 `except Exception` 收尾（会话失败退出）。
- 拉取恢复：广播一条 `status: running`、`message: "live fetch recovered"`，清零错误计数。
- **按时间戳去重**：`new_candles` 只保留 `ts` 不在 `seen_ts` 集合中、且严格大于 `last_ts` 的行，天然处理 OKX 接口可能返回的重叠/已见过的 K 线。若本轮没有新 bar，检查 `max_bars` 是否已到（到则整体结束），否则 `sleep(poll_ms/1000)` 后继续下一轮。
- 对每一根新 bar 依次：
  - 首次处理时若配置了 `ws_wait_ms`，先 sleep 一下（给前端 WebSocket 来得及连上，避免错过第一条事件）；
  - 归一化字段类型（`_normalize_candle`：ts 转 int，OHLCV 转 float）；
  - **写回行情库**：`source.insert_candles(params.inst, params.bar, [candle])` —— 这一步让实盘抓到的新数据落入与回测同一张 `candles` 表，供之后离线复盘/回放；
  - **喂给纸上经纪商**：`broker.on_bar(candle)`，更新持仓估值、`equity_curve`、检查挂单撮合；
  - **驱动引擎**：`engine.step(BarEvent(candle, bar_ms))` —— 与回测里 `engine.run(events)` 内部调用的是同一个 `step()` 方法（`backend/alphaloom/runtime/engine.py:89-129`），按拓扑序跑一遍所有节点，沙箱节点拿受限 ctx，`recorder.record(...)` 把每个节点的输入/输出落盘；
  - `recorder.flush()` 保证录制及时可查；
  - 计算本轮新增成交 `new_fills = broker.fills[fills_seen:]`；
  - 组装并通过 `sink()` 广播一条 **bar 事件**：

```python
payload = {
    "type": "bar", "mode": "live",
    "idx": bars, "ts": candle["ts"], "candle": candle,
    "close": candle["close"], "equity": broker.equity(),
    "active": compiled.order,          # 本轮参与计算的拓扑序节点 id 列表
    "fills": sanitize(new_fills),
}
sink(payload)
```
（`live.py:371-382`）

  - 若启用分析且 `bars % analysis_every == 0`，调用 `self._analyze(...)`（见 7.4），得到非 `None` 结果则再广播一条 `{"type": "analysis", **analysis}` 事件；
  - `bars += 1`；若达到 `max_bars` 则标记 `limit_reached = True` 并跳出内外两层循环。

**收尾**：根据是否是 `stop.is_set()` 主动停止还是达到 `max_bars` 判断最终 `status`（`"stopped"` 或 `"completed"`），组装报告：

```python
report = {
    "run_id": session_id, "blueprint_id": bp.id, "bars": bars,
    "summary": sanitize(broker.summary()),
    "certificate": sanitize(compiled.certificate.to_dict()),
    "equity_curve": broker.equity_curve,
    "fills": [f.__dict__ for f in broker.fills],
    "mode": "live",
}
self.store.set_status(session_id, status, report_json=json.dumps(report), recording_path=rec_path)
sink({"type": "done", "report": report})
```
这份 `report` 的结构与回测报告一致（同样带 `certificate`、`equity_curve`、`fills`），只是多了 `"mode": "live"` 标记，因此前端 `getRun(session_id)` 复用同一套报告解析代码即可。

`finally` 块确保 `recorder` / `analysis_store` / `source` 三个持有文件句柄的对象都被 `close()`（吞掉异常，避免二次报错掩盖真实原因），并把 `session_id` 从 `self._sessions` 中摘除——这也是为什么 `_prune()` 判断"线程是否存活"就能正确回收已完结的会话。

任何异常（编译失败之外）都会被最外层 `except Exception` 捕获、写入 `store.set_status(..., "failed", error=str(exc))` 并广播 `{"type":"error",...}`，保证工作线程"报告完再退出"而不是静默挂死。

#### 7.3.3 `command("stop")`

```python
def command(self, session_id: str, cmd: str) -> bool:
    if cmd != "stop":
        return False
    with self._lock:
        state = self._sessions.get(session_id)
    if state:
        state.stop.set()
        return True
    return False
```
（`live.py:272-280`）唯一支持的命令是 `"stop"`（相比回测 `RunService` 支持 `step/resume/stop` 断点调试，Live 会话没有单步/断点概念，因为它是被真实行情节奏驱动的，不能"暂停等你调试"）。`stop()` 只是把 `threading.Event` 置位，真正的退出发生在 worker 轮询循环下一次检查 `stop.is_set()` 时（最坏情况下要等到当前 `poll_ms` 或当前 bar 批次处理完）。FastAPI 路由 `/api/live/{session_id}/stop`（`app.py:340-345`）和 WebSocket 收到 `{"cmd":"stop"}` 时（`app.py:830-831`）都会调用同一个 `command()`。

### 7.4 LLM "分析旁路"（analyst sidecar）

#### 7.4.1 角色定位：只解说，不下单

`_analyze()`（`live.py:437-491`）在每 `analysis_every` 根 bar 触发一次，其系统提示词明确限定了角色边界：

```python
messages = [
    {"role": "system", "content": (
        "You are AlphaLoom's live trading analyst sidecar. You explain the "
        "running blueprint; you never create orders or bypass RiskGate. "
        "Reply with ONLY JSON containing market_state, current_gate, "
        "risk_reason, suggestion, confidence."
    )},
    {"role": "user", "content": json.dumps(input_summary, ...)},
]
```

这段提示词直接写明了它是"旁路"（sidecar）：只负责用自然语言解释当前蓝图正在做什么、风险闸门（RiskGate）处于什么状态，绝不创建订单、绝不绕过风控节点。也就是说，这个 LLM 调用完全在主执行路径（`engine.step()` → 撮合 → 风控）之外，其输出只进入 `sink()` 广播和录制表，不会反向影响 `broker`/`engine` 的任何状态——从代码结构上看，`_analyze()` 只读 `broker`、`latest_outputs`、`bp`，不写任何执行期状态，只把结果 `return` 给调用方去 `sink()`。

#### 7.4.2 输入摘要 `_analysis_input_summary`

喂给 LLM 的上下文由 `_analysis_input_summary()`（`live.py:494-520`）组装，是对当前引擎/账本状态的一次结构化快照：

```python
{
  "blueprint": {"id": bp.id, "name": bp.name},
  "bar": candle,                                   # 当前这根 K 线
  "recent_candles": recent[-30:],                  # 最近 context_bars 根（clamp 到 30 上限）
  "compiled_order": compiled_order,                # 拓扑序节点 id 列表
  "node_outputs": latest_outputs,                  # 每个节点最近一次输出（Stamped 已展开为 {as_of, value}）
  "risk_outputs": {nid: latest_outputs.get(nid) for nid in risk_nodes},        # type 含 "risk" 或 == "position_sizer"
  "reflection_memory": {nid: ... for nid in reflection_nodes},                # type 含 "reflect"/"experience"/"memory"
  "position": broker.position().__dict__,
  "fills": [f.__dict__ for f in broker.fills[-5:]],
  "closed_trades": broker.closed_trades[-5:],
  "equity": equity,
  "drawdown": drawdown,                            # (peak - equity) / peak，peak 取 equity_curve 历史最高
}
```

其中 `risk_nodes` / `reflection_nodes` 是按节点 `type` 字符串匹配筛出来的，目的是让 LLM 明确看到"风控节点这一轮的输出是什么"和"反思/记忆节点里存了什么教训"，而不是把整张图的全部节点输出一股脑塞给它。

#### 7.4.3 调用与容错

```python
prompt_hash = hashlib.sha256(json.dumps(messages, sort_keys=True, ...).encode()).hexdigest()
try:
    response = llm.chat(messages, temperature=0.1, max_tokens=500)
    text = _content(response)
    output = _extract_json(text) or {  # 兜底：模型没吐合法 JSON 时的占位结构
        "market_state": "unparsed", "current_gate": "unknown",
        "risk_reason": "LLM output was not valid JSON",
        "suggestion": text[:500], "confidence": 0.0,
    }
except ReplayMissError as exc:          # 离线回放模式下命中未录制的 prompt
    output = {
        "market_state": "offline replay miss", "current_gate": "analysis skipped",
        "risk_reason": str(exc),
        "suggestion": "Switch to live LLM mode or record this sidecar prompt.",
        "confidence": 0.0,
    }
```

- `prompt_hash` 对完整消息体做 SHA-256，既用作幂等/追溯键，也和项目里 LLM 调用记录/回放机制（`alphaloom.llm.recording`）的哈希约定一致。
- `_extract_json`（`live.py:42-59`）用括号计数的方式从模型输出文本中提取第一个完整 JSON 对象，容忍模型在 JSON 前后夹带说明文字。
- `ReplayMissError` 是"离线回放"模式下的专属异常（当 LLM 客户端处于录制回放而非真实在线模式，且这个 prompt 之前没有被录制过时抛出）；分析旁路捕获它后不让整个会话失败，而是把"这次没法分析"本身也变成一条结构化输出，附带引导用户切换到在线 LLM 或补录 prompt 的建议——这体现了分析旁路"锦上添花、不影响主流程"的设计定位：即使 LLM 完全不可用，K 线驱动、下单、风控这条主链路也照常运行。

#### 7.4.4 持久化：`LiveAnalysisStore`

`LiveAnalysisStore`（`live.py:137-213`）是一个独立的小型 SQLite 封装，表结构：

```sql
CREATE TABLE IF NOT EXISTS live_analysis (
  session_id TEXT, event_idx INTEGER, bar_ts INTEGER,
  prompt_hash TEXT, model TEXT, input_json TEXT,
  output_json TEXT, created_ms INTEGER,
  PRIMARY KEY (session_id, event_idx))
```

`record()` 用 `sanitize()`（`alphaloom.api.serialize.sanitize`，与其它子系统统一的"确保可 JSON 序列化"清洗函数）分别处理 `input_summary` 和 `output` 后 `INSERT OR REPLACE`；`list()` 按 `event_idx DESC` 取最近 `limit`（clamp 到 `[1,2000]`）条再倒序返回，供 HTTP `GET /api/live/{session_id}/analysis` 使用（`app.py:347-356`，该接口直接用 `store.get(session_id)["recording_path"]` 重新打开这份 sqlite 文件读取，不依赖内存态）。这个存储文件与 `Recorder` 的节点 trace 共用同一个 `live_{session_id}.sqlite`（一个连接对象打开两张表），会话结束后随 `finally` 块一并 `close()`，但文件本身留存供之后查询/复盘。

### 7.5 API 与 WebSocket 事件面

`LiveService` 本身不知道 FastAPI/WebSocket 的存在——它只接受一个通用的 `sink: Callable[[dict], None]` 回调（并用 `_safe_sink` 包一层，吞掉 sink 内部抛出的异常，防止一次前端断连搞崩整条 worker 线程）。真正把 `sink` 接到 WebSocket 的是 `backend/alphaloom/api/app.py`：

- `POST /api/live`（`app.py:318-338`，body 走 `LiveStartIn` 校验）：先校验 `bar` 在白名单、`compile_blueprint` 编译通过，再生成 `session_id`，调用 `app.state.live_service.start(bp, params, sink=_live_sink_for(session_id), session_id=session_id)`，返回 `{"session_id": ..., "run_id": ...}`（两个字段值相同，方便前端复用回测的 `getRun(id)` 逻辑）。若 `LiveService.start` 因为并发上限抛 `RuntimeError`，这里转译成 `HTTPException(429, ...)`。
- `_live_sink_for(session_id)`（`app.py:138-147`）：把每个事件先追加进 `app.state.live_event_log[session_id]`（上限 20,000 条的重放缓冲，供 WS 连接建立时补发历史事件），再通过 `loop.call_soon_threadsafe` 把事件塞进所有已订阅该 session 的 `asyncio.Queue`——因为 worker 跑在独立线程而 WebSocket 消费在事件循环里，这里是线程到协程的桥接点。
- `WS /ws/live/{session_id}`（`app.py:806-840`）：连接建立时先检查 `live_service.has(session_id)`（会话必须存在，否则 4404 关闭），随后先把 `live_event_log` 里已经发生过的事件全部按序 `send_json` 补发一遍（这样即使前端是在 bar 事件已经产生之后才连上 WS，也能看到完整历史），再进入"`recv`（前端指令）与 `pull`（队列事件）二选一"的 `asyncio.wait` 循环：收到 `{"cmd":"stop"}` 就调用 `live_service.command(session_id, "stop")`；收到队列事件就转发给前端，遇到 `"done"`/`"error"` 类型后主动跳出循环结束连接。
- `POST /api/live/{session_id}/stop`：调用 `command()`，返回 `"stopping"` 或 `"not_active"`；若既没能 stop 又 `has()` 返回假则 404。
- `GET /api/live/{session_id}/analysis?limit=`：如前述，直接读取会话录制文件里的 `live_analysis` 表。

事件面一共四种 `type`：`status`（`starting`/`running`/`retrying`/自定义 message）、`bar`（每根新 bar 一条，含 `equity`/`fills`/`active` 节点列表）、`analysis`（LLM 旁路输出）、`done`/`error`（终态，各携带最终 `report` 或 `message`）。

### 7.6 前端 LiveDesk 页面

`frontend/src/pages/LiveDesk.tsx` 是消费上述接口的 UI，与 `BacktestDesk`（回放历史）共享大量组件（`CandleChart`、`EquityChart`、`NodeCard`/`ReactFlow` 画布、`buildLiveStageSnapshots` 门控阶段可视化），但驱动方式是"实时流"而非"游标回放"。

**启动会话**（`runLive`，LiveDesk.tsx:236-306）：

1. 校验蓝图已加载且编译通过（`compileErrors.length === 0`）；
2. 先 `stopActiveSession(true)` 关掉上一次可能残留的会话/连接，清空 `fills`/`equityCurve`/`traceRows`/`liveAnalyses`/`activeNodeIds`；
3. 调用 `startLiveSession({...buildBacktestRunBody(blueprint, config, []), poll_ms, analysis: true, analysis_every: 1, context_bars: 30, fetch_limit: 5})`——`poll_ms` 由 UI 上的"流速"（1x/4x/instant）挡位映射为 `5000/1000/250` 毫秒，其余字段沿用回测配置构造函数拼出的蓝图与市场参数（`inst`/`bar`/`cash`/`fee_rate` 等）；
4. 拿到 `session_id` 后用 `openLiveSocket(session_id, onEvent, onClose)`（`frontend/src/lib/ws.ts:8-11`）建立 `ws(s)://.../ws/live/{session_id}` 连接；
5. `onEvent` 分支处理四类事件：
   - `"bar"`：更新 `activeNodeIds`（驱动 ReactFlow 画布高亮和门控阶段卡片状态）、`runState.cursor/equity`，把 `ev.candle` 去重合并进本地 `candles`（按 `ts` 用 `Map` 去重、排序、只保留最近 500 根，避免无限增长），把 `[ts, equity]` 追加进 `equityCurve` 折线，把 `ev.fills` 追加进 `fills` 列表，并 `scheduleTraceRefresh(session_id)`（180ms 防抖后调用 `getTrace()` 拉取节点级 trace，驱动委员会/反思卡片和"门控协议"面板）；
   - `"analysis"`：追加进 `liveAnalyses`（只保留最近 20 条），渲染到右侧 `LiveAnalysisCards`；
   - `"done"`：清空 `liveSessionId`，`getRun(session_id)` 取最终报告的 `fills`/`equity_curve` 覆盖本地状态（确保和后端落盘的最终报告完全一致），并刷新一次 trace；
   - `"error"` / `"status"`：分别映射到 `runState.status` 的 `error: ...` 或原样状态字符串（如 `retrying`/`running`）。
6. `onClose`（WS 断线回调）：只要当前状态不是 `done`/`stopped`/`error*`，就标记为 `disconnected`，避免断线后 UI 卡在 "running"。

**停止**：`sendCommand("stop")` 直接调用 `stopActiveSession(false)`——同时向 WS 发 `{"cmd":"stop"}` 又调用 REST `POST /api/live/{id}/stop`（双保险：无论 WS 是否还连着，后端都能收到停止信号）。组件卸载时的 `useEffect` 清理函数会以 `closeSocket=true` 调一次，确保离开页面时后台线程也被要求停止、WS 也被关闭。

**UI 呈现的内容**（对应 `type: "bar"/"analysis"` 事件驱动的三大区块）：

- **中列**：K 线图（`CandleChart`，叠加 `visibleFills`）、当前 bar 的 OHLCV 卡片、权益曲线（`EquityChart`）、顶部的 `equity`/`status`/`progress`（Live 模式下显示 `"{barsSeen} live bars"`）等指标条；
- **左列**：ReactFlow 蓝图画布，节点按 `activeNodeIds`（即最新一条 `bar` 事件里的 `compiled.order`）高亮，编译失败的节点标 `blocked`；
- **右列（分析栏）**：
  - `liveNarrative`（useMemo 合成的"当前解说"卡片）——若有最新 `analysis` 事件则显示其 `market_state`/`current_gate`/`risk_reason`/`suggestion`，否则退化为基于当前 K 线 close/volume 的通用描述；
  - `LiveAnalysisCards`：最近 3 条 LLM 分析旁路输出的完整卡片（`market_state`/`current_gate`/`risk_reason`/`suggestion` + `prompt_hash` 前 12 位）；
  - 委员会/反思/引用（`insights.committees`/`verdicts`/`citations`，来自 `parseInsights(traceRows)`，与回测页复用同一套 trace 解析）；
  - "门控协议"面板（`buildLiveStageSnapshots`，`frontend/src/lib/liveDesk.ts:27-90`）：把蓝图节点按 `meta.gateProtocol`（若蓝图自带）或内置的数据/决策/风控/执行/反思五段分类规则（`categoryHint`，按节点 `type` 字符串关键字匹配）分组，每组依据 `activeNodeIds` 和 `traceRows` 判定 `"waiting"/"seen"/"active"` 三态，可视化"当前 bar 走到门控链路的哪一步"；
  - 成交列表：`visibleFills` 按 `side`/`qty`/`price` 展示最近 8 笔。

由于 Live 模式没有"单步/继续"的概念，`step`/`resume` 两个按钮在 `runState.mode === "live"` 时被禁用（`disabled={!runState.id || runState.mode === "live"}`），只有 `stop` 保持可用——这与后端 `LiveService.command()` 只认 `"stop"` 一个指令是完全对应的。

---

**关键文件与行号索引**

| 内容 | 位置 |
|---|---|
| `LiveParams` 定义/钳制 | `backend/alphaloom/api/live.py:90-134` |
| `okx_candle_fetcher`（默认 candle 抓取器） | `backend/alphaloom/api/live.py:62-87` |
| `LiveService.start` | `backend/alphaloom/api/live.py:249-270` |
| `LiveService._worker` 轮询主循环 | `backend/alphaloom/api/live.py:282-435` |
| `LiveService.command` | `backend/alphaloom/api/live.py:272-280` |
| `LiveService._analyze` + 提示词 | `backend/alphaloom/api/live.py:437-491` |
| `_analysis_input_summary` | `backend/alphaloom/api/live.py:494-520` |
| `LiveAnalysisStore` | `backend/alphaloom/api/live.py:137-213` |
| `LiveStartIn` schema | `backend/alphaloom/api/schemas.py:36-48` |
| FastAPI `/api/live*` 路由 + `_live_sink_for` | `backend/alphaloom/api/app.py:115-147, 318-356` |
| `/ws/live/{session_id}` | `backend/alphaloom/api/app.py:806-840` |
| `Engine.step`（回测/实盘共用） | `backend/alphaloom/runtime/engine.py:89-129` |
| `PaperBroker` | `backend/alphaloom/brokers/paper.py` |
| 前端启动/事件处理 `runLive` | `frontend/src/pages/LiveDesk.tsx:236-306` |
| 前端 WS 封装 | `frontend/src/lib/ws.ts:8-24` |
| 前端 API 封装 | `frontend/src/lib/api.ts:33-38,65-66` |
| 门控阶段快照构建 | `frontend/src/lib/liveDesk.ts:27-90` |

---

## 8. LLM 集成、录制回放与 Copilot

AlphaLoom 把"调用大模型"当作图里的一等公民：LLM 既是决策节点（`llm_analyst`/`committee`）的推理后端，也是 Copilot 元 Agent（自然语言 → 蓝图）的生成引擎。为了让一个每根 bar 都要调网络模型的策略图能在 CI、demo、离线环境下**确定性重放**，AlphaLoom 在"真实 transport"与"节点/Copilot 业务逻辑"之间插入了一层录制/回放代理，并用统一的 retry 策略吸收模型提供商的限流抖动。本节自底向上讲清这条链路：`llm/client.py`（transport 抽象）→ `llm/retry.py`（重试包装）→ `llm/recording.py`（录制/回放代理）→ `nodes/llm_nodes.py`、`nodes/rag_nodes.py`、`nodes/reflection.py`（业务节点）→ `copilot/*`（把 LLM 接到蓝图生成/解释/优化）。

### 8.1 LLM 客户端抽象（`llm/client.py`）

`llm/client.py` 只做一件事：把"配置从哪来"和"怎么发一次 chat 请求"这两个关注点从业务代码里剥离出来。

**`Transport` 类型**是核心抽象：

```python
Transport = Callable[[dict[str, Any]], dict[str, Any]]
```

一个 transport 就是"请求 dict → 响应 dict"的纯函数签名，不预设是 HTTP、SDK 调用还是本地 fake。上层（录制层、节点、Copilot）只认这个签名，因此测试可以传入任意 in-process 的 fake transport 而不必真的打网络。

**`LLMConfig`**（pydantic `BaseModel`）持有三元组 `base_url` / `api_key` / `model`，通过 `LLMConfig.from_env()` 构建，来源有两条路径：

- **离线默认值**：当 `ALPHALOOM_OFFLINE=1`（或显式 `offline=True`）时，直接返回硬编码的 `OFFLINE_DEFAULTS`：

```python
OFFLINE_DEFAULTS = {
    "LLM_BASE_URL": "http://offline.invalid/v1",
    "LLM_API_KEY": "offline-replay",
    "LLM_MODEL": "spark-x1",
}
```

  代码注释明确写出了这个设计意图：*"离线回放需要零真实端点：一个刚 clone 下来、没有 `.env` 的仓库，仍必须能跑起提交在仓库里的录制。model 默认值必须和 `llm_calls.sqlite` 里的录制完全一致——回放 key 里嵌了 model 字符串，换一个值就全部 miss。"* 换句话说，`"spark-x1"` 不是随便选的占位符，而是与仓库自带的 `data/llm_calls.sqlite` 录制库耦合的契约值，`scripts/seed_recordings.py` 里也把它硬编码为 `MODEL = "spark-x1"` 并在注释里强调"必须与 `OFFLINE_DEFAULTS` 一致，否则离线 replay key miss"。
- **实盘（live）路径**：从若干候选 `.env` 路径（当前目录、`backend/.env`、仓库根、`backend/.env` 等去重后的列表）加载 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`，缺任何一个都抛 `LLMConfigError`，错误信息里会列出缺失的 key 和已检查过的 `.env` 路径，方便定位。

**`openai_transport(config)`** 是目前唯一内建的生产 transport：用 OpenAI 官方 SDK 构造一个指向 `config.base_url`（因此可指向任何 OpenAI 兼容端点，包括讯飞星火 Spark 的 MaaS 网关）的 `OpenAI` 客户端，`send()` 里调用 `client.chat.completions.create(**request)` 并把响应 `model_dump()` 成 dict 返回。这意味着上层所有代码（录制层、节点、Copilot）看到的响应形状始终是 OpenAI Chat Completions 的标准结构 `{"choices": [{"message": {"content": "..."}}], ...}`，`nodes/llm_nodes.py` 与 `copilot/blueprint.py` 里各自的 `_content()` 辅助函数都是按这个形状取 `response["choices"][0]["message"]["content"]`。

一次 `chat()` 调用被建模为一个纯 dict：`{"model", "messages", "temperature", **params}`，`tools` 参数可选附加。这个"请求即 dict"的设计直接为下一层的哈希/录制铺路。

### 8.2 重试与限流退避（`llm/retry.py`)

`with_retry(transport)` 是一个 transport → transport 的包装器，只做失败分类和退避等待，不改变请求/响应形状：

```python
RATE_LIMIT_WAITS = (15.0, 30.0, 60.0)
GENERIC_WAITS = (2.0, 4.0, 8.0)
```

模块 docstring 直接点明了退避策略的来源：*"讯飞星火 Spark MaaS 对突发请求限流（约 4 次快速调用 → 429，错误码 11210）。限流等待故意设得很长；通用错误走一个短的指数梯度。重试耗尽后重新抛出最后一次异常。"* `_is_rate_limit(exc)` 的判定逻辑是"异常类型名是 `RateLimitError`，或异常文本里含 `'429'` 或 `'11210'`"——这是一个务实的、不依赖特定 SDK 异常类型继承关系的字符串/类型名双重判定，能同时兼容 openai SDK 抛出的 `RateLimitError` 和讯飞网关返回的裸 HTTP 错误文本。

`send()` 内部维护两个独立的重试计数器 `rate_i`/`generic_i`，命中限流走长等待表，其它异常走短指数表；任一表耗尽都直接 `raise` 把最后一次异常抛给调用方（不吞异常、不做无限重试）。测试 `test_retry_backoff_on_rate_limit` 验证了这个行为：一个前两次抛 `"HTTP 429 code 11210 busy"`、第三次成功的 fake transport，配合 `with_retry` 后实际睡眠序列是 `[15.0, 30.0]`。

这一层被组合在 transport 链的最外层，`api/app.py::_build_llm_client` 里的组装顺序是 `transport = with_retry(openai_transport(cfg))`，即"先重试退避，再进录制层"——`RecordingLLMClient` 收到的已经是一个"扛得住瞬时限流"的 transport。

### 8.3 录制/回放代理（`llm/recording.py`）

`RecordingLLMClient` 是节点与 Copilot 代码实际持有、调用 `.chat()` 的对象（而不是直接持有 transport）。它的职责是：把每次请求规范化后做内容寻址缓存，命中直接返回、未命中才真的打 transport，并把新结果写回本地 SQLite。

**请求哈希/缓存 key**：

```python
@staticmethod
def _key(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`chat()` 组装请求时把 `temperature` 强制转成 `float`（哪怕调用方传的是 `int`），docstring 备注是"int/float 必须哈希到同一个 replay key"——测试 `test_temperature_int_float_same_key` 验证了 `temperature=1` 与 `temperature=1.0` 命中同一条缓存。这是一处很典型的"为了让哈希键稳定而做的规范化"设计。

**存储**：SQLite 单表 `llm_calls(hash TEXT PRIMARY KEY, request_json TEXT, response_json TEXT, created_at TEXT)`，`INSERT OR REPLACE` 保证幂等写入；连接是 `check_same_thread=False` 配合显式 `threading.Lock()`，因为一次回测运行里多个节点（如 committee 的三角色）可能在同一线程内连续调用，但服务层可能有多个并发的 run 线程共享同一个注入的 `RecordingLLMClient` 实例。

**三种结果路径**（`chat()` 内部）：

1. **缓存命中**（`row` 非空）：直接 `cache_hits += 1`，反序列化 `response_json` 返回，**完全不碰 transport**——这是离线演示"零配额、瞬间跑完"的根本原因。
2. **离线模式下缓存未命中**：抛 `ReplayMissError`，携带请求哈希和提示信息 `"re-run in record mode to capture it"`。
3. **在线模式下缓存未命中**：`cache_misses += 1`，真调 `self._transport(request)`，成功后把 `(hash, request_json, response_json, created_at)` 写入 SQLite 并 commit。

```python
class ReplayMissError(Exception):
    """Offline mode requested a call that was never recorded."""
```

`cache_hits`/`cache_misses` 这两个计数器被模块注释称为"provenance 计数器"——按运行暴露出"多少次决策是从缓存重放、多少次真的打了端点"，供 UI 解释"这次 run 为什么瞬间跑完"（对应 `api/app.py` 里 `llm_client.cache_hits + llm_client.cache_misses` 的运行报告字段）。

**这一层为什么重要**：AlphaLoom 的 backtest/委员会决策依赖 LLM，但 LLM 调用本质上是非确定性、有网络依赖、有成本的外部副作用。录制/回放把"图逻辑的确定性"和"LLM 推理内容的可重复性"解耦开——只要 prompt（进而请求哈希）不变，同一次回测可以在没有网络、没有 API Key、没有真实推理开销的情况下被任何人在任何机器上**逐字重放**。这直接支撑了三个场景：

- **CI 测试**：`test_llm_recording.py`、`test_copilot_api.py` 全部用 in-process fake transport 包一层 `RecordingLLMClient`，测试不联网、不依赖真实模型的输出稳定性。
- **仓库自带的可复现 demo**：`scripts/seed_recordings.py` 用一个纯本地、按请求 system prompt 内容路由的 fake transport，预先跑一遍 committee 决策、Copilot 生成、消融实验、进化实验，把结果录进 `data/llm_calls.sqlite` 并提交入库（`.gitignore` 对它开例外）。任何人 `git clone` 后设 `ALPHALOOM_OFFLINE=1` 就能重放出完全相同的 demo 结果，不需要配置任何 LLM 凭证。
- **`api/app.py::_raise_llm_http_error`** 把 `ReplayMissError` 映射成 HTTP 422（而非 500），并给出可执行的提示：*"Switch to Live mode, or record this exact Copilot prompt before using offline replay."* 这让"离线模式下问了一个没录过的问题"成为一个可理解的用户错误，而不是服务崩溃。

### 8.4 LLM 决策节点（`nodes/llm_nodes.py`）

#### 8.4.1 `LLMAnalystNode`：单角色人格化分析师

`llm_analyst`（`category="decision"`）每根 bar 调一次 LLM，输入 `candle`/`atr`，输出 `signal`。其 cost 证书如实反映调用特征：

```python
cost=CostAnnotation(
    llm_calls_per_bar=1,
    max_tokens_per_call=512,
    latency_class="llm",
    deterministic=False,   # 诚实：调 LLM 就不是确定性
)
```

系统提示词把 `persona` 参数嵌进人格设定，并严格约束输出格式为纯 JSON：

```python
_SYSTEM = (
    "You are {persona}, a disciplined trading analyst. Read the latest candle and ATR, "
    "then decide. Reply with ONLY a JSON object: "
    '{{"side": "long|short|flat|hold", "rationale": "<one sentence>", '
    '"confidence": <0..1 float>}}. No prose outside the JSON.'
)
```

`on_bar()` 的控制流：组装 `{close, high, low, atr}` 的 user JSON → `ctx.llm.chat(messages, temperature=0.2, max_tokens=512)` → 若 `ctx.audit` 存在则记一条 `tool="llm_chat"` 的审计条目（含 `data_max_ts=candle["ts"]`，用于因果时间戳追踪）→ 用 `_extract_json()` 从回复文本里"扫描出第一个配对平衡的 `{...}`"解析出结构化决策（模型常在 JSON 外包裹说明性文字，这个括号计数解析器专门处理这种情况）→ `side` 不在 `("long","short","flat","hold")` 或解析失败一律**降级为 hold**（`_hold("parse failed")`），不会把脏输出传下去。若 `side` 是 `long`/`short` 且 `atr` 有值，止损价按 `close ∓ atr_mult * atr` 计算。

值得注意：`if ctx.llm is None: raise RuntimeError(...)`——这不是静默跳过，而是清晰报错，呼应 `api/service.py` 注释里的设计取舍："LLM 节点缺席时零影响（D2 行为不变），LLM 节点在场但没配 LLM 客户端时，run 会明确失败而不是悄悄空转"。

#### 8.4.2 `CommitteeNode`："策略师委员会"多角色决策

`committee` 节点是本节最有代表性的设计：把一次交易决策拆成三个角色的**结构化 JSON 接力**，而不是一次性让单个 LLM 输出决策。

| 角色 | System Prompt 意图 | 输出契约 |
|---|---|---|
| 策略师（strategist） | 读 candle+ATR，提出交易提案 | `{"side", "rationale", "confidence"}` |
| 风控官（risk officer） | 读策略师 JSON，审查风险，可一票否决 | `{"veto": bool, "concern", "confidence"}` |
| 主席（chair） | 综合策略师[+风控官]的 JSON，给出终案；风控官否决时**必须**输出 hold | `{"side", "rationale", "confidence"}` |

三个角色的 system prompt 都以模板字符串形式存在（`_STRATEGIST_SYSTEM`/`_RISK_SYSTEM`/`_CHAIR_SYSTEM`），可通过节点 params（`strategist_prompt`/`risk_prompt`/`chair_prompt`、`*_persona`）整体覆盖，为实验不同话术提供了钩子。cost 证书如实标注 **`llm_calls_per_bar=3`**（三角色各调一次）。

**数据流接力**是这个节点的核心机制：`_ask(ctx, system, user, role, ts)` 是统一的单轮调用辅助函数，每次调用都单独打一条 `ctx.audit.record(tool=f"committee:{role}", ...)`；上一角色的 JSON 输出会被塞进下一角色的 user 消息里（比如风控官的 user prompt 是 `{"market": ..., "strategist": strat_json}`，主席的 user prompt 是 `{"strategist": strat_json, "risk_officer": risk_json}`）。任一环节解析失败（不是合法 JSON 或缺关键字段）整个决策**立即降级为 hold**，`rationale` 里会写明是哪个角色解析失败（如 `"strategist parse failed"`、`"risk officer parse failed"`、`"chair parse failed"`），并把已经拿到的 JSON 塞进 `committee_trace` 里，方便前端定位。

**风控官否决具有强制力**：即便主席在 `veto=true` 时仍返回了 `side=long/short`，`CommitteeNode.on_bar()` 也会在代码层面强制把最终结果改写为 hold（`if veto: return self._hold(...)`）——不信任主席的自由发挥，尊重风控官的否决权。这是一处"代码兜底 LLM 输出"的典型例子：即使 prompt 里已经要求"风控官否决时你必须返回 hold"，业务代码依然不完全信任模型会遵守指令。

**趋势上下文（`context_window`）是显式的选择性功能**：默认 `context_window=0`，此时喂给策略师的市场 JSON 只有单根 K 线（`{close, high, low, atr}`），与早期版本完全一致（字节级不变，保证已提交的录制回放不失效）。设为正数后节点自身用 `self._recent` 累积最近 N 根收盘价，追加 `recent_closes`/`trend_pct`/`trend`（`up`/`down`/`flat`，阈值 ±0.3%）字段，让策略师能看到趋势而不是只盯着当前这一根 K 线做决策。

**消融开关 `skip_risk_officer`**（用于 D4-T4 消融实验）：设为 `True` 时跳过风控官角色，主席只读策略师 JSON，`committee_trace` 只有两项、不可能有 veto。代码注释特别强调这只是"软护栏"的消融：*"RiskGate '硬护栏' 由类型系统强制、无法被消融"*——因为 `execute_order` 节点在类型层面只接受 `risk_stamped_signal`（见 8.6 节与第 5 节的联系），无论 committee 内部怎么配置都无法绕过图编译期的强制路由。同时 cost 注解**不会**因为 `skip_risk_officer=True` 而降到 2——它维持静态上界 3，代码注释解释了这个设计原则："成本证书是编译期注解，不随参数收窄；只许高估不许低估"。

`agent_committee.loom`（仓库自带演示蓝图）展示了 committee 节点在真实图中的位置：

```json
{
  "id": "committee",
  "type": "committee",
  "params": { "atr_mult": 2 }
}
```

它的 `signal` 输出接到 `require_citations`（8.5 节）而不是直接进 `position_sizer`，形成"决策必须有知识库背书"的软约束链路。

### 8.5 RAG 节点（`nodes/rag_nodes.py`）

本文件里的四个节点分别解决"给决策注入外部知识/历史经验"和"强制决策必须有依据"两类问题，且全部 `deterministic=True`、`llm_calls_per_bar=0`——它们做的是检索和确定性副作用写入，不调用 LLM。

| 节点 type | category | 作用 | 输入 | 输出 |
|---|---|---|---|---|
| `knowledge_retrieve` | rag | BM25 检索自撰知识库 | `candle`, `query`（可选） | `citations` |
| `require_citations` | rag | 强制引用门控 | `signal`, `citations`（可选） | `signal` |
| `experience_retrieve` | rag | 按市场状态桶检索历史经验教训 | `candle`, `ema`, `atr` | `lessons` |
| `experience_write` | reflection | 把 Reflector 的 verdict 落库 | `verdict` | `written` |

**`KnowledgeRetrieveNode`**：对一个进程内全局缓存的语料库（`load_default_corpus()`，只加载一次）做 BM25 检索，运行时 `query` 输入引脚（若非 `None`）优先于静态 `query` 参数，允许下游动态决定检索什么。每条 citation 格式化为 `"<doc_id>: <前 160 字的片段>…"`，保留了溯源用的 `doc_id`。

**`RequireCitationsNode`** 是一个"检索背书门"：`citations` 输入引脚可选连接——连了 `knowledge_retrieve.citations` 就与 `signal` 自带的 citations 合并；不连（悬空 → `None`）就只看 `signal` 自身携带的 citations。核心规则很直白：

```python
if side in ("long", "short") and not citations:
    sig["side"] = "hold"
    sig["qty"] = 0.0
    sig["stop"] = None
    sig["reason"] = "blocked: trade requires non-empty citations"
```

模块 docstring 把这个设计定性为"软约定"：*"D3 软约定 + 测试锁的形态；D4 可升级为编译期 RAG 盖章类型"*——即当前是运行时门控（未经知识库背书的 long/short 会被静默降级为 hold），尚未像 `risk_gate` 那样上升为类型系统强制的编译期约束，但已经用测试锁定了行为。

**经验库读写闭环**（`experience_retrieve` + `experience_write`，与 8.6 节的 `ReflectorNode` 三者构成完整的"决策 → 平仓 → 反思 → 归档 → 下次决策前检索"闭环）：

- **市场状态桶**由 `memory/experience_store.py::derive_regime_bucket(ema, ema_prev, atr)` 这个纯函数派生：任一输入缺失（未 warmup）返回 `"range"`；否则用 `|ema - ema_prev| / atr` 相对斜率（阈值 `0.10`）判断 `trend_up`/`trend_down`/`range`。用 ATR 归一化斜率是为了让阈值在不同价位、不同波动尺度下保持稳健。
- **`ExperienceRetrieveNode`** 只需要 `ema`+`atr` 两个引脚（画布上都有真实产出源），"上一根 ema"由节点自己用 `self.state["ema_prev"]` 记住，docstring 特别说明这样设计是为了避免要求画布提供一个"没有产出源的 `ema_prev` 引脚"，否则这条记忆检索链路在真实蓝图上根本连不通。
- **`ExperienceWriteNode`** 收 `verdict`（`None` 则 no-op，即"没有平仓的那根 bar"）并调用 `ExperienceStore.write(bucket, trade_key, config_summary, outcome, pnl, lesson)`。幂等性由 `ExperienceStore` 的 `(bucket, trade_key)` 主键 `ON CONFLICT ... DO UPDATE`（UPSERT）兜底——同一笔平仓触发多次反思也只留一行。

`ExperienceStore`（`memory/experience_store.py`）是一个极简的 SQLite 封装：单表 `experience(bucket, trade_key, config_summary, outcome, pnl, lesson, PRIMARY KEY(bucket, trade_key))`，`retrieve(bucket, top_k)` 按 `rowid DESC`（最近写入优先）取回该桶下最多 `top_k` 条记录——检索键就是市场状态桶本身，保证"上升趋势中学到的教训只在下次处于上升趋势时被召回，不会跨状态串味"。

### 8.6 反思闭环节点（`nodes/reflection.py`）

`ReflectorNode`（`type="reflector"`, `category="reflection"`）实现的是借鉴自 Hindsight 的 **`reasonable_but_wrong` 分类学**：把"决策过程的好坏"和"最终结局的好坏"分开评判，避免让运气污染对过程本身的评价。四象限判定：

```python
def _classify(process_sound: bool, pnl: float) -> str:
    outcome_good = pnl > 0
    if process_sound and outcome_good:
        return "reasonable_and_right"
    if process_sound and not outcome_good:
        return "reasonable_but_wrong"   # 好过程坏运气：不惩罚
    if not process_sound and outcome_good:
        return "lucky"                  # 坏过程好运气：别当本事
    return "bad_process"
```

"过程是否健全"（`_process_is_sound`）的判据是 `confidence >= 0.5` 且 `rationale` 或 `citations` 至少有一项非空——即"决策既有足够信心，又有可追溯的依据"。这个判据直接消费了 `LLMAnalystNode`/`CommitteeNode` 输出信号里的 `confidence`/`rationale`/`citations` 字段，把"LLM 决策节点吐出的元数据"变成了反思闭环的输入，形成节点间的隐式契约。

**数据流接缝**（模块 docstring 特别强调的设计要点）：`pnl` 不占用任何决策引脚——`ReflectorNode` 直接读 `ctx.broker.closed_trades`（`PaperBroker` 每次平仓后追加的 `{ts, pnl, entry_side}` 记录），用 `self.state["consumed"]` 记住已经消费到第几笔，只有 `closed_trades` 变长（意味着这根 bar 发生了平仓）才产出 `verdict`，否则 `verdict=None`。这样设计是为了让 Reflector 在**真实蓝图**里就能接得到平仓 pnl，而不是靠一个假想的、图上不存在产出源的引脚——反思闭环因此不是纸面功能，而是能在 `agent_committee.loom` 里真实跑通的链路。一根 bar 内如果有多笔平仓（比如反手后止损），游标每根 bar 只前进 1、消费"最旧的未反思那笔"，保证跨多根 bar 逐笔排空、一笔不丢；`trade_key` 里嵌入全局序号 `idx`，避免同一根 bar 内多笔同向平仓在 `ExperienceStore` 的 UPSERT 里被误合并。

`verdict` 载荷包含四象限判定、市场状态桶、pnl、`config_summary`（从平仓信号提炼的一句可读快照，如 `"side=long; rationale=..."`）、`lesson`（四个模板之一，比如 `reasonable_but_wrong` 对应 *"In {bucket}, {side} was well-reasoned but lost to the market — process was fine, don't over-correct on one bad outcome."*）、`trade_key`，供下游 `ExperienceWriteNode` 落库。cost 证书是全 0 + `deterministic=True`——四象限分类本身是纯规则判断，不调用 LLM。

这三个节点（`ExperienceRetrieveNode` → 决策节点 → `ReflectorNode` → `ExperienceWriteNode`）构成的完整闭环，在 `agent_committee.loom` 里的接线是：`experience_retrieve`（读 `ema`/`atr`）产 `lessons`，`reflector`（读 `committee.signal` + `ema`/`atr`）产 `verdict`，`experience_write` 消费 `verdict` 写库——下一次遇到相同市场状态桶时，`experience_retrieve` 就能检索到之前的教训。

### 8.7 Copilot：自然语言 → 蓝图（`copilot/blueprint.py` + `copilot/prompts.py` + `copilot/layout.py`）

Copilot 子系统把 LLM 接到了图编译器上，实现"自然语言描述策略 → 合法可编译的 `.loom` 蓝图"，并提供 `explain`（图 → 自然语言解释）和 `optimize`（回测报告 → 变异图 + diff）两个配套能力。

#### 8.7.1 提示词设计（`copilot/prompts.py`）

`build_system_prompt(registry)` 是核心提示词构造函数，**动态从 `REGISTRY` 生成完整的节点目录**，而不是手写一份可能过时的清单：

```python
def _format_node_line(node_def) -> str:
    params = ", ".join(sorted(node_def.params)) if node_def.params else "(none)"
    cost = node_def.cost
    return (
        f"- {node_def.type} [{node_def.category}] "
        f"inputs={{{_format_pins(node_def.inputs)}}} "
        f"outputs={{{_format_pins(node_def.outputs)}}} "
        f"params=[{params}] "
        f"llm_calls_per_bar={cost.llm_calls_per_bar}"
    )
```

每一行列出类型、类别、输入/输出引脚（`名称:PinType 字符串值`）、参数名、每 bar 调用 LLM 的次数。由于这个目录是从 `REGISTRY` 实时生成的，**任何新注册的节点类型（包括通过沙箱热注册的自定义节点）都会立刻出现在 Copilot 能看到、能使用的目录里**——这是与第 5 节沙箱系统的第一处直接连接点：Copilot 并不区分内置节点和沙箱自定义节点，两者在提示词里一视同仁。

拼接完整系统提示词还包括：

- `.loom` JSON schema 的文字说明（`_SCHEMA`）：字段 `id`/`name`/`nodes`（每个含 `id`/`type`/`params`）/`edges`（`{"from": "nodeId.pin", "to": "nodeId.pin", "feedback": bool}`）/`meta`，并强调"边两端的类型必须严格匹配"、"故意构造环时只能在一条回边上设 `feedback: true`"。
- **硬约束提示**（`_RISK_CONSTRAINT`），这是整个 Copilot 设计里最关键的一段文字：

```
HARD CONSTRAINT — orders must pass the RiskGate:
The execute_order node's "signal" input only accepts the type "risk_stamped_signal".
The ONLY node in the entire universe that produces "risk_stamped_signal" is the
risk_gate node (RiskGate). Therefore EVERY order-executing path MUST route the trading
signal through a risk_gate node before reaching execute_order. ...the compiler's type
system is the compliance officer and will reject it with a TYPE_MISMATCH error.
```

模块 docstring 把这个机制称为"招牌卖点"：*"即便 LLM 也造不出绕过风控的图，因为 `execute_order` 的输入只吃 `risk_stamped_signal`，而全宇宙唯一能产它的是 `RiskGate`。编译器的类型系统就是合规官，绕风控的图会被 `TYPE_MISMATCH` 拦下并把 `fix_hint` 喂回 LLM。"*——这句提示词本身不是强制手段（LLM 完全可能"忘记"遵守），真正的强制力来自图编译器的类型系统（对应第 5 节 `PinType.RISK_STAMPED_SIGNAL` 这个专用引脚类型），提示词只是帮助 LLM **一次性生成正确图**、少走自修复弯路的"提示"，而不是安全边界本身。

`build_explain_messages(loom)` 和 `build_optimize_messages(loom, report, registry)` 是另外两个更简单的消息构造函数：前者是固定的"交易系统教程老师"人设 + loom JSON；后者同样带上完整节点目录和风控硬约束文字，再附上当前 loom 和回测报告摘要（`num_trades`/`win_rate`/`total_return` 等），要求 LLM"提出一个可信地改善指标的变异图"。

#### 8.7.2 自修复生成循环（`copilot/blueprint.py::text_to_blueprint`）

这是 Copilot 最核心的机制，模块 docstring 称之为"Agent 在可验证环境自修复的自证"：

```python
for attempt in range(1, max_retries + 1):
    response = llm.chat(messages, temperature=temperature, max_tokens=4096)
    loom = _extract_json(_content(response))
    if loom is None:
        # 回复不是合法 JSON —— 把这个反馈也喂回去，要求只回 JSON
        ...
        continue

    result = compile_fn(loom)
    if getattr(result, "ok", False):
        positions = _layout.layout(loom, list(getattr(result, "order", [])))
        loom.setdefault("meta", {})
        loom["meta"]["positions"] = positions
        return {"loom": loom, "notes": notes}

    # 编译失败：把结构化错误（含 fix_hint）喂回 LLM 让它自己改
    last_feedback = _errors_to_feedback(result.errors)
    messages.append({"role": "assistant", "content": json.dumps(loom, ...)})
    messages.append({"role": "user", "content": f"...Fix EXACTLY these...:\n{last_feedback}"})
```

流程是：LLM 生成一份 loom JSON → `compile_fn`（默认 `default_compile_fn`，内部做 `loads_loom` 解析 + `compile_blueprint` 编译）尝试编译 → 编译成功则调用 `layout.layout()` 自动布局并把结果写进 `loom["meta"]["positions"]`、返回 `{"loom": ..., "notes": [...]}`；编译失败则把 `CompileError` 列表（每条含 `code`/`message`/`node_id`/`fix_hint`）通过 `_errors_to_feedback()` 渲染成结构化文本，作为新的 user 消息追加进对话历史，让 LLM 在下一轮"读着编译器的反馈自己修"。最多重试 `max_retries`（默认 3）次，全部失败则抛 `BlueprintGenerationError`（消息里带最后一轮的完整编译报告）。

`_extract_json()` 用的是一个手写的、感知字符串内转义的括号计数解析器（区别于 `llm_nodes.py` 里更简单的版本，这里显式处理了字符串内的 `"`/`\` 转义，避免 JSON 字符串值里出现 `{`/`}` 导致误判平衡点），因为 loom JSON 比决策 JSON 复杂得多，字符串字段（如 `rationale`、`name`）里出现花括号的概率更高。

`default_compile_fn` 是"copilot 层拥有的 loom dict → `BlueprintSpec` → `compile_blueprint` 转换"，docstring 说明它既可以被生产代码直接复用，也可以被测试用自己的 `compile_fn` 替换（复用真实 `compile_blueprint` 或注入 fake）；即便 `loads_loom` 因为 JSON 结构错误抛异常，也会被包成一个 `ok=False` 的 `_Result`，让"解析错误"和"类型错误"走同一条反馈回路喂给 LLM。

#### 8.7.3 自动布局（`copilot/layout.py`）

`layout(loom, order)` 解决"LLM 生成的图没有可视坐标"的问题：按拓扑深度分层分列摆放节点，无重叠，前端画布可以直接渲染。

**深度计算**（`_depths`）是纯粹的依赖关系推导：`depth(n) = 1 + max(depth(src) for 每条入边的源节点)`，无入边节点深度为 0；`feedback` 边被显式排除在深度计算之外（回边参与深度计算会把整层节点错误地推到右边，扰乱布局）。

```python
COL_WIDTH = 260
ROW_HEIGHT = 150
MARGIN_X = 40
MARGIN_Y = 40
```

这几个常量的注释指出它们"与前端 `loom.ts` 的 `GRID_X`/`GRID_Y` 同量级"，目的是让 Copilot 自动生成的图和用户手工在画布上拖拽连线的图在视觉密度上保持一致的观感。列（x 坐标）对应节点在依赖链上的深度（数据源永远在最左），行（y 坐标）是同一深度列内按"拓扑序优先、孤立节点补在最后"的稳定顺序分配的序号。返回值是 `{node_id: {"x": int, "y": int}}`，被 `text_to_blueprint`/`optimize` 写入 `loom["meta"]["positions"]`，前端的 `loomToFlow`（`frontend/src/lib/loom.ts`）从这里读取节点坐标。

#### 8.7.4 `explain` 与 `optimize`

**`explain(loom, llm)`**：把 loom 序列化后配合"交易系统教程老师"人设发给 LLM，返回一段自然语言解释。有一个值得注意的健壮性设计——`_fallback_summary(loom)`：当 LLM 返回空文本时，用确定性规则拼出一句"Blueprint '{name}' wires {N} nodes ({type 列表}) into a trading pipeline; orders are gated through a RiskGate before execution."保证 `explain` 的契约"返回值非空"始终成立，不依赖 LLM 的"配合度"。

**`optimize(loom, report, llm)`**：复用与 `text_to_blueprint` 完全相同的"生成 → 编译 → 失败则把错误喂回重试"自修复循环（只是提示词换成了"读回测报告提出图变异"），保证任何被接受的优化建议都是可运行的图。生成的变异图额外经过 `diff_blueprints(before, after)` 计算结构化差异：

```python
return {
    "added": [...],       # after 里有、before 没有的节点
    "removed": [...],     # before 里有、after 没有的节点
    "changed": [...],     # 同 id 但 type 或 params 变了的节点（含 before/after 两份）
    "added_edges": [...], # 按 (from, to, feedback) 三元组比对新增的边
    "removed_edges": [...],
}
```

这份 diff 是前端"优化建议预览"UI 的数据源——用户看到的是"新增了哪些节点/边、哪些参数改了"，而不是直接看两份完整 JSON 的区别；只有用户主动点击"应用"才会让新图落地替换旧图。

#### 8.7.5 与 API 层、录制回放、沙箱系统的连接

`api/app.py` 把 Copilot 接成三个端点：`POST /api/copilot/blueprint`（`text_to_blueprint`）、`POST /api/copilot/explain`（`explain`）、`POST /api/copilot/optimize`（`optimize`）。`_require_llm()` 在没有配置 LLM 客户端时直接返回 503；`_raise_llm_http_error()` 统一把 `ReplayMissError` 映射成 422（`{"error": "offline_replay_miss", "hint": "Switch to Live mode, or record this exact Copilot prompt..."}`），把 openai SDK 的连接类异常映射成 502，把 `BlueprintGenerationError`（自修复耗尽仍未编译通过）映射成 422（`{"error": "generation_failed", ...}`）——三种失败模式都不会让服务进程崩溃或吐出裸 500 堆栈。

Copilot 与录制回放层的关系是"Copilot 的每次 LLM 调用都经过同一个 `RecordingLLMClient`"——`api/app.py::_build_llm_client()` 构造的那个全局单例既服务于 `llm_analyst`/`committee` 节点，也服务于三个 Copilot 端点。`scripts/seed_recordings.py` 因此也预先录制了一段"Copilot 先返回一个绕过风控的坏图 → 触发 `TYPE_MISMATCH` → 读到 `fix_hint` 后返回修正图"的完整自修复序列，用于离线演示"编译器把 LLM 管教好"的效果。

Copilot 与第 5 节沙箱系统（`sandbox/node_sandbox.py`）之间的连接点有两处：

1. **提示词层面**：`build_node_catalog(REGISTRY)` 是直接读取进程级 `REGISTRY` 生成的，任何经 `POST /api/nodes/custom` 沙箱编译并通过 `mark_sandboxed()` 标记后热注册进 `REGISTRY` 的自定义节点类型，会立刻出现在下一次 Copilot 调用的系统提示词节点目录里——Copilot 因此可以"用上"用户刚刚通过沙箱注册的自定义节点，两个子系统共享同一个节点类型的事实来源（`REGISTRY`）。
2. **运行时能力隔离**：即使 Copilot（或恶意 prompt 注入）生成了一个自定义节点、把它接进图里并企图让它在 `on_bar()` 里访问 `ctx.llm` 偷偷调用大模型，`runtime/engine.py::_RestrictedContext` 也会拦下这次访问——`NodeDef.sandboxed=True` 的节点在运行时被 `Engine._step_inner()` 分配一个剥离了 `llm`/`audit`/`broker` 三个属性的受限 `ctx` 视图，访问即抛 `SandboxEscapeError`。`sandbox/node_sandbox.py` 侧的静态 AST 白名单（第 5 节详述）与 `engine.py` 侧的运行时能力剥离，构成了"声明的 cost 证书（`llm_calls_per_bar=0`）必须等于运行时真实行为"的双重保障——沙箱节点无法通过谎报确定性证书来绕过配额或悄悄消耗真实 LLM 调用。`registry.py` 的模块注释把这一点说得很直白：*"沙箱节点可声明 `llm_calls_per_bar=0` 却运行期偷调 LLM——运行期给沙箱节点一个剥离 `.llm`/`.audit` 的受限 `ctx` 视图"*。

这样，"LLM 生成的自定义节点"这条链路的完整闭环是：Copilot/用户手写源码 → 沙箱 AST 编译（第 5 节）→ 热注册进 `REGISTRY`（带 `sandboxed=True` 标记）→ 立刻出现在下次 Copilot 提示词的节点目录里、也可被拖进画布连线 → 图编译期类型检查（不管节点是否沙箱来源，边的 `PinType` 必须匹配，`RISK_STAMPED_SIGNAL` 只能来自 `risk_gate`）→ 运行时若 `sandboxed=True` 则强制走 `_RestrictedContext`，即使节点源码里写了 `ctx.llm.chat(...)` 也会在执行到那一行时抛出 `SandboxEscapeError` 而不是真的发起网络调用。

---

## 9. 数据与持久化层

AlphaLoom 的持久化层不是单一数据库，而是按职责拆分的若干个 SQLite 文件，各自服务于系统中不同的时间尺度和读写模式：K 线行情是只读为主、批量写入的历史数据；run 元数据是高频状态更新的轻量注册表；每次回测/实盘的节点级 I/O 轨迹是海量追加写入；经验库是反思闭环的小体量索引结构；知识语料库则完全不落库，是进程内内存对象。本节逐一说明这几类存储的 schema、读写路径，以及它们之间的关系。

### 9.1 行情数据源抽象（`data/source.py`）

`data/source.py` 定义了一个极薄的抽象基类 `DataSource`，只有一个契约方法：

```python
class DataSource(ABC):
    """多市场行情抽象：D1 只有 SQLite 实现；D4 加 OKX 实时。"""

    @abstractmethod
    def iter_candles(self, inst: str, bar: str,
                     start_ms: int | None = None,
                     end_ms: int | None = None) -> Iterator[dict]: ...
```

这个类的存在意义是把"回测引擎需要按 `(inst, bar, 时间窗)` 拿到一串蜡烛"这件事，与"蜡烛具体从哪来"解耦——D1 阶段只有 `SQLiteMarketData` 一种实现，回测引擎、实盘 session runner 都只依赖 `DataSource` 接口，不关心底层是 SQLite 文件还是（文件顶部注释预告的）D4 阶段接入的 OKX 实时行情源。

同一文件里还定义了各周期到毫秒数的映射表 `_BAR_MS` 和取值函数 `bar_to_ms`：

| bar | 毫秒数 |
|---|---|
| 1m | 60,000 |
| 3m | 180,000 |
| 5m | 300,000 |
| 15m | 900,000 |
| 30m | 1,800,000 |
| 1H | 3,600,000 |
| 2H | 7,200,000 |
| 4H | 14,400,000 |
| 6H | 21,600,000 |
| 12H | 43,200,000 |
| 1D | 86,400,000 |

这张表既是 `SQLiteMarketData` 做多周期聚合分桶的依据，也被 `api/app.py` 的 `_BARS` 列表和前端周期选择器共用，是全系统对"周期"这个概念的唯一真源。

### 9.2 SQLite 行情实现（`data/sqlite_source.py`）

#### 9.2.1 表结构与连接参数

`SQLiteMarketData` 是 `DataSource` 目前唯一的实现，构造时直接在给定路径上建表（`CREATE TABLE IF NOT EXISTS`），不需要单独的迁移步骤：

```sql
CREATE TABLE IF NOT EXISTS candles (
    inst TEXT, bar TEXT, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (inst, bar, ts)
)
```

`(inst, bar, ts)` 三元组联合主键决定了这张表可以同时存放同一个交易对在不同周期下的蜡烛（例如 `BTC-USDT-SWAP` 的 `1m` 和 `1H` 两行互不冲突），也是后面"精确命中优先"路由逻辑的依据。连接建立时设置了三个 PRAGMA：

```python
self._db.execute("PRAGMA journal_mode=WAL")
self._db.execute("PRAGMA synchronous=NORMAL")
self._db.execute("PRAGMA busy_timeout=30000")
```

WAL 模式 + `synchronous=NORMAL` 是为了在批量写入历史数据（回补脚本一次性灌入几千到几万根蜡烛）时兼顾一定的写入吞吐与崩溃安全；`busy_timeout=30000` 配合内部的 `_with_retry` 帮助方法（对 `sqlite3.OperationalError` 里的 "locked" 错误做指数退避重试，最多 6 次、单次最长约 0.8 秒）应对多个连接（比如回测线程与查询 API 同时打开同一份 `demo.sqlite`）并发访问时的锁等待。

从实际部署的 `data/demo.sqlite` 可以看到真实数据分布：

```
inst              bar   count  ts_min          ts_max
BTC-USDT-SWAP     1m    4001   0               1700000000000
ETH-USDT-SWAP     1m    4000   0               239940000
SOL-USDT-SWAP     1m    20206 1782101520000    1783403520000
```

库里只落了 `1m` 一档，其余周期（`5m`/`15m`/`1H` 等）在查询时按需从 `1m` 现算，这正是下面聚合逻辑存在的原因。

#### 9.2.2 插入路径

`insert_candles(inst, bar, candles)` 是唯一的写入口，接受一批 `{"ts", "open", "high", "low", "close", "volume"}` 字典，用 `INSERT OR REPLACE` 批量写入并立即提交：

```python
def insert_candles(self, inst: str, bar: str, candles: list[dict]) -> None:
    self._executemany(
        "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
        [(inst, bar, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"])
         for c in candles])
    self._commit()
```

`INSERT OR REPLACE` 使得按主键重复写入是幂等的（补数据、重跑回补脚本不会产生重复行），这对行情回补这种"可能因网络问题重试"的操作是必要的设计。

#### 9.2.3 查询路径：精确命中优先，否则从 1m 聚合

`iter_candles` 是核心读接口，走的是"先查有没有该周期原始数据，没有就从 1m 现场聚合"的两级路由：

```python
def iter_candles(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
    if self._has_bar(inst, bar):
        yield from self._iter_exact(inst, bar, start_ms, end_ms)
        return
    if bar != "1m" and self._has_bar(inst, "1m"):
        yield from self._iter_aggregated_from_1m(inst, bar, start_ms, end_ms)
```

`_has_bar` 只是一个 `SELECT 1 ... LIMIT 1` 的存在性探测。这个设计的好处是：数据源只需要采集/回补最细粒度的 `1m` 数据，其余所有粗粒度周期（`3m` 到 `1D`）都能按需推导，不需要为每个周期单独跑一遍历史回补，也不会因为聚合逻辑的 bug 而污染原始数据表——聚合结果从不写回数据库，纯粹是查询时的流式计算。

聚合函数 `_iter_aggregated_from_1m` 按时间桶做流式归并：

```python
def _iter_aggregated_from_1m(self, inst, bar, start_ms=None, end_ms=None) -> Iterator[dict]:
    base = self._iter_exact(inst, "1m", start_ms, end_ms)
    ...
    bar_ms = bar_to_ms(bar)
    current_bucket = None
    agg = None
    for candle in chain([first], base):
        bucket = (int(candle["ts"]) // bar_ms) * bar_ms
        if current_bucket is None or bucket != current_bucket:
            if agg is not None:
                yield agg
            current_bucket = bucket
            agg = {"ts": bucket, "open": candle["open"], "high": candle["high"],
                   "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
            continue
        agg["high"] = max(agg["high"], candle["high"])
        agg["low"] = min(agg["low"], candle["low"])
        agg["close"] = candle["close"]
        agg["volume"] += candle["volume"]
    if agg is not None:
        yield agg
```

分桶键是 `(ts // bar_ms) * bar_ms`，即向下取整到该周期的边界时间戳；每个桶内第一根 1m 蜡烛的 `open` 作为聚合桶的 open，最后一根的 `close` 作为聚合桶的 close，`high`/`low` 取桶内极值，`volume` 累加——这是标准的 OHLCV 重采样规则。整个过程是基于生成器的流式处理（`_iter_exact` 本身也是生成器，逐行从游标 yield），不会把整段历史一次性载入内存，对几万根蜡烛的聚合也不会有明显的内存压力。

需要注意的是聚合结果并不保证桶内 1m 蜡烛无缺口——如果原始数据在某个 1H 桶内缺了若干根 1m K 线，聚合出的这根 1H 蜡烛依然会基于剩余的 1m 数据产出（这是一个隐式假设，行情数据本身应尽量连续）。

#### 9.2.4 辅助查询：`bounds` 与 `catalog`

`bounds(inst, bar)` 返回某个 `(inst, bar)` 组合的时间范围（`MIN(ts), MAX(ts)`），供前端 K 线图确定可选的时间窗口。

`catalog()` 用于给前端 UI 提供"当前库里有哪些可用的 (inst, bar) 组合"的清单。它先按 `GROUP BY inst, bar` 枚举库里实际存在的精确记录，然后对每个只有 `1m` 数据的 `inst`，把 `_BAR_ORDER` 中尚未出现的周期也合成一条"虚拟目录项"补进结果——`count` 字段用 `math.ceil(span_ms / bar_to_ms(bar))` 估算聚合后大致会有多少根蜡烛（不是精确查询，只是给前端一个数量级参考）：

```python
def catalog(self) -> list[dict]:
    exact = self._execute(
        "SELECT inst, bar, MIN(ts), MAX(ts), COUNT(*) "
        "FROM candles GROUP BY inst, bar ORDER BY inst, bar").fetchall()
    ...
    for base in base_rows:          # 只有 1m 的 inst
        span_ms = base["end_ms"] - base["start_ms"] + bar_to_ms("1m")
        for bar in _BAR_ORDER:
            ...
            count = max(1, math.ceil(span_ms / bar_to_ms(bar)))
            rows.append({"inst": base["inst"], "bar": bar, ...})
```

这样前端下拉框里能直接列出 `5m`/`15m`/`1H` 等选项，尽管库里物理上只存了 `1m`。

### 9.3 RunsStore —— 运行生命周期注册表（`api/runs_store.py`）

#### 9.3.1 定位与并发模型

`RunsStore` 是回测 run 和实盘 live session 的统一状态注册表，落盘为 `data/runs.sqlite`（见 `serve.py` 中 `create_default_app` 的默认路径配置）。类头部注释直接点明了它的并发设计取舍：

```python
class RunsStore:
    """run 生命周期注册表。连接串行化（check_same_thread=False + 锁），D2 单进程足够。"""

    def __init__(self, path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
```

因为 FastAPI + 回测 worker 线程会从不同线程访问同一个 sqlite3 连接，`check_same_thread=False` 关掉了 sqlite3 默认的同线程校验，配合一把 `threading.Lock()` 把所有读写方法串行化——这是"单进程内跑多个 run/live session"这个 D2 阶段的场景下最简单可行的方案，注释明确说明了这是针对当前部署规模（单进程）的选择，不是通用的高并发方案。

#### 9.3.2 表结构

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, blueprint_id TEXT, blueprint_json TEXT,
    params_json TEXT, status TEXT, report_json TEXT, error TEXT,
    recording_path TEXT, created_ms INTEGER
)
```

| 字段 | 含义 |
|---|---|
| `run_id` | 主键，回测为 `uuid4().hex[:12]`；实盘 live session 复用同一张表，`run_id` 即 `session_id` |
| `blueprint_id` | 关联的蓝图 ID |
| `blueprint_json` | 创建时刻蓝图的完整 `.loom` JSON 快照（用 `dumps_loom` 序列化），保证 run 结果可追溯到当时确切的蓝图版本，即便之后蓝图文件被编辑 |
| `params_json` | 本次 run 的运行参数（如起止时间、初始资金等） |
| `status` | 生命周期状态：`running` → `completed` / `failed`（实盘另有 `stopping`/`not_active` 等瞬态，见下） |
| `report_json` | run 完成后的回测报告（指标、closed trades 等）序列化 JSON |
| `error` | 失败时的错误信息 |
| `recording_path` | 本次 run/session 对应的节点级 I/O 轨迹 SQLite 文件路径（见 9.5 节），前端"查看 trace"/"查看 LLM 分析记录"都靠这个字段定位到具体文件 |
| `created_ms` | 创建时间（毫秒时间戳），列表接口按此倒序 |

#### 9.3.3 生命周期方法

`create` 插入一行，状态硬编码为 `'running'`：

```python
def create(self, run_id, blueprint_id, blueprint_json, params_json, created_ms):
    with self._lock:
        self._db.execute(
            "INSERT INTO runs VALUES (?,?,?,?, 'running', NULL, NULL, NULL, ?)",
            (run_id, blueprint_id, blueprint_json, params_json, created_ms))
        self._db.commit()
```

`set_status` 用 `COALESCE(?, 旧值)` 模式做"只更新提供的字段，其余保持不变"的部分更新，一次调用既能只改状态，也能在状态转移的同时补上 `report_json`/`recording_path`：

```python
def set_status(self, run_id, status, report_json=None, error=None, recording_path=None):
    with self._lock:
        self._db.execute(
            "UPDATE runs SET status=?,"
            " report_json=COALESCE(?, report_json),"
            " error=COALESCE(?, error),"
            " recording_path=COALESCE(?, recording_path) WHERE run_id=?",
            (status, report_json, error, recording_path, run_id))
        self._db.commit()
```

实际状态转移由调用方驱动：`api/service.py` 里的 `RunService._worker` 在回测线程结束时调用 `set_status(run_id, status, report_json=..., recording_path=report.recording_path)`（成功）或 `set_status(run_id, "failed", error=str(exc))`（异常）；`api/live.py` 的 `LiveService` 则在启动实盘 session 时把 `recording_path` 设为 `record_dir / f"live_{session_id}.sqlite"`。

`get`/`list` 是只读查询：`get(run_id)` 取整行并转成 dict；`list()` 只取轻量字段（不含 `blueprint_json`/`report_json`/`recording_path` 这些大字段）按 `created_ms DESC` 排序，用于前端的 run 列表页，避免一次性拉回全部大字段拖慢列表接口。

`api/app.py` 里暴露的 REST 接口直接包装这两个方法：

```python
@app.get("/api/runs")
def runs_list():
    return store.list()

@app.get("/api/runs/{run_id}")
def run_get(run_id: str):
    row = store.get(run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    out = {"run_id": row["run_id"], "status": row["status"],
           "params": json.loads(row["params_json"] or "{}"),
           "error": row["error"]}
    if row["report_json"]:
        out["report"] = sanitize(json.loads(row["report_json"]))
    return out
```

真实落盘的 `data/runs.sqlite` 中的一行示例（字段裁剪展示）：

```
run_id=066adae7ab02, blueprint_id=agent_committee_v1, status=failed,
error="offline replay miss for request hash 642350b2...; re-run in record mode to capture it",
created_ms=1783266781992

run_id=61c117f5d7dd, blueprint_id=agent_committee_v1, status=completed, error=NULL
run_id=9819dc9b4e90, blueprint_id=real_sol_breakout_demo_v1, status=completed, error=NULL
```

第一行的错误信息（"offline replay miss"）体现了 LLM 调用录制/回放机制与 run 生命周期的耦合：offline 模式下若某次 LLM 请求没有命中此前录制的缓存，run 会直接失败并把这个诊断信息写进 `error` 字段。

FastAPI 生命周期钩子在应用关闭时调用 `store.close()` 释放连接：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.loop = asyncio.get_running_loop()
    yield
    store.close()   # 关闭 RunsStore 的 sqlite 连接，避免 ResourceWarning 泄漏
```

### 9.4 ExperienceStore —— 反思闭环的记忆层（`memory/experience_store.py`）

#### 9.4.1 设计动机：市场状态桶隔离

`ExperienceStore` 是"反思—经验复用"闭环的记忆载体：`ReflectorNode` 在每笔交易平仓后对"决策过程"与"结局"做四象限打分并生成一条教训（lesson），`ExperienceWriteNode` 把它落库；下一次决策时 `ExperienceRetrieveNode` 按**当前市场状态桶**查库，把历史教训作为上下文注入决策链路。模块文档字符串把这个数据流讲得很清楚：

> 反思闭环的记忆层：平仓后 Reflector 把 {桶, 配置摘要, 结局, 教训} 写进 ExperienceStore；下一次决策时 ExperienceRetrieve 按**当前市场状态桶**检索历史教训注入决策上下文。

"桶"（regime bucket）是纯函数 `derive_regime_bucket(ema, ema_prev, atr)` 从 EMA 斜率和 ATR 派生的三态标签：

```python
_SLOPE_ATR_RATIO = 0.10

def derive_regime_bucket(ema, ema_prev, atr) -> str:
    if ema is None or ema_prev is None or atr is None:
        return "range"
    atr_f = float(atr)
    if atr_f <= 0:
        return "range"
    slope = float(ema) - float(ema_prev)
    if abs(slope) / atr_f <= _SLOPE_ATR_RATIO:
        return "range"
    return "trend_up" if slope > 0 else "trend_down"
```

用 ATR 归一化斜率（把"EMA 涨了多少"换算成"涨了多少个 ATR"）是为了让阈值 `0.10` 在不同价位、不同波动率的品种上都稳健，而不是一个绝对价格阈值。数据不足（未 warmup）时保守归为 `range`，不冒进猜趋势。这个桶既是写入键的一部分，也是检索的过滤键——**训练于 `trend_up` 市况下的教训只会在下次识别为 `trend_up` 时被召回**，不会跨状态串味到 `range` 或 `trend_down` 的决策里。

#### 9.4.2 表结构与幂等写入

```sql
CREATE TABLE IF NOT EXISTS experience (
    bucket         TEXT NOT NULL,
    trade_key      TEXT NOT NULL,
    config_summary TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    pnl            REAL NOT NULL,
    lesson         TEXT NOT NULL,
    PRIMARY KEY (bucket, trade_key)
)
CREATE INDEX IF NOT EXISTS idx_bucket ON experience(bucket)
```

`(bucket, trade_key)` 联合主键是幂等写入的关键：`write()` 用 `ON CONFLICT DO UPDATE` 做 UPSERT，同一笔平仓交易即便因为引擎重放或重复触发导致反思逻辑跑了多次，也只会在库里留下一行，不会把同一条教训重复灌入：

```python
def write(self, *, bucket, trade_key, config_summary, outcome, pnl, lesson) -> None:
    conn = self._connect()
    try:
        with conn:
            conn.execute(
                """INSERT INTO experience
                       (bucket, trade_key, config_summary, outcome, pnl, lesson)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bucket, trade_key) DO UPDATE SET
                       config_summary=excluded.config_summary,
                       outcome=excluded.outcome, pnl=excluded.pnl, lesson=excluded.lesson""",
                (bucket, trade_key, config_summary, outcome, float(pnl), lesson))
    finally:
        conn.close()
```

`_connect()` 每次调用都开一条**短命连接**并显式关闭，注释解释了原因：节点（`ExperienceRetrieveNode`/`ExperienceWriteNode`）没有显式的 `close()` 生命周期钩子，如果持有长命连接会造成句柄泄漏，在 Windows 上还会锁住数据库文件、破坏测试的 teardown——这是代码注释中明确标注的"评审 PLAUSIBLE 修"（即一次代码评审后采纳的修复）。

#### 9.4.3 检索路径

`retrieve(bucket, top_k)` 按桶过滤、按 `rowid DESC`（最近写入优先）取前 `top_k` 条：

```python
def retrieve(self, *, bucket: str, top_k: int = 5) -> list[dict]:
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT bucket, trade_key, config_summary, outcome, pnl, lesson
                   FROM experience WHERE bucket = ?
                   ORDER BY rowid DESC LIMIT ?""",
            (bucket, int(top_k))).fetchall()
    finally:
        conn.close()
    cols = ("bucket", "trade_key", "config_summary", "outcome", "pnl", "lesson")
    return [dict(zip(cols, r)) for r in rows]
```

"最近写入优先"的排序策略意味着经验库更看重近期教训的时效性，而不是做全局统计意义上的最优检索——这与整个反思闭环"实时调整、快速响应最近一次失误"的设计目标一致。

#### 9.4.4 与节点层的接线：写入与读取两端

`nodes/reflection.py` 中的 `ReflectorNode` 是教训的生产者。它读取 `ctx.broker.closed_trades`（`PaperBroker` 每笔平仓后追加的 `{ts, pnl, entry_side}`），用四象限规则区分"过程好坏 × 结局好坏"：

| 过程 \ 结局 | 好结局（pnl > 0） | 坏结局 |
|---|---|---|
| 过程好（confidence ≥ 0.5 且有 rationale/citations） | `reasonable_and_right` | `reasonable_but_wrong`（不惩罚，是设计上的招牌卖点：好过程遇到坏运气不该被过度纠正） |
| 过程坏 | `lucky`（好运气不算本事） | `bad_process`（该改进） |

`ReflectorNode` 只在检测到 `closed_trades` 相对上次消费游标有新增时才产出非 `None` 的 `verdict`（每根 bar 最多消费一笔，用 `self.state["consumed"]` 记录游标，保证多笔平仓也能逐 bar 排空、一笔不丢）。`verdict` 载荷形如：

```python
{"verdict": "reasonable_but_wrong", "bucket": "trend_up", "pnl": -69.0,
 "trade_key": "2:3660000:short",
 "config_summary": "side=long; rationale=committee endorses the strategist's long thesis",
 "lesson": "In trend_up, long was well-reasoned but lost to the market — process was fine, don't over-correct on one bad outcome."}
```

`trade_key` 编码了"全局序号 : 平仓时间戳 : 入场方向"，确保同一根 bar 内多笔同向平仓也能生成互不覆盖的唯一键。

`nodes/rag_nodes.py` 中的 `ExperienceWriteNode` 消费这个 `verdict` 并调用 `ExperienceStore.write(...)` 落库（`verdict is None` 时 no-op）；`ExperienceRetrieveNode` 则在每根 bar 用当前 `ema`/`atr`（配合自身 `self.state` 记住的上一根 `ema_prev`）算出当前桶，调用 `retrieve(bucket, top_k)`，把命中的 `lesson` 列表作为 `lessons` 输出引脚喂给下游决策节点。两个节点的 `db_path` 参数默认都是 `_DEFAULT_EXPERIENCE_DB = "data/experience.sqlite"`（可在蓝图 params 里覆盖，测试中会指向 `tmp_path`）。真实运行产生的 `data/experience.sqlite` 里已经积累了上百条这样的记录（如上表所示的三条真实样本）。

这两个节点的 cost 标注都是 `llm_calls_per_bar=0, deterministic=True`——反思分类和经验检索都是确定性的纯计算/纯 SQL 查询，不触发 LLM 调用，因此不计入 LLM 审计红线的调用预算。

### 9.5 知识语料库（`knowledge/corpus.py`）

#### 9.5.1 定位：自撰静态语料 + 零依赖 BM25

`knowledge/corpus.py` 与 `ExperienceStore` 是两种截然不同的"记忆"：`ExperienceStore` 是动态积累的、由本系统自己交易产生的经验；`Corpus` 则是静态的、人工撰写的策略机制说明文档，作为 RAG 节点的知识背书来源。模块文档字符串写明了两个设计取舍：

> 语料是 `data/` 下三个自撰 markdown（grid/dca/price_action）。... 纯函数、无随机——同 query 同结果（确定性，配合 KnowledgeRetrieve cost 0 deterministic）。不引第三方（无 rank_bm25 / sklearn）。

语料文件位于 `backend/alphaloom/knowledge/data/{grid,dca,price_action}.md`，每个文件都是中英对照、手写的策略机制说明。例如 `grid.md` 开头即注明"Hand-written summary for AlphaLoom RAG. Original notes, not copied from any third-party corpus"，内容覆盖网格间距、层数、单边趋势突破网格边界的核心风险等要点。这个语料库本身不含任何数据库，是运行时从磁盘 markdown 文件加载进内存的只读结构。

#### 9.5.2 索引结构与分词

`Corpus.__init__` 把每个文档按空行切成"段落"作为检索单元，每个单元是 `(doc_id, 段落原文, token 列表)` 三元组：

```python
class Corpus:
    def __init__(self, docs: dict[str, str]):
        self._units: list[tuple[str, str, list[str]]] = []
        for doc_id, raw in docs.items():
            for para in _split_paragraphs(raw):
                self._units.append((doc_id, para, _tokenize(para)))
        self._df: dict[str, int] = {}
        for _, _, toks in self._units:
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1
        self._n = len(self._units)
        self._avg_len = sum(len(toks) for _, _, toks in self._units) / self._n if self._n else 0.0
```

分词函数 `_tokenize` 同时处理英文和中文：英文走 `[a-z0-9]+` 正则分词；中文没有天然分词空格，采用"CJK 连续片段按相邻 2-gram 切分"的简单策略（如"马丁格尔"切成"马丁/丁格/格尔"），单字连续片段则保留单字。查询串和文档共用同一套分词规则，所以中文查询（如"马丁格尔 爆仓"）能命中语料里的中文段落。

#### 9.5.3 BM25 打分

`_score(query_terms, toks)` 实现标准 BM25 公式，`k1=1.5, b=0.75` 是经典默认参数：

```python
def _score(self, query_terms, toks) -> float:
    length = len(toks)
    tf = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for term in query_terms:
        f = tf.get(term, 0)
        if f == 0:
            continue
        idf = self._idf(term)
        denom = f + _K1 * (1 - _B + _B * length / self._avg_len)
        score += idf * (f * (_K1 + 1)) / denom
    return score
```

`idf` 用带 +0.5 平滑的经典 BM25 idf 公式且非负截断。`search(query, top_k)` 对语料全体检索单元打分、过滤掉零分命中，按 `(-score, doc_id)` 排序（分数相同按 `doc_id` 兜底，保证结果确定性、可复现），返回 `Hit(doc_id, text, score)` 列表：

```python
@dataclass(frozen=True)
class Hit:
    doc_id: str
    text: str
    score: float
```

`load_default_corpus()` 从磁盘三个 markdown 文件构造默认语料库实例，是外部代码使用该模块的唯一入口。

#### 9.5.4 与 RAG 节点的接线

`nodes/rag_nodes.py` 里的 `KnowledgeRetrieveNode` 在模块级用一个全局单例缓存语料库（`_CORPUS`，`_corpus()` 惰性加载一次后全局复用，因为语料是纯只读、无网络/随机的静态数据，没有必要每次 `on_bar` 都重新解析 markdown）：

```python
_CORPUS = None

def _corpus():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = load_default_corpus()
    return _CORPUS
```

节点在每根 bar 用运行时输入的 `query`（若提供，覆盖静态 params 里配置的 `query`）调用 `_corpus().search(query, top_k=self.top_k)`，把命中格式化成形如 `"grid: 核心风险是强单边趋势突破网格边界..."` 的 citation 字符串列表输出：

```python
def _format_citation(hit) -> str:
    snippet = hit.text.strip()
    if len(snippet) > 160:
        snippet = snippet[:160].rstrip() + "…"
    return f"{hit.doc_id}: {snippet}"
```

下游的 `RequireCitationsNode` 则实现"强制引用"的软约束门控：`signal.side` 为 `long`/`short` 且 `citations` 为空时，把该信号强行降级为 `hold`（`qty=0.0, stop=None`，并在 `reason` 字段写明 `"blocked: trade requires non-empty citations"`）。这样知识库检索与交易信号形成了一条可选的背书链路——画布上把 `knowledge_retrieve.citations` 接到 `require_citations.citations` 引脚，就构成"未经知识库背书的交易不放行"的约束；不接则该引脚悬空为 `None`，退回到只看 `signal` 自带的 citations。`KnowledgeRetrieveNode` 的 cost 标注同样是 `deterministic=True`——BM25 检索是纯计算，不调用 LLM，不占用 LLM 审计红线的预算。

### 9.6 全系统 SQLite 数据库一览

把上述几个存储放在一起看，AlphaLoom 在磁盘上实际运行时会产生这样一组 `.sqlite` 文件（以 `serve.py` 里 `create_default_app()` 的默认路径 和 实际观测到的 `data/` 目录内容为例）：

| 文件 | 归属模块 | 生命周期 / 粒度 | 内容 |
|---|---|---|---|
| `data/demo.sqlite` | `SQLiteMarketData`（9.2 节） | 长期保留，跨 run 复用 | 行情蜡烛（`1m` 原始 + 运行时聚合出的其他周期），按 `inst/bar` 划分 |
| `data/runs.sqlite` | `RunsStore`（9.3 节） | 长期保留，全局唯一一份 | 所有回测 run + 实盘 live session 的元数据、状态、蓝图快照、report 摘要 |
| `runs/run_{run_id}.sqlite`（如 `data/run_7ea7e6616ac2.sqlite`） | `Recorder`（`runtime/recorder.py`） | 每次 run 一份，由 `RunsStore.recording_path` 指向 | 节点级 I/O 逐 bar 轨迹（`node_io` 表：`run_id, event_idx, ts, node_id, inputs_json, outputs_json`），供 `/api/runs/{run_id}/trace` 回放调试 |
| `runs/live_{session_id}.sqlite` | `LiveAnalysisStore`（`api/live.py`） | 每个实盘 session 一份，同样由 `recording_path` 指向 | LLM 驱动的分析/委员会节点在实盘轮询过程中的逐次调用记录（`live_analysis` 表：`session_id, event_idx, bar_ts, prompt_hash, model, input_json, output_json`），供 `/api/live/{session_id}/analysis` 查看 |
| `data/experience.sqlite` | `ExperienceStore`（9.4 节） | 长期保留，跨 run 累积 | 按市场状态桶索引的交易反思教训 |
| `data/llm_calls.sqlite` | `RecordingLLMClient`（`llm/recording.py`，第 8 节涉及） | 长期保留 | LLM 请求/响应的录制缓存，支撑 offline 回放模式 |

这些数据库彼此的关系可以概括为：

1. **行情库（`demo.sqlite`）以回测只读、Live 可追加为边界**——普通回测只从 `DataSource.iter_candles` 拉蜡烛喂给节点图，不会反向写回；实时纸上交易会话会把轮询到的新 OKX K 线通过 `SQLiteMarketData.insert_candles()` 写入同一张 `candles` 表，供后续离线复盘/回放复用。
2. **`runs.sqlite` 是索引/注册表**，本身不存重量级数据，而是通过 `recording_path` 字段"指针式"地关联到每次 run/session 各自独立的 `Recorder`/`LiveAnalysisStore` 数据库文件——这是一个一对多的星型关系：一行 `runs` 记录对应一个独立的 per-run 录制文件。这样设计的好处是重量级的逐 bar 轨迹数据不会拖慢 `runs.sqlite` 本身的读写（`RunsStore.list()` 之所以能只选轻量字段快速返回列表，正是因为大数据量的 node_io/live_analysis 都分离到了各自的文件里）。
3. **`experience.sqlite` 是横向共享的记忆**，不属于任何单次 run，而是被所有 run（只要蓝图里接了 `ExperienceRetrieve`/`ExperienceWrite` 节点）共同读写、跨 run 持续积累——这是它与 per-run 的 `Recorder`/`LiveAnalysisStore` 在生命周期上的本质区别。
4. **知识语料库不是数据库**，是进程内存对象（`Corpus` 实例），从 markdown 静态文件加载，服务重启后重新解析，不持久化任何检索状态或用户数据。
5. **`llm_calls.sqlite`** 独立于以上所有存储，是 LLM 传输层的横切关注点（跨 run 的调用去重/回放缓存），与具体某次 run 的业务数据无直接外键关系，只通过 `prompt_hash` 等键做请求级别的复用判断。

从 API 层能直观看到这种"指针关联"模式的体现，例如 `/api/runs/{run_id}/trace` 先查 `RunsStore` 拿到 `recording_path`，再单独打开那个 per-run 的 SQLite 文件查询 `node_io` 表：

```python
@app.get("/api/runs/{run_id}/trace")
def run_trace(run_id: str, node_id: str | None = None, event_idx: int | None = None, limit: int = 200):
    row = store.get(run_id)
    if row is None or not row["recording_path"]:
        raise HTTPException(404, "run or recording not found")
    db = sqlite3.connect(row["recording_path"])
    q = "SELECT run_id, event_idx, ts, node_id, inputs_json, outputs_json FROM node_io WHERE run_id=?"
    ...
```

`/api/live/{session_id}/analysis` 是完全对称的模式，只是指向 `LiveAnalysisStore` 而非 `Recorder`。这种"轻量注册表 + 指针指向重量级 per-run 文件"的分层，是本节几个存储组件共同遵循的一条设计主线。

---

## 10. 评估实验室与策略进化

AlphaLoom 的评估层不是"跑一次回测拿个收益率"那么简单——它的核心卖点是**诚实**：承认回测会撒谎、承认样本内表现不可信、承认单窗口因果结论是轶事而非定律。这一整套设计的方法论说明集中写在 `docs/evaluation-methodology.md` 里，开篇就定了调："AlphaLoom is a **methodology demonstration**... Honesty is the selling point; overstating the tools would defeat the entire purpose."（`docs/evaluation-methodology.md:6-8`）本节覆盖的五个模块——保真度阶梯、消融实验、记分卡、排行榜、进化实验室——全部遵循同一条纪律：**缺证据不给满分，证据必须能被批判**。

它们的实现都集中在 `backend/alphaloom/eval/` 与 `backend/alphaloom/evolve/` 两个包，全部是**零 LLM 配额的纯数值计算**（进化实验室的变异算子例外，见下文），供 FastAPI 层在 `backend/alphaloom/api/app.py` 的 `/api/eval/*` 与 `/api/evolve` 端点下暴露。

### 10.1 保真度阶梯（fidelity.py）：回测测谎仪

**要解决的问题**：任何回测引擎在"决策"与"成交假设"之间都有一层隐含的乐观偏差——用收盘价决策却假设能以更好的价格成交，是最常见的自欺欺人方式。`fidelity_ladder()` 的设计目标不是重新模拟市场，而是**在决策不变的前提下，只替换成交假设**，从而单独隔离出"回测在哪一档开始撒谎"。模块头部注释把这个定位讲得很直白：

> "把一次回测**已生成的成交序列（Fill 列表）**在四档成交模型下**重新撮合**，量化'回测在哪一档开始撒谎'。**不重跑 LLM**：决策（哪根 bar 想 long/short/平仓）不变，只变成交撮合假设，故零配额、纯数值。"（`backend/alphaloom/eval/fidelity.py:1-5`）

#### 四档成交模型

| 档位 | 成交价假设 | 含义 |
|---|---|---|
| L0 | 信号 bar 收盘价 vs L1 基准价，取对交易者更有利一侧 | 最乐观——无滑点、无时序延迟惩罚 |
| L1 | 次 bar 开盘价 | `PaperBroker` 现状语义（回测基线） |
| L2 | L1 价 + 不利偏移 `(high-low)/2`，clamp 到执行 bar `[low, high]` | 盘中路径代理——用 OHLC 估更差成交 |
| L3 | L2 价（clamp 后）+ `slippage_bps` 名义滑点（不 clamp） | 手续费+滑点加压 |

四档定价的核心函数是 `_price_for_level()`（`fidelity.py:115-139`）。关键设计点：

- **L2 必须 clamp，L3 故意不 clamp**。L2 的偏移是路径代理，必须落在观测到的 `[low, high]` 区间内，否则病态宽振幅的 bar 会产生负价；L3 的滑点代表市场冲击，允许越出观测成交带——如果对 L3 也做 clamp，会在最需要加压的病态 bar 上把滑点惩罚归零，与 L3"加压"的目的相反（`fidelity.py:11-16`）。
- **腿级基准价随 fill 的来源（tag）而不同**，以便 L1 精确复现 `PaperBroker` 的真实 `net_pnl`：
  - 常规腿：执行 bar（信号次 bar）开盘价；
  - **stop 腿**（`tag="stop"`，`PaperBroker` 约定）：基准价 = stop 位本身（盘中触发成交），因为 `PaperBroker.on_bar` 是盘中触发、不是等到次 bar 开盘；
  - **eod_close 结算腿**（数据外合成 bar，`ts` 不在 candles 里）：基准价 = 末 bar 收盘价（runner 的 EOD 结算价），且 `signal_ts = exec_ts`，因为结算腿没有"时序谎言"可言。

  这套腿级基准价的反推逻辑在 `replay_intents()`（`fidelity.py:65-101`）中实现——它从 fills + candles 反推出一个 `Intent` 列表（`side`、`qty`、`exec_ts`、`signal_ts`、`base_price`、`tag`），供逐档重放使用。

- **单调性契约（模块的"心脏"）**：`net_pnl` 满足 `L0 ≥ L1 ≥ L2 ≥ L3`，且是**按构造成立**的——每一档对每笔成交施加的都是相对 L1 基准价单调非减的不利偏移，未平仓头寸的盯市价（mark）恒用 L1 基准价，不随档位变化（否则进场滑点会反向抬高盯市、破坏单调性，见 `fidelity.py:159-161` 的注释）。这意味着测试如果发现某组 fills 下 `L2 > L1`，那一定是成交模型实现有 bug，而不是行情方向使然。

#### 输出结构

```python
@dataclass(frozen=True)
class LevelResult:
    level: str            # "L0" | "L1" | "L2" | "L3"
    net_pnl: float
    max_dd: float
    num_trades: int
    profit_factor: float  # inf 序列化为 None

@dataclass(frozen=True)
class LadderReport:
    levels: list[LevelResult]
    optimism_gap: float    # L0.net_pnl - L3.net_pnl，越大回测越乐观
```

`fidelity_ladder(fills, candles, *, initial_cash=10_000.0, fee_rate=0.0005, slippage_bps=5.0) -> LadderReport`（`fidelity.py:250-267`）是唯一入口。`optimism_gap` 是这个模块对外暴露的**头条数字**：它回答"一旦你不再相信最乐观的成交故事，账面利润蒸发了多少"。

在 API 层，`/api/eval/fidelity`（`app.py:535-560`）接收 `run_id`，从 `RunsStore` 里取出已完成回测的 `report.fills` 与同窗 `candles`（重新从 `SQLiteMarketData` 拉取），调用 `fidelity_ladder()` 后原样返回。运行状态非 `completed` 直接 409（`app.py:543-546`）——保真度阶梯天然依赖一条**已经产生过**的 fill 序列，不能对着"还没跑完"的 run 重放。

方法论文档明确划了这个模块的能力边界：它不是订单簿模拟、没有排队位置和部分成交、L3 的滑点是"名义额线性模型"而非校准过的市场冲击曲线；stop 腿的重放依赖 `tag="stop"` 这个约定识别，其它来源不带该 tag 的 fills 会被系统性乐观地按"次 bar 开盘"重放（`docs/evaluation-methodology.md:38-52`，与 `fidelity.py:37-39` 的"已知局限"一致）。这类边界在文档里被称为"诚实声明的窄claim"：它量化的是**回测的乐观偏差**，不是"实盘能赚多少"。

### 10.2 委员会消融实验（ablation.py）：护栏价值量化

消融实验回答的问题是："LLM 软护栏（风控官角色）真的有用吗？RAG 引用门真的有用吗？"它的实现方式不是手写三份蓝图，而是**对同一张蓝图做图变换（graph surgery）**——模块头部注释直接把这一点标为卖点：

> "三臂**由图变换生成**（消融 = 可编程的图手术，不是三份手写蓝图——这是卖点）"（`ablation.py:1-3`）

#### 三条臂

| 臂名 | 手术方式 | 消融对象 |
|---|---|---|
| `full` | 原蓝图深拷贝，不做任何改动 | （对照组） |
| `no_risk_officer` | `committee` 节点参数注入 `skip_risk_officer=True` | LLM 软护栏（风控官角色） |
| `no_rag` | `graph_bypass()` 旁路所有 `require_citations` 节点，再 `_drop_nodes()` 移除 `knowledge_retrieve` | RAG 引用背书门槛 |

`arm_blueprint(bp, arm)`（`ablation.py:96-121`）是分发函数，按臂名做变换并返回**全新副本**（绝不改动传入的 `bp`）。图手术的核心原语是 `graph_bypass()`（`ablation.py:49-78`）：把某类型节点从图上摘掉，其输出端口的所有下游消费者改接原节点输入端口的上游源。

这里有一个刻意设计、且被测试锁定的边界：`graph_bypass()` **故意不做类型检查**，把裁决权完全交给编译器。这正是"硬护栏不可消融"这一卖点的机制来源——`RiskGate` 节点是全宇宙唯一能产出 `risk_stamped_signal` 类型的节点，`execute_order` 只接受这个类型；如果消融想把 `RiskGate` 也旁路掉，编译器会直接报 `TYPE_MISMATCH` 拒绝：

> "消融能拆的只有 LLM 软护栏；类型系统硬护栏不可消融——这是卖点，不是缺陷。"（`ablation.py:9-11`）

而 `require_citations` 之所以能被消融，是因为它是一个 `SIGNAL → SIGNAL` 的**透传门**（软约定），旁路后类型仍然闭合、编译照过；`risk_gate` 是 `SIGNAL → RISK_STAMPED_SIGNAL` 的**换类型门**，旁路后下游拿到裸 `SIGNAL` 类型不匹配。软约定与硬类型的差别在这里显形。

#### 运行期证据收集

每条臂用同一数据同一窗口跑 `run_backtest(..., breakpoints="all", on_pause=collector.on_pause)`，通过 `_ArmCollector`（`ablation.py:127-143`）在**不修改 runner 本身**的前提下旁路收集两类运行期证据：

- **`num_vetoes`**：下游节点输入信号里 `committee_trace` 含 `veto=true` 的 bar 数，按 bar 去重；
- **`verdict_counts`**：反思四象限（reflector）verdict 的计数，按 `trade_key` 去重。

#### `guardrail_value`：正负都如实报告

`_guardrail_value()`（`ablation.py:185-211`）计算 `full` 与 `no_risk_officer` 两臂之间的验证窗指标差（`net_pnl_delta`、`return_pct_delta`、`max_drawdown_delta` 等），并给出布尔值 `guardrail_helped = delta > 0`。设计上明确拒绝"美化"：

```python
"note": ("computed from paired runs on the same data/window; the sign is "
         "whatever the data says - a negative delta (risk officer "
         "net-blocked winners) is reported as-is"),
```

方法论文档记录了一个真实的、颇具张力的演示结果：在录制的 demo 窗口里，`guardrail_helped = False`——风控官净拦下了本会盈利的交易（`docs/evaluation-methodology.md:159-165`）。这不是被隐藏或调整过的结果，而是刻意如实展示：消融模块的测试套件里同时准备了"风控官拦下暴跌前危险提案"（正向剧本）与"偏执风控官净杀盈利交易"（负向剧本）两种录制脚本，无论真实录制落在哪一边都原样展示。

主入口 `committee_ablation(base_blueprint, source, *, inst, bar, start_ms=None, end_ms=None, llm=None, arms=None, initial_cash=10_000.0, fee_rate=0.0005) -> AblationReport`（`ablation.py:217-253`）默认跑 `DEFAULT_ARMS = ("full", "no_risk_officer", "no_rag")` 全三臂，`llm` 由调用方注入（测试用确定性 fake transport，demo 用录制回放），本模块自身不创建任何 LLM 客户端。

`AblationReport.to_dict()` 的形状：

```python
{
  "arms": [
    {"arm": "full", "run_id": "...", "blueprint_id": "agent_committee_v1",
     "bars": 50, "summary": {...}, "num_vetoes": 3, "num_trades": 7,
     "verdict_counts": {"good_hold": 2, "bad_hold": 1, ...}},
    {"arm": "no_risk_officer", ...},
    {"arm": "no_rag", ...}
  ],
  "guardrail_value": {
    "arms_compared": ["full", "no_risk_officer"],
    "net_pnl_full": ..., "net_pnl_no_risk_officer": ...,
    "net_pnl_delta": ..., "return_pct_delta": ..., "max_drawdown_delta": ...,
    "num_vetoes_full": 3, "num_trades_full": 7, "num_trades_no_risk_officer": 9,
    "guardrail_helped": false,
    "note": "computed from paired runs..."
  }
}
```

方法论文档同样标注了这个模块能证明什么、不能证明什么：这是 **N=1、单窗口**的因果claim——"某个具体窗口里风控官的否决恰好命中了本会赢的交易"，不能推出"风控官是好是坏"的一般性结论（`docs/evaluation-methodology.md:167-176`）。

### 10.3 记分卡与排行榜：诚实综合分与对手盘

#### 记分卡（scorecard.py）

`scorecard(train_report, valid_report=None, *, ladder=None, cost_cert=None, ablation=None) -> Scorecard`（`scorecard.py:214-289`）把一次回测的多类证据聚合成一个**可批判的排序键**（0-100 综合分），用于 gallery 排序。

综合分 = 四个分项加权求和，权重集中在模块顶部常量区（`scorecard.py:62-72`），设计理由写在 docstring 里：

| 分项 | 权重 | 计算方式 | 设计理由 |
|---|---|---|---|
| `valid_performance` | 0.40 | 验证窗 `return_pct` 经 `tanh(r / 10)` 压缩到 0-100（0% = 50 分中性） | 单项最大——样本内表现是"自己出题自己判卷"，保真度阶梯已证明回测会撒谎，不能让样本内数字进排序键 |
| `generalization` | 0.25 | `gap = train.return_pct - valid.return_pct`；`gap ≤ 0` 满分，每 1 个百分点扣 10 分 | 过拟合的直接定价 |
| `fidelity` | 0.20 | 以 L1 pnl 绝对值为尺度，惩罚 `optimism_gap` 与 L1→L3 衰减各半 | 盈利在更真实成交假设下蒸发越多，分越低 |
| `determinism` | 0.15 | `100 × deterministic_ratio`（来自成本证书） | 可复现性是证据强度——全确定性蓝图任何人可零成本复算 |

关键的保守机制是 `MISSING_EVIDENCE_SCORE = 25.0`：**缺证据不给中性 50 分、更不给满分，而是按"弱证据"计**，且 `evidence_coverage` 字段如实标注缺了什么维度。docstring 举了一个具体设计上限："无验证窗（但有交易）的记分卡 composite 封顶 61.25"（`scorecard.py:38-40`）——验证窗维度至多贡献 20/40，样本外证据缺席时排序键被显著压低。

**零交易 = 零证据**是另一条硬规则，且有真实审查教训作为依据：

> "躺平策略曾靠这 0.60 权重的空洞满分拿 80 分、压过 78.33 的真实盈利者。"（`scorecard.py:45-47`）

因此排序窗口（valid 优先，否则 train）`num_trades == 0` 时，`valid_performance`/`generalization`/`fidelity` 三项一律强制按 `MISSING_EVIDENCE_SCORE` 计，`determinism` 保留（它是编译期属性，不依赖交易）——`scorecard()` 函数体第 247-249 行直接覆写这三项。"躺平 + 全证据"的 composite 约为 36.25，低于任何有真实交易证据的盈利策略。

`Scorecard` 数据类（`scorecard.py:182-211`）的 `to_dict()` 输出：

```json
{
  "composite": 61.25,
  "components": {"valid_performance": 50.0, "generalization": 25.0,
                 "fidelity": 100.0, "determinism": 100.0},
  "weights": {"valid_performance": 0.4, "generalization": 0.25,
              "fidelity": 0.2, "determinism": 0.15},
  "evidence_coverage": {"valid_window": false, "fidelity_ladder": true,
                        "cost_certificate": true, "ablation": false,
                        "trading_activity": true, "ratio": 0.6},
  "generalization_gap": null,
  "in_sample_only": true,
  "notes": ["no validation window: performance is in-sample only (discounted), generalization unverifiable"]
}
```

`docs/evaluation-methodology.md:87-100` 进一步坦白这套评分是"设计决策不是统计推断"——`tanh` 压缩尺度（`RETURN_SQUASH_SCALE_PCT=10`，+10% 收益 ≈ 88 分）是主观锚点，四权重是编辑立场不是拟合参数，"零交易=零证据"这条规则会误伤"合法低频但真有信号"的策略。这些常量全部集中在模块顶部，方法论文档称之为"欢迎批判的排序键，不是 p 值"。

`/api/eval/scorecard`（`app.py:562-575`）是这套评分数学的**唯一后端实现入口**——注释解释了为什么要单独设一个端点而不是让前端自己算：

> "评分数学只在后端一份实现（tanh 压缩 / 四权重 / 缺证据保守分），前端绝不重实现以防与诚实评分口径漂移。"（`app.py:565-566`）

#### 排行榜（leaderboard.py）

`leaderboard(entries) -> Board`（`leaderboard.py:213-242`）的定位是给 Agent 蓝图找"对手"——"让 Agent 蓝图有对手（零 LLM 配额）"（`leaderboard.py:1`）。它提供三个零 LLM 基线生成器，全部产出 `BacktestReport` 形状（`certificate.llm_calls_per_bar=0`）：

| 基线 | 逻辑 | 用途 |
|---|---|---|
| `baseline_buy_hold` | 首 bar 开盘全仓买入，持有到末 bar 收盘卖出；fee 语义与 `PaperBroker._fill` 完全一致 | 最朴素的"什么都不做但持有"对照 |
| `baseline_ema_default` | 默认参数 `ema_cross.loom` 跑真实 `run_backtest`（纯确定性节点，零 LLM） | 一个简单规则策略的对照 |
| `baseline_random` | 随机进出场（固定 `seed` 可复现），决策次 bar 开盘成交（对齐 `PaperBroker` 时序） | "运气基线"——`certificate` 显式带 `luck_baseline=True` 与 `seed` |

`baseline_random` 的存在意义被直接写明："它存在的意义是给排行榜一个'纯运气能拿多少'的参照，任何打不过它的策略都不配谈信号。"（`leaderboard.py:15-16`）

排行榜的诚实规则（`leaderboard.py:17-26`）：

- 排序默认按**验证窗** `return_pct`；无 valid 的行用 train 排序并标 `in_sample_only=true`，行指标同样取排序窗口——绝不用样本内的漂亮数字给无验证窗的行贴金；
- 蓝图打不过基线时如实排在下面，零美化；
- `generalization_gap = train.return_pct - valid.return_pct`（无 valid 为 `None`），过拟合直接暴露在行上。

`Board.to_dict()` 形状：

```json
{
  "rows": [
    {"name": "baseline_random", "kind": "baseline", "net_pnl": 120.5,
     "return_pct": 1.2, "max_dd": 0.03, "win_rate": 0.5, "num_trades": 6,
     "generalization_gap": null, "in_sample_only": true},
    {"name": "my_blueprint", "kind": "blueprint", "return_pct": -0.8, ...}
  ],
  "sort_key": "return_pct",
  "ranking_window": "valid_first"
}
```

`/api/eval/leaderboard`（`app.py:577-618`）固定跑三条基线，可选再塞一张指定蓝图（`body.blueprint`）一起上榜；若该蓝图含 LLM 节点且当前 LLM 客户端非 offline，`_guard_llm_blueprint()` 会 409 拒绝（详见 10.5 节）。

方法论文档如实标注了排行榜证据基础的薄弱之处：默认离线数据库是合成的 BTC/ETH 蜡烛加一段真实 OKX `SOL-USDT-SWAP` smoke 窗口，窗口规模是"几十到几百根 1 分钟 bar"，没有跨市场/跨周期的鲁棒性验证——"在这一个小型演示窗口上打赢基线"只是一次 sanity check，不是 alpha 证据（`docs/evaluation-methodology.md:126-140`）。

### 10.4 进化实验室（evolve/lab.py）：LLM 作变异算子的遗传算法

进化实验室是评估层里唯一**消耗 LLM 配额**的模块，定位是"Agent 即研究员"的终极形态（`lab.py:1`）。本质是一个标准遗传算法，唯一非常规之处是**变异算子由 LLM 承担**：LLM 读【蓝图 JSON + 回测报告摘要】，提出一个变异 patch（JSON diff），系统应用 patch、编译守门、跑 train 窗回测算适应度，如此代代迭代。

#### 变异 patch 契约

LLM 的输出被约束为一个**结构化 patch**，而不是完整蓝图，应用顺序固定为 `set_params → del_nodes → add_nodes → del_edges → add_edges`：

```json
{"summary": "<一句话变异描述>",
 "set_params": {"<nodeId>": {"<param>": <value>}},
 "add_nodes":  [{"id": "...", "type": "<catalog type>", "params": {...}}],
 "del_nodes":  ["<nodeId>"],
 "add_edges":  [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}],
 "del_edges":  [{"from": "<nodeId>.<outPin>", "to": "<nodeId>.<inPin>"}]}
```

`apply_patch(bp, patch, *, param_only=False) -> BlueprintSpec`（`lab.py:103-183`）是应用函数，返回**全新深拷贝**，父代蓝图纹丝不动。任何非法引用（未知节点、不存在的边、`param_only` 模式下出现结构变异键）都抛 `MutationRejected`，与编译失败同等对待——拒绝原因会作为反馈喂回 LLM 重试。

#### 进化循环的三层防护

1. **编译守门 + 自修复**：变异生成的蓝图先过 `compile_blueprint()`。编译失败会把 `CompileError.fix_hint` 通过 `_errors_to_feedback()` 喂回 LLM，最多重试 `MAX_REPAIR_RETRIES = 2` 次；仍失败则该变异**废弃**，如实记为 `compile_status="stillborn"` 进谱系树，不隐藏失败（`lab.py:298-357`）。

2. **运行期错误收容**：编译通过不代表运行安全——编译器不校验参数取值（例如 `period="very fast"` 能编译通过，但在节点 `setup()` 里做 `int()` 转换时会崩）。进化循环把这类异常捕获为 `compile_status="runtime_error"`，记录错误摘要，**不让这个孩子进入种群，但进化继续**：

   > "一个变异垃圾绝不炸掉整棵谱系（API 暴露后这是网络可达的 DoS 面，收容是硬性）。"（`lab.py:14`）

   种子蓝图本身跑炸则是例外——直接 raise，因为"种子坏是调用方错误不是变异风险，不静默收容"（`lab.py:14`）。

3. **类型系统硬护栏同样不可被进化绕过**：如果变异试图删掉 `risk_gate`，`execute_order` 只接受 `risk_stamped_signal` 类型（唯一产地是 `RiskGate` 节点），编译守门天然产生 `TYPE_MISMATCH`，`fix_hint` 会把 LLM 引导回补 `RiskGate`；冥顽不灵则该孩子 stillborn。这与消融实验的"硬护栏不可消融"是同一条卖点（`lab.py:38-41`）。

#### 适应度与选择

`fitness_of(summary)`（`lab.py:189-198`）：`train_return_pct` 为主，但 `num_trades == 0` 一律判 0 分——"零交易=零证据"这条 T3 记分卡教义在这里被复用。docstring 承认了一个刻意接受的诚实结论："在全员亏损的种群里躺平者（0 分）居首"是合理结果（不亏就是赢），不是 bug。

每一代结束后，`存活个体 + 本代合法孩子` 按适应度排序，取 top-N（N=population）进下一代——精英保留、父代不自动死亡，只有被更强的孩子挤出去才死（`lab.py:468-470`）。

#### 规模硬锁定与防泄漏

```python
MAX_POPULATION = 4
MAX_GENERATIONS = 3
MAX_MUTATIONS_PER_GEN = 2 * MAX_POPULATION
MAX_REPAIR_RETRIES = 2
```

超出即 `ValueError`（`evolve()` 入口二次校验，`lab.py:397-411`），API 层的 `EvolveIn` Schema 也用 Pydantic `Field(ge=1, le=4)` / `Field(ge=1, le=3)` 做了第一层拦截（`schemas.py:141-142`），双保险。

防止验证窗泄漏是另一条被测试锁定的硬约束：进化循环内任何个体都不查询 valid 窗，valid 窗只在**终选**时被查询恰好一次；`train_window`/`valid_window` 一旦重叠（`_windows_overlap()`）直接 `ValueError`（`lab.py:369-374, 414-417`）。

`param_only=True` 是一个降级保险丝：变异提示词只允许 `set_params`，`apply_patch()` 侧强制拒绝任何结构变异 patch——图结构与父代逐位一致，只允许参数差异（`lab.py:117-123`）。demo 预设默认启用这个模式（见下）。

#### 谱系树数据结构（前端 GenealogyTree 消费）

```python
@dataclass
class GenealogyNode:
    id: str
    gen: int
    parent_id: str | None
    mutation_summary: str
    fitness: float | None       # stillborn/runtime_error = None
    compile_status: str         # "ok" | "repaired" | "stillborn" | "runtime_error"
    blueprint_json: dict | None
    survived: bool = False
    error: str | None = None
```

`Genealogy.to_dict()` 输出 `{nodes: [...], winner: {...}, param_only, population, generations}`，是一棵扁平列表 + `parent_id` 表示的树，前端 `GenealogyTree.tsx` 的 `genealogyToFlow()`（`frontend/src/components/GenealogyTree.tsx:26-52`）直接按 `gen` 分层、`parent_id` 连边渲染为 React Flow 图；节点卡片按 `compile_status` 上色（`ok`=绿、`repaired`=蓝、`stillborn`=灰、`runtime_error`=红），winner 节点金色高亮（`GenealogyTree.tsx:16-21, 54-60`）。前端注释也强调了同一条"诚实"原则：winner 的 valid 交易数要摆在 valid_fitness 旁边，以便区分"没交易"与"交易亏光"（`GenealogyTree.tsx:8-9`）。

`winner` 字段的形状：

```python
winner = {
    "id": best.node.id,
    "train_fitness": best.node.fitness,
    "valid_fitness": valid_fitness,
    "generalization_gap": round(best.node.fitness - valid_fitness, 6),
    "train_summary": dict(best.summary),
    "valid_summary": dict(valid_report.summary),
}
```

#### 与沙盒自定义节点系统的关系

进化实验室与沙箱节点系统（AST 白名单热注册的自定义节点，`nodes/registry.py` 中 `sandboxed: bool` 标记）之间有两条交汇线：

1. 变异算子的系统提示词内嵌**节点目录**（`build_node_catalog(defs)`，来自 `copilot.prompts`），LLM 只能在这个目录范围内 `add_nodes`——目录来源即 `defs` 参数，默认是全局 `REGISTRY`（含运行期动态注册的沙箱节点）。
2. **demo 回放的稳定性依赖目录的确定性**：demo 预设（`body.demo=True`）录制时的目录只包含内置节点（脚本 `seed_recordings.py` 只 `import alphaloom.nodes`，不含任何沙箱/测试夹具节点）。若运行期进程恰好注册过一个自定义沙箱节点，全局 `REGISTRY` 的目录字符串就会变化，进而改变喂给 LLM 的 prompt 内容 → 请求哈希变化 → 命中不了录制回放（`replay_miss`）。因此 `/api/evolve` 在 `demo=True` 分支里显式构造了一份"仅内置、排除 sandboxed 与 test 类别"的 `evolve_defs` 视图（`app.py:704-710`），与生产路径（`evolve_defs=None`，用完整全局 `REGISTRY`）区分开来，专门保证离线 demo 的可重放性不受运行时沙箱节点热注册状态的影响。

换句话说，沙箱自定义节点系统对进化实验室是**透明可用**的（变异出的孩子可以是含自定义节点的蓝图，只要能通过编译与运行期沙箱 ctx 限制），但 demo 场景为了录制回放的确定性主动把它从目录里剔除。

### 10.5 API 层：eval/evolve 端点相对普通回测的额外审查

普通的 `/api/run`（走 `RunService`，异步、WebSocket 推流事件）与 `/api/eval/*`、`/api/evolve`（同步直接返回报告）在数据规模和使用场景上有明显区别——消融、进化这类端点天然涉及"同一份蓝图跑好几遍"（消融 3 臂、进化每个孩子一次回测 + 一次变异 LLM 调用），如果蓝图带 LLM 节点，重复调用会成倍放大真实配额消耗。因此这些评估类端点在普通编译校验之外，**额外做了一层"LLM 蓝图配额安全"审查**。

核心是 `_guard_llm_blueprint(compiled)`（`app.py:505-516`）：

```python
def _guard_llm_blueprint(compiled):
    """LLM 蓝图（或含不受信沙箱节点的蓝图）须 offline 客户端；否则 409
    （不烧真配额，评估拒绝跑）。"""
    if _needs_llm(compiled) and not _llm_quota_safe():
        reason = ("an untrusted sandbox-registered node (its zero-LLM cost "
                  "certificate is not trusted)" if _has_sandbox_node(compiled)
                  else "LLM node(s)")
        raise HTTPException(
            409, f"blueprint contains {reason}; evaluation refuses to run it "
                 "against live quota. ...")
```

它依赖两个判定函数：

- `_needs_llm(compiled)`（`app.py:484-489`）：编译证书 `certificate.llm_calls_per_bar > 0` 即为真；否则兜底看 `_has_sandbox_node()`。
- `_has_sandbox_node(compiled)`（`app.py:473-482`）：蓝图里是否存在任何 `REGISTRY` 标记为 `sandboxed=True` 的节点类型。这里的设计考虑是**不信任沙箱节点自证的零成本证书**——沙箱节点理论上可以声明 `llm_calls_per_bar=0` 却在运行期悄悄调用 LLM，即便运行期沙箱 ctx 已经做了能力剥离，这里仍作为深度防御再拒绝一次（`app.py:474-477` 注释）。

`_llm_quota_safe()`（`app.py:491-493`）判定当前 `app.state.llm` 是否为 offline 客户端（`getattr(llm, "offline", False) is True`），即录制回放或本地确定性剧本，零网络零配额。

这套守门在具体端点里的应用：

- `/api/eval/leaderboard`：仅当请求体额外携带 `body.blueprint`（把某张蓝图也加入排行榜对比）时才调用 `_guard_llm_blueprint()`（`app.py:613`）；三条内置基线本身零 LLM，无需守门。
- `/api/eval/ablation`：委员会消融的臂天然含 `committee`（LLM 节点），所以必然触发守门检查（`app.py:655`）——非 offline 客户端直接 409。
- `/api/evolve`：不仅蓝图本身要过 `_guard_llm_blueprint()`（`app.py:726`），还**额外**判断变异算子本身的配额安全——因为进化循环每个孩子都要调一次 LLM 做变异（`app.py:728-733`）：

  ```python
  if not _llm_quota_safe():
      raise HTTPException(
          409, "evolution runs the LLM mutation operator every child; it "
               "requires an offline LLM client ...")
  ```

  这是比 `_guard_llm_blueprint()` 更进一步的检查——即便种子蓝图本身是纯确定性的（如 `ema_cross`，`llm_calls_per_bar=0`），只要变异算子会调用 LLM，就必须保证客户端处于 offline 状态。

此外，消融与进化的端点都支持 `demo: bool` 字段（`EvalAblationIn.demo` / `EvolveIn.demo`）。当 `demo=True` 时，端点**服务端硬用** `eval/demo_coords.py` 定义的规范坐标（招牌蓝图 `agent_committee` + 固定 50-bar 窗口用于消融；`ema_cross` 种子 + 固定 train/valid 窗 + `population=2, generations=2, param_only=True` 用于进化），**忽略请求体里其余的 blueprint/inst/窗口字段**——这是为了与 `scripts/seed_recordings.py` 录制时用的坐标逐字同源，避免前端传错坐标导致请求哈希对不上、离线回放 miss（`demo_coords.py:1-16`，`app.py:637-643, 694-710`）。这套"demo 坐标单一真源"设计本身就是 D4-T8 审查发现的一次真实漂移修复：此前前端从"选中 run 的 params"派生窗口且硬编码 population/generations，与种子录制坐标不符，导致离线点击消融/进化触发 `422 replay_miss`，消融表和谱系树渲染不出来——修复方式是把这套坐标常量提炼为后端共享模块，前端与种子脚本、demo 端点三方永远同源。

同时，`_guard_demo_recordings()`（`app.py:495-503`）在 `demo=True` 分支额外检查录制数据库是否为空（`recording_count() == 0`），为空则 422 提示需要先在 record 模式下重跑一次以捕获种子调用——这也是普通 `/api/run` 端点不会遇到的检查，因为普通回测不强制走 offline 录制回放路径。

综合来看，eval/evolve 端点相对普通回测端点的额外审查可以概括为三层：**编译期成本证书检查**（`_needs_llm`）→ **沙箱节点不信任兜底**（`_has_sandbox_node`）→ **运行时 LLM 客户端 offline 状态检查**（`_llm_quota_safe`），外加进化端点独有的"变异算子本身也要配额安全"检查，以及 demo 模式下的"坐标单一真源 + 录制库非空"前置检查。这套设计的落脚点始终是同一句话：评估类端点会重复调用同一张蓝图很多次，所以在放行之前要比普通单次回测更谨慎地确认不会意外烧掉真实 LLM 配额。

---

## 11. REST/WebSocket API 层

`backend/alphaloom/api/` 包是整个系统面向前端暴露的唯一入口：一个由 `create_app()` 组装的 FastAPI 应用，把编译器、回测引擎、实时会话、评估/进化实验室、Copilot 元 Agent 和沙箱节点注册统一封装成 HTTP + WebSocket 接口。这一层本身不包含业务逻辑，核心职责是**编排**（把请求参数转成对底层模块的调用）、**状态承载**（进程内的运行注册表、事件日志、WS 订阅队列）和**契约整形**（Pydantic 校验请求、`sanitize()` 整形响应）。

### 11.1 `create_app()` 与应用状态

`create_app()`（`backend/alphaloom/api/app.py:74-858`）是唯一的应用工厂，签名收敛了整个后端运行所需的全部外部依赖：

```python
def create_app(*, db_path, runs_db, record_dir, blueprints_dir, user_blueprints_dir,
               frontend_dist, llm_client=None, llm_db=None, live_fetcher=None) -> FastAPI:
```

- `db_path`：历史行情 SQLite（`SQLiteMarketData` 数据源）；
- `runs_db`：`RunsStore` 的持久化路径（run/live session 生命周期表）；
- `record_dir`：每次 run/live session 的录制库（`Recorder`）落盘目录；
- `blueprints_dir` / `user_blueprints_dir`：预置蓝图 vs 用户保存蓝图的两个目录；
- `frontend_dist`：构建后的前端静态资源目录（SPA fallback 用）；
- `llm_client` / `llm_db` / `live_fetcher`：测试/生产的注入接缝——测试可注入 fake transport 的 `RecordingLLMClient` 和 fake `candle_fetcher`，生产则为 `None`，由 `_build_llm_client()` 从环境变量现场构建。

#### lifespan 与事件循环兜底

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.loop = asyncio.get_running_loop()
    yield
    store.close()
```

`app.state.loop` 用来在**非异步线程**（run/live 的后台 worker 线程）里安全地把事件推回 asyncio 世界：worker 线程调用 `sink(event)` 时通过 `loop.call_soon_threadsafe(q.put_nowait, event)` 把事件塞进对应 WebSocket 连接的 `asyncio.Queue`。注释解释了一个容易踩的坑：lifespan 抓到的 loop 只是**兜底**，因为测试用的 `TestClient.websocket_connect` 每次会起一个独立 portal/新 loop，与 startup 时的 loop 不是同一个；真正生效的覆盖发生在 `ws_run`/`ws_live` handler 内部（见 11.3），它们在连接建立时用**当前连接的运行中 loop** 覆盖 `app.state.loop`。生产环境下 uvicorn 是单一事件循环，二者等价、无副作用。

#### 全局异常处理器：沙箱逃逸 → 422

```python
from alphaloom.runtime.engine import SandboxEscapeError as _SandboxEscapeError

@app.exception_handler(_SandboxEscapeError)
async def _sandbox_escape_handler(_request, exc):
    return JSONResponse(status_code=422, content={
        "error": "sandbox_escape",
        "message": f"a sandboxed node attempted a forbidden capability: {exc}. "
                   "Sandbox nodes are stripped of the LLM handle; this blueprint cannot be evaluated."})
```

沙箱节点在运行期被剥夺 `ctx.llm`（详见第 7 节的运行时上下文分层）；如果一个热注册的自定义节点试图偷取 LLM 句柄，引擎会抛 `SandboxEscapeError`。这个全局 handler 把它统一转成一个**可读的 422**，而不是让请求以裸 500 收场——这是"Layer 1（沙箱剥离能力）生效"在 API 层的可见证据。

#### `app.state` 携带的全局状态

`create_app()` 在应用对象上挂了一组贯穿请求生命周期的可变状态：

| 字段 | 类型 | 用途 |
|---|---|---|
| `app.state.store` | `RunsStore` | run / live session 的 sqlite 生命周期表（见 11.4） |
| `app.state.llm` | `RecordingLLMClient \| None` | 当前生效的 LLM 客户端；`None` 表示未配置 |
| `app.state.service` | `RunService` | 回测 run 的线程/会话管理器 |
| `app.state.live_service` | `LiveService` | 实盘 paper-trading 会话管理器（第 7 节详述） |
| `app.state.ws_queues` | `dict[run_id, list[asyncio.Queue]]` | 每个 run 当前订阅的 WS 连接队列，一对多广播 |
| `app.state.event_log` | `dict[run_id, list[event]]` | 每个 run 的事件重放缓冲，上限 20,000 条 |
| `app.state.live_ws_queues` / `app.state.live_event_log` | 同上 | live session 版本 |
| `app.state.loop` | `asyncio.AbstractEventLoop \| None` | 后台线程向 WS 推事件用的事件循环句柄 |

`_sink_for(run_id)` / `_live_sink_for(session_id)` 是两个闭包工厂，生成的 `sink` 回调既写入重放缓冲（带 20k 上限，防止长跑 run 无限吃内存），又把事件广播给该 run/session 当前所有已连接的 WS 队列。这个 sink 会作为回调一路传进 `RunService.start()` / `LiveService.start()`，再传进回测/实盘引擎的 `on_bar`/`on_pause` 钩子——即**事件从引擎内部产生，经 sink 落盘到重放缓冲，再经 loop.call_soon_threadsafe 跨线程推给所有当前订阅者**，是这一层实时性的核心机制。

LLM 客户端的构建由 `_build_llm_client()`（`app.py:40-72`）完成：从 `LLMConfig.from_env()` 读取 `.env`/环境变量，套上 `with_retry`（429 退避）包装 `openai_transport`，再用 `RecordingLLMClient` 包一层录制/回放。构建失败（比如非 offline 模式却缺少 `.env` 配置）返回 `None`，不让服务启动失败——纯确定性蓝图（无 LLM 节点）仍可以正常跑，LLM 节点在场时运行期 `ctx.llm is None` 会抛出清晰的 `RuntimeError`，run 状态变成 `failed`，而不是让整个服务崩溃。

本项目未见显式 CORS 中间件配置（`create_app()` 中没有 `CORSMiddleware`）——这与其"单用户本地/演示部署"的既定假设一致（`nodes/registry.py` 模块 docstring 明确写了这一点，见 11.2 的 `/api/nodes/custom`）：前后端同源由 SPA fallback 路由（11.2 末尾）保证，不需要跨域放行。

### 11.2 路由清单

以下按功能域列出全部 HTTP 路由。除标注异步的（`ws_run`/`ws_live`）外均为同步 `def` 端点——FastAPI 对同步 def 自动丢进线程池执行，不阻塞事件循环，这也是评估/进化端点选择同步实现的原因（见 11.2.5 的注释）。

#### 11.2.1 元信息 / 状态

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `GET /api/nodes` | — | `list[{type, category, inputs, outputs, params, cost}]` | 枚举 `REGISTRY` 中所有非 `test` 分类的节点定义，按 `(category, type)` 排序 |
| `GET /api/status` | — | `{llm_mode, model}` | 当前 LLM 运行模式的诚实读数 |
| `POST /api/runtime-mode` | `RuntimeModeIn{mode}` | `{llm_mode, model}` | 热切换 LLM 客户端为 offline/live/none |

`/api/nodes` 返回形状示例（对齐 `NodeDef` 字段，`PinType` 值直接取 `.value`）：

```python
out.append({"type": d.type, "category": d.category,
            "inputs": {k: v.value for k, v in d.inputs.items()},
            "outputs": {k: v.value for k, v in d.outputs.items()},
            "params": {k: getattr(v, "__name__", str(v))
                       for k, v in d.params.items()},
            "cost": d.cost.__dict__})
```

其中 `inputs`/`outputs` 的值来自 `PinType(str, Enum)`（`graph/types.py:6-12`）：`exec | candle | series | signal | risk_stamped_signal | bool`；`cost` 来自 `CostAnnotation`（`graph/types.py:20-25`）：`{llm_calls_per_bar, max_tokens_per_call, latency_class, deterministic}`。

`/api/runtime-mode` 是"诚实运行时状态"机制的写入端：`mode="live"` 若无法从环境变量构建出真实客户端会返回 422（附具体缺失的环境变量提示）；`mode="none"` 直接清空 `app.state.llm` 并同步下发给 `service`/`live_service`。`_status_payload()` 依据 `llm is None`（`none`）/`llm.offline`（`offline`）/其余（`live`）三态给前端头部渲染诚实的运行模式徽标，避免"看起来在线实际上在离线回放"这类误导。

#### 11.2.2 编译 / 蓝图 / 行情

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `POST /api/compile` | `CompileIn{blueprint, bar}` | `{ok, errors, certificate, order}` | 编译 `.loom` 蓝图但不运行 |
| `GET /api/blueprints` | — | `list[{id, name, meta, source}]` | 预置 + 用户蓝图清单（按 id 去重，预置优先） |
| `GET /api/blueprints/{bp_id}` | — | 原始 `.loom` JSON | 按 id 查找单个蓝图 |
| `POST /api/blueprints` | `SaveBlueprintIn{blueprint}` | `{id}` | 保存到 `user_blueprints_dir`，id slug 化 |
| `GET /api/market/candles` | query: `inst,bar,start,end,limit` | `list[candle]` | 历史 K 线读取（上限 5000 根） |
| `GET /api/market/catalog` | — | 数据源目录 | 可用 inst/bar 组合 |

`/api/compile` 的响应形状（`app.py:200-215`）：

```python
{"ok": r.ok,
 "errors": [e.to_dict() for e in r.errors],
 "certificate": sanitize(r.certificate.to_dict()) if r.certificate else None,
 "order": r.order}
```

`errors` 里每一项对应 `CompileError`（`graph/errors.py:4-13`）的 `to_dict()`：

```python
{"code": str, "message": str, "node_id": str | None,
 "port": str | None, "fix_hint": str | None}
```

`fix_hint` 字段的注释明确说明其设计意图是"面向 LLM 的修复提示"——这是结构化反馈环路的一部分：Copilot 的自修复循环（11.2.6）会把编译失败的 `errors` 连同 `fix_hint` 一起喂回 LLM，让它据此改写蓝图。`certificate` 对应 `CostCertificate`（`graph/cost.py:7-15`）：

```python
{"llm_calls_per_bar": int, "daily_token_ceiling": int,
 "worst_latency_class": "fast"|"slow"|"llm", "deterministic_ratio": float}
```

它是整条"配额守门"链路的信任根：评估/进化端点（11.2.5）依据 `llm_calls_per_bar > 0` 判断蓝图是否需要 LLM，从而决定是否放行非 offline 客户端执行。

蓝图 body 是标准 `.loom` JSON（`graph/model.py` 的 `loads_loom`/`dumps_loom` 序列化），例如 `blueprints/ema_cross.loom` 的结构：

```json
{
  "id": "ema_cross_v1",
  "name": "EMA Cross Trend Follow",
  "nodes": [
    {"id": "feed", "type": "candle_feed", "params": {"inst": "BTC-USDT-SWAP", "bar": "1m"}},
    {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
    {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2}},
    {"id": "risk", "type": "risk_gate", "params": {"max_qty": 100, "require_stop": true}}
  ],
  "edges": [
    {"from": "feed.out", "to": "ema_fast.candle"},
    {"from": "cross.signal", "to": "sizer.signal"},
    {"from": "risk.stamped", "to": "exec.signal"}
  ],
  "meta": {"preset": true, "gateProtocol": { "...": "前端讲解面板用的展示元数据" }}
}
```

所有携带 `blueprint` 字段的端点都统一走 `loads_loom(json.dumps(body.blueprint))` 把 Pydantic 校验过的裸 dict 转成强类型的 `BlueprintSpec`；反序列化失败（`ValueError|KeyError|TypeError`）在大多数端点转 422，附带 `PARAM_INVALID` 错误码。

`/api/blueprints` 保存逻辑（`blueprint_save`，`app.py:251-268`）值得一提：id 会正则清洗为 `[a-z0-9_-]` 的 64 字符 slug，且若该 slug 已被预置蓝图占用会返回 409（`"blueprint id ... is reserved by a preset"`），防止用户保存覆盖内置蓝图的展示语义。

#### 11.2.3 回测 Run

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `POST /api/runs` | `RunIn` | `{run_id}` | 编译并异步启动一次回测（线程） |
| `GET /api/runs` | — | `list[{run_id, blueprint_id, params_json, status, error, created_ms}]` | run 列表（按创建时间倒序） |
| `GET /api/runs/{run_id}` | — | `{run_id, status, params, error, report?}` | 单个 run 详情 |
| `GET /api/runs/{run_id}/trace` | query: `node_id?,event_idx?,limit` | `list[{event_idx, ts, node_id, inputs, outputs}]` | 从录制库回放节点级 I/O |

`RunIn`（`schemas.py:20-33`）的字段：

```python
class RunIn(BaseModel):
    blueprint: dict
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    end_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    breakpoints: list[str] = Field(default_factory=list)
    playback_ms: int = 15
    ws_wait_ms: int = 0
    mode: str = "backtest"
```

`start_ms`/`end_ms` 的上界 `4_102_444_800_000`（约公元 2100 年）是防止毫秒时间戳越界后打穿到 sqlite 的整数溢出防护；`breakpoints` 决定 `BreakBridge` 是否要拦截节点执行（见 11.4）；`playback_ms` 控制推送节奏（模拟"慢放"回测供前端观感），`ws_wait_ms` 是让 WS 客户端有时间连上再发第一个 bar 事件的等待窗口。

`POST /api/runs` 的处理流程（`app.py:297-316`）：先按 `bar` 校验、`loads_loom` 解析、`compile_blueprint` 编译，编译失败直接 422（`{"errors": [...]}`）；编译成功后生成 `run_id = uuid4().hex[:12]`，**先确定 run_id 再构造 sink**（两段式，因为 sink 需要闭包住 run_id 才能定位对应的 WS 队列/事件日志），再调用 `service.start(bp, params, sink=_sink_for(run_id), run_id=run_id)`。若当前活跃 run 数已达上限（`RunService.max_active_runs`，默认 4），`start()` 抛 `RuntimeError`，被转成 429。

`GET /api/runs/{run_id}` 的响应组装（`app.py:362-372`）：

```python
out = {"run_id": row["run_id"], "status": row["status"],
       "params": json.loads(row["params_json"] or "{}"),
       "error": row["error"]}
if row["report_json"]:
    out["report"] = sanitize(json.loads(row["report_json"]))
```

`report` 字段的真实形状来自 `RunService._worker`（见 11.4）落盘的 payload：`{run_id, blueprint_id, bars, summary, certificate, equity_curve, fills}`。

`/api/runs/{run_id}/trace` 直接查询录制库（`row["recording_path"]` 指向的 sqlite）里的 `node_io` 表，按 `run_id`（+可选 `node_id`/`event_idx`）过滤，`limit` 硬夹在 `[1, 2000]`。返回前用内部 `_decode()` 把 `Stamped`（`value + as_of`）对象摊平成 `{"as_of":..., "value":...}` 字典，再统一 `sanitize()`。

#### 11.2.4 Copilot 元 Agent

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `POST /api/copilot/blueprint` | `CopilotBlueprintIn{nl}` | 生成的 `.loom` blueprint dict | 自然语言 → 蓝图，自带自修复循环 |
| `POST /api/copilot/explain` | `CopilotExplainIn{blueprint}` | `{explanation}` | 讲解蓝图 |
| `POST /api/copilot/optimize` | `CopilotOptimizeIn{blueprint, report}` | 优化后的蓝图/建议 | 基于回测报告给出改进建议 |

三个端点都先 `_require_llm()`（`app.py:404-410`）——如果 `app.state.llm is None` 直接 503（"no LLM client configured..."）。`copilot.text_to_blueprint()` 内部自带 `default_compile_fn`（即真正的 `compile_blueprint`）驱动的自修复循环：LLM 生成的蓝图编译失败时，把 `CompileError` 列表（含 `fix_hint`）反馈给 LLM 重新生成，直到编译通过或放弃并抛 `BlueprintGenerationError`（转 422，`{"error": "generation_failed", "message": ...}`）。

三个端点还统一走 `_raise_llm_http_error(exc)`（`app.py:412-425`）区分两类 LLM 传输层失败：`ReplayMissError`（offline 回放模式下没录到这句 prompt）转 422 并给出切换 Live 模式或补录的提示；`openai.OpenAIError` 系列转 502（"could not reach the provider"）；其余异常原样抛出（走 FastAPI 默认 500）。

#### 11.2.5 评估 / 进化实验室

这一组端点共享几个关键守门函数：

- `_needs_llm(compiled)`：编译证书 `llm_calls_per_bar > 0`，或蓝图含**沙箱热注册节点**（不信任其零 LLM 自证，见 `_has_sandbox_node`）即为真；
- `_llm_quota_safe()`：当前 `app.state.llm.offline is True`（录制回放/本地剧本，零配额）；
- `_guard_llm_blueprint(compiled)`：`_needs_llm` 为真但 `_llm_quota_safe()` 为假 → 409，拒绝用真实配额跑评估；
- `_guard_demo_recordings()`：`demo=True` 预设模式下若录制库为空 → 422（"offline demo mode needs recorded LLM calls"）。

评估类端点全部为**同步执行**，注释里给出了明确理由：数据规模锁定得很小（离线 ≤400 bar 数秒级；消融 ≤3 臂、进化 pop≤4/gen≤3），走 `RunService` 那套异步/WS 只会徒增复杂度，同步直接返回报告是这个 demo 规模下的正解。

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `POST /api/eval/fidelity` | `EvalFidelityIn{run_id, initial_cash, fee_rate, slippage_bps}` | fidelity ladder dict | 保真度阶梯 L0-L3，零 LLM，从已完成 run 的 fills+candles 重放 |
| `POST /api/eval/scorecard` | `EvalScorecardIn{train_report, valid_report?, ladder?, cost_cert?, ablation?}` | scorecard dict | 聚合各路证据成综合分，纯数值零 LLM |
| `POST /api/eval/leaderboard` | `EvalLeaderboardIn{inst, bar, start_ms, end_ms, valid_*, blueprint?, ...}` | leaderboard dict | buy-hold/EMA默认/随机三基线 + 可选指定蓝图对比 |
| `POST /api/eval/ablation` | `EvalAblationIn{blueprint?, inst?, ..., demo}` | ablation report dict | committee 消融三臂（full/no_risk_officer/no_rag） |
| `POST /api/evolve` | `EvolveIn{blueprint?, inst?, ..., population, generations, demo}` | evolution report dict | LLM 变异算子 + 编译守门 + 谱系树 |

`/api/eval/fidelity`（`app.py:535-560`）要求 `run_id` 对应的 run 状态为 `completed` 且有 `report_json`（否则 409），从中取出 `fills` 和同窗 `candles`（重新查询 `SQLiteMarketData`），送入 `fidelity_ladder()` 在四档成交模型下重放——这是一个**回测测谎仪**：检验策略的账面收益有多依赖乐观的成交假设。

`/api/eval/ablation` 和 `/api/evolve` 都支持 `demo=True` 模式（`EvalAblationIn`/`EvolveIn` 的 `demo` 字段），这是离线演示预设：服务端**忽略请求体里的 blueprint/inst/窗口**，改用 `eval.demo_coords` 模块里硬编码的规范坐标（与种子录制逐字同源），保证离线场景下必定命中录制回放。`evolve` 的 demo 分支还专门把变异算子可见的节点目录收窄成"排除 sandboxed 与 test 分类"的内置子集——因为种子录制是对**固定的内置节点目录**录的，运行时如果多注册了自定义节点或测试夹具节点，全局 `REGISTRY` 的目录字符串会变化，导致变异请求的 hash 变化从而回放 miss。

`/api/eval/ablation` 在非 demo 模式下还做了臂选择的预筛（`app.py:659-667`）：只运行蓝图实际支持的臂——没有 `committee` 节点就没有 `no_risk_officer` 臂的意义，没有 `require_citations` 节点就没有 `no_rag` 臂的意义；如果蓝图连 committee 节点都没有，直接 422（而不是让 `committee_ablation` 内部对不存在的目标抛 `ValueError` 变成 500）。

两者都会捕获 `ReplayMissError` 转成 422（`{"error": "replay_miss", "message": ...}`），以及 `ValueError`（消融的图手术失败、进化的规模/窗口超限）转 422。`evolve` 端点额外校验 `not _llm_quota_safe()` → 409（变异算子本身每个子代都调 LLM，非 offline 客户端会烧真配额）。

#### 11.2.6 自定义节点沙箱注册

| 方法+路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `POST /api/nodes/custom` | `CustomNodeIn{source}` | `{type, category}` | AST 白名单编译并热注册进全局 `REGISTRY` |

```python
@app.post("/api/nodes/custom")
def custom_node(body: CustomNodeIn):
    result = compile_node_source(body.source)
    if isinstance(result, SandboxError):
        raise HTTPException(422, result.to_dict())   # {reason, message, lineno}
    return {"type": result.type, "category": result.category}
```

`compile_node_source()`（第 4/5 节详述的沙箱子系统）返回值要么是 `SandboxError`（AST 校验/执行失败，附 `reason`/`message`/`lineno`），要么是编译成功后的 `NodeDef`。这里的关键设计约束在 `nodes/registry.py` 模块 docstring 里写得很清楚：`REGISTRY` 是**进程级全局单例**，跨请求/跨 `create_app` 实例/跨用户可见——A 用户注册的自定义节点立即对 B 用户的 `GET /api/nodes`、`/api/compile` 可见。这是 AlphaLoom 当前"单用户本地/演示部署"定位下的**已接受设计**（并有测试锁定：`tests/test_registry.py::test_registry_is_process_global_single_user`），文档里也明确标注了多用户生产部署需要引入 session/租户命名空间（列为 D4 Carryover）。

#### 11.2.7 SPA fallback

```python
@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    dist = Path(frontend_dist)
    if path.startswith(("api/", "ws/")):
        raise HTTPException(404)
    dist_root = dist.resolve()
    candidate = (dist / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(dist_root):
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return JSONResponse({"hint": "frontend not built; run npm run build"}, status_code=200)
```

这条通配路由注册在所有 `/api`、`/ws` 路由之后，承担两件事：把构建产物目录下的静态文件（JS/CSS/图片）按路径原样返回，其余一切路径 fallback 到 `index.html`（前端 SPA 路由自己接管），并显式排除 `api/`/`ws/` 前缀防止 404 被这条路由吞掉。`candidate.is_relative_to(dist_root)` 是收容检查——注释标注这是"T3 审查 Critical-1"，用于确认解出的候选路径没有转义构建产物根目录（uvicorn 解码后的 `%2F`/`%2e` 编码穿越会以字面 `../` 到达这里）。`index.html` 响应带 `Cache-Control: no-store`，确保前端每次都拿到最新构建而不是被浏览器缓存住。

### 11.3 WebSocket 协议

两条 WS 路由结构几乎对称，分别对应回测 run 和实盘 live session：

```
/ws/runs/{run_id}
/ws/live/{session_id}
```

#### 连接建立与重放

```python
@app.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    await ws.accept()
    app.state.loop = asyncio.get_running_loop()   # 连接期覆盖，见 11.1
    if store.get(run_id) is None:
        await ws.close(code=4404)
        return
    q: asyncio.Queue = asyncio.Queue()
    app.state.ws_queues.setdefault(run_id, []).append(q)
    try:
        for ev in list(app.state.event_log.get(run_id, [])):
            await ws.send_json(ev)                      # 重放
        while True:
            ...
    finally:
        app.state.ws_queues.get(run_id, []).remove(q)
```

`run_id`/`session_id` 若在 `RunsStore`（或 `LiveService.has()`）中查不到会以自定义关闭码 `4404` 直接断开——语义上等价 HTTP 404，但 WS 协议没有状态码复用 HTTP 语义的标准，所以用了这个易识别的自定义码。连接建立后立即**重放**该 run/session 目前为止在 `event_log`/`live_event_log` 里积累的全部事件——这解决了"客户端连接较晚，错过了前几个 bar 事件"的问题：不管 WS 何时连上，都能拿到从头开始的完整事件序列。之后把自己的 `Queue` 注册进 `ws_queues[run_id]`，随后转入主循环。

#### 主循环：双路 `asyncio.wait`

```python
while True:
    recv = asyncio.create_task(ws.receive_json())
    pull = asyncio.create_task(q.get())
    done_set, pending = await asyncio.wait(
        {recv, pull}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    if recv in done_set:
        try:
            msg = recv.result()
        except Exception:
            break
        cmd = msg.get("cmd")
        if cmd in ("resume", "step", "stop"):
            service.command(run_id, cmd)
    if pull in done_set:
        ev = pull.result()
        await ws.send_json(ev)
        if ev["type"] in ("done", "error"):
            break
```

这是典型的"双向单连接"模式：同一个 WS 连接上，服务端既要**接收**客户端命令（`ws.receive_json()`），又要**主动推送**引擎事件（从 `q.get()` 取出 sink 塞进来的事件）。用两个并发 task 分别等这两件事，`asyncio.wait(..., FIRST_COMPLETED)` 谁先完成就处理谁，另一个取消掉重新起——保证命令通道和事件推送通道互不阻塞对方。收到 `type in ("done", "error")` 事件后主动 `break` 结束循环（run 结束/失败，没有更多事件可推了），`finally` 块里把自己的 `q` 从 `ws_queues[run_id]` 摘除。

`/ws/live/{session_id}` 结构完全一致，唯一区别是命令集合窄化为只认 `stop`（live session 没有单步/断点调试语义）：

```python
if msg.get("cmd") == "stop":
    app.state.live_service.command(session_id, "stop")
```

#### 事件类型清单

综合 `RunService._worker`（回测）与 `LiveService._worker`（实盘，第 7 节详述）里 `sink({...})` 的调用点，流经这两条 WS 的事件 `type` 有：

| `type` | 产生场景 | 关键字段 |
|---|---|---|
| `status` | run/session 状态迁移（`running`/`starting`/`retrying` 等） | `status`, 实盘还有 `mode`, 重试时有 `attempt`/`message`/`next_retry_ms` |
| `bar` | 每根 bar 处理完毕 | `run_id`/回测：`**payload`；实盘：`idx, ts, candle, close, equity, active, fills` |
| `paused` | 断点命中（仅回测，`BreakBridge.on_pause`） | `node_id, ts, inputs`（已 `sanitize`） |
| `analysis` | LLM 分析节点产出（仅实盘，`analysis_every` 控制频率） | 依 `_analyze()` 内部结构而定 |
| `done` | run/session 正常结束 | `report`（完整报告 payload） |
| `error` | 编译失败/运行时异常 | `message` |

#### 客户端 → 服务端命令协议

客户端通过 `ws.send_json({"cmd": "..."})` 发送命令。回测 run 支持三种：

- `"step"`：`BreakBridge.command("step")` → 打开单步模式，放行当前断点，下一个断点还会停；
- `"resume"`：关闭单步模式，放行当前断点，之后不再拦截（除非命中新断点）；
- `"stop"`：`_stopped = True` 且放行当前阻塞的 `_gate.wait()`，之后所有断点直接放行，引擎自然结束。

Live session 只支持 `"stop"`，直接 `set()` 一个 `threading.Event`，`LiveService._worker` 的轮询循环里 `while not stop.is_set()` 检测到后跳出。

### 11.4 RunService —— 回测 Run 的线程/会话模型

`RunService`（`api/service.py:70-179`）是 `LiveService` 的回测版对应物，管理"一次回测 run"的完整生命周期：启动、断点交互、状态落盘、并发上限。

#### `BreakBridge`：引擎暂停 ↔ 外部命令的线程桥

```python
class BreakBridge:
    def __init__(self, user_breakpoints, sink):
        self.user_breakpoints = set(user_breakpoints)
        self.step_mode = False
        self._gate = threading.Event()
        self._sink = sink
        self._stopped = False

    def on_pause(self, node_id, ev, inputs):
        try:
            if self._stopped:
                return
            if not self.step_mode and node_id not in self.user_breakpoints:
                return
            self._gate.clear()
            if self._stopped:
                return            # stop TOCTOU 闭合：clear 后复检
            self._sink({"type": "paused", "node_id": node_id,
                        "ts": getattr(ev, "ts_close", 0),
                        "inputs": sanitize(_jsonable(inputs))})
            self._gate.wait()
        except Exception:
            pass  # 断点桥绝不让异常泄进引擎
```

引擎侧的设计是"以全节点为断点"（`breakpoints="all"` 传给 `run_backtest`，只要 `want_break` 为真就对每个节点都调 `on_pause`），真正的过滤在 `BreakBridge` 内完成：只有 `step_mode` 开启，或者 `node_id` 在用户设置的 `user_breakpoints` 集合里，才真正暂停（`_gate.clear()` + 推 `paused` 事件 + `_gate.wait()` 阻塞工作线程）。`command()` 方法把 WS 收到的 `step`/`resume`/`stop` 转成对 `_gate`（`threading.Event`）的操作：

```python
def command(self, cmd):
    if cmd == "step":
        self.step_mode = True; self._gate.set()
    elif cmd == "resume":
        self.step_mode = False; self._gate.set()
    elif cmd == "stop":
        self._stopped = True; self.step_mode = False; self._gate.set()
```

注释里提到的"stop TOCTOU 闭合"：`_gate.clear()` 之后、真正调用 `_sink`/`_gate.wait()` 之前，重新检查一次 `self._stopped`——防止在极窄的时间窗口内，另一个线程已经调用了 `command("stop")`（此时 `_gate.set()` 已经调用过了），但本线程还没检测到就把 `_gate` 又 `clear()` 了，导致工作线程永久卡死在 `_gate.wait()` 上等一个不会再来的信号。

`on_pause` 整体包在 `try/except Exception: pass` 里——这是显式的设计约束：断点桥是调试便利功能，它的任何异常都不能传播进引擎导致整个回测崩溃。

#### `RunService` 主体

```python
class RunService:
    def __init__(self, store, db_path, record_dir, llm=None, max_active_runs: int = 4):
        ...
        self._threads: dict[str, threading.Thread] = {}
        self._bridges: dict[str, BreakBridge] = {}
        self._lock = threading.Lock()
        self.max_active_runs = max(1, int(max_active_runs))
```

`start()` 方法（`service.py:88-106`）：生成/接受 `run_id`，构造 `BreakBridge(params.get("breakpoints", []), sink)`，在锁内先 `_prune_threads()`（清理已结束的线程记录）再检查并发上限——超限直接抛 `RuntimeError`（被 `app.py` 转 429）。通过检查后，把 `self.llm` 拍一份快照（`llm_snapshot`）传给工作线程——这保证了 run 启动那一刻绑定的 LLM 客户端在整个 run 生命周期内保持不变，即便运行期间有人调用 `/api/runtime-mode` 切换了全局 LLM 客户端也不影响正在跑的 run。`self.store.create(...)` 把 run 元数据（`run_id, blueprint_id, dumps_loom(bp), params_json, created_ms`）写入 `RunsStore`，状态初始为 `'running'`；随后启动 daemon 线程 `self._worker(...)`。

`_worker()` 方法（`service.py:124-178`）是实际执行回测的地方：

1. 先 `sink({"type": "status", "status": "running"})`；
2. 打开 `SQLiteMarketData(self.db_path)` 数据源；
3. 定义 `on_bar_event(payload)` 回调——首个 bar 前若 `ws_wait_ms > 0` 会 `sleep`，给 WS 客户端连接窗口，之后每个 bar 都 `sink({"type": "bar", **payload})`，若 `playback_ms > 0` 再额外 `sleep` 模拟慢放；
4. 调用 `run_backtest(bp, source, inst=..., bar=..., ..., record_dir=self.record_dir, run_id=run_id, breakpoints="all" if want_break else None, on_pause=bridge.on_pause if want_break else None, on_bar=on_bar_event, llm=llm, should_stop=bridge.stopped)`——引擎跑完返回 `BacktestReport`；
5. 状态判定：`report.summary.get("halted")` 为真则状态是 `"halted"`（比如触发了 `kill_switch`），否则 `"completed"`；
6. 组装并落盘/推送最终报告：

```python
payload = {"run_id": report.run_id, "blueprint_id": report.blueprint_id,
           "bars": report.bars, "summary": sanitize(report.summary),
           "certificate": report.certificate,
           "equity_curve": report.equity_curve, "fills": report.fills}
self.store.set_status(run_id, status, report_json=json.dumps(payload),
                      recording_path=report.recording_path)
sink({"type": "done", "report": payload})
```

异常处理分两支：`CompileFailed`（理论上不会走到这里，因为 `app.py` 在调用 `service.start()` 前已经先编译过一次；这里是防御性兜底）转成 `status="failed"` + `error` 存编译错误列表；其余任意 `Exception` 都遵循"Engine 崩溃契约"——转成 `status="failed"` + `error=str(exc)`，推 `{"type": "error", "message": str(exc)}"`，run 实例直接弃用，不做恢复重试。`finally` 块保证 `source.close()` 和线程/bridge 从内部字典里摘除，即使前面路径抛了异常也不泄漏。

`command()`/`join()` 是两个薄封装：前者查到对应 `BreakBridge` 就转发命令（找不到静默忽略，因为 run 可能已经结束/清理），后者供测试同步等待线程收尾。整个类没有用线程池，而是每个 run 一个独立 daemon 线程——量级匹配"演示级并发（默认上限 4 个活跃 run）"的定位，不需要更重的任务队列基础设施。

### 11.5 Pydantic Schemas —— 请求契约

`api/schemas.py` 定义了全部请求体模型（响应体大多是手写 dict + `sanitize()`，未走 Pydantic 序列化）。几个跨端点复用的模式：

- **时间戳上界统一** `_TS_MAX = 4_102_444_800_000`（约 2100 年），`RunIn` 单独重复了同样的数值——两处都是为了防止毫秒级时间戳的整数溢出穿透到底层 sqlite；
- **`demo: bool = False`** 出现在 `EvalAblationIn`/`EvolveIn` 上，语义是"服务端接管坐标选择，走离线种子录制回放路径"，请求体的其余业务字段在 `demo=True` 时被忽略；
- **规模硬锁定**：`EvolveIn.population`（`ge=1, le=4`）、`generations`（`ge=1, le=3`）在 Pydantic 层面先挡一次明显超限的请求（422），`evolve.lab` 内部还有一层 `ValueError` 兜底（同样转 422）——双保险,而不是只依赖内部校验。

代表性字段约束（`LiveStartIn`，`schemas.py:36-48`）：

```python
class LiveStartIn(BaseModel):
    blueprint: dict
    inst: str
    bar: str = "1m"
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    poll_ms: int = Field(default=5_000, ge=250, le=300_000)
    analysis: bool = True
    analysis_every: int = Field(default=1, ge=1, le=100)
    context_bars: int = Field(default=30, ge=1, le=120)
    max_bars: int | None = Field(default=None, ge=1, le=10_000)
    fetch_limit: int = Field(default=5, ge=1, le=100)
    ws_wait_ms: int = Field(default=0, ge=0, le=30_000)
```

`poll_ms` 下限 250ms 防止把 OKX 轮询打得过于频繁，`max_bars` 上限 10,000 给了一个"总会停下来"的硬上限,避免忘记停止的 live session 无限跑下去。`EvalScorecardIn`（`schemas.py:110-121`）的注释直接点出了这个端点存在的原因：

> 评分数学只在后端一份实现（tanh 压缩 / 四权重 / 缺证据保守分），前端绝不重实现以防与诚实评分口径漂移。

这是一条贯穿评估子系统的设计原则——凡是涉及"打分"、"排名"这类有算法口径的计算，一律留在后端单一实现，前端只负责传入已经算好的证据碎片（`train_report`/`ladder`/`cost_cert`/`ablation`）并展示结果，避免出现前后端两份评分逻辑各自演化、逐渐不一致的情况。

### 11.6 `serialize.sanitize()` —— 为什么原始引擎输出需要整形

```python
# backend/alphaloom/api/serialize.py
from __future__ import annotations
import math

def sanitize(obj):
    """递归把 inf/-inf/nan 变 None，保证严格 RFC 8259 JSON。"""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj
```

实现只有 12 行，但覆盖了一个 Python 生态里很常见的陷阱：Python 的 `json.dumps` 默认会把 `float('inf')`/`float('-inf')`/`float('nan')` 编码成裸的 `Infinity`/`-Infinity`/`NaN` 字面量——这是合法的 Python/JS 行为，但**不是**合法的 RFC 8259 JSON（标准 JSON 语法里没有这几个 token）。严格的 JSON 解析器（包括不少浏览器端/其他语言的 JSON 库）在遇到这些字面量时会直接解析失败。

回测/实盘引擎内部大量计算指标（收益率、夏普比率、回撤百分比、仓位盈亏等），在边界情况下天然容易产生 `inf`/`nan`——比如除以零仓位、空样本集的标准差、极端杠杆下的收益率溢出。`sanitize()` 被用作这些数值离开引擎、进入 HTTP/WS 响应之前的**最后一道整形**，递归地把它们全部换成 JSON 原生支持的 `null`，前端拿到后可以安全地 `JSON.parse`。

调用点覆盖了几乎所有会把引擎/报告数值往外传的地方：`/api/compile` 的 `certificate`、`/api/runs/{run_id}` 的 `report`、`/api/runs/{run_id}/trace` 的 `inputs`/`outputs`、`RunService._worker` 组装 `payload` 时的 `summary`、`LiveService` 里的 `report`/`broker.summary()`/`new_fills`、以及全部 `eval/*` 端点的返回值（`fidelity_ladder`/`scorecard`/`leaderboard`/`ablation`/`evolve` 的 `to_dict()` 结果）。`BreakBridge.on_pause` 推送 `paused` 事件时也对 `inputs` 做了 `sanitize(_jsonable(inputs))`——这里 `_jsonable()` 是配套的另一层防御：先尝试 `json.dumps(obj)` 探测是否可序列化，不行就退化成 `repr(obj)` 字符串，双重保证断点事件里携带的任意节点输入值（可能是自定义节点产生的非标准对象）不会导致 WS 推送本身抛异常。

---

**与相邻子系统的连接关系**：这一层是编译器（第 3 节）、运行时引擎与沙箱上下文（第 4/5/7 节）、回测/实盘服务（第 6/7 节）、评估与进化实验室（第 9/10 节）、Copilot 元 Agent（第 8 节）共同的对外门面——它本身不重新实现任何领域逻辑，只做参数校验（Pydantic schemas）、编排调用、线程/会话生命周期管理（`RunService`/`LiveService`）、事件广播（sink + WS 队列）和输出整形（`sanitize`）。前端（第 12 节）与这一层的全部交互——无论是拖拽节点面板取数据（`/api/nodes`）、编译画布（`/api/compile`）、启动/观察回测或实盘（`/api/runs` + `/ws/runs/{id}`、`/api/live` + `/ws/live/{id}`）、还是调用评估/进化/Copilot 功能——都严格通过本节描述的这套 REST + WebSocket 契约完成。

---

## 12. 前端架构

前端位于 `frontend/`，是一个纯 SPA：Vite + React 18 + TypeScript，用 hash 路由做四页切换，不依赖任何后端模板渲染。整体分层是典型的"页面（pages）→ 组件（components）→ 客户端库（lib）"三层结构：`lib/` 下沉了所有与后端契约相关的类型和纯函数（REST 客户端、WebSocket 封装、蓝图数据结构、i18n 字典、回测参数派生逻辑),`components/` 是可复用的展示与交互单元，`pages/` 把它们编排成四个功能完整的工作台。

### 12.1 应用整体结构：路由与 i18n

#### App.tsx 的路由与全局状态

`frontend/src/App.tsx` 是应用外壳，职责很窄：渲染顶部导航栏、维护当前路由、拉取并展示 LLM 运行模式（runtime mode）、承载语言切换器。四个页面全部用 `lazy()` 懒加载（`App.tsx:5-8`），路由本身就是 `location.hash`：

```tsx
const [route, setRoute] = useState(location.hash || "#/studio");
useEffect(() => {
  const f = () => setRoute(location.hash || "#/studio");
  addEventListener("hashchange", f);
  ...
}, []);
...
{route.startsWith("#/eval") ? <Eval />
  : route.startsWith("#/live") ? <LiveDesk />
  : route.startsWith("#/terminal") ? <Terminal /> : <Studio />}
```

没有引入 React Router 之类的路由库——四个页面、无嵌套路由、无参数化路径，`hashchange` 事件加字符串前缀匹配已经够用，这是一个刻意的"够用就好"的最小化设计。

App.tsx 另一个职责是全局 LLM 运行模式切换器（`offline` / `live` / `none`）。挂载时调用 `getStatus()` 拿当前模式，切换时调用 `setRuntimeMode(mode)`（`App.tsx:50-63`），并把 `live` 模式的按钮标注为"real endpoint - live calls burn real quota"提示用户这是会消耗真实配额的模式。三态用 `MODE_STYLE`/`MODE_LABEL` 两张表分别映射颜色和双语文案（`App.tsx:10-20`），错误处理走统一的 `modeErrorMessage`：优先解析 HTTP 错误体里的 `detail` 字段，解析失败则回退到原始 body 或 `Error.message`（`App.tsx:24-34`）——这个模式（尝试 JSON.parse 错误体、取 `detail`、否则展示原文）在 `api.ts` 消费方和 `Eval.tsx` 的 `errText` 中重复出现，是前端统一的错误展示惯例。

#### i18n.ts：无依赖的双语系统

`frontend/src/lib/i18n.ts` 没有使用 i18next 一类的库，而是手写了一个基于 `useSyncExternalStore` 的极简订阅式方案：

```ts
const dict = { zh: {...}, en: {...} } as const;
export type LangKey = keyof typeof dict.zh;

let lang: "zh" | "en" = (localStorage.getItem("alphaloom.lang") as "zh" | "en") || "zh";
const subs = new Set<() => void>();
export function setLang(l: "zh" | "en") {
  lang = l; localStorage.setItem("alphaloom.lang", l); subs.forEach((f) => f());
}
export function useLang() {
  const l = useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => lang);
  return { lang: l, t: (k: LangKey) => dict[l][k], setLang };
}
```

`dict` 是一个模块级单例（不是 Context），`lang` 是模块作用域的可变变量，持久化在 `localStorage["alphaloom.lang"]`。任意组件调用 `useLang()` 都会通过 `useSyncExternalStore` 订阅同一个 `subs` 集合；调用 `setLang()` 时广播给所有订阅者触发重渲染，因此不需要把语言状态提升到某个 Provider 里，也不需要 prop drilling——这是一个"全局单例 + 手动订阅"模式，规避了 Context 在这种简单标量全局状态上的样板代码。字典中默认导出的是**扁平键值对**（如 `studio`、`compileOk`、`fidelityTitle` 等），键名即文案用途,两种语言的键集合完全对齐（`LangKey = keyof typeof dict.zh` 保证了类型层面 en/zh 不会漏字段）。组件消费方式统一为 `const { t, lang } = useLang();` 然后 `t("someKey")` 取当前语言文案,或直接读 `lang` 做条件渲染（如 `GateProtocol` 的 `localize()` 函数）。

多语言富文本（不只是简单字符串）用 `LocalizedText = string | { en?: string; zh?: string }` 类型表达（`lib/loom.ts:14`），配合 `localize(value, lang, fallback)` 函数取值,并在缺省时按 `value[lang] ?? value.en ?? value.zh ?? fallback` 顺序回退（`lib/loom.ts:37-41`）。这用于蓝图 meta 里嵌入的门控协议叙事文本（见 12.4）。

### 12.2 api.ts：REST 客户端层

`frontend/src/lib/api.ts` 是唯一与后端 HTTP 接口打交道的模块，所有页面/组件都通过它发请求，不直接调用 `fetch`。核心是一个泛型响应解包函数：

```ts
async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status, body: await res.text() });
  return res.json();
}
```

约定：非 2xx 响应统一抛出一个"增强版"`Error`——在标准 `Error` 对象上挂载 `status`（HTTP 状态码）和 `body`（原始响应文本，通常是 FastAPI 的 `{"detail": ...}` JSON）两个附加字段，而不是定义专门的异常类。调用方按需 `catch (err) { const body = (err as { body?: string }).body; ... }` 取用,这个模式在 `App.tsx` 的 `modeErrorMessage` 和 `Eval.tsx` 的 `errText` 中各自实现了一遍（尝试 `JSON.parse(body).detail`，失败则回退原文）。

`api.ts` 按功能分组导出了以下端点封装（均为无状态纯函数，返回 `Promise<T>`）：

| 分类 | 函数 | HTTP | 用途 |
|---|---|---|---|
| 节点/状态 | `getNodes()` | GET `/api/nodes` | 拉取全部 `NodeDef`（节点类型元数据） |
| | `getStatus()` | GET `/api/status` | 拉取当前 `RuntimeMode` 和模型名 |
| | `setRuntimeMode(mode)` | POST `/api/runtime-mode` | 切换 LLM 运行模式 |
| 蓝图编译/存储 | `compileLoom(blueprint, bar)` | POST `/api/compile` | 编译校验，返回 errors/certificate/order |
| | `listBlueprints()` | GET `/api/blueprints` | 蓝图库列表 |
| | `getBlueprint(id)` | GET `/api/blueprints/{id}` | 取单个蓝图（`Loom` 结构） |
| | `saveBlueprint(blueprint)` | POST `/api/blueprints` | 保存蓝图 |
| 运行 | `startRun(body)` | POST `/api/runs` | 启动一次回测/回放 |
| | `startLive(body)` / `stopLive(id)` | POST `/api/live`、`/api/live/{id}/stop` | 启停实时纸面会话 |
| | `getRun(id)` / `listRuns()` | GET `/api/runs/{id}`、`/api/runs` | 查询运行详情/列表 |
| | `getTrace(runId, nodeId?, eventIdx?, limit)` | GET `/api/runs/{id}/trace` | 拉取节点执行轨迹（committee/reflection 等） |
| | `getLiveAnalysis(sessionId, limit)` | GET `/api/live/{id}/analysis` | 实时会话的分析记录 |
| 行情 | `getMarketCatalog()` | GET `/api/market/catalog` | 可用市场窗口目录 |
| | `getCandles(inst, bar, opts)` | GET `/api/market/candles` | K 线数据，`opts` 可以是简单 `limit` 数字或 `{start,end,limit}` 对象 |
| Eval 套件 | `evalFidelity/evalLeaderboard/evalAblation/evalScorecard/evolve` | 均为 POST，走统一的 `post<T>()` helper | 评估实验室的五类分析（见第 12.5 节 Eval 页） |

其中 Eval 相关端点全部走一个内部 `post` helper（`api.ts:71-73`），注释明确写道"全 POST，返回对应 to_dict JSON，无 envelope"——即后端直接把 dataclass 的 `to_dict()` 结果作为响应体，没有额外的 `{data: ...}` 包装层，前端类型（`LadderReport`、`Board`、`AblationReport`、`Scorecard`、`Genealogy`，定义在 `lib/eval.ts`）直接对应后端 dataclass 字段。`getCandles` 的参数重载（数字或对象）是一个典型的渐进式 API：早期调用只传 `limit`，后来需要按时间窗口分页查询时加了 `{start, end, limit}` 形式，用联合类型 + 运行时 `typeof` 判断保持向后兼容（`api.ts:43-57`）。

### 12.3 ws.ts：WebSocket 客户端封装

`frontend/src/lib/ws.ts` 只有 25 行，封装了两个具体端点和一个通用工厂：

```ts
export function openRunSocket(runId: string, onEvent: (e: RunEvent) => void, onClose?: () => void) {
  return openSocket(`/ws/runs/${runId}`, onEvent, onClose);
}
export function openLiveSocket(sessionId: string, onEvent: (e: RunEvent) => void, onClose?: () => void) {
  return openSocket(`/ws/live/${sessionId}`, onEvent, onClose);
}
function openSocket(path: string, onEvent: (e: RunEvent) => void, onClose?: () => void) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}${path}`);
  ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch { /* ignore */ } };
  ws.onclose = () => onClose?.();
  return {
    send: (cmd: "resume" | "step" | "stop") => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd }));
    },
    close: () => ws.close(),
  };
}
```

设计要点：

- **事件类型是开放的**：`RunEvent` 只约束了一个必填 `type: string` 字段，其余用 `[k: string]: any` 兜底（`ws.ts:2`）。具体的事件形状（`bar_update`、`paused`、`completed` 等）由消费方（`Studio.tsx`/`LiveDesk.tsx` 的事件分发 switch）自行按 `type` 窄化，`ws.ts` 本身不关心业务语义,只做"连接 + JSON 解析 + 分发"的传输层工作。
- **收发不对称**：接收方向是自由格式事件回调；发送方向被收窄成一个封闭的命令字面量联合类型 `"resume" | "step" | "stop"`，对应断点调试协议里仅有的三种前端可下发指令（配合 `PausedInspector` 组件的暂停/继续/单步/停止交互）。
- **两个具体端点复用同一套底层实现**：`/ws/runs/{runId}`（回测/回放的事件流）和 `/ws/live/{sessionId}`（实时纸面交易的事件流）在协议层完全一致，只是路径参数和语义不同，因此没有必要写两套连接逻辑。
- **静默容错**：`onmessage` 里 `JSON.parse` 失败直接吞掉（`catch { /* ignore */ }`），不抛出、不上报，避免单条畸形消息打断整个事件流。
- **协议自适应**：根据 `location.protocol` 是否为 `https:` 决定用 `wss` 还是 `ws`（`ws.ts:14`），使得同一份代码在开发环境（Vite proxy，`ws://127.0.0.1:8000`，见 `vite.config.ts:9-10`）和生产部署（可能在 `https` 之后）都能拿到正确的 scheme。

调用方（如 `Studio.tsx`）典型用法是 `sock.current = openRunSocket(runId, handleEvent, handleClose)`，在组件卸载或重新发起运行前调用 `sock.current?.close()`。

### 12.4 loom.ts：蓝图图的客户端表示与后端往返

`frontend/src/lib/loom.ts` 文件头注释自称"契约区"（锁定 TS 类型 + 映射 + 色板），是前后端 `.loom` 蓝图格式在前端的唯一权威类型定义与转换逻辑所在。

#### 核心数据类型

```ts
export type PinType = "exec" | "candle" | "series" | "signal" | "risk_stamped_signal" | "bool";
export interface NodeDef {
  type: string; category: string;
  inputs: Record<string, PinType>; outputs: Record<string, PinType>;
  params: Record<string, string>; cost: Record<string, unknown>;
}
export interface LoomNode { id: string; type: string; params: Record<string, unknown>; }
export interface LoomEdge { from: string; to: string; feedback?: boolean; }
export interface Loom {
  id: string; name: string; nodes: LoomNode[]; edges: LoomEdge[];
  meta: Record<string, unknown>;
}
```

`PinType` 六种取值直接对应后端节点系统里的端口类型标签（第 3/4 节讨论的图编译器/节点运行时用同一套类型体系做端口兼容性校验）。`Loom`/`LoomNode`/`LoomEdge` 是 `.loom` JSON 文件在内存里的直接映射——字段名、嵌套结构与磁盘上的 `.loom` 文件（如 `blueprints/ema_cross.loom`）逐一对应：

```json
{
  "id": "ema_cross_v1", "name": "EMA Cross Trend Follow",
  "nodes": [{ "id": "feed", "type": "candle_feed", "params": { "inst": "BTC-USDT-SWAP", "bar": "1m" } }, ...],
  "edges": [{ "from": "feed.out", "to": "ema_fast.candle" }, ...],
  "meta": { "preset": true, "description_zh": "...", "gateProtocol": { ... } }
}
```

边（edge）用 `"nodeId.portName"` 字符串拼接表示端口引用（如 `"feed.out"`、`"ema_fast.candle"`），而不是拆成独立字段——这与 `flowToLoom`/`loomToFlow` 里 `e.from.split(".")` 的解析逻辑一致。`feedback?: boolean` 标记反馈边（用于带环反馈的图,如委员会节点回读历史决策），渲染时会加虚线动画样式区分。

#### 与 React Flow 的双向转换

可视化画布用的是 `@xyflow/react`（React Flow v12），它的节点/边模型（`FlowNode`/`FlowEdge`，带 `position` 和 `data`）与后端 `.loom` 格式不同源，因此 `loom.ts` 提供了一对纯函数做双向映射：

- **`loomToFlow(loom, defs)`**：把磁盘/后端格式转换成画布可渲染的节点和边。节点位置优先取 `loom.meta.positions[nodeId]`（用户上次拖拽保存的坐标），缺失时调用 `layoutLoomPositions()` 做自动布局兜底。边的 `"nodeId.port"` 字符串被拆分成 React Flow 需要的 `source`/`sourceHandle`（`out:${port}`）和 `target`/`targetHandle`（`in:${port}`）。
- **`flowToLoom(nodes, edges, base)`**：反向转换，画布编辑后的节点/边写回 `.loom` 结构，同时把当前节点坐标写入 `meta.positions`，并调用 `pruneGateProtocolNodeRefs()` 清理 `meta.gateProtocol` 里失效的节点 ID 引用（用户删除节点后，门控协议卡片里指向该节点的 `nodes: string[]` 数组会被过滤，避免悬空引用）。

`layoutLoomPositions()`（`loom.ts:125-162`）实现了一个简单的分层自动布局：按边（忽略 `feedback` 边）做拓扑排序求每个节点的最大深度（`depth`，类似关键路径),同深度节点纵向堆叠、不同深度节点按 `GRID_X=260`/`GRID_Y=150` 网格错开,这就是 Studio 页面"整理布局"按钮背后的算法。

#### 连接合法性校验

`validateFlowConnection(nodes, edges, connection)` 在用户于画布上拖拽连线时被调用，依次检查：源/目标端口是否都已选中、是否自环、端口类型是否已知、目标端口是否已被占用（每个输入端口只能接一条边）、以及两端 `PinType` 是否相同（`signal` 不能连到 `candle` 等）。校验失败返回 `{ ok: false, reason }`，前端据此在 UI 上给出连线被拒绝的提示，是画布交互层面对"类型系统"的即时反馈，呼应后端编译期的严格端口类型检查（第 3 节）。

#### 门控协议（GateProtocol）类型

除了图结构本身,`Loom.meta` 还可以携带一份人类可读的"门控协议"叙事（`GateProtocol` 类型,`loom.ts:24-31`），用双语富文本描述蓝图的执行阶段、风险门、失败路径等,每张卡片（`GateProtocolCard`）可关联一组 `nodes: string[]` 高亮画布上对应节点。`ema_cross.loom` 的 `meta.gateProtocol` 示例展示了实际形状：`steps`（inputs → stage1 → stage2 → risk_gate → runtime 五个阶段卡片,每张有 `tone`/`eyebrow`/`title`/`body`/`nodes`）、`sidecars`（如 `kill_switch` 组合熔断的旁路卡片）、以及一条 `invariant`（"模型或策略的原始输出不能直接执行,必须先经过仓位计算和 RiskGate 盖章"）。`isGateProtocol()` 类型守卫用 `Array.isArray(value.steps)` 做运行时鉴别,`GateView` 组件消费这份数据渲染成第 7 节提到的"门控视图"。

### 12.5 页面清单

四个顶层页面通过 `App.tsx` 的路由懒加载,各自是一个自包含的功能工作台，彼此通过后端 REST/WS 接口和 `lib/loom.ts` 的类型间接耦合，不共享组件级状态。

**Studio（`pages/Studio.tsx`，约 624 行）—— 可视化蓝图编辑器**：核心工作台，左侧是蓝图库/节点库,中间是 React Flow 画布（`ReactFlow` + 自定义 `NodeCard` 节点渲染器 + `smoothstep` 边），右侧标签页在 Copilot（`CopilotPanel`）和 Inspect（编译错误 `ErrorPanel`、成本证书 `CertPanel`、暂停检查器 `PausedInspector`）之间切换。管理蓝图的编译（`compileLoom`）、保存（`saveBlueprint`）、启动回测并通过 `openRunSocket` 订阅事件、维护断点集合（`bps`）、在"门控视图/节点视图"（`blueprintView: "gate" | "graph"`）间切换，并将回测参数（`BacktestConfig`）与市场目录（`getMarketCatalog`）联动。是唯一同时驱动图编辑、编译、回测、Copilot 会话的页面。

**LiveDesk（`pages/LiveDesk.tsx`，约 714 行,第 7 节已详述）—— 实时纸面交易台**：PA_Agent 风格的三栏布局（蓝图在左、K 线在中、诊断/门控/反思在右）,通过 `startLive`/`stopLive` 管理一个轮询 OKX 公开行情的纸上交易会话,用 `openLiveSocket` 订阅推送，画布上的节点会随当前 bar 的执行路径实时高亮（`buildLiveStageSnapshots`/`currentLiveCandle`，定义于 `lib/liveDesk.ts`）。此处仅记录其存在与职责，具体设计已在第 7 节展开。

**Terminal（`pages/Terminal.tsx`，约 205 行）—— 运行复盘/回放终端**：面向"事后分析"的只读工作台。用 `RunPicker` 选择一个历史 run，展示 `CandleChart`+`Fill` 叠加、`EquityChart`权益曲线、`SummaryCards`汇总指标、`TradesTable`成交明细。其特色是 `AgentInsights`/`CommitteeRoleCard` 两个内部组件：通过 `getTrace(runId)` 一次性拉全部节点轨迹，用 `parseInsights()`（`lib/insights.ts`）解析出委员会角色轨迹、RAG 引用、反思判定等 Agent 侧富信息并渲染。委员会角色标签用 `inferCommitteeRole(trace, idx)` **按元素形状推断**而非按下标——代码注释明确指出这是为了修复消融实验（`no_risk_officer` 臂）中角色错位的问题：策略师有 `{side, rationale, confidence}` 字段、风控官有 `{veto, concern, confidence}`、主席有 `{side, rationale, confidence}`，按下标固定分配会在角色缺失的消融臂里张冠李戴。

**Eval（`pages/Eval.tsx`，约 242 行）—— 评估实验室**：文件头注释自称"诚实评估套件"，从一个已完成的 run 出发，依次拉取五类后端权威评估结果并可视化：保真度阶梯（`evalFidelity` → `FidelityLadder`,零 LLM 成交重放）、记分卡（`evalScorecard` → `ScorecardPanel`）、基线排行榜（`evalLeaderboard` → `LeaderboardTable`，三基线零 LLM 对比）、委员会消融（`evalAblation` → `AblationTable`，full/no_risk_officer/no_rag 三臂对比）、进化谱系（`evolve` → `GenealogyTree`）。消融与进化是"蓝图级"实验（因为 run 记录本身不存蓝图——注释指出 `app.py:207` 用 `model_dump(exclude=blueprint)`），所以这两块单独从蓝图库（`listBlueprints`/`getBlueprint`）选一张蓝图驱动,而不是从 run 派生。用 `Block` 内部组件统一处理"运行按钮 + 加载态 + 错误态 + 内容"的复合展示模式。所有评估类型（`LadderReport`/`Board`/`AblationReport`/`Scorecard`/`Genealogy`）在 `lib/eval.ts` 中定义，字段名严格对齐后端 dataclass 的 `to_dict()` 输出。

### 12.6 组件清单

`frontend/src/components/` 下 17 个组件，按用途可分四组：

**图表与数据展示**

| 组件 | 用途 |
|---|---|
| `CandleChart.tsx` | 基于 `lightweight-charts` 封装的 K 线图，叠加成交标记（`Fill[]`），`Studio`/`Terminal`/`LiveDesk` 共用 |
| `EquityChart.tsx` | 同样基于 `lightweight-charts`，渲染权益曲线（`[timestamp, equity][]`） |
| `SummaryCards.tsx` | 六项回测汇总指标卡片（net_pnl/return_pct/max_drawdown/num_trades/win_rate/profit_factor） |
| `TradesTable.tsx` | 成交明细表（time/side/qty/price/fee/tag） |
| `CertPanel.tsx` | 展示编译产出的"成本证书"（如 `deterministic_ratio` 等字段） |

**图编辑与门控可视化**

| 组件 | 用途 |
|---|---|
| `NodeCard.tsx` | React Flow 自定义节点渲染器：按 `PinType`/`CATEGORY_COLORS` 上色端口和节点头，支持 active/blocked/breakpoint/Copilot diff（added/removed/changed）等视觉态 |
| `GateView.tsx` | 消费 `Loom.meta.gateProtocol`，把蓝图渲染成阶段化的"门控叙事"卡片视图，而非节点图 |
| `ErrorPanel.tsx` | 编译错误列表，点击错误可定位到对应节点（`onFocus(nodeId)`） |
| `PausedInspector.tsx` | 断点暂停时展示当前节点的输入快照，并提供 resume/step/stop 三个命令按钮（对接 `ws.ts` 的 `send`） |
| `RunPicker.tsx` | 通用的历史 run 选择下拉/列表（`Terminal`、`Eval` 共用） |
| `BacktestLab.tsx` | Studio 内嵌的回测参数与结果面板：市场/周期/时间窗口选择 + K 线/权益图联动回放游标 |
| `CopilotPanel.tsx` | 聊天式蓝图生成侧栏：输入自然语言 → `POST /api/copilot/blueprint` → 画布 diff 预览（新增/删除/改动分色）→ 应用/应用并回测；同时承担选中节点时的信号详情展示（rationale/confidence/citations/committee_trace，从 run trace 读取） |

**Eval 实验室专用可视化**

| 组件 | 用途 |
|---|---|
| `FidelityLadder.tsx` | L0–L3 四档成交模型净利柱状图 + 乐观差距（optimism gap）高亮 |
| `ScorecardPanel.tsx` | 综合分大字 + 四维分项（valid_performance/generalization/fidelity/determinism）+ 证据覆盖 + 可展开的批判 notes |
| `LeaderboardTable.tsx` | 基线排行榜表格，`in_sample_only` 行视觉降权、运气基线标 luck 角标 |
| `AblationTable.tsx` | 委员会消融三臂对比表 + 护栏价值（`guardrail_value`，正负如实展示，不美化负值） |
| `GenealogyTree.tsx` | 基于 React Flow 的进化谱系树：按代（gen）分层布局，`parent_id` 连边，`compile_status`（ok/repaired/stillborn/runtime_error）分色，winner 金色高亮 |

多个组件的文件头注释直接写明了对应的后端 dataclass `to_dict()` 字段形状（例如 `AblationTable.tsx` 开头注释给出 `AblationReport.to_dict(): {arms:[...], guardrail_value:{...}}` 的完整结构），这是前端在没有共享类型生成工具（如 OpenAPI codegen）情况下，用注释手动维护前后端契约一致性的约定。

### 12.7 构建与工具链

前端是独立的 Vite 工程（`frontend/package.json`），与后端解耦部署，开发态通过 Vite dev server 的反向代理与本地 FastAPI 通信：

```ts
// vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  test: { environment: "jsdom" },
} as never);
```

`/api` 和 `/ws` 两个前缀分别代理到本地 FastAPI 服务（默认 `127.0.0.1:8000`），`ws: true` 开启 WebSocket 代理支持——这也是 `lib/api.ts`/`lib/ws.ts` 里所有请求都用相对路径（`fetch("/api/...")`、`` `${proto}://${location.host}/ws/...` ``）而不写死后端地址的原因：开发环境靠 Vite 代理，生产环境假定前后端同源部署。

**依赖构成**（`package.json`）：核心运行时依赖只有 `react`/`react-dom`（18.3）、`@xyflow/react`（v12，图编辑器画布）、`lightweight-charts`（K 线/权益图）,加三套 `@fontsource` 字体包（Chakra Petch 做展示字体、IBM Plex Sans 做正文、JetBrains Mono 做等宽/数字）。没有引入状态管理库（Redux/Zustand 等）——全部状态用 React 自带的 `useState`/`useSyncExternalStore` 承载,符合项目"页面级自包含、组件通过 props 和 lib 纯函数通信"的轻量架构取向。

**开发脚本**：`dev`（`vite`）、`build`（`tsc -b && vite build`，先类型检查再打包）、`test`（`vitest run`）、`preview`。测试用 **vitest**（`test: { environment: "jsdom" }` 配置在 `vite.config.ts` 里，未单开 `vitest.config.ts`），测试文件分布在各目录下的 `__tests__/` 子目录（如 `lib/__tests__/loom.test.ts`、`lib/__tests__/api.test.ts`、`lib/__tests__/copilot.test.ts`、`lib/__tests__/insights.test.ts`、`lib/__tests__/backtestConfig.test.ts`、`lib/__tests__/demoDefaults.test.ts`、`lib/__tests__/liveDesk.test.ts`、`lib/__tests__/runtimeMode.test.tsx`、`lib/__tests__/evalRender.test.tsx`，以及 `components/__tests__/`（`GateView`、`NodeCard`、`BacktestLab`、`RunPicker`、`ChartLifecycle`）和 `pages/__tests__/LiveDesk.test.tsx`）——覆盖面既包括纯函数（`loom.ts` 的转换/校验逻辑、`backtestConfig.ts` 的默认值推导）也包括组件渲染（`jsdom` 环境下测试 React Flow 节点、图表生命周期等）。

**样式**：Tailwind CSS（`tailwind.config.js`）定义了项目专属的深色调色板（`void`/`panel`/`edge`/`grid` 背景色阶,`loom.{blue,cyan,violet,amber,gold,green,red}` 语义色,对应 `PIN_COLORS`/`CATEGORY_COLORS` 在图上的配色）、三套字体族映射（`sans`/`mono`/`display`），以及几个自定义关键帧动画（`boot` 启动淡入上浮、`sweep` 扫描线、`flicker` 呼吸闪烁），共同构成 App.tsx 顶栏、`hud-label`、`live-dot`、`panel` 等到处复用的"HUD/终端"视觉风格。`content` 扫描范围是 `index.html` + `src/**/*.{ts,tsx}`，未使用 CSS-in-JS 或 CSS Modules，样式完全走 utility class。TypeScript 侧用标准 `tsconfig.json`（项目引用式 `tsc -b`），`vite build` 前先跑一遍类型检查，保证类型错误在构建期而非运行期暴露。

---

## 13. 部署、运维与 CI

AlphaLoom 的部署故事是"单机、离线优先、零配置也能跑起来"：整套一键启动链路（`START_ALPHALOOM.cmd` → 端口守护 → demo 库幂等生成 → 前端构建 → 启动 uvicorn）设计目标是让一个刚 clone 仓库、没有任何 API Key 的人在几十秒内看到一个功能完整的 Studio；而 CI 复用同一条"确定性、零网络"的原则，把录制/回放的 LLM 调用当作生产环境同款依赖来跑，从而彻底不需要在 CI 里注入任何真实密钥。

### 13.1 一键启动:`START_ALPHALOOM.cmd`

`START_ALPHALOOM.cmd`（仓库根目录）是面向"第一次拿到这个仓库的人"设计的单文件启动脚本，`demo.bat` 只是对它的一层转发（`call START_ALPHALOOM.cmd %*`），二者是同一入口的两个别名。脚本按编号步骤输出进度，任何一步失败都跳转到 `:fail` 标签打印错误并 `pause` 停住窗口（Windows 双击运行时不会一闪而过）：

```bat
if not exist "%PY%" (
  echo [0/4] Backend virtualenv not found. Creating it now...
  ...
  .venv\Scripts\python.exe -m pip install -e .[dev]
)
...
echo [1/4] Ensuring deterministic demo market database...
"%PY%" scripts\ensure_demo_db.py
...
echo [2/4] Installing frontend dependencies...   (仅当 node_modules 不存在)
...
echo [3/4] Building frontend bundle...
pushd frontend && call npm.cmd run build
...
echo [4/4] Starting AlphaLoom in offline replay mode...
```

完整编排顺序是:

1. **虚拟环境自举**(`0/4`,条件执行):若 `backend\.venv\Scripts\python.exe` 不存在,优先用 `py -3.12` 创建虚拟环境(找不到 `py` 启动器则退回系统 `python`),再 `pip install -e .[dev]` 把 `backend/pyproject.toml` 声明的运行时依赖(`fastapi`、`uvicorn[standard]`、`openai`、`python-dotenv`)与开发依赖(`pytest`、`hypothesis`、`httpx`)一起装好。已存在则跳过,保证重复运行是幂等的、快速的。
2. **npm 存在性检查**:`where npm.cmd` 找不到就直接报错退出,提示安装 Node.js——这是脚本对 Windows 环境唯一的硬性外部依赖检查。
3. **`1/4` demo 数据库自举**:调用 `scripts\ensure_demo_db.py`(见 13.3),生成/校验 `data/demo.sqlite`。这一步在 `npm ci` 之前执行,即便前端安装很慢,行情库已经就绪。
4. **`2/4` 前端依赖安装**(条件执行):只有 `frontend\node_modules` 不存在时才跑 `npm ci`(锁定 `package-lock.json` 版本,与 CI 的 frontend job 用同一命令,保证本地与 CI 装出同一套依赖树)。
5. **`3/4` 前端构建**:`npm run build` 对应 `frontend/package.json` 里的 `"build": "tsc -b && vite build"`——先做 TypeScript 项目级类型检查再 Vite 打包,构建产物落进 `frontend/dist`(FastAPI 通过 `frontend_dist` 参数把它当静态资源 + SPA fallback 挂载,见后端 `create_app`)。
6. **`--check` 早停**:若脚本第一个参数是 `--check`,构建通过后立即打印 `[OK] Startup check passed` 并 `exit /b 0`,不启动服务器——这给"只想验证环境是否装得起来"(例如自动化冒烟测试)提供了一条不占端口、不常驻的路径。
7. **`4/4` 设置离线模式并启动**:导出 `ALPHALOOM_OFFLINE=1`,过端口守护(见 13.2),用 `powershell ... Start-Sleep -Seconds 3; Start-Process '%URL%'` 延时 3 秒后自动拉起默认浏览器指向 `http://127.0.0.1:8000/?alphaloom=%RANDOM%#/studio`(URL 里塞入 `%RANDOM%` 查询参数是为了在浏览器已经打开过旧 tab 时避免缓存复用同一地址),随后前台运行:
   ```bat
   "%PY%" -m uvicorn alphaloom.serve:app --host 127.0.0.1 --port %PORT% --app-dir backend
   ```
   这是一条阻塞命令,`Ctrl+C` 是唯一的停止方式,进程退出后打印 "AlphaLoom server stopped." 并 `pause`。

`PORT` 环境变量可覆盖默认的 `8000`(脚本头部 `if not defined PORT set "PORT=8000"`),错误提示里也直接给出了改端口重跑的示例:`set PORT=8010 && START_ALPHALOOM.cmd`。

#### `dev.bat`:双窗口热更新替代路径

`dev.bat` 是面向开发者(而非最终用户体验 demo)的极简版本,不做任何虚拟环境/依赖检查,只做三件事:

```bat
backend\.venv\.venv\Scripts\python scripts\ensure_demo_db.py
start "alphaloom-api" cmd /k backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --reload --app-dir backend
start "alphaloom-web" cmd /k "cd frontend && npm run dev"
```

它假定虚拟环境和 `node_modules` 已经装好(不做存在性判断,也不设 `ALPHALOOM_OFFLINE`),用 `start` 打开两个独立的 `cmd` 窗口分别跑:

- 后端 `uvicorn --reload`——代码改动自动重载,监听 `:8000`;
- 前端 `npm run dev`(Vite dev server)——监听 `:5173`,走 Vite 自身的模块热替换,不需要每次改动重新 `tsc -b && vite build`。

两个前端入口(`START_ALPHALOOM.cmd` 用生产构建 + FastAPI 托管静态资源在同一 `:8000` 端口;`dev.bat` 用 Vite dev server 在独立 `:5173` 端口、后端单独 `:8000`)对应"体验产品"与"改代码调试"两种不同场景,`dev.bat` 不设 `ALPHALOOM_OFFLINE`,因此默认会尝试用 `.env` 里配置的真实 LLM 端点(如果有的话)。

### 13.2 端口守护:`alphaloom_port_guard.ps1`

这个 PowerShell 脚本解决的问题是:双击 `START_ALPHALOOM.cmd` 时,端口 `8000` 可能处于三种状态之一——(a) 完全空闲、(b) 已经跑着**同一个** AlphaLoom 实例(用户此前开过一次忘了关,或者开了第二个窗口)、(c) 被**别的**服务占用(比如另一个项目的开发服务器恰好也用 8000)。脚本必须精确区分 (b) 和 (c),因为处理方式完全不同——前者应该直接打开浏览器复用现有实例,后者应该报错并建议换端口,而不能贸然尝试再启动一个 uvicorn 抢占已被占用的端口。

区分逻辑分两层:

```powershell
try {
  $openapi = Invoke-RestMethod -Uri "$baseUrl/openapi.json" -TimeoutSec 2
  $title = [string]$openapi.info.title
  if ($title -eq "AlphaLoom API") {
    Write-Output "ALPHALOOM_RUNNING $baseUrl"
    exit 0
  }
  ...
  Write-Output "PORT_OCCUPIED_BY_OTHER $Port title=$title"
  exit 2
} catch {
  $probeError = $_.Exception.Message
}

try {
  $tcp = [System.Net.Sockets.TcpClient]::new()
  $task = $tcp.ConnectAsync($HostName, $Port)
  $listener = $task.Wait(500) -and $tcp.Connected
  $tcp.Close()
} catch { $listener = $false }

if (-not $listener) {
  Write-Output "PORT_FREE $Port"
  exit 1
}
Write-Output "PORT_OCCUPIED_BY_OTHER $Port error=$probeError"
exit 2
```

1. **第一层:业务层探针**——直接请求 `$baseUrl/openapi.json`(FastAPI 自动暴露的 OpenAPI schema 端点),检查 `info.title` 字段是否恰好等于 `"AlphaLoom API"`(这个标题串是 FastAPI app 构造时设置的应用标识,后端 `serve.py`/`app.py` 里创建 `FastAPI(title="AlphaLoom API", ...)`)。命中则确证"这就是我自己的实例",退出码 `0`。如果请求成功但 title 不匹配(端口上跑着别的 HTTP 服务,也长得像一个 OpenAPI 应用),判定为 (c),退出码 `2`。
2. **第二层:TCP 层探针**(仅当第一层请求直接失败/超时,即端口上根本没有 HTTP 响应或访问不了 OpenAPI 端点时才走到):用 `TcpClient.ConnectAsync` 加 500ms 超时判断端口是否有任何进程在监听。如果连不上,判定为 (a) 空闲,退出码 `1`;如果能连上但不是 AlphaLoom 的 OpenAPI(比如非 HTTP 的服务、或 HTTP 但探针第一层因为其他原因抛了异常),判定为 (c),退出码 `2`。

**退出码契约**(`START_ALPHALOOM.cmd` 消费方):

| 退出码 | 含义 | 调用方(`START_ALPHALOOM.cmd`)动作 |
|---|---|---|
| `0` | 端口上正跑着**同一个** AlphaLoom 实例 | 打印 `[INFO] AlphaLoom is already running...`,直接 `start` 打开浏览器指向现有实例,不再启动新进程 |
| `1` | 端口空闲 | 静默放行,继续走到 `uvicorn` 启动那一行 |
| `2` | 端口被**别的**进程/服务占用 | 打印 `[ERROR] Port %PORT% is already used by another service`,提示换端口(`set PORT=8010`)后 `exit /b 1` |

这套"探针分级 + 退出码即状态机"的设计让批处理脚本(bat 本身没有结构化返回值机制)可以用简单的 `if "%PORT_GUARD%"=="0"` / `"2"` 字符串比较来驱动分支,同时把探测逻辑的复杂度(两层探针、超时、异常处理)完全封装进独立的 PowerShell 脚本,不污染 `.cmd` 主流程。

### 13.3 Demo 数据库自举:`ensure_demo_db.py`

`scripts/ensure_demo_db.py` 的文档字符串直接点明了它的定位:"幂等生成确定性 demo 行情库(秒级、零联网)。dev.bat/demo.bat 启动前调用。" 它要解决的核心问题是——一个刚 clone 仓库、没有配置任何交易所 API Key 的新用户,必须能立刻在 Studio 里跑回测、看到有意义的 K 线和策略行为,而不是面对一个空数据库或报错。

实现上采用**合成 K 线为主、真实 OKX 快照为辅**的混合策略,并且全程幂等(`db.bounds(inst, bar)` 查询该 `inst/bar` 组合是否已有数据,有则跳过,不重复生成):

```python
OUT = Path(__file__).resolve().parents[1] / "data" / "demo.sqlite"
REAL_OKX_DB = Path(__file__).resolve().parents[1] / "data" / "real_okx_14d.sqlite"

def main() -> int:
    db = SQLiteMarketData(OUT)
    built_synth = False
    if not db.bounds("BTC-USDT-SWAP", "1m"):
        up = gen_candles(2000, seed=11, trend=0.0008, start_price=60_000, vol=0.003)
        down = gen_candles(1200, seed=12, trend=-0.0009, start_ts=up[-1]["ts"] + 60_000,
                           start_price=up[-1]["close"], vol=0.004)
        chop = gen_candles(800, seed=13, trend=0.0, start_ts=down[-1]["ts"] + 60_000,
                           start_price=down[-1]["close"], vol=0.002)
        db.insert_candles("BTC-USDT-SWAP", "1m", up + down + chop)
        built_synth = True
    if not db.bounds("ETH-USDT-SWAP", "1m"):
        eth = gen_candles(4000, seed=21, trend=0.0003, start_price=3000, vol=0.004)
        db.insert_candles("ETH-USDT-SWAP", "1m", eth)
        built_synth = True
    copied_sol = _copy_real_candles(db, "SOL-USDT-SWAP")   # 若本地存在 real_okx_14d.sqlite 才会执行
    ...
```

关键设计点:

- **BTC-USDT-SWAP**:用 `backend/tests/fixtures/synth.py` 的 `gen_candles(...)` 合成三段共 4000 根 1 分钟 K 线——先是 2000 根固定随机种子(`seed=11`)、正趋势(`trend=0.0008`)、起价 60,000 的上涨段;紧接 1200 根负趋势下跌段(`seed=12`,承接上一段收盘价);再接 800 根零趋势震荡段(`seed=13`)。三段拼接刻意制造"趋势 → 反转 → 盘整"的完整市场状态机,让基于均线交叉/ATR 之类的策略节点在离线 demo 里能真实触发开仓、止损、反手等各种路径,而不是一条单调的直线。
- **ETH-USDT-SWAP**:单段 4000 根、`seed=21`、弱正趋势(`trend=0.0003`)、起价 3000。
- 所有合成调用都传入固定的 `seed`,加上 `gen_candles` 本身是纯函数式生成器,保证**同一份代码在任何机器、任何时间跑出来的 `demo.sqlite` 内容逐字节确定**——这与整个项目"确定性优先"的基调一致,也是它能被 CI 复用而不需要网络访问的前提。
- **SOL-USDT-SWAP 走另一条路径**:`_copy_real_candles` 检查本地是否存在 `data/real_okx_14d.sqlite`(一份此前用 `scripts/build_sample_db.py` 从 OKX 公共接口拉取的真实 14 天快照,不随仓库分发,`.gitignore` 里 `data/*.sqlite` 默认排除、只有 `demo.sqlite` 与 `llm_calls.sqlite` 被显式 `!` 排除在忽略规则之外重新纳入版本控制),如果存在就把对应 `inst/bar` 的整段真实 K 线原样拷进 `demo.sqlite`;不存在则静默跳过(`return 0`),不报错、不阻塞启动。这让**核心开发者**在本地能用真实数据丰富 demo 库,而**普通用户/CI clone 下来的仓库**因为没有 `real_okx_14d.sqlite` 这份大文件,SOL 数据就是空的,完全不影响 BTC/ETH 合成数据驱动的 demo 流程。

最终 `data/demo.sqlite` 作为**已提交的确定性资产**直接进版本库(`git ls-files data/` 确认只有 `demo.sqlite` 与 `llm_calls.sqlite` 两个 `.sqlite` 被追踪,其余 `real_okx_14d.sqlite`、`runs.sqlite` 等都被 `.gitignore` 排除在外)。即便某个环境下 `ensure_demo_db.py` 因为某种原因跑不了,已经提交的 `demo.sqlite` 本身也能直接用——脚本存在的意义主要是"首次 clone 后自愈式生成/补全",以及"验证幂等性不会因为已有数据而重新造轮子"。

对照的 `scripts/build_sample_db.py` 走的是完全不同的路径:它会真的通过 `urllib.request` 访问 OKX 公共 REST(`https://www.okx.com/api/v5/market/history-candles`),分页拉取、指数退避重试限流,把结果写进 `data/sample.sqlite`。文件头注释明确写着"仅公共端点、无鉴权;限流退避;**测试/CI 绝不调用本脚本**"——这条脚本只在开发者需要真实历史数据(例如生成 `real_okx_14d.sqlite` 素材)时手动运行,与 demo/CI 的确定性流程完全隔离。

### 13.4 CI 场景下的离线 LLM 录制/回放(与第 8 节呼应)

CI 里的三类 demo(agent_committee 回测、committee_ablation 消融、evolve 进化)都会调用 LLM 节点,但 CI job 里既没有配置 `.env`,也没有注入任何真实 API Key。这之所以行得通,是因为 `RecordingLLMClient`(`backend/alphaloom/llm/recording.py`)提供的记录/回放层,与 `scripts/seed_recordings.py` 生成并提交进仓库的确定性录制语料 `data/llm_calls.sqlite` 配合工作:

- **录制(record)阶段**(离线开发时手动跑一次 `python scripts/seed_recordings.py`):`RecordingLLMClient` 把每次 `chat(messages, tools, temperature, **params)` 调用组装成的请求字典按 `json.dumps(request, sort_keys=True)` 规范化后取 SHA-256,作为主键连同请求/响应原文一起 `INSERT OR REPLACE` 进 SQLite 的 `llm_calls` 表(`hash TEXT PRIMARY KEY, request_json, response_json, created_at`)。`seed_recordings.py` 不连接任何真实 LLM 服务,而是用一个纯本地的 **fake transport**——按请求里 `system` 消息的关键字(如 `"committee's strategist"`、`"committee's risk officer"`、`"committee chair"`、`"translate a user's plain-language strategy"`、`"mutation operator"`)路由到对应角色的确定性响应生成函数(如 `_strategist_response`、`_risk_response`、`_chair_response`),这些函数读取请求里携带的市场 JSON(收盘价在 `[low, high]` 区间的相对位置等)算出方向/置信度,保证响应**随市场数据变化而非恒定值**,同时又是输入的纯函数、可重复生成。四类演示场景(committee 回测、copilot 自修复、消融三臂、进化变异)分别录制,且录制坐标(`DEMO_ABLATION_*`、`DEMO_EVOLVE_*` 等常量)是从后端 `alphaloom.eval.demo_coords` 模块直接 import 的"单一真源",保证录制时用的 `inst/bar/窗口/blueprint/population/generations` 与 `/api/eval/ablation`、`/api/evolve` 端点实际被调用时(`demo=True`)完全一致——请求哈希才能对上。
- **回放(replay)阶段**(`offline=True`,由环境变量 `ALPHALOOM_OFFLINE=1` 或显式参数触发):`chat()` 只在缓存命中时返回记录的响应,`self.cache_hits` 计数器加一;缓存未命中时**直接抛 `ReplayMissError`**,绝不会退化去调用真实 transport。这是硬性的失败快(fail-fast)设计——离线模式下任何一次 cache miss 就意味着回放语料与被测代码路径不匹配,必须显式报错而不是悄悄联网。
- **CI 后端测试则不走 `ALPHALOOM_OFFLINE` 全局开关**,而是各测试用例自行通过 `monkeypatch` 精确控制。`ci.yml` 顶部注释明确记录了这个设计决策的来龙去脉:

  ```yaml
  # Offline / deterministic only: the backend tests inject a scripted fake LLM (pure
  # local functions, zero network); tests that need record/replay set ALPHALOOM_OFFLINE
  # themselves via monkeypatch. There is no .env in CI, so _build_llm_client returns
  # None and no real LLM endpoint is ever reached — zero quota, no secrets needed.
  # (Do NOT set ALPHALOOM_OFFLINE globally here: it makes create_app auto-build an
  # offline client, breaking test_deterministic_endpoints_work_without_llm_client,
  # which asserts app.state.llm is None when nothing is configured.)
  ```

  提交历史里能直接对应到这条注释背后的真实教训:`262382d fix(ci): drop global ALPHALOOM_OFFLINE env — it broke the no-LLM-configured test`。也就是说,团队一度尝试在 CI 里全局设置 `ALPHALOOM_OFFLINE=1` 图省事,但这会让 `create_app`(`backend/alphaloom/api/app.py`)里的 `_build_llm_client` 无条件构造出一个离线客户端,导致原本要验证"完全没配置 LLM 时 `app.state.llm` 应为 `None`、纯确定性节点的蓝图仍应正常跑"这条契约的测试(`test_deterministic_endpoints_work_without_llm_client`)失效——于是改为不设全局环境变量,让 `_build_llm_client` 在没有 `.env`、没有 `ALPHALOOM_OFFLINE` 的"裸" CI 环境里必然返回 `None`(`LLMConfig.from_env` 在非 offline 分支缺 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` 时抛 `LLMConfigError`,被 `_build_llm_client` 捕获后返回 `None`),需要 LLM 的测试各自局部 `monkeypatch` 注入 `RecordingLLMClient(offline=True)` 或 fake transport。
- `data/llm_calls.sqlite` 与 `data/demo.sqlite` 一样被 `.gitignore` 显式放行(`!data/llm_calls.sqlite`)提交进仓库,`seed_recordings.py` 头部注释里还专门保护了一段真实录制不被脚本自身的幂等重建覆盖:该脚本重跑时只删除 `model == "spark-x1"`(demo 用的确定性录制模型)的行,显式保留 `model == "astron-code-latest"`(真实讯飞录制)的既有行,并在结尾用 `_EXPECTED_ASTRON = 123` 硬断言这批真实行数没有被误删——这是一次真实事故("D3-T11")后加固出来的幂等安全网。

因此,CI 完全不需要真实 API Key:非 LLM 相关的端点测试因为 `app.state.llm is None` 直接走"无 LLM 也能跑"的分支;LLM 相关测试通过局部注入离线 `RecordingLLMClient` + 已提交的 `llm_calls.sqlite` 录制库回放,`cache_misses` 恒为零。

### 13.5 CI 流水线:`.github/workflows/ci.yml`

工作流名为 `CI`,触发条件是任意分支的 `push` 和面向任意分支的 `pull_request`(`branches: ["**"]`),并用 `concurrency` 分组按 `ci-${{ github.ref }}` 取消同一分支上排队中的旧运行(`cancel-in-progress: true`),避免连续推送堆积过时的 CI 任务。两个 job 完全并行、互不依赖:

#### backend job

```yaml
backend:
  name: backend (pytest, py${{ matrix.python-version }})
  runs-on: ubuntu-latest
  strategy:
    fail-fast: false
    matrix:
      python-version: ["3.12", "3.13"]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: ${{ matrix.python-version }}
        cache: pip
        cache-dependency-path: backend/pyproject.toml
    - name: Install backend (with dev extras)
      working-directory: backend
      run: |
        python -m pip install --upgrade pip
        pip install -e .[dev]
    - name: Run pytest
      working-directory: backend
      run: python -m pytest -q
```

- 用矩阵策略在 **Python 3.12 与 3.13** 两个版本上各跑一遍(`backend/pyproject.toml` 声明 `requires-python = ">=3.12"`,矩阵覆盖了下限版本和下一个大版本,提前发现新 Python 版本上的兼容性回归),`fail-fast: false` 让两个矩阵分支互不影响、都跑完出结果。
- 依赖安装用可编辑安装 `pip install -e .[dev]`,拉取 `[project.optional-dependencies].dev` 声明的 `pytest>=8`、`hypothesis>=6`、`httpx>=0.27`(`httpx` 是 FastAPI `TestClient` 依赖的底层 HTTP 客户端库,`hypothesis` 用于基于属性的测试)。
- `pip` 缓存以 `backend/pyproject.toml` 为缓存键依据(`cache-dependency-path`),依赖不变时跳过重复下载。
- 测试命令是纯粹的 `python -m pytest -q`(安静模式),对应 `[tool.pytest.ini_options] testpaths = ["tests"]`——即 `backend/tests/` 目录下的全部用例。因为不设 `ALPHALOOM_OFFLINE` 且没有 `.env`,任何测试如果不小心真的尝试联网发起 LLM 请求都会在 `LLMConfig.from_env` 那一层直接因为缺 key 而失败,不存在"意外消耗真实配额"的可能性。

#### frontend job

```yaml
frontend:
  name: frontend (tsc + vitest + build)
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with:
        node-version: "20"
        cache: npm
        cache-dependency-path: frontend/package-lock.json
    - name: Install frontend deps
      working-directory: frontend
      run: npm ci
    - name: Unit tests (vitest)
      working-directory: frontend
      run: npm test
    - name: Type-check + build (tsc -b && vite build)
      working-directory: frontend
      run: npm run build
```

- 固定 Node.js 20,`npm` 缓存以 `frontend/package-lock.json` 为键。
- `npm ci` 严格按锁文件安装(与 `START_ALPHALOOM.cmd` 里前端依赖安装用的同一条命令,保证本地一键启动和 CI 装出的依赖树完全一致)。
- `npm test` 对应 `package.json` 里的 `"test": "vitest run"`——一次性运行全部 Vitest 单元测试(非 watch 模式)。
- `npm run build` 对应 `"build": "tsc -b && vite build"`,先做 TypeScript **项目级**类型检查(`tsc -b`,即 `--build` 模式,会遵循 `tsconfig` 的项目引用配置)再执行 Vite 生产构建——这一步同时充当"类型检查关卡"和"构建产物是否可生成"的双重校验,job 名称里的 `tsc + vitest + build` 精确概括了它做的三件事。

两个 job 没有互相依赖(没有 `needs:`),GitHub Actions 会并行调度;只要有一个 job 失败,PR 的整体 CI 状态即标红,但不会互相阻塞对方的执行与报告。

### 13.6 环境配置:`.env.example`

`.env.example` 是唯一需要用户填写的配置文件模板(其余一切——demo 数据、LLM 录制、前后端依赖——都随仓库自带或自动生成),内容极简:

```bash
# Copy this file to .env and fill in your real OpenAI-compatible LLM endpoint.
# The committed .env.example is safe to share; never commit your real .env.

LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=replace-with-your-real-key
LLM_MODEL=astron-code-latest

# START_ALPHALOOM.cmd starts in offline replay mode by default.
# The UI "Live" toggle forces live mode after these three values are configured.
```

三个键(`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`,对应 `backend/alphaloom/llm/client.py` 里的 `LIVE_KEYS` 元组)只有在用户想切换到**真实**LLM 端点("Live" 模式)时才需要配置:

- 这三者被设计为兼容**任意 OpenAI-compatible** 接口(不锁定具体供应商),`openai_transport(config)` 直接用官方 `openai` SDK 的 `OpenAI(base_url=..., api_key=...)` 构造客户端,再调用标准的 `client.chat.completions.create(**request)`——只要目标服务实现了 OpenAI 的 chat completions 协议就能接入。示例里的默认模型名 `astron-code-latest` 对应团队自己接入过的讯飞星火(iFlytek Spark)系列模型,也是 `data/llm_calls.sqlite` 里那 123 条真实录制(`_EXPECTED_ASTRON`)的来源模型。
- **不配置 `.env` 完全不影响离线体验**:`LLMConfig.from_env(offline=True)`(由 `ALPHALOOM_OFFLINE=1` 触发,`START_ALPHALOOM.cmd` 默认设置)直接短路,返回硬编码的 `OFFLINE_DEFAULTS`:

  ```python
  OFFLINE_DEFAULTS = {
      "LLM_BASE_URL": "http://offline.invalid/v1",
      "LLM_API_KEY": "offline-replay",
      "LLM_MODEL": "spark-x1",
  }
  ```
  这里 `LLM_MODEL` 固定为 `"spark-x1"` 不是随意选的占位符,而是**必须**与 `data/llm_calls.sqlite` 里 `seed_recordings.py` 录制时用的 `MODEL = "spark-x1"` 逐字一致——因为 `RecordingLLMClient._key()` 把 `model` 字段编进请求哈希参与运算,模型名一旦不匹配,离线回放会全部 cache miss。这也是为什么文档注释反复强调"model default MUST match the recordings"。
- 只有当用户不满足于离线 demo、想让 Studio 走真实模型时,才需要复制 `.env.example` 为 `.env`(仓库通过 `.gitignore` 的 `.env` / `.env.*` / `!.env.example` 规则组合,确保真实密钥永不会被误提交,只有示例模板留在版本库里),填入真实三元组,再通过前端 UI 的 "Live" 切换(对应后端 `POST /api/runtime-mode`,`body.mode == "live"` 分支)把 `app.state.llm` 从离线客户端换成用 `.env` 配置构建的生产 `RecordingLLMClient(offline=False)`——若三个键任一缺失,该端点会把 `LLMConfigError` 的具体缺失信息(`_LAST_LLM_CONFIG_ERROR`)透传为 HTTP 422 响应,而不是静默失败。

### 13.7 小结:各文件的角色分工

| 文件 | 角色 | 关键契约 |
|---|---|---|
| `START_ALPHALOOM.cmd` | 面向最终用户的一键启动器 | 幂等自举 venv → demo db → 前端 build → 端口守护 → 启动 uvicorn;`--check` 参数可做无副作用验证 |
| `demo.bat` | `START_ALPHALOOM.cmd` 的转发别名 | 无独立逻辑 |
| `dev.bat` | 开发者热更新双窗口启动 | 假定依赖已装好,不设离线模式,`--reload` + Vite dev server |
| `scripts/alphaloom_port_guard.ps1` | 端口占用判别 | 退出码 `0`=同实例已跑/`1`=端口空闲/`2`=被他物占用 |
| `scripts/ensure_demo_db.py` | 幂等生成/校验 `data/demo.sqlite` | BTC/ETH 合成、seed 固定确定性;SOL 视本地 `real_okx_14d.sqlite` 是否存在选择性拷贝 |
| `scripts/build_sample_db.py` | 从 OKX 公共接口拉取真实历史 K 线 | 仅供开发者手动运行,`测试/CI 绝不调用` |
| `scripts/seed_recordings.py` | 生成/校验确定性 LLM 录制语料 `data/llm_calls.sqlite` | fake transport 零网络;坐标与后端 `demo_coords` 单一真源对齐;保护真实 `astron-code-latest` 录制不被冲掉 |
| `.github/workflows/ci.yml` | CI 流水线 | backend(pytest × py3.12/3.13)+ frontend(vitest + tsc + vite build)并行;不设全局 `ALPHALOOM_OFFLINE` |
| `backend/pyproject.toml` | 后端包元数据与依赖声明 | `requires-python>=3.12`;`dev` extras 供 CI/本地测试复用 |
| `.env.example` | 真实 LLM 接入配置模板 | 三键均为可选(仅 Live 模式需要);离线默认值硬编码在 `llm/client.py` |
| `.gitignore` | 区分"随仓库分发"与"本地生成"数据 | `data/*.sqlite` 默认忽略,但显式放行 `demo.sqlite`/`llm_calls.sqlite` 两份确定性资产入库 |

---

**关键文件路径索引**(便于后续读者定位):

- `START_ALPHALOOM.cmd`
- `demo.bat`
- `dev.bat`
- `scripts/alphaloom_port_guard.ps1`
- `scripts/ensure_demo_db.py`
- `scripts/build_sample_db.py`
- `scripts/seed_recordings.py`
- `.github/workflows/ci.yml`
- `backend/pyproject.toml`
- `.gitignore`
- `.env.example`
- `backend/alphaloom/llm/client.py`(`LLMConfig.from_env`、`OFFLINE_DEFAULTS`、`openai_transport`)
- `backend/alphaloom/llm/recording.py`(`RecordingLLMClient`、`ReplayMissError`)
- `backend/alphaloom/api/app.py`(`_build_llm_client`、`create_app`、`/api/runtime-mode`)
