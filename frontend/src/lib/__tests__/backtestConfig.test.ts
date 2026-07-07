import { describe, expect, it } from "vitest";
import {
  buildBacktestRunBody,
  initialBacktestConfig,
  replayCandlesForCursor,
  type MarketWindow,
} from "../backtestConfig";
import type { Loom } from "../loom";

const loom: Loom = {
  id: "demo",
  name: "Demo",
  nodes: [{ id: "feed", type: "candle_feed", params: { inst: "SOL-USDT-SWAP", bar: "1m" } }],
  edges: [],
  meta: { demo_start_ms: 1_000, demo_end_ms: 4_000 },
};

const catalog: MarketWindow[] = [
  { inst: "BTC-USDT-SWAP", bar: "1m", start_ms: 0, end_ms: 9_000, count: 10 },
  { inst: "SOL-USDT-SWAP", bar: "1m", start_ms: 500, end_ms: 5_500, count: 6 },
];

describe("backtest config", () => {
  it("uses the current blueprint feed and demo window when available", () => {
    expect(initialBacktestConfig(loom, catalog)).toMatchObject({
      inst: "SOL-USDT-SWAP",
      bar: "1m",
      start_ms: 1_000,
      end_ms: 4_000,
      cash: 10_000,
      fee_rate: 0.0005,
      speed: "4x",
    });
  });

  it("falls back to the first market catalog window when the blueprint has no demo window", () => {
    const blank: Loom = { id: "blank", name: "Blank", nodes: [], edges: [], meta: {} };

    expect(initialBacktestConfig(blank, catalog)).toMatchObject({
      inst: "BTC-USDT-SWAP",
      bar: "1m",
      start_ms: 0,
      end_ms: 9_000,
    });
  });

  it("builds an explicit run body from the selected backtest controls", () => {
    const config = initialBacktestConfig(loom, catalog);

    expect(buildBacktestRunBody(loom, config, ["risk"])).toMatchObject({
      blueprint: loom,
      inst: "SOL-USDT-SWAP",
      bar: "1m",
      start_ms: 1_000,
      end_ms: 4_000,
      cash: 10_000,
      fee_rate: 0.0005,
      playback_ms: 20,
      ws_wait_ms: 300,
      breakpoints: ["risk"],
      mode: "backtest",
    });
  });

  it("reveals candles up to the active cursor while a run is streaming", () => {
    const candles = [
      { ts: 1, open: 1, high: 2, low: 1, close: 2, volume: 10 },
      { ts: 2, open: 2, high: 3, low: 1, close: 1, volume: 10 },
      { ts: 3, open: 1, high: 4, low: 1, close: 4, volume: 10 },
    ];

    expect(replayCandlesForCursor(candles, undefined)).toHaveLength(3);
    expect(replayCandlesForCursor(candles, 1)).toEqual(candles.slice(0, 2));
    expect(replayCandlesForCursor(candles, 99)).toEqual(candles);
  });
});
