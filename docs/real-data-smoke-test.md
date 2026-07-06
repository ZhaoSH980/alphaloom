# Real Data Smoke Test

This is a small real-market sanity check for the README/demo story. It is not an alpha claim and not investment advice. The goal is to show that AlphaLoom can run the same compile-gated trading graph on public historical market data, not only on the synthetic offline demo database.

## Data

Source: OKX public REST `history-candles`, downloaded with the repository script:

```bash
backend/.venv/Scripts/python scripts/build_sample_db.py --days 14 --inst BTC-USDT-SWAP ETH-USDT-SWAP SOL-USDT-SWAP XRP-USDT-SWAP --out data/real_okx_14d.sqlite
```

The generated SQLite file is intentionally git-ignored (`data/*.sqlite`). Rebuild it locally when needed.

Local dataset used for this run:

| Instrument | Rows | UTC range |
|---|---:|---|
| BTC-USDT-SWAP | 20,200 | 2026-06-22 04:10Z to 2026-07-06 04:49Z |
| ETH-USDT-SWAP | 20,200 | 2026-06-22 04:11Z to 2026-07-06 04:50Z |
| SOL-USDT-SWAP | 20,200 | 2026-06-22 04:12Z to 2026-07-06 04:51Z |
| XRP-USDT-SWAP | 20,200 | 2026-06-22 04:13Z to 2026-07-06 04:52Z |

## Selected Demo Run

Blueprint: `blueprints/real_sol_breakout_demo.loom`

Instrument/window:

| Field | Value |
|---|---|
| Instrument | `SOL-USDT-SWAP` |
| Bar | `1m` |
| Start | 2026-06-25 04:12Z |
| End | 2026-06-26 04:12Z |
| Bars | 1,441 |

Parameters:

| Parameter | Value |
|---|---:|
| `scenario.lookback` | 45 |
| `scenario.cooldown` | 10 |
| `scenario.atr_mult` | 2.5 |
| `sizer.risk_pct` | 0.005 |
| `risk.require_stop` | true |

Backtest result with `initial_cash=10000` and `fee_rate=0.0005`:

| Metric | Result |
|---|---:|
| Net PnL | 946.46408889 |
| Return | 9.4646% |
| Max drawdown | 2.7693% |
| Trades | 29 |
| Win rate | 68.97% |
| Profit factor | 3.0025 |

Buy-and-hold on the same SOL window returned 0.4761% with 7.6801% max drawdown. The fidelity ladder on this window is also cleaner for README screenshots: L0 +1006.5746, L1 +946.4641, L2 +423.2728, and L3 +148.6884 after the harshest fill model. So this is a useful demo window: the graph makes money, beats buy-and-hold in this slice, survives the L3 fill model, and still goes through the same `RiskGate -> ExecuteOrder` typed compliance path.

## Caveat

This was found by scanning a small parameter set over a short recent historical window. Treat it as a real-data smoke test for the system, not as evidence of a deployable trading edge. For a stronger claim, use a train/validation split, out-of-sample instruments, higher-fidelity fills, and a larger walk-forward evaluation.
