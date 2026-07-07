# AlphaLoom 设计文档（Design Spec）

日期：2026-07-05（v3：v2 平台型重构 + "诚实编译器"四创新；v1 "Crucible" 研究型方案已废弃）
状态：历史目标规格 / 施工蓝图（部分已实现，部分已延期；当前代码事实以 `docs/architecture.md` 与 `docs/future-work.md` 为准）
作者：赵成浩 + Claude

> **当前实现校准（2026-07-07）：** 本文保留 2026-07-05 的产品目标和面试叙事，不再作为“已实现能力清单”。与当前代码对齐的权威文档是 `docs/architecture.md`；刻意延期或已知边界见 `docs/future-work.md`。
>
> 当前主要差异：
> - Live 路径是 **OKX 公开 K 线 + 本地 `PaperBroker` 纸上撮合**，不是 OKX demo 账户下单；`OKXDemoBroker` / `x-simulated-trading=1` 仍属后续方向。
> - 内置节点当前没有 `OrderBook`、`FundingRate`、`Notifier`；已实现的是 CandleFeed、EMA/ATR/RSI、CrossSignal/ScenarioGate、RiskGate/KillSwitch、PositionSizer、ExecuteOrder、LLMAnalyst、Committee、RAG/Experience/Reflector 等。
> - 反事实分叉的底座（节点 I/O 录制、确定性回放）已具备，但交互式分叉 UI/CLI PnL 归因报告尚未实现，列入 future work。
> - 沙箱已有 AST 白名单、range 上限、风险盖章防伪和受限 ctx；尚未提供 cgroup 级 CPU/内存/墙钟限制。

## 0. 定位

**AlphaLoom：Agent-Native 量化交易平台。** *The graph IS the agent.*

策略不是代码文件，而是一张可视化蓝图（虚幻引擎 Blueprint 式节点图）；蓝图不是配置，而是可执行的 Agent 定义。AI 不只是帮你写策略代码（QuantDinger 的模式），而是与你在同一张画布上共同设计、调试、进化交易系统。

面试叙事定位：展示项目 #2（#1 为 Hindsight）。覆盖 JD 空位：**agent 工具调用、ReAct、RAG、算法设计、agent 系统架构**。与 Hindsight 共享 DNA：evaluation-driven、可观测、诚实披露。

**对标 QuantDinger**（用户本地部署过，已完成竞品调研）：其空位 = 我们的主攻——①无可视化工作流编辑器（策略纯 Python）②Agent 只读+回测、无自主闭环 ③无多智能体推理 ④决策无溯源 ⑤评估无诚实度设计。**刻意不对标的部分**（README 明说取舍）：10+ 交易所、多用户、计费、移动端；当前代码以单交易所 OKX 公开行情 + 本地纸上撮合打穿演示链路，保留 DataSource 抽象证明可扩展；OKX demo 账户下单未实现。

**对 LangChain/LangGraph/Langflow 的差异化（"诚实编译器"品牌，v3 新增）**——诚实前提：可视化编排、子图/循环/checkpoint、断点/time-travel 在 LangGraph Studio 等产品里已存在，单靠画布无差异化。结构性差异在于：**通用框架的图跑在"对话时间"里（无地面真值），AlphaLoom 的图跑在"市场时间"里（每个决策被真实价格宣判）**。由此派生四个它们结构上做不了的创新：
1. **因果类型系统**：数据引脚携带 as-of 时间戳，编译器+运行时证明图不可能读到未来数据——look-ahead bias 成为类型层面不可表达的错误（Hindsight 时间闸门从"测试性质"升级为"编译性质"）。
2. **静态成本证书**：编译器图遍历输出每 bar 最坏 LLM 调用数、每日 token 成本上限、最坏决策延迟、路径确定性分类；Studio 连线时实时更新。没有任何 agent 框架做编译期成本分析。
3. **反事实分叉**：全程录制+确定性世界 ⇒ 分叉任意历史 bar、改写任意节点输出、平行世界重放、PnL 差值归因——"决策错还是运气差"从哲学问题变成可计算问题。
4. **蓝图即可证伪对象**：每张图自带记分卡（当前覆盖样本内/验证窗/保真度衰减/成本证书；反事实稳健性为后续补全项），gallery 按证据排序而非感觉。

