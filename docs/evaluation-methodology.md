# Evaluation methodology (and its honest limits)

> This is the capstone document for AlphaLoom's "honest evaluation" brand. It exists
> to state, plainly, **what the platform's evaluation tools measure, what they do
> *not* measure, and where they are wrong.** AlphaLoom is a **methodology
> demonstration** — it shows the engineering of *how to honestly evaluate an agent's
> trading strategy*. It is **not** a directly-deployable alpha. Honesty is the
> selling point; overstating the tools would defeat the entire purpose.

The Eval Lab ships five visualizations, each with a specific, bounded claim:
fidelity ladder, blueprint scorecard, baseline leaderboard, committee ablation, and
the evolution genealogy tree. This document takes each in turn and says where it
stops being trustworthy.

---

## 1. Fidelity ladder (L0–L3): a backtest lie-detector, not a market simulator

**What it does.** The ladder takes the fill sequence a backtest **already
generated** and *re-matches those same fills* under four progressively more
pessimistic execution models. The decisions (which bar wanted to go long / short /
flat) never change — only the *fill assumption* changes — so it re-runs zero LLM
calls and is purely numeric.

| Level | Fill assumption | What it represents |
|---|---|---|
| **L0** | Signal-bar close vs. next-bar-open, whichever is **more favorable** to the trader | The most optimistic lie — no slippage, no timing penalty |
| **L1** | Next-bar-open | The current `PaperBroker` semantics — the D1 backtest baseline |
| **L2** | L1 price + an adverse "half-bar-amplitude" offset `(high−low)/2`, clamped into the execution bar's `[low, high]` | An intra-bar path *proxy* — worse fills using the OHLC path |
| **L3** | L2 price + an extra `slippage_bps` notional slippage (always adverse, deliberately *not* clamped) | Fees + slippage pressure |

The headline number is **`optimism_gap = L0_pnl − L3_pnl`**: how much of the paper
profit evaporates once you stop believing the most optimistic fill story. The ladder
also enforces a **monotonicity contract** (`net_pnl` L0 ≥ L1 ≥ L2 ≥ L3) *by
construction* — each level applies a monotone-non-decreasing adverse price offset, so
a violation means the fill model has a bug, not that the market moved.

**What it does NOT guarantee.**

- **It is not real market microstructure.** There is no order book, no queue
  position, no partial fills, no latency to the exchange, no adverse selection.
- **It contains no full impact-cost model.** L3's slippage is a **nominal linear
  `bps` model** applied to the notional — a stand-in, not a calibrated market-impact
  curve. Large orders in thin markets would be far worse than L3 suggests.
- **L2/L3 are conservative proxies, not tick-level replays.** L2's half-amplitude
  offset is a *path proxy*, not a tick-by-tick reconstruction of what actually filled
  when. If you have real tick/L2 data, the honest fill could sit anywhere inside the
  bar; the ladder picks a pessimistic representative point, not the true one.
- **Stop-leg replay depends on a convention.** The ladder recognizes stop legs by the
  `PaperBroker` `tag="stop"` marker. Fills from another source without that tag would
  be replayed with ordinary "next-bar-open" semantics — systematically *optimistic*
  for stops.

The ladder's honest claim is narrow and real: **it quantifies backtest optimism
bias** — it tells you *which rung of the ladder your paper profit starts lying at*.
It does not tell you what you would actually have earned live.

---

## 2. Blueprint scorecard: a design decision, not a statistical inference

**What it does.** The scorecard aggregates the evidence from one backtest —
in-sample vs. validation-window performance, fidelity-ladder decay, the cost
certificate, and (optionally) an ablation summary — into a single **composite score
(0–100)** used to rank the gallery by *evidence* rather than by feel.

The composite is `Σ weightᵢ × componentᵢ` over four components:

| Component | Weight | What it rewards |
|---|---|---|
| `valid_performance` | 0.40 | Validation-window return, `tanh`-squashed (0% ≈ 50, neutral) |
| `generalization` | 0.25 | Small train→valid gap (overfitting is priced directly) |
| `fidelity` | 0.20 | Small ladder decay (paper profit surviving into L3) |
| `determinism` | 0.15 | Reproducibility (`deterministic_ratio` from the cost cert) |

**Why it is honest by design.** The 0.40-weighted headline is the *validation* window,
not the in-sample window — because the fidelity ladder already proved that a backtest
lies, so the scorecard refuses to let in-sample numbers drive the ranking key.
Missing evidence scores **25** (weak evidence, not a neutral 50 and not full marks),
and `evidence_coverage` states exactly what was missing. **Zero trades scores as zero
evidence**: a strategy that never traded has nothing to overfit and nothing to decay,
so its "perfect" fidelity/generalization scores are hollow — the code caps them at
the missing-evidence floor. (This was found the hard way: a do-nothing strategy once
rode those hollow perfect scores to an 80 and beat a genuinely-profitable 78.)

