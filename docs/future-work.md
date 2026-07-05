# Future work — known boundaries and roadmap

This is a roadmap of **known, deliberate boundaries**, not a defect list and not a
place to hide limitations. Everything here was consciously scoped out (with a working
seam or a documented single-user assumption left in place) so that the parts that
*were* built could be built honestly and end-to-end. Each item is aggregated from the
per-day plan Carryover sections (`docs/superpowers/plans/`).

## 1. Multi-market real-data ingestion

Today every demo number comes from a **single synthetic instrument**
(`BTC-USDT-SWAP`) on synthetic candles in `data/demo.sqlite`. The **`DataSource`
abstraction is already in place** (and the OKX-demo broker is sketched), so the seam
for real, multi-market data exists — it just isn't wired to a live feed. The spec
deliberately kept a single exchange (OKX demo) as the target and preserved the
abstraction to prove extensibility rather than shipping ten half-working connectors.

**Next:** connect a real OKX-demo market feed (and eventually additional
instruments), then re-run the leaderboard and fidelity ladder against real market
data to replace the synthetic-window caveats in `evaluation-methodology.md` §3.

## 2. Evolution lab — scale beyond the demonstration lock

The evolution lab is **hard-locked to `population ≤ 4`, `generations ≤ 3`** (the demo
preset uses `population = 2`, `generations = 2`, `param_only = True`). This is a
*demonstration* of the evolutionary loop and its safety properties, not a serious
hyperparameter search.

**Next:** lift the scale lock behind a proper budget/quota guard, add walk-forward and
multiple out-of-sample folds, and enrich the fitness function beyond single-window
`return_pct`. Structural (non-`param_only`) mutation is already supported and
type-gated; it just needs the larger search budget to be meaningful.

## 3. Sandbox runtime resource limits (full, cgroup-level)

The Text-to-Node sandbox is defended in depth today: an **AST whitelist** (red-teamed
against 40+ classic Python escapes, all blocked), a ban on sandboxed nodes forging the
risk stamp or declaring `RISK_STAMPED_SIGNAL` outputs, a private-attribute-access ban
that closes graph-escape routes, a runtime-stripped restricted context so a sandboxed
node genuinely can't reach the LLM handle, and a `MAX_RANGE` cap on `range()` bounds
(including arithmetic/aug-assign propagation) that bounds per-bar CPU.

What is **not** yet in place is **full cgroup-level CPU / memory / wall-clock limits**.
The residual exposure is a bounded per-bar CPU DoS, not a sandbox escape.

**Next:** before opening a public custom-node marketplace, add real CPU / memory /
timeout enforcement (cgroup or subprocess-level), folded together with item 4.

## 4. Registry namespacing (currently a single-user assumption)

The node registry is **process-level**, so custom nodes registered via
`POST /api/nodes/custom` are visible across users and across `create_app` instances.
The malformed-pin-type and risk-stamp-forgery DoS holes found in review are closed,
but the **cross-user visibility remains a documented single-user assumption** — this
demo assumes one operator.

**Next:** namespace the registry per session (a session prefix at minimum) before any
multi-user deployment, bundled with the resource-limit work in item 3.

## 5. Eval-endpoint window-span / bar-count hard caps

The `/api/eval/*` endpoints (fidelity / leaderboard / ablation) and `/api/evolve` run
**synchronously** (FastAPI thread pool). They have **no explicit window-span or
bar-count upper bound** — today they're implicitly bounded by the small demo DB
(≤ ~400 bars). The evolution endpoint *does* hard-lock population/generations, but the
window span itself is unbounded.

**Next:** before connecting a real or large-volume data source, add a hard span- or
bar-count cap (in the spirit of evolution's scale lock), so an over-long window can't
tie up a worker on a synchronous endpoint.

## 6. Counterfactual fork UI

The spec's innovation ③ — **counterfactual forking** (fork any historical bar, rewrite
a node's output such as "the risk officer didn't veto" / "the LLM said short," replay a
parallel deterministic world, attribute the PnL delta) — has its substrate ready: the
runtime records every node's I/O and replay is deterministic. The **interactive
right-click UI** was scoped out of D4.

**Next:** build the runtime fork + CLI diff report first (the degrade-fuse), then the
right-click canvas interaction. This turns "was the decision wrong or was it just bad
luck?" from a philosophical question into a computable one.

## 7. EOD-close reflection

The runner's end-of-day forced close happens *after* the last `engine.step`, so the
final synthetic EOD-close trade of each backtest is **not reflected** into the
experience store. Every normal in-run close *is* reflected and engine-proven; only this
one synthetic settlement leg is absent.

**Next:** add a post-report reflector step for the EOD leg (the fidelity ladder and
scorecard don't depend on it, so it was safely deferred).

## 8. Smaller documented deferrals

- **Forced-citation as a compile-time type.** Today `RequireCitations` is a soft gate +
  test. It could be promoted to a compile-time type (a RAG-stamp type, analogous to
  `RiskStampedSignal`) so an un-cited trade becomes structurally inexpressible.
- **BM25 score thresholds.** CJK 2-gram tokenization shifted absolute BM25 scores
  (ranking unchanged, and there is no score-threshold logic today). Any future
  score-gating logic must re-calibrate.
- **Subgraph collapse / navigation UI polish** and **Terminal lazy-loading** for very
  long runs (candle + fill markers are loaded in full today; fine at demo scale, would
  need windowed lazy-loading at 10k+ bars).
- **WS event-log durability.** The in-memory WS event log is capped at 20k
  entries/run; the recording SQLite is the authoritative source. Late-connecting
  clients on very long runs could replay incompletely — the recording, not the WS log,
  is the ground truth.

---

None of the above blocks the interview demo, which runs fully offline at zero quota.
They are the honest edges of a **methodology demonstration** — see
[`evaluation-methodology.md`](evaluation-methodology.md) for where the evaluation tools
themselves stop being trustworthy, and [`demo-script.md`](demo-script.md) for the
walkthrough.
