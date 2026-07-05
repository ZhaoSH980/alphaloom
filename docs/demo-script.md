# AlphaLoom — 10-minute demo script (offline talk track)

<!-- screenshots added in T10 -->

This is a step-by-step **offline** walkthrough (zero API key, zero quota) for an
interview demo. Every step is annotated with **"what interview capability this
proves"** — mapped to the JD: tool design, ReAct, RAG, multi-agent architecture,
evaluation design, agent safety.

**Golden rule for the offline demo:** the deterministic recordings only replay at the
**official demo coordinates**. For the Eval Lab's **ablation** and **evolution**
panels you MUST click the **"▶ Run offline demo"** preset button — it sends
`{demo: true}` and the backend hard-uses the fixed `DEMO_*` coordinates
(`backend/alphaloom/eval/demo_coords.py`). If you instead click the per-run buttons,
the window is derived from the *selected run's* coordinates, which do **not** match the
seed, so the offline replay **misses** and you get an HTTP 422 with an empty table /
tree. This is the single most important operational detail of the whole demo.

---

## Step 0 — Start the offline server (before the audience arrives)

```bash
# one-process offline demo (built frontend served by the backend)
ALPHALOOM_OFFLINE=1 backend/.venv/Scripts/python -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
# open http://localhost:8000
```

`ALPHALOOM_OFFLINE=1` makes every LLM call replay from committed recordings — no
network, no quota. The three tabs are **Studio** (canvas), **Terminal** (results), and
**Eval Lab** (honest evaluation).

> One-liner to open with: *"LangGraph orchestrates conversation, which has no ground
> truth. AlphaLoom compiles a falsifiable trading organism — the graph IS the agent —
> and lets the compiler prove things a conversation framework structurally can't:
> you can't peek at the future, you can't bypass risk control, cost has a ceiling, and
> every decision is auditable."*

---

## Step 1 — Studio: the type system IS the compliance officer (the hard-guardrail moment)

1. Open the **Studio** tab. Load the `agent_committee` blueprint from the gallery
   (left panel). The typed node graph renders: data → committee (strategist →
   risk-officer → chair) → **RiskGate** → **ExecuteOrder**.
2. **Try to break it.** Drag a connection that routes the committee's raw signal
   *directly* into `ExecuteOrder`, **bypassing the `RiskGate`**. Watch the live compile
   feedback go red with **`TYPE_MISMATCH`**: `ExecuteOrder` only accepts a
   `risk_stamped_signal`, a type that *only* a `RiskGate` node can produce. The
   error's `fix_hint` points straight back to `RiskGate`.

> **What this proves — agent safety as a compiler property, not a policy.** Bypassing
> risk control is not a rule you enforce; it is *a sentence the language cannot
> express*. This is the structural difference from a conversation framework, where
> "don't skip risk control" is at best a prompt.

---

## Step 2 — Studio: the static cost certificate

1. With `agent_committee` loaded, open the **cost-certificate panel**. Before running
   anything, the compiler has already walked the graph and reports: **worst-case LLM
   calls per bar**, the **daily token ceiling**, the **worst-case latency class**, and
   the **fraction of the graph that is deterministic** (offline-replayable).

> **What this proves — tool/agent-environment design.** No agent framework does
> *compile-time* cost analysis. The certificate is the budget guardrail the offline
> gate later trusts (an LLM-heavy graph run by a non-offline client is refused with a
> 409 rather than silently burning quota).

---

## Step 3 — Terminal: run the signature `agent_committee` replay

1. Go to the **Terminal** tab and run the `agent_committee` blueprint. It replays
   **committed LLM recordings with zero network calls** — instantly.
2. Walk the audience through what renders:
   - **Candles + fill markers** and the **equity curve** (lightweight-charts).
   - **Committee traces** — strategist proposal → risk-officer review → chair verdict,
     with the **code-enforced veto** (not a chat log; structured JSON hand-offs).
   - **RAG citation badges** — the decision JSON carries the `KnowledgeRetrieve` hits
     that a `RequireCitations` gate forced it to cite.
   - **Reflection four-quadrant verdicts** — every closed trade scored on
     process × outcome (reasonable-but-wrong, etc.), feeding the experience store.

> **What this proves — multi-agent architecture (committee: roles / hand-offs / veto),
> RAG (forced citations + badges), and memory (reflection loop → experience store).**

---

## Step 4 — Eval Lab: fidelity ladder, scorecard, leaderboard (zero LLM)

Open the **Eval Lab** tab and select the completed `agent_committee` run.

1. **Fidelity ladder (L0–L3) — the backtest lie-detector.** Runs automatically, zero
   LLM. Point at the **`optimism_gap`** and the monotone `net_pnl` decay L0 ≥ L1 ≥
   L2 ≥ L3. Say plainly: *"This re-matches the same fills under worse execution
   assumptions. It's not a market simulator — it quantifies how much of the paper
   profit is a lie."*
2. **Blueprint scorecard.** Click **"Build scorecard."** Explain the composite is a
   **design decision, not a p-value**: 0.40 weight on the *validation* window (not
   in-sample, because the ladder proved backtests lie), zero-trades = zero evidence,
   missing evidence scored as weak (25), not neutral.