**What it is NOT — and this is the important part.** The composite is a **design
decision, not a statistical inference.** Specifically:

- **The `tanh` anchors are subjective.** `RETURN_SQUASH_SCALE_PCT = 10` means +10%
  return maps to ≈88 points. That "10%" is a chosen anchor, not a fact derived from
  data. Pick a different scale and every score moves.
- **The four weights are choices, not fitted parameters.** 0.40 / 0.25 / 0.20 / 0.15
  is a defensible editorial stance on what evidence matters most. It is not the output
  of any optimization or any theory.
- **"Zero trades = zero evidence" is a deliberate trade-off that misfires on genuinely
  low-frequency strategies.** A strategy that legitimately fired zero times in a short
  validation window is *indistinguishable* from a strategy that deliberately did
  nothing, and both are penalized. This prices "unfalsifiable" as "weak evidence" — a
  self-aware choice, but one that will unfairly punish a real, rare-signal strategy on
  a short window.

All scoring constants live at the top of `backend/alphaloom/eval/scorecard.py` and are
invited criticism, not settled truth. The composite is a **ranking key you can argue
with**, which is the whole point — it is not a p-value.

---

## 3. Baseline leaderboard: the agent must beat brainless baselines

**What it does.** The leaderboard puts the agent's blueprint on the same table as
three zero-LLM baselines and ranks by **validation-window** return:

- **`baseline_buy_hold`** — full-position buy at the first bar, hold to the last,
  fees charged with the exact same semantics as `PaperBroker`.
- **`baseline_ema_default`** — the default-parameter `ema_cross.loom` run through the
  real backtest engine (fully deterministic, zero LLM).
- **`baseline_random`** — random entries/exits with a fixed seed (reproducible). Its
  certificate carries `luck_baseline=True`: it exists so the board has a "what can
  pure luck earn?" reference. **Any strategy that can't beat it has no business
  claiming to have a signal.**

If the agent's blueprint loses to a baseline, **it ranks below, as-is** — no
beautification. The `generalization_gap` (train return − valid return) is printed on
every row, so overfitting is exposed inline.

**Current limitations — the leaderboard's evidence base is thin.**

- **Small demonstration data.** The default offline database contains synthetic
  BTC/ETH candles for deterministic tests plus a real OKX `SOL-USDT-SWAP` smoke
  window used by the headline demo. The SOL window is real market history, but it is
  still a selected smoke-test slice, not a broad research sample.
- **Small windows.** Demo windows are on the order of tens to a few hundred 1-minute
  bars. This is nowhere near enough for a statistically meaningful edge claim.
- **No cross-market / cross-regime robustness.** There is no test across many
  instruments, many periods, or different volatility regimes. "Beats the baseline
  here" means "beats it on this one small demonstration window," nothing more.

Beating a brainless baseline on one small demonstration window is a *sanity check*, not
evidence of alpha. The leaderboard is honest about *ranking* the contestants; it makes
no claim that the winner would generalize.

---

## 4. Committee ablation: bounded causal claims (N = 1)

**What it does.** The ablation runs three arms generated *by graph surgery* on the
same blueprint (ablation is programmable graph surgery, not three hand-written
blueprints):

- **`full`** — the blueprint unchanged.
- **`no_risk_officer`** — the LLM risk-officer role is skipped (the *soft* guardrail).
- **`no_rag`** — the `require_citations` gate and the knowledge-retrieve sub-chain are
  bypassed.

It then reports **`guardrail_value`** — the validation-window metric delta between
`full` and `no_risk_officer` — plus a `guardrail_helped` boolean. This is a **pure
calculation; positive and negative results are both reported as-is.**

**The honest demo result: `guardrail_helped = False`.** In the recorded demo window,
the risk officer *hurt* — a cautious officer vetoed proposals that would have been
winners, so removing it *improved* the arm's return. **This is shown, not hidden.**
The code contains no hard-coded conclusion; the ablation module ships with test
scripts for *both* a positive scenario (officer blocks a dangerous pre-crash proposal)
and a negative one (paranoid officer nets out killing profitable trades), precisely so
that whatever the real recording shows gets displayed truthfully.

**What it can NOT claim.**

- **N = 1, single window.** The ablation compares arms on **one window**. A single
  window's `guardrail_value` is an anecdote, not a causal law. On a different window
  the sign could flip.
- **It measures correlation on that window, not a generalizable causal effect.**
  A negative `guardrail_value` (however large) does not mean "the risk officer is bad."
  It means: *in this particular slice of this particular demonstration instrument, the
  officer's vetoes happened to land on trades that would have won.* Read the exact
  delta off the panel; don't read a verdict about risk officers into it.