面试弹药："LangGraph 编排对话，AlphaLoom 编译可证伪的交易生命体——不靠承诺靠编译器证明：不可能偷看未来、不可能绕过风控、成本有上限、每个决策可反事实审计。"为什么不用 LangGraph：它的 checkpoint 为对话状态设计，无 bar 同步市场时间语义、无确定性重放保证，故自研小而专运行时（目标 <2k 行），此选型本身即谈资。

## 1. 已确认约束（用户决策，2026-07-05）

| 决策项 | 结论 |
|---|---|
| 项目名 | **AlphaLoom**（织机——把节点织成策略之网） |
| 工期 | **4 天核心 + D5 弹性**（MCP/消融/A·B 对比视进度与面试日期定） |
| LLM | 讯飞 Spark，复用 Hindsight client + 录制回放层；`ALPHALOOM_OFFLINE=1` 零配额全站演示 |
| 市场 | 当前实现：OKX 公开行情 / 本地 SQLite 演示数据 + `PaperBroker` 纸上撮合；OKX demo 盘下单延期 |
| 蓝图范围 | **激进版**：子图、循环/状态、断点单步、自定义节点 SDK + Text-to-Node、进化实验室（用户明确要求"再激进一点"） |
| 差异化创新 | **"诚实编译器"四件套设计上采纳**（用户 2026-07-05 确认）：①因果类型系统 ②静态成本证书 ③反事实分叉 ④蓝图记分卡；当前实现状态见本文顶部校准与 `docs/future-work.md` |
| 仓库 | 全新公开 monorepo，MIT |

## 2. 系统架构

技术栈：Python 3.13 + FastAPI + WS；React 18 + Vite + Tailwind + **React Flow (@xyflow/react)** + lightweight-charts；SQLite（自包含演示，不引 Postgres）。

```
alphaloom/
├── backend/
│   ├── alphaloom/
│   │   ├── graph/       # 蓝图核心：schema、类型系统、校验器、编译器（子图展开+环检查）
│   │   ├── runtime/     # 事件驱动执行引擎：时钟抽象、节点调度、状态、断点、全程录制
│   │   ├── nodes/       # 节点注册表 + SDK（@node 装饰器）+ 内置节点六大类
│   │   ├── sandbox/     # Text-to-Node 沙箱：AST 白名单 + 受限执行环境
│   │   ├── brokers/     # 当前：PaperBroker；延期：OKXDemoBroker（x-simulated-trading=1）
│   │   ├── backtest/    # okx_algorithnm 引擎移植 + 四档成交模型（L0-L3）
│   │   ├── data/        # DataSource 抽象 + OKX 公共行情 + sample.sqlite 读取
│   │   ├── copilot/     # Text-to-Blueprint / Explain / Optimize（图 diff 生成）
│   │   ├── evolve/      # 进化实验室：LLM 变异算子、适应度评估、谱系树
│   │   ├── memory/      # 经验库：Reflector 写入 + 按市场状态桶检索
│   │   ├── knowledge/   # RAG：自撰策略知识库 + 检索 + 引用追踪
│   │   ├── eval/        # 保真度阶梯、基线排行榜、泛化差距、记忆开关对比
│   │   ├── llm/         # Spark client + 录制回放（Hindsight 移植）
│   │   └── api/         # FastAPI + WS 流、SPA fallback
│   └── tests/           # pytest 目标 150+
├── frontend/            # 四区：Studio / Terminal / Runs&Eval / Copilot 侧栏
├── data/                # sample.sqlite（2-3 合约数月 1m K 线 + tick/L2 样本窗口）
├── blueprints/          # 预置蓝图 .loom 文件（gallery 内容）
└── docs/                # architecture / evaluation-methodology / demo-script / future-work
```

## 3. 蓝图系统（旗舰）

