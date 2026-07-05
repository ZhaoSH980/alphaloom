// frontend/src/lib/__tests__/copilot.test.ts —— computeDiff 纯函数（D3 Task 9）
import { describe, expect, it } from "vitest";
import { computeDiff } from "../copilot";
import type { Loom } from "../loom";

const mk = (nodes: Loom["nodes"], edges: Loom["edges"]): Loom =>
  ({ id: "t", name: "t", nodes, edges, meta: {} });

describe("computeDiff", () => {
  it("classifies added / removed / changed nodes and edges", () => {
    const before = mk(
      [{ id: "a", type: "ema", params: { period: 12 } },
       { id: "b", type: "atr", params: {} }],
      [{ from: "a.value", to: "b.candle" }],
    );
    const after = mk(
      [{ id: "a", type: "ema", params: { period: 20 } },  // changed (param)
       { id: "c", type: "risk_gate", params: {} }],       // added; b removed
      [{ from: "a.value", to: "c.signal", feedback: true }], // added edge; old removed
    );
    const d = computeDiff(before, after);
    expect(d.nodeKind).toEqual({ a: "changed", c: "added", b: "removed" });
    expect(d.counts).toEqual({ added: 1, removed: 1, changed: 1,
                               addedEdges: 1, removedEdges: 1 });
    expect(d.addedEdges.has("a.value|c.signal|1")).toBe(true);
    expect(d.removedEdges.has("a.value|b.candle|0")).toBe(true);
  });

  it("reports no structural change for identical looms", () => {
    const l = mk([{ id: "a", type: "ema", params: { period: 12 } }],
                 [{ from: "a.value", to: "b.candle" }]);
    const d = computeDiff(l, l);
    expect(d.nodeKind).toEqual({});
    expect(d.counts).toEqual({ added: 0, removed: 0, changed: 0,
                               addedEdges: 0, removedEdges: 0 });
  });

  it("treats a same-id node with a different type as changed", () => {
    const before = mk([{ id: "x", type: "ema", params: {} }], []);
    const after = mk([{ id: "x", type: "sma", params: {} }], []);
    expect(computeDiff(before, after).nodeKind).toEqual({ x: "changed" });
  });
});
