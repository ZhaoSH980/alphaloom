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
| Start | 2026-06-24 04:12Z |
| End | 2026-06-27 04:12Z |
| Bars | 4,321 |

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
| Net PnL | 904.3905602 |
| Return | 9.0439% |
| Max drawdown | 8.2020% |
| Trades | 93 |
| Win rate | 50.54% |
| Profit factor | 1.3774 |

Buy-and-hold on the same SOL window returned 4.0152% with 8.5091% max drawdown. So this is a useful demo window: the graph makes money, beats buy-and-hold in this slice, and still goes through the same `RiskGate -> ExecuteOrder` typed compliance path.

## Caveat

This was found by scanning a small parameter set over a short recent historical window. Treat it as a real-data smoke test for the system, not as evidence of a deployable trading edge. For a stronger claim, use a train/validation split, out-of-sample instruments, higher-fidelity fills, and a larger walk-forward evaluation.
