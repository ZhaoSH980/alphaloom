# Crucible 设计文档（Design Spec）

日期：2026-07-05
状态：已获用户批准（brainstorming 环节完成）
作者：赵成浩 + Claude

## 0. 背景与目标

用户正冲刺 AI Agent Engineer 面试（JD 要点：transformer 基础、RAG、agent 工具调用、长文档 deep research、ReAct 架构；项目经验考察：算法设计、评估设计、agent 系统架构；有现场 coding/伪代码环节）。展示项目 #1 Hindsight（时光机评估深度研究 Agent，已公开发布）覆盖了 deep research、评估设计、可观测性。

**Crucible 是展示项目 #2**，覆盖 JD 剩余空位：**工具调用、ReAct、RAG、算法设计**。素材来自用户四个私有仓库的整合：PA_Agent（两阶段 LLM 编排+校验栈）、okx_algorithnm（OKX 回测引擎+tick/L2 回放）、trade-system（语义信号+多智能体委员会+自评估闭环）、Trade-Tools（分层安全闸门思想）。

**一句话定位**：An AI agent team that researches trading strategies — and an evaluation harness that keeps it honest.

**与 Hindsight 的品牌呼应**：Hindsight 考察"研究过去的能力"，Crucible 考察"在历史中锻造策略且不自欺的能力"。共享 DNA：evaluation-driven、falsifiable、诚实披露负面结果。

## 1. 已确认的约束（用户决策）

| 决策项 | 结论 |
|---|---|
| 时间预算 | 3-4 天 |
| LLM 提供商 | 讯飞 Spark（复用 Hindsight client + 录制回放层，429 耐心退避） |
| 市场范围 | OKX 加密币主市场；保留 DataSource 多市场抽象接口（演示只用 OKX） |
| 方案主干 | A「Agent 即量化研究员」+ 吸收 B 的确定性风控闸门 + C 的排行榜 |
| 标题级创新 | ①成交模型保真度阶梯 ②委员会消融实验 ③经验记忆回灌+跨项目验证 |
| 仓库形态 | 全新公开 monorepo，MIT 协议 |
| 离线演示 | 必须支持 `CRUCIBLE_OFFLINE=1` 零配额全站回放（面试断网保险） |

样本外托管（评估防火墙）**不做**标题创新，但 walk-forward 样本内/验证窗切分 + 阶段性数据访问封锁作为基础卫生保留。

## 2. 系统架构

技术栈：Python 3.13 + FastAPI + WebSocket 后端；React 18 + Vite + Tailwind 前端（与 Hindsight 完全一致，开发环境/测试习惯/部署脚本直接复用）。

```
crucible/
├── backend/
│   ├── crucible/
│   │   ├── agents/      # ReAct 循环、委员会（策略师/风控官/主席）、提示词
│   │   ├── tools/       # 工具注册表：market_data / indicators / knowledge_search
│   │   │                #   / run_backtest / risk_check（JSON schema 定义）
│   │   ├── backtest/    # okx_algorithnm 引擎移植 + 四档成交模型
│   │   │                #   L0 ohlc / L1 1m-proxy / L2 tick-touch / L3 L2-queue
│   │   ├── knowledge/   # RAG：自撰策略知识库 + 检索器 + 引用追踪
│   │   ├── memory/      # 经验库：自评估回灌写入器 + 按市场状态桶检索
│   │   ├── eval/        # walk-forward、消融 runner、保真度阶梯 runner、
│   │   │                #   指标计算、泄漏测试
│   │   ├── llm/         # Spark client + 录制回放层（Hindsight 移植）
│   │   └── api/         # FastAPI + WS 流式推送、SPA fallback
│   └── tests/           # pytest，目标 100+
├── frontend/            # 三页 SPA + 双语 i18n
├── data/                # sample.sqlite：2-3 合约数月 1m K 线 + 一段 tick/L2 窗口
└── docs/                # evaluation-methodology / design-decisions / demo-script
```

**核心数据流**：
mission（品种+周期+目标+约束）→ ReAct 循环【行情语义摘要 → 知识检索 → 策略配置提案（JSON schema 锁定）→ 确定性风控闸门 → 样本内回测 → 读指标 → 迭代 N 轮】→ 终选配置 → 评估工具链【验证窗打分 + 保真度阶梯 + 泛化差距报告】→ 前端展示 + 经验库回灌。

**防泄漏硬约束**：工具层按 mission 阶段封锁数据访问——Agent 迭代期物理上无法读取验证窗数据；专门的泄漏测试用例证明该性质（Hindsight 时间闸门模式移植）。

## 3. Agent 设计

- **ReAct**：显式 Thought/Action/Observation 循环，Spark 原生 function calling（Hindsight 已探针验证），每步事件进 WS 流供前端实时渲染。
- **工具调用**：5 个工具（market_data / indicators / knowledge_search / run_backtest / risk_check）全 JSON schema 定义；验证窗打分不是 Agent 工具而是评估工具链职权（防泄漏）；校验栈移植 PA_Agent 模式——schema 校验 → 截断诊断 → 带结构化反馈重试 → 不可变字段防作弊。
- **RAG**：知识库为自撰精要文档（网格机制、DCA/马丁风险、Al Brooks 价格行为核心概念，中英对照），不整包搬运原语料；检索命中必须以引用字段出现在决策 JSON 中；前端展示引用徽章。
- **委员会**：三角色——策略师（提案）、风控官（否决/收紧）、主席（合成终案）；角色间结构化 JSON 交接。
- **确定性护栏**："LLM proposes, code disposes"——风控闸门为纯函数模块（杠杆上限、参数合法域、最小资金、止损强制），单元测试穷举边界。