### 3.1 图模型
- **双流设计**（Unreal 风格）：执行流（触发顺序）+ 数据流（类型化引脚，按类型着色）。
- **类型系统即合规官**：引脚强类型；`ExecuteOrder` 的信号输入只接受 `RiskStampedSignal` 类型，该类型**只有** `RiskGate` 节点能产出——未过风控的图编译失败。安全由编译器强制，不靠约定。
- **因果类型系统（创新①）**：所有市场数据引脚携带 as-of 时间戳；运行时守卫拒绝任何 `t > 当前bar` 的读取，编译器对窗口类操作做静态越界检查；泄漏测试从"验证行为"升级为"验证类型系统本身"。
- **静态成本证书（创新②）**：每个节点声明成本注解（LLM 调用数/token 上限/延迟档/确定性类别），编译器聚合输出全图成本证书；Studio 编辑时实时显示"这张图每天最多烧多少 token、最坏延迟多少、哪些路径可离线回放"。
- **子图**：graph-in-node，可折叠/复用/嵌套；编译器递归展开；预置蓝图即子图库。
- **循环与状态**：事件驱动语义（bar/order/timer 事件触发执行波）天然支持反馈环；状态机节点持有跨 bar 状态；编译器区分合法反馈环与非法瞬时环（同一事件波内的环报错）。
- **序列化**：`.loom` JSON 文件，export/import + 内置 gallery。

### 3.2 内置节点面板（六大类，v1 共 19 种）
| 类别 | 节点 | 来源 |
|---|---|---|
| 数据 | 当前：CandleFeed（三模式同源）；目标：OrderBook、FundingRate | okx_algorithnm 数据层 |
| 指标 | EMA/ATR/RSI、SemanticDigest（九维语义摘要）、PAFeatures | PA_Agent、trade-system |
| 决策 | LLMAnalyst（人格+提示词可编辑）、Committee（扇出+表决）、PADecisionTree（确定性门控）、ScenarioGate（状态机） | PA_Agent 决策节点引擎、Trade-Tools 状态机 |
| RAG/记忆 | KnowledgeRetrieve、ExperienceRetrieve、ExperienceWrite | trade-system 经验模式 |
| 风控/执行 | 当前：RiskGate、PositionSizer、ExecuteOrder（PaperBroker）、KillSwitch；目标：OKX demo 执行 | Trade-Tools 四层闸门、trade-system risk_control |
| 反思 | 当前：Reflector（过程/结局分离打分→经验库）；目标：Notifier | Hindsight 分类学 |

### 3.3 调试与观测
- **断点单步**：节点断点，暂停时检查所有引脚实时值，单步/继续（UE 蓝图同款体验）。
- **时间旅行**：运行时全程录制每节点每事件的 I/O，时间轴拖动回看任意 bar 的全图状态。
- **反事实分叉（创新③，未完成）**：目标是在时间旅行视图中分叉任意历史 bar：改写指定节点输出（如"风控官没拦""LLM 说做空"）→ 平行世界确定性重放 → PnL 差值归因报告。当前已具备节点 I/O 录制与确定性回放底座，运行时分叉、CLI 报告和 UI 右键交互仍在 `docs/future-work.md`。
- **运行时可视化**：活跃节点发光、执行流沿线流光、数据流脉冲、拦截变红（Hindsight RunFlow 模式升级）。

### 3.4 自定义节点 SDK + Text-to-Node
- SDK：`@node(inputs=..., outputs=..., category=...)` 装饰器定义新节点，自动注册进面板。
- Text-to-Node：Copilot 生成节点代码 → **AST 白名单沙箱**（禁 import 白名单外模块/禁 IO/禁 exec，PA_Agent 校验栈经验）→ 校验通过热注册。降级保险丝：受限模板版（LLM 只填模板参数）。

## 4. 三种时间模式（同一张图零改动）

执行引擎只抽象三件事：**时钟、数据源、券商**。
1. **回测**：历史快进，okx_algorithnm 引擎成交撮合；LLM 节点走录制回放或真调（可选）。
2. **加速回放**：历史流按 10-60 倍速推进，走完整 live 管线，LLM 真实决策——现场演示"实盘循环"的答案。
3. **实时纸上交易（当前实现）/ OKX demo 执行（后续目标）**：当前版本是轮询 OKX 公共行情 + 本地 `PaperBroker` 纸上撮合，永不碰真实资金；demo 盘下单（x-simulated-trading=1）仍是后续方向。Trade-Tools 四层安全思想已内化为节点链 + KillSwitch。

## 5. Agent 架构（面试主叙事）