**A crucial distinction the ablation makes visible.** The ablation can only remove the
**LLM soft guardrail** (the risk *officer*). It **cannot** remove the **hard
guardrail** — the type system. `execute_order` accepts only a `risk_stamped_signal`,
a type that only a `RiskGate` node can produce. An ablation that tries to bypass
`risk_gate` **fails to compile with `TYPE_MISMATCH`** (locked by test). That the soft
guardrail is ablatable and the hard guardrail is not is the selling point, not a
defect.

---

## 5. Evolution lab: overfitting risk mitigated, not eliminated

**What it does.** The evolution lab is a genetic algorithm whose **mutation operator
is an LLM**. It reads a backtest report, proposes a blueprint mutation (a graph diff),
compiles the mutant (through the same risk type-system gate — mutations can't escape
the compliance officer either), scores fitness on the **train window**, keeps the
elite, and repeats — then final-selects the best individual on a **held-out
validation window** that never participated in evolution.

**Overfitting mitigations (real, but partial).**

- **Train/validation separation with leakage protection.** No individual queries the
  validation window during the evolution loop; the valid window is touched exactly
  once, at final selection. **Overlapping train/valid windows raise `ValueError`** —
  leak prevention is both a structural guarantee *and* an entry-point check.
- These mitigations **reduce** overfitting risk. They **do not eliminate** it: the LLM
  mutation operator is still selecting for train-window return, so the winner is still
  a strategy that happened to look good on the training slice. A single held-out
  window is a weak generalization test, not a strong one.

**Demonstration scale, not a serious search.** The scale is **hard-locked to
`population ≤ 4`, `generations ≤ 3`** (the demo preset uses `population = 2`,
`generations = 2`, `param_only = True`). This is deliberately a *demonstration* of the
evolutionary loop and its safety properties, **not a serious hyperparameter search**.
A real search would need orders of magnitude more population, generations, walk-forward
windows, and out-of-sample folds. Fitness is deliberately simple: train-window
`return_pct`, with `num_trades == 0` scored as **0** ("zero trades = zero evidence,"
the same doctrine as the scorecard) — so in an all-losing population a do-nothing
individual can legitimately rank first ("not losing beats losing"), which is an honest
conclusion, not a bug.

---

## 6. On the recordings: deterministic seeds are synthetic, not real LLM output

The offline demo replays committed LLM recordings so it runs anywhere at zero quota.
The committed `data/llm_calls.sqlite` has **two clearly-distinguished sources**, and
the distinction is load-bearing for honesty:

1. **Deterministic seed responses** (`model: spark-x1`) — **hand-authored, valid-shaped
   canned responses.** They drive a rich, reproducible demo (varied committee decisions,
   vetoes, all four reflection quadrants, the ablation, the evolution lab). **These are
   synthetic — they are NOT real LLM output.** Their purpose is a deterministic,
   zero-quota showcase that anyone can regenerate with `scripts/seed_recordings.py`. The
   official demo coordinates are the `DEMO_*` constants in `backend/alphaloom/eval/
   demo_coords.py` (shared by the seed script and the API so they can never drift).
2. **Real 讯飞 (iFlytek Spark) `astron-code-latest` calls** — **genuine recorded
   responses** from a real 40-bar `agent_committee` run against the live endpoint,
   offline-verified to replay at full hits / zero miss. In that window the real
   committee traded conservatively (all flat/hold) — **authentic LLM behavior, not
   curated.** This is a **small sample and is not statistically significant**; its only
   claim is that **the pipeline works against a real LLM.**

Do not read the deterministic-seed demo as evidence about LLM decision quality. It is a
reproducibility fixture. The real-LLM window is the only genuine-behavior evidence, and
it is tiny.

---

## 7. Summary: what AlphaLoom's evaluation is, and is not

**It is** a demonstration of the *engineering* of honest agent-strategy evaluation:
falsifiable fill assumptions, a criticizable ranking key that refuses to trust
in-sample numbers, brainless baselines the agent must beat, ablations that quantify
guardrail value with the sign reported either way, and an evolution loop that can't
escape the compliance type system.

**It is not** a source of tradeable alpha, a validated market study, a
statistically-significant result, or a claim that any blueprint here would make money
live. Small selected windows, N = 1 causal claims, demonstration-scale search,
mostly-synthetic recordings.

**The honesty is the deliverable.** A tool that told you it had found alpha on one
small demo window would be lying, and lying is exactly the failure mode this whole
system is built to detect.

See also: [`demo-script.md`](demo-script.md) (the 10-minute walkthrough) and
[`future-work.md`](future-work.md) (known boundaries and roadmap).