## 4. 评估设计（三个标题创新）

### 4.1 成交模型保真度阶梯（零 LLM 配额）
同一终选策略在四档成交模型下重放：L0 天真 OHLC → L1 盘中路径代理 → L2 tick 逐笔 touch → L3 订单簿排队。产出"乐观偏差报告"：各档 PF/最大回撤/收益差值，量化"回测在哪一档开始撒谎"。基于 okx_algorithnm 现成 replay 引擎，主要工作为统一接口 + 报告生成。

### 4.2 委员会消融（配额控制：3 臂 × 3 mission）
实验臂：完整委员会 / 去掉风控官 / 去掉 RAG。同样 3 个 mission，比较验证窗表现 + 风控官拦截的危险提案计数。全程录制供离线回放。预期卖点：无风控官臂样本内更好看、验证窗更差——护栏价值被量化。若结果相反，如实发布。

### 4.3 经验记忆回灌 + 跨项目验证（2 臂 × K≈4 mission 序列）
mission 结束后自评估器写入（市场状态桶、配置、验证结果、教训摘要）；后续 mission 按桶检索注入上下文。实验：记忆开/关两臂。跨项目叙事：Hindsight 测得记忆使 Brier 变差 +0.039，Crucible 在交易域复测——结果一致则"记忆污染是普遍现象"，相反则"记忆有效性依赖任务结构"，两个方向都是有价值的发现。

### 4.4 基础指标与基线
profit factor、最大回撤、R 倍数分布、样本内-验证窗泛化差距。Leaderboard 将 Agent 各配置与固定基线（buy-and-hold、默认参数网格、随机参数）同表对比——Agent 必须证明打得过无脑基线。

## 5. 数据与离线演示

- `data/sample.sqlite`：2-3 个合约数月 1m K 线 + 一段 tick/L2 窗口。优先检查用户本地是否已有 okx_algorithnm 建好的多 GB 库可直接抽样（省数小时限流下载）。
- LLM 调用全程录制；`CRUCIBLE_OFFLINE=1` 全站零配额回放。
- `demo.bat`（离线单进程）/ `dev.bat`（双窗口热更新）沿用 Hindsight 模式。

## 6. 复用与移植清单（含法务卫生）

| 来源 | 拿什么 | 处理 |
|---|---|---|
| okx_algorithnm | 回测引擎+tick/L2 replay+数据层整包 | 用户唯一作者，直接进新库（MIT），补测试 |
| PA_Agent | 校验栈模式、指标+hypothesis property 测试、经验库读写模式 | AGPL 但用户持有版权可再授权；**不碰** workbuddy 连接器；语料重新蒸馏 |
| trade-system | 9 维语义分类器、digest-bundle 聚合模式、风控闸门、React 图表组件参考 | 移植；Telegram ID 等个人痕迹不带 |
| Trade-Tools | 四层安全闸门思想、场景状态机思想 | 只移植设计不移植 PowerShell |
| Hindsight | Spark client+录制回放、泄漏测试模式、前端设计系统、文档模板、CI workflow | 复制适配 |
| **不带走** | OKX 官方文档 1.7MB 拷贝（版权）、诊断脚本、捐赠码/QQ 群等个人痕迹 | — |

## 7. 前端设计

沿用 Hindsight 深空科幻设计系统，主色调改琥珀/熔金（"锻造"主题），双语 i18n，reduced-motion 尊重，零外部资源：

1. **Mission Control**：发起 mission、实时 ReAct 轨迹流、委员会流转动画（RunFlow 模式复用）、工具调用时间线、RAG 引用徽章。
2. **Backtest Explorer**：K 线叠加成交点（lightweight-charts）、权益曲线、逐笔明细、MFE/MAE。
3. **Eval Dashboard**：保真度阶梯对比图、消融表、记忆开/关对比、Agent vs 基线排行榜。
4. **README 主页**：英文为主+中文版、banner、GIF、架构图、诚实 limitations 段。

## 8. 测试与工程

- pytest 目标 100+：引擎黄金测试（对照 okx_algorithnm 已提交 runs/ 产物）、指标 property 测试（hypothesis）、阶段闸门泄漏测试、风控闸门穷举、schema 校验栈测试。
- CI：GitHub Actions，测试 + 前端构建（Hindsight workflow 改造）。
- 安全红线：`.env`（讯飞 key）绝不入库；收尾跑密钥泄漏 grep 检查。
- CLI 输出 utf-8 reconfigure（cp1252 陷阱，Hindsight 经验）。

## 9. 四天施工计划概要（详细任务拆分见 writing-plans 产出）

- **D1**：仓库脚手架 + 回测引擎/数据层移植 + 内置样本库 + 保真度阶梯跑通出第一份报告。
- **D2**：工具层 + ReAct/委员会 + Spark 录制层 + 风控闸门 + RAG。
- **D3**：评估工具链（walk-forward/消融/记忆实验）+ 实验实跑录制 + API/WS。
- **D4**：前端三页 + 文档 + README + 发布 + 演示脚本。

## 10. 风险与对策

1. **消融+记忆实验配额消耗**：全部走录制层；429 耐心退避；实验规模锁定 3×3 与 2×4。
2. **移植量超预期**：okx_algorithnm 零测试，移植时边写测试边验；D1 排最重，若 D1 溢出则砍 L3 排队模型为 future work（保留 L0-L2 三档，阶梯叙事仍成立）。
3. **实验结果不好看**（Agent 打不过基线）：如实发布，叙事转为"评估协议成功暴露了 X"。
4. **执行流程**：沿用 Hindsight 执行约定——superpowers 技能链、子智能体双审、计划即权威、sanctioned deviation。