**叙事主次（v4 明确，用户要求以此证明 Agent 设计能力）**：Agent 是主角，编译器是配角——编译器+回测+记分卡构成 Agent 的**可验证反馈环境**（coding agent 成功配方移植到交易域）；编译器报错为 LLM 消费设计（结构化 JSON + 修复提示，双受众：人+Agent）。demo-script 与 README 均以 Agent 故事开场，编译器作为支撑出场。

### 5.1 图上层：Copilot 元 Agent（旗舰证明，贯穿全站侧栏）
完整 ReAct Agent，工具面即平台 API：`compile_graph` / `run_backtest` / `read_scorecard` / `mutate_graph` / `register_node`（Text-to-Node）。能力：
- **Text-to-Blueprint**："搭一个 ETH 突破策略，加移动止损，回撤别超 10%" → 图 JSON（schema 校验+dagre 自动布局）→ **diff 预览** → 应用 → 一键回测。
- **编译错误自修复（杀手演示时刻）**：Copilot 生成的图被编译器打回（因果/风控类型错误）→ 读结构化报错 → 自行修图 → 重新提交，全程 ReAct 轨迹可视化。血统：PA_Agent validation_retry（schema 校验、结构化反馈重试、防作弊字段）。
- **Explain**：选中图/子图 → 自然语言解释。
- **Optimize**：读回测报告 → 图变异建议（diff 形式）→ 进化实验室的变异算子同源。
- 所有生成物过编译器+风控类型检查，Copilot 也造不出危险策略。

### 5.2 图内层：交易 Agent 架构模式
- **委员会**：角色分工（策略师/风控官/主席）+ 结构化 JSON 交接 + 否决权，非聊天记录堆叠。
- **确定性-LLM 混合决策**：PADecisionTree 数值门控（不信 LLM 嘴）+ LLMAnalyst 语义判断，各司其职。
- **强制引用 RAG**：决策 JSON 必须携带 KnowledgeRetrieve 命中的引用字段，前端徽章可视化。
- **反思闭环**：过程/结局分离打分 → 经验库 → 回灌（§7）。
- **可测量记忆**：开/关对比，诚实回答"记忆有没有用"。

### 5.3 面试能力映射表
| JD 考察点 | AlphaLoom 证据 |
|---|---|
| 工具设计 | Copilot 工具面（为 Agent 设计工具；报错即提示词） |
| ReAct | Copilot 循环全程可视化 + 自修复轨迹 |
| 多智能体 | 委员会节点（分工/交接/否决） |
| RAG | 引用强制 + 检索徽章 |
| 记忆 | 反思闭环 + 开关消融 |
| 评估设计 | 记分卡/基线排行榜/保真度阶梯/泛化差距 |
| Agent 安全 | 环境层类型约束（编译器只占此一格） |

## 6. 进化实验室（"Agent 即研究员"的终极形态）

Agent 读回测报告 → 提出蓝图**变异**（参数/节点/连线，图 diff）→ 变异版自动过编译（风控类型系统守门）→ 回测评估适应度 → 优者存活进入下代 → **谱系树可视化**（哪张图从哪来、性能怎么变）。本质：遗传算法，变异算子是 LLM。规模控制：种群 ≤4、代数 ≤3、适应度评估用样本内窗口 + 终选做验证窗打分（防过拟合基础卫生，泄漏测试保障）。降级保险丝：只做参数变异不动图结构。

## 7. 反思闭环与评估

- **反思**：每笔平仓 → Reflector 过程/结局分离打分（reasonable_but_wrong 分类学，Hindsight 跨项目移植）→ 经验库（市场状态桶索引）→ ExperienceRetrieve 注入未来决策。记忆开/关一键切换，Dashboard 展示对比（跨项目叙事：Hindsight 测得记忆使 Brier 变差，交易域复测）。
- **保真度阶梯（回测测谎仪）**：终选策略在 L0 天真 OHLC / L1 盘中路径代理 / L2 tick-touch / L3 订单簿排队 四档成交模型下重放，量化"回测乐观偏差"。零 LLM 配额。
- **基线排行榜**：蓝图 vs buy-and-hold vs 默认参数网格 vs 随机参数，同表对比 + 样本内-验证窗泛化差距。
- **蓝图记分卡（创新④）**：每张保存的蓝图自动附记分卡——当前实现覆盖样本内/验证窗表现、保真度阶梯衰减、成本证书与消融/进化证据；反事实稳健性摘要需等反事实分叉落地后补入。gallery 按证据排序。
- 委员会消融 → D5/future-work。

