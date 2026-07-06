<div align="center">

<img src="docs/assets/alphaloom-logo-primary.png" alt="AlphaLoom" width="300">

<p>
  <strong>English</strong> |
  <a href="README.zh-CN.md">中文</a>
</p>

# AlphaLoom

**The graph IS the agent.**

Agent-native quant trading: a `.loom` visual blueprint compiles into a typed, auditable, replayable trading agent.

[![CI](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml)
&nbsp;![backend](https://img.shields.io/badge/backend-pytest-2ea043?style=flat-square)
&nbsp;![frontend](https://img.shields.io/badge/frontend-tsc--strict_+_vitest-3178c6?style=flat-square)
&nbsp;![offline](https://img.shields.io/badge/demo-offline_zero--quota-f59e0b?style=flat-square)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

<br>

<img src="docs/assets/architecture-loop.gif" alt="AlphaLoom animated compile-gated agent loop" width="100%">

**Draw a graph. Compile the guardrails. Replay every decision. Score it on real market data.**

</div>

## One Click Demo

```bat
START_ALPHALOOM.cmd
```

Opens `http://127.0.0.1:8000/#/studio`, builds what is missing, runs offline replay, and spends zero LLM quota.

## Blueprint Features

<img src="docs/assets/feature-type-gate.svg" alt="Typed risk gate diagram" width="100%"><br>
<strong>Risk is a type contract.</strong><br>
<code>ExecuteOrder</code> only accepts a <code>risk_stamped_signal</code>. Raw LLM output cannot trade.

<img src="docs/assets/feature-cost-cert.svg" alt="Static cost certificate diagram" width="100%"><br>
<strong>Cost is known before runtime.</strong><br>
The compiler emits LLM calls per bar, token ceiling, latency class, and deterministic ratio.

<img src="docs/assets/feature-replay-loop.svg" alt="Offline replay loop diagram" width="100%"><br>
<strong>Demos are deterministic.</strong><br>
Recorded LLM calls replay from hashed requests: same prompts, same responses, zero network calls.

<img src="docs/assets/feature-eval-lab.svg" alt="Falsifiable evaluation stack diagram" width="100%"><br>
<strong>Evaluation is falsifiable.</strong><br>
Real candles, fidelity ladder, baselines, risk sensitivity, and evolution genealogy judge the graph.

## Real Market Smoke Test

Same blueprint, same type gate, public OKX candles:

| Item | Value |
|---|---|
| Blueprint | [`blueprints/real_sol_breakout_demo.loom`](blueprints/real_sol_breakout_demo.loom) |
| Market data | OKX public `SOL-USDT-SWAP` 1m candles |
| Window | 2026-06-25 04:12Z to 2026-06-26 04:12Z |
| Result | **+9.4646% return**, **2.7693% max drawdown** |
| Trades | 29 trades, 68.97% win rate, profit factor 3.0025 |
| Buy and hold | +0.4761% return, 7.6801% max drawdown |
| Fidelity L3 | +148.6884 net PnL after the harshest fill model |

This is a smoke test, not an alpha claim. Reproduction notes live in [`docs/real-data-smoke-test.md`](docs/real-data-smoke-test.md).

## Visual Proof

<strong>Preset Blueprint Studio.</strong> The first image is a clean render of the committed `agent_committee_v1` preset blueprint, including all 13 nodes and 19 typed edges.

<img src="docs/screenshots/studio.png" alt="Blueprint Studio with typed graph and cost certificate" width="100%">

<strong>Realtime Offline Player.</strong> Generated from the same real OKX SOL replay: progress, equity, and fill events advance from recorded runtime data.

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

## 60 Second Demo Path

| Step | Show | Point |
|---|---|---|
| 1 | Studio graph | The agent is the blueprint, not hidden prompt glue. |
| 2 | `risk_gate -> execute_order` | Risk control is enforced by the compiler. |
| 3 | Cost certificate | LLM cost and determinism are visible before the run. |
| 4 | Offline player | Fills, equity, citations, and traces are inspectable offline in a realtime-style replay. |
| 5 | Eval Lab | Backtest results face baselines and harsher fill models. |

<details>
<summary><strong>Manual startup</strong></summary>

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
<summary><strong>System map</strong></summary>

| Layer | What it does |
|---|---|
| Blueprint compiler | `.loom` JSON to typed graph, topological plan, and cost certificate. |
| Event runtime | Wave execution, deterministic replay, breakpoints, full node I/O recording. |
| Backtest engine | Next-bar-open fills, attached stops, EOD settlement, no look-ahead reads. |
| Agent nodes | `LLMAnalyst`, `Committee`, deterministic gates, BM25 RAG, citation checks, reflector, memory. |
| Copilot | Natural language to blueprint, compile-error self-repair, explain, optimize. |
| Sandbox | AST-whitelisted custom nodes with no LLM handle and no ability to forge the risk stamp. |
| Eval Lab | Fidelity ladder, scorecard, leaderboard, risk sensitivity, committee ablation, evolution genealogy. |

</details>

<details>
<summary><strong>Offline LLM recordings</strong></summary>

`ALPHALOOM_OFFLINE=1` replays committed recordings from `data/llm_calls.sqlite`.

- 835 deterministic seed responses for a rich zero-quota demo.
- 123 real iFlytek Spark `astron-code-latest` calls from a recorded `agent_committee` run.
- Live mode uses `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` from `.env`.

</details>

## Docs

- [`docs/demo-script.md`](docs/demo-script.md) - 10-minute talk track.
- [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) - scoring caveats and trust boundaries.
- [`docs/real-data-smoke-test.md`](docs/real-data-smoke-test.md) - exact data window and reproduction notes.
- [`docs/future-work.md`](docs/future-work.md) - roadmap and known limits.

<div align="center">

**MIT (c) 2026 Zhao Chenghao**

</div>
