<div align="center">

# 🧬 AlphaLoom

### The graph **IS** the agent.

An agent-native quant trading platform where a strategy is not a code file — it's a visual **blueprint** (an Unreal-Engine-style node graph) that *compiles* into an executable, falsifiable trading agent.

[![CI](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhaoSH980/alphaloom/actions/workflows/ci.yml)
&nbsp;![tests](https://img.shields.io/badge/backend_tests-404_passing-2ea043?style=flat-square)
&nbsp;![frontend](https://img.shields.io/badge/frontend-tsc--strict_+_vitest-3178c6?style=flat-square)
&nbsp;![offline](https://img.shields.io/badge/demo-offline_·_zero--quota-f59e0b?style=flat-square)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

<br>

<img src="docs/assets/architecture.svg" alt="AlphaLoom architecture — a blueprint compiles into a trading agent, runs, is evaluated, and the LLM rewrites the graph in a compile-gated closed loop" width="100%">

</div>

## Why it's different

General agent frameworks (LangChain, LangGraph, Langflow) orchestrate **conversation**, which has no ground truth. AlphaLoom compiles **falsifiable trading organisms**, where every decision is judged by realized market data. That difference lets the compiler *prove* things a conversation framework structurally cannot:

|   | |
|---|---|
| 🔒 **The type system is the compliance officer** | `ExecuteOrder` only accepts a `risk_stamped_signal` — a type that *only* a `RiskGate` node can produce. A graph that routes a raw signal to an order (or a Copilot/LLM that tries to synthesize one) **fails to compile**. Bypassing risk control isn't a policy you enforce; it's a sentence the language cannot express. |
| 📜 **Static cost certificate** | The compiler walks the graph and tells you — *before you run it* — worst-case LLM calls per bar, the daily token ceiling, the worst-case latency class, and what fraction of the graph is deterministic. |
| ⏳ **Causal typing** | Market-data pins carry an *as-of* timestamp; the runtime rejects any read of data later than the current bar. Look-ahead bias becomes a compiler-assisted, runtime-enforced error. |
| 📊 **Honest evaluation, by design** | Five *falsifiable* evaluation tools (below) — AlphaLoom is a **methodology demonstration**, not a directly-deployable alpha. The honesty is the deliverable. |

## See it

**The graph *is* the agent.** A blueprint of typed nodes; the **cost certificate** (top-right) is computed *before* you run — 3 LLM calls/bar, 92.3% deterministic. The red **`risk_gate`** node is the only path into `execute_order`.

<img src="docs/screenshots/studio.png" alt="Blueprint Studio: the agent_committee node graph with the cost certificate panel" width="100%">

**Fidelity ladder — the backtest lie detector.** The *same* fills replayed under four fill models; net PnL degrades monotonically L0→L3, and the **optimism gap** is exactly how much rosier the naive backtest was than realistic fills. (Zero LLM calls — it re-matches existing fills.)

<img src="docs/screenshots/fidelity.png" alt="Fidelity ladder L0 to L3 with the optimism gap highlighted" width="100%">

**Honest scoring.** A blueprint scorecard that *refuses to trust in-sample numbers* (composite is driven by the held-out window, penalized for the fidelity gap, and zero-trades = zero-evidence), and a baseline leaderboard the agent has to earn its place above.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/scorecard.png" alt="Blueprint scorecard with evidence coverage"></td>
<td width="50%"><img src="docs/screenshots/leaderboard.png" alt="Baseline leaderboard: buy-hold, ema-default, random"></td>
</tr>
</table>

**Committee ablation — honest even when it's unflattering.** Three arms report the guardrail's value *with its sign either way*. In this window the risk officer **hurt** performance (net-blocked winners) — shown as-is, never spun.

<img src="docs/screenshots/ablation.png" alt="Committee ablation table showing guardrail value hurt, reported honestly" width="100%">

**Evolution genealogy — the agent as researcher.** The LLM mutates the graph (compile-gated: a mutation that drops the risk stamp simply won't compile); survivors breed; the winner is scored on a **held-out** window. `repaired` nodes are ones whose first mutation failed to compile and self-repaired from the compiler's fix-hint.

<img src="docs/screenshots/genealogy.png" alt="Evolution genealogy tree with a winner, survived and repaired nodes" width="100%">

**Every decision is traceable.** RAG citations, and the reflection **four-quadrant** taxonomy that separates *reasonable-but-wrong* from *bad-process* — don't punish a sound decision for one bad outcome.

<img src="docs/screenshots/terminal.png" alt="Terminal: committee run summary, RAG citations, reflection verdicts" width="100%">

## What's inside

| Layer | What it does |
|---|---|
| **Blueprint compiler** | `.loom` JSON → typed node graph → topological plan + cost certificate. Type-checks pins, expands subgraphs, rejects illegal cycles, enforces the risk-stamp rule. |
| **Event-driven engine** | Wave-based execution, deterministic replay, breakpoints + full I/O recording (the substrate for time-travel debugging). |
| **Backtest + brokers** | Next-bar-open fill semantics (no look-ahead), attached stops, EOD settlement; paper broker (OKX-demo broker sketched). |
| **Agent nodes** | `LLMAnalyst`, `Committee` (strategist → risk-officer → chair with **code-enforced veto**), `PADecisionTree` (deterministic numeric gate — "don't trust the LLM's mouth"), `KnowledgeRetrieve` (BM25 RAG, EN + 中文), `RequireCitations`, `Reflector` (process/outcome scoring), experience store by market-regime bucket. |
| **Copilot** | Text-to-Blueprint: natural language → a compilable graph. **Compile-error self-repair**: a graph that fails to compile is repaired from the `CompileError` fix-hint (≤3 tries). Explain / Optimize. |
| **Text-to-Node sandbox** | AST-whitelist compiler that hot-registers custom nodes; red-teamed against 40+ classic Python escapes; sandboxed nodes are stripped of the LLM handle and cannot forge the risk stamp. |
| **Eval Lab** | Five offline-replayable tools: fidelity ladder · scorecard · baseline leaderboard · committee ablation · evolution genealogy. |
| **Studio + Terminal (React)** | Drag-and-connect canvas with typed pins, live compile feedback, cost-certificate panel, run-time glow, breakpoint inspector; Terminal shows candles + fills, equity, committee traces, RAG citations, and reflection verdicts. |

## Quick start (zero API key, zero quota)

```bash
# backend
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -e .[dev]
# frontend
cd ../frontend && npm install && npm run build
# one-process offline demo  (Windows: demo.bat does all of this)
cd .. && ALPHALOOM_OFFLINE=1 backend/.venv/Scripts/python -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
# open http://localhost:8000  → Studio · Terminal · Eval Lab
```

Run the `agent_committee` blueprint in the Studio: it replays **committed LLM recordings** with **zero network calls** — 301 bars, 150 trades, committee decisions, and reflection verdicts across all four quadrants, instantly. In the **Eval Lab**, click each panel's **"▶ Run offline demo"** to replay the ablation and evolution recordings offline.

## On the LLM recordings (honest framing)

Every LLM call goes through a **record/replay layer** (ported from Hindsight): each request is canonicalized and hashed; a cache hit means no network. `ALPHALOOM_OFFLINE=1` replays committed recordings so the demo runs anywhere, offline, at zero quota. The committed `data/llm_calls.sqlite` has **two clearly-distinguished sources**:

1. **835 deterministic seed responses** (`model: spark-x1`) — hand-authored, valid-shaped canned responses driving a *rich, reproducible* demo: varied committee decisions, risk-officer vetoes, all four reflection quadrants, a Copilot compile-error self-repair, plus the three-arm ablation and the small-scale evolution lab so every Eval Lab panel renders offline. **These are synthetic, not real LLM output** — regenerate them with `scripts/seed_recordings.py`. The demo coordinates live in `backend/alphaloom/eval/demo_coords.py`, which both the seed script and the API import, so they can never drift.
2. **123 real 讯飞 (iFlytek Spark) `astron-code-latest` calls** — genuine recorded responses from a 40-bar `agent_committee` run against the real endpoint, offline-verified to replay at **123 hits / 0 miss**. In that window the real committee traded conservatively (all flat/hold) — authentic behavior, not curated. This proves the pipeline works against a real LLM.

To run live or record your own: put `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` in a repo-root `.env` (never committed) and run without `ALPHALOOM_OFFLINE`. 429 backoff is built in.

## Testing & safety

- **404 backend tests** (pytest), frontend tsc-strict + vitest. CI runs both offline/deterministically — no real LLM, zero quota.
- Reviewed task-by-task by an independent adversarial reviewer; a red-team pass on the sandbox tried 40+ classic escapes (dunder chains, format-string dunder, metaclass hooks, import bypass, private-slot reach-back) — all blocked. Sandboxed nodes are stripped of the LLM handle (a `WeakKeyDictionary`-backed restricted context), so a custom node **cannot** quietly burn your API quota — the same "compliance officer" principle as the risk stamp.
- No secrets in the repo or its history; `.env` is git-ignored and was never committed.

## Documentation

- [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md) — what each evaluation tool measures, what it does **not**, and exactly where it stops being trustworthy (single synthetic instrument, small windows, N=1 causal claims, synthetic-seed vs. real-LLM recordings). The capstone of the honest-evaluation brand.
- [`docs/demo-script.md`](docs/demo-script.md) — a 10-minute offline talk track, each step mapped to a JD capability.
- [`docs/future-work.md`](docs/future-work.md) — known boundaries and roadmap.
- `docs/superpowers/` — the design spec, per-day plans, and per-task adversarial review trail.

## Status

**Complete** — D1 (graph core / compiler / engine / backtest) · D2 (API/WS + Studio + Terminal) · D3 (LLM nodes / Copilot / reflection / recordings) · D4 (evaluation suite: fidelity ladder, scorecard, baseline leaderboard, committee ablation, evolution lab + genealogy). Tagged `d1-complete` → `d4-complete`; every task passed a two-stage adversarial review plus a live browser walkthrough.

<div align="center">

**MIT © 2026 Zhao Chenghao**

</div>
