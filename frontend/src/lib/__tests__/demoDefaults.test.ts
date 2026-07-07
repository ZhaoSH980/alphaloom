import { describe, expect, it } from "vitest";
import { pickDefaultBlueprint, runDefaultsFromLoom } from "../demoDefaults";
import type { Loom } from "../loom";

describe("demo defaults", () => {
  it("prefers the profitable real OKX SOL smoke-test blueprint", () => {
    const gallery = [
      { id: "ema_cross_v1", name: "EMA Cross", source: "preset" },
      { id: "real_sol_breakout_demo_v1", name: "Real OKX SOL Breakout Demo", source: "preset" },
      { id: "agent_committee_v1", name: "Agent Committee", source: "preset" },
    ];

    expect(pickDefaultBlueprint(gallery)).toBe("real_sol_breakout_demo_v1");
  });

  it("derives run instrument, bar, and demo window from the selected loom", () => {
    const loom: Loom = {
      id: "real_sol_breakout_demo_v1",
      name: "Real SOL",
      nodes: [
        { id: "feed", type: "candle_feed", params: { inst: "SOL-USDT-SWAP", bar: "1m" } },
        { id: "risk", type: "risk_gate", params: {} },
      ],
      edges: [],
      meta: { demo_start_ms: 1782360720000, demo_end_ms: 1782447120000 },
    };

    expect(runDefaultsFromLoom(loom)).toEqual({
      inst: "SOL-USDT-SWAP",
      bar: "1m",
      start_ms: 1782360720000,
      end_ms: 1782447120000,
    });
  });

  it("falls back to the original BTC 1m demo defaults for blank looms", () => {
    expect(runDefaultsFromLoom({ id: "x", name: "x", nodes: [], edges: [], meta: {} }))
      .toEqual({ inst: "BTC-USDT-SWAP", bar: "1m" });
  });
});
