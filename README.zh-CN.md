<div align="center">

<img src="docs/assets/alphaloom-logo-primary.png" alt="AlphaLoom" width="300">

<p>
  <a href="README.md">English</a> |
  <strong>中文</strong>
</p>

# AlphaLoom

**The graph IS the agent.**

一个 Agent-native 量化交易展示系统：`.loom` 可视化蓝图会被编译成带类型约束、可审计、可回放、可评估的交易 Agent。

[![CI](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml)
&nbsp;![backend](https://img.shields.io/badge/backend-pytest-2ea043?style=flat-square)
&nbsp;![frontend](https://img.shields.io/badge/frontend-tsc--strict_+_vitest-3178c6?style=flat-square)
&nbsp;![offline](https://img.shields.io/badge/demo-offline_zero--quota-f59e0b?style=flat-square)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

<br>

<img src="docs/assets/architecture-loop.gif" alt="AlphaLoom animated compile-gated agent loop" width="100%">

**画出蓝图。编译护栏。回放每个决策。用真实行情验证结果。**

</div>

## 一键启动

```bat
START_ALPHALOOM.cmd
```

会自动补齐缺失依赖、构建前端、启动离线回放服务，并打开 `http://127.0.0.1:8000/#/studio`。默认不需要 API Key，不消耗 LLM quota。

## 蓝图为什么不一样

<img src="docs/assets/feature-type-gate.svg" alt="Typed risk gate diagram" width="100%"><br>
<strong>风控是类型约束。</strong><br>
<code>ExecuteOrder</code> 只接收 <code>risk_stamped_signal</code>。裸 LLM 信号不能直接下单。

<img src="docs/assets/feature-cost-cert.svg" alt="Static cost certificate diagram" width="100%"><br>
<strong>运行前就知道成本。</strong><br>
编译器会输出每根 bar 的 LLM 调用数、token 上限、延迟等级和确定性比例。

<img src="docs/assets/feature-replay-loop.svg" alt="Offline replay loop diagram" width="100%"><br>
<strong>演示可以完全离线。</strong><br>
LLM 请求被 canonicalize 后哈希记录；离线模式复用同一批响应，零网络、零 quota。

<img src="docs/assets/feature-eval-lab.svg" alt="Falsifiable evaluation stack diagram" width="100%"><br>
<strong>评估是可证伪的。</strong><br>
真实 K 线、保真度阶梯、基线排行榜、风险敏感性和进化谱系一起判断蓝图。

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

## 视觉证据

<strong>预设蓝图 Studio。</strong> 第一张图现在只展示提交到仓库里的 `agent_committee_v1` 预设蓝图，完整包含 13 个节点和 19 条类型边。

<img src="docs/screenshots/studio.png" alt="Blueprint Studio with typed graph and cost certificate" width="100%">

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
| 3 | Cost certificate | LLM 成本和确定性在运行前可见。 |
| 4 | Offline player | 成交、权益曲线、引用和 trace 都能用实时风格的离线回放检查。 |
| 5 | Eval Lab | 结果要面对基线、保真度阶梯和参数变体。 |

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
| Blueprint compiler | `.loom` JSON 编译成 typed graph、topological plan 和 cost certificate。 |
| Event runtime | wave-based 执行、确定性回放、断点、完整 node I/O 记录。 |
| Backtest engine | next-bar-open 成交、止损、EOD 结算、禁止 look-ahead。 |
| Agent nodes | `LLMAnalyst`、`Committee`、确定性 gate、BM25 RAG、citation check、reflector、memory。 |
| Copilot | 自然语言生成蓝图、编译错误自修复、解释和优化。 |
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