3. **Baseline leaderboard.** Run it. Three brainless baselines (buy-and-hold, default
   EMA, random-with-seed). **The agent must beat them; if it can't, it ranks below,
   as-is.** The `generalization_gap` column exposes overfitting inline.

> **What this proves — evaluation design.** Falsifiable fill assumptions, a criticizable
> ranking key that refuses to trust in-sample numbers, and brainless baselines the
> agent has to earn its place above.

---

## Step 5 — Eval Lab: committee ablation (click "▶ Run offline demo") — the honest story

1. In the **Committee ablation** panel, click **"▶ Run offline demo"** (NOT the
   per-run button — see the Golden Rule above; the preset uses the fixed 50-bar demo
   window on `agent_committee` so the recordings replay at zero quota).
2. The three-arm table renders: **full / no_risk_officer / no_rag**, with
   **`guardrail_value`** and **`guardrail_helped`**.
3. **Tell the honest story:** in this demo window **`guardrail_helped = False`** — the
   cautious risk officer actually *vetoed winners*, so removing it improved the arm's
   return. **We show it, we don't hide it.** The code has no hard-coded conclusion;
   whatever the recording shows is what you see.
4. **The distinction that matters:** the ablation can only remove the **LLM soft
   guardrail** (the risk *officer*). Trying to ablate the **hard guardrail** — the
   `RiskGate` type gate — **fails to compile with `TYPE_MISMATCH`**. Soft guardrail
   ablatable, hard guardrail not.

> **What this proves — evaluation design + honesty.** Quantifying guardrail value with
> the sign reported either way is the opposite of cherry-picking. N = 1 on one window —
> stated as such.

---

## Step 6 — Eval Lab: evolution genealogy tree (click "▶ Run offline demo")

1. In the **Evolution genealogy** panel, click **"▶ Run offline demo"** (again, NOT the
   per-run button — the preset uses the fixed `ema_cross` seed with `population = 2`,
   `generations = 2`, `param_only = True`, so the LLM mutation-operator calls replay
   offline).
2. The **React Flow genealogy tree** renders: seed → mutated children across
   generations, with fitness, survivors highlighted, and `stillborn` / `runtime_error`
   nodes shown honestly (a mutation that failed to compile or crashed at runtime does
   not silently disappear — it's recorded in the tree).
3. **Narrate the loop:** the agent reads a backtest report → proposes a blueprint
   **mutation** (a graph diff) → the mutant passes through the **same risk type-system
   gate** (mutations can't escape the compliance officer either — a mutation that
   removes `risk_gate` gets `TYPE_MISMATCH` and lands as `stillborn`) → fitness on the
   **train** window → elites survive → final selection on a **held-out valid** window.
   Say plainly: *"This is demonstration scale — population ≤ 4, generations ≤ 3 — not a
   serious search. It proves the loop and its safety, not that it found alpha."*

> **What this proves — the "Agent as researcher"終形态: ReAct-style read-report →
> propose-mutation → compile → evaluate loop, with agent safety preserved under
> evolution.**

---

## Step 7 — Copilot: text-to-blueprint + compile-error self-repair (the killer moment)

1. Back in **Studio**, open the **Copilot** sidebar. Ask it to build a strategy in
   natural language (e.g. *"build an ETH breakout strategy with a trailing stop, keep
   drawdown under 10%"*). It emits a graph, schema-checked, laid out, shown as a
   **diff preview** before you apply.
2. **The self-repair demo:** when the LLM emits a graph that *fails to compile* (a
   causal or risk type error), the `CompileError`'s **`fix_hint` is fed back to the
   LLM**, and it **repairs its own graph** (≤ 3 tries) — the full ReAct trajectory is
   visible. The seeded recording drives this deterministically offline.

> **What this proves — ReAct + tool design.** The compiler error is *designed as a
> prompt* (structured JSON + fix-hint, dual audience: human and agent). The Copilot's
> tool surface (`compile_graph` / `run_backtest` / `read_scorecard` / `mutate_graph` /
> `register_node`) is the platform API — tools built for an agent to call.

---

## Closing line

> *"Every number you just saw is offline, reproducible, and honestly framed. The
> deterministic seeds are synthetic fixtures — not real LLM output — and the one real
> 讯飞 window is small and traded conservatively, which we say out loud. This is a
> methodology demonstration of how to honestly evaluate an agent's strategy — the
> honesty is the deliverable, not a directly-deployable alpha. See
> `docs/evaluation-methodology.md` for exactly where every tool stops being
> trustworthy."*

---

### JD capability → demo-step map (quick reference)

| JD capability | Where in this demo |
|---|---|
| Tool design | Step 2 (cost cert), Step 7 (Copilot tool surface, error-as-prompt) |
| ReAct | Step 6 (evolution read→mutate→compile loop), Step 7 (self-repair trajectory) |
| Multi-agent | Step 3 (committee: strategist / risk-officer / chair + veto) |
| RAG | Step 3 (forced citations + badges) |
| Memory | Step 3 (reflection four-quadrant → experience store) |
| Evaluation design | Steps 4–6 (ladder, scorecard, leaderboard, ablation, genealogy) |
| Agent safety | Step 1 (`TYPE_MISMATCH` hard guardrail), Steps 5–6 (safety preserved under ablation & evolution) |