## 8. 前端（四区，产品级外观）

深空霓虹设计系统（Hindsight 移植），主色电光蓝+熔金，双语 i18n，reduced-motion，零外部资源：
1. **Blueprint Studio**：React Flow 画布、节点面板、子图导航、断点调试条、运行时流光、gallery。
2. **Terminal**：K 线+成交标记（lightweight-charts）、持仓、权益曲线、交易日志（JSONL ledger）。
3. **Runs & Eval**：回测报告、保真度阶梯图、排行榜、记忆对比、进化谱系树。
4. **Copilot 侧栏**：全站常驻。
README：英文主+中文版、banner、GIF（画布连线→一键回测→进化树）、架构图、诚实 limitations。

## 9. 复用与移植清单（含法务卫生）

| 来源 | 拿什么 | 处理 |
|---|---|---|
| okx_algorithnm | 回测引擎+tick/L2 replay+数据管线 | 唯一作者，直接并入（MIT），补测试 |
| PA_Agent | 决策树门控引擎（PADecisionTree 节点）、校验栈、指标+property 测试 | 持有版权可再授权；不碰 workbuddy 连接器；语料重新蒸馏 |
| trade-system | 九维语义分类器、risk_control、OKXDemoTrader 执行经验、React 图表参考 | 移植；清除 Telegram ID 等个人痕迹 |
| Trade-Tools | 四层安全闸门、场景状态机、JSONL ledger 模式 | 只移植设计不移植 PowerShell |
| Hindsight | Spark client+录制回放、泄漏测试、设计系统、文档模板、CI | 复制适配 |
| **不带走** | OKX 官方文档拷贝（版权）、诊断脚本、个人痕迹 | — |

## 10. 测试与工程

- pytest 150+：图编译器（类型/环/子图/风控盖章穷举）、引擎黄金测试（对照 okx_algorithnm runs/ 产物）、沙箱逃逸测试、泄漏测试、指标 property 测试、券商适配器契约测试。
- CI：测试 + 前端构建。安全红线：`.env` 绝不入库，收尾密钥 grep；当前 CI/默认演示不需要 OKX demo key。
- CLI utf-8 reconfigure（cp1252 陷阱）。

## 11. 施工计划概要（详细拆分见 writing-plans）

- **D1**：图 schema/类型系统（风控盖章 + **as-of 因果类型①**）/编译器（子图+环+**成本证书②**）+ 事件驱动引擎（状态/断点/全程录制）+ 回测模式 + PaperBroker + 预置蓝图 + sample.sqlite。
- **D2**：Blueprint Studio 画布（面板/子图导航/断点 UI/流光/**成本证书面板②**）+ Terminal 页。
- **D3**：LLM 节点/委员会 + Text-to-Blueprint + Text-to-Node 沙箱 + 反思闭环 + 加速回放 + 录制层。
- **D4**：进化实验室+谱系树 + 评估模块（含**记分卡④**）+ 实时纸上交易 + 文档/README/发布；**反事实分叉③** 与 OKX demo 账户执行延期。
- **D5（弹性）**：MCP server（外部 Agent 操控平台，对标并超越 QuantDinger 只读 MCP）、委员会消融、A/B 蓝图对比。

每天收尾有可演示增量；降级保险丝：断点→只读检查；Text-to-Node→模板版；进化→仅参数变异。核心承诺不动摇：画布 + 三时间模式 + Copilot + 反思闭环。

## 12. 风险

1. **范围最大的一次**——依赖子智能体双审流程（Hindsight 执行约定沿用）+ 每日降级保险丝。
2. **React Flow 学习曲线**——D2 若溢出，砍子图导航动画保核心画布。
3. **配额**——进化实验室与委员会是消耗大户；全程录制、429 耐心退避；演示路径全部离线回放。
4. **OKX demo key（历史目标）**——最初计划接 OKX demo 盘；当前代码路径不读取该 key，Live Desk 默认是 OKX 公开行情 + `PaperBroker` 纸上撮合。
5. **实验结果不好看**——如实发布，Hindsight 诚实传统。
