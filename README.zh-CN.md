<div align="center">

<img src="docs/assets/alphaloom-logo-primary.png" alt="AlphaLoom" width="300">

<p>
  语言：
  <a href="README.md">English</a>
  ·
  <strong>中文</strong>
</p>

# AlphaLoom

**The graph IS the agent.**

一个 Agent-native 交易工作台：`.loom` 可视化蓝图会被编译成带类型约束、成本证书、可回放、可证伪评估的交易 Agent。

[![CI](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml)
&nbsp;![backend](https://img.shields.io/badge/backend-pytest-2ea043?style=flat-square)
&nbsp;![frontend](https://img.shields.io/badge/frontend-tsc--strict_+_vitest-3178c6?style=flat-square)
&nbsp;![offline](https://img.shields.io/badge/demo-offline_zero--quota-f59e0b?style=flat-square)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

<br>

<img src="docs/assets/zh/zh-readme-overview.png" alt="AlphaLoom 中文总览：蓝图、编译器、风控、运行时、离线回放和评估实验室" width="100%">

**画出门控图。编译合约。回放每根 K 线。用市场证据评价 Agent。**

</div>

## 这是什么

AlphaLoom 是为 AI Agent Engineer 展示设计的完整交易系统 demo：它把策略表达成可以编辑的 Agent 蓝图，而不是藏在 prompt 里的胶水逻辑。蓝图里可以随时添加或替换确定性门控、LLM 委员会节点、RAG/引用检查、反思记忆、仓位 sizing 和执行风控。编译器会在运行前验证合法下单路径。

这不是投资建议，也不是 alpha 收益承诺；它展示的是一个可审计、可回放、可评估的 Agent 交易架构。

## 一键启动

双击：

```bat
START_ALPHALOOM.cmd
```

启动器会自动补齐缺失依赖、构建前端、准备确定性 demo 数据库、启动后端，并打开：

```text
http://127.0.0.1:8000/?alphaloom=...#/studio
```

默认是 **离线模式**：录制好的 LLM 调用、录制好的市场数据、零网络请求、零 LLM quota。

## 运行模式

顶栏可以随时切换三种模式：

| 模式 | 适合做什么 | 需要什么 |
|---|---|---|
| 离线 | 安全演示、确定性回放、已提交的讯飞/seed 录制 | 不需要配置 |
| 实时 | 在 UI 里调用真实 OpenAI-compatible LLM | `.env` 填好 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` |
| 无 LLM | 纯确定性蓝图、编译检查、市场回测 | 不需要配置 |

实时模式配置：

```env
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=astron-code-latest
```

重启 `START_ALPHALOOM.cmd` 后，在右上角把 `离线 -> 实时` 即可。实时模式会消耗真实 quota。

## 产品界面

| 界面 | 展示什么 | 为什么重要 |
|---|---|---|
| 蓝图工坊 | 可编辑 `.loom` 蓝图、类型边、成本证书、Copilot diff | Agent 就是图；你可以替换门控或添加新的 LLM 组件。 |
| Live Desk | PA_Agent 风格纸面实时台：左侧蓝图，中间 K 线，右侧诊断/门控/反思 | 图会按 bar 运行，演示时像一个真实交易台。 |
| 交易终端 | Run 选择器、trace explorer、节点 I/O、委员会/反思证据 | 每个决策都能回放和审计。 |
| 评估实验室 | 保真度阶梯、记分卡、基线排行榜、消融、进化谱系 | 结果要面对市场证据，而不是只靠好听的解释。 |

## 蓝图为什么不一样

<img src="docs/assets/zh/zh-feature-risk-gate.png" alt="类型风控：裸信号不能下单" width="100%"><br>
<strong>风控是类型约束。</strong><br>
<code>ExecuteOrder</code> 只接收 <code>risk_stamped_signal</code>。裸 LLM 信号不能直接下单。

<img src="docs/assets/zh/zh-feature-cost-cert.png" alt="成本证书：运行前先知道成本" width="100%"><br>
<strong>运行前就知道成本。</strong><br>
编译器会输出每根 bar 的 LLM 调用数、token 上限、延迟等级、确定性比例和合法执行路径。

<img src="docs/assets/zh/zh-feature-offline-replay.png" alt="离线回放：零网络、零配额" width="100%"><br>
<strong>演示可以完全离线。</strong><br>
LLM 请求被 canonicalize 后哈希记录；离线模式复用同一批响应，零网络、零 quota。

<img src="docs/assets/zh/zh-feature-eval-lab.png" alt="可证伪评估：真实数据面对基线" width="100%"><br>
<strong>评估是可证伪的。</strong><br>
真实 K 线、成交保真度阶梯、基线、风险敏感性、消融和进化谱系一起判断蓝图。

## 回测和回放怎么做

在蓝图工坊或 Live Desk 里启动回测时，需要选择：

| 控件 | 含义 |
|---|---|
| Blueprint | 要运行的 `.loom` 编译图 |
| Market / bar | 本地行情目录里的交易对和 K 线周期 |
| Start / end | 精确回放窗口；图表只展示这个时间范围 |
| Cash / fee | 初始资金和手续费假设 |
| Speed | `1x`、`4x` 或 instant 回放 |

运行时，图表游标会按顺序揭示 K 线、成交、权益曲线和活跃节点。回测引擎使用 next-bar-open 成交、附加止损、EOD 结算，并禁止 look-ahead；评估实验室还能把同一批成交放到更严格的保真度模型下重放。

## Copilot 的作用

Copilot 可以根据自然语言生成、解释、优化和修复蓝图，但它不能绕过编译器。所有建议都会先以 diff 展示，然后必须通过类型图编译，才允许进入回测或执行路径。

所以 LLM 是策略作者和修复助手，真正守住执行边界的是门控图和编译器。

## 真实市场 Smoke Test

同一张蓝图、同一条类型风控路径，跑 OKX 公开历史数据：

| 项目 | 数值 |
|---|---|
| 蓝图 | [`blueprints/real_sol_breakout_demo.loom`](blueprints/real_sol_breakout_demo.loom) |
| 行情 | OKX public `SOL-USDT-SWAP` 1m candles |
| 窗口 | 2026-06-25 04:12Z 到 2026-06-26 04:12Z |
| 结果 | **+9.4646% return**, **2.7693% max drawdown** |
| 交易 | 29 trades, 68.97% win rate, profit factor 3.0025 |
| Buy and hold | +0.4761% return, 7.6801% max drawdown |
| Fidelity L3 | 最严格成交模型后仍有 +148.6884 net PnL |

这不是 alpha claim，只是一个真实数据 smoke test：证明系统能在真实历史行情上跑完整的编译、执行、回放和评估链路。复现说明见 [`docs/real-data-smoke-test.md`](docs/real-data-smoke-test.md)。

## 反思消融实验

这组 paired smoke ablation 只切换反思闭环，门控执行路径保持一致。它不是泛化收益承诺，而是在问一个更工程的问题：反思闭环是否真的改变交易行为，而不是只多写一段漂亮解释？

| 版本 | 交易 | 回报 | 胜率 | 最大回撤 | 盈利因子 |
|---|---:|---:|---:|---:|---:|
| 闭环学习版，含反思 | 9 笔 | **+0.90%** | **66.7%** | **4.65%** | **1.36** |
| 去反思版，消融 | 5 笔 | **-5.64%** | **0.0%** | 6.78% | 0.0 |

在这组对照里，去掉反思后 5 笔全亏；保留反思后变成小幅正收益。这个结果很适合展示 AlphaLoom 的核心：反思不是 prompt 装饰，而是可以被消融、被回放、被评估的系统组件。

## 视觉证据

<strong>预设蓝图 Studio。</strong> 第一张图把提交到仓库里的 `agent_committee_v1` 表达为两阶段门控协议：先诊断，弱机会短路，订单校验后通过 RiskGate 盖章，再执行或回放。

<img src="docs/screenshots/studio-zh.png" alt="两阶段门控协议蓝图：诊断、短路、订单校验、RiskGate 盖章、执行与反思" width="100%">

<strong>实时离线 Player。</strong> 这张 GIF 由同一段真实 OKX SOL 回放数据生成，进度、权益曲线和成交事件都会随时间推进。

<img src="docs/assets/offline-player.gif" alt="Realtime offline replay player for real OKX SOL smoke test" width="100%">

<table>
<tr>
<td width="50%"><img src="docs/screenshots/scorecard.png" alt="Real-data smoke scorecard"></td>
<td width="50%"><img src="docs/screenshots/leaderboard.png" alt="Baseline leaderboard"></td>
</tr>
</table>

<img src="docs/screenshots/fidelity.png" alt="Fidelity ladder" width="100%">

<table>
<tr>
<td width="50%"><img src="docs/screenshots/ablation.png" alt="Risk budget sensitivity"></td>
<td width="50%"><img src="docs/screenshots/genealogy.png" alt="Parameter evolution genealogy"></td>
</tr>
</table>

<img src="docs/screenshots/terminal.png" alt="Terminal trace for profitable real-data run" width="100%">

## 60 秒展示路线

| 步骤 | 展示什么 | 说明什么 |
|---|---|---|
| 1 | Studio 蓝图 | Agent 就是图，不是藏在 prompt 里的胶水代码。 |
| 2 | `risk_gate -> execute_order` | 风控由编译期类型系统保证。 |
| 3 | Live Desk | 选中的蓝图会对着 K 线运行，活跃门控同步可见。 |
| 4 | Terminal 回放 | 成交、权益曲线、引用、节点 I/O 和反思都能追溯。 |
| 5 | Eval Lab | 结果要面对基线、消融和更严格成交模型。 |

<details>
<summary><strong>手动启动</strong></summary>

```powershell
# backend
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[dev]

# frontend
cd ..\frontend
npm install
npm run build

# offline server
cd ..
$env:ALPHALOOM_OFFLINE = "1"
backend\.venv\Scripts\python.exe -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
```

</details>

<details>
<summary><strong>系统模块</strong></summary>

| 层 | 作用 |
|---|---|
| Blueprint compiler | `.loom` JSON 编译成 typed graph、topological plan、cost certificate 和合法下单路径。 |
| Event runtime | wave-based 执行、确定性回放、断点、WebSocket 进度、完整 node I/O 记录。 |
| Backtest engine | next-bar-open 成交、止损、EOD 结算、禁止 look-ahead。 |
| Agent nodes | `LLMAnalyst`、`Committee`、确定性 gate、BM25 RAG、citation check、reflector、memory。 |
| Copilot | 自然语言生成蓝图、编译错误自修复、解释、优化、应用并回测。 |
| Sandbox | AST 白名单自定义节点，拿不到 LLM handle，也不能伪造 risk stamp。 |
| Eval Lab | fidelity ladder、scorecard、leaderboard、risk sensitivity、committee ablation、evolution genealogy。 |

</details>

<details>
<summary><strong>离线 LLM 录制</strong></summary>

`ALPHALOOM_OFFLINE=1` 会回放 `data/llm_calls.sqlite` 里提交的记录：

- 835 条 deterministic seed response，用于丰富的零 quota demo。
- 123 条真实 iFlytek Spark `astron-code-latest` 调用记录。
- live 模式通过 `.env` 里的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 启用。

</details>

## 文档

- [`docs/demo-script.md`](docs/demo-script.md) - 10 分钟展示讲稿。
- [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) - 评分方法、可信边界和 caveat。
- [`docs/real-data-smoke-test.md`](docs/real-data-smoke-test.md) - 真实 OKX 数据窗口与复现说明。
- [`docs/future-work.md`](docs/future-work.md) - 已知边界和路线图。

<div align="center">

**MIT (c) 2026 Zhao Chenghao**

</div>
