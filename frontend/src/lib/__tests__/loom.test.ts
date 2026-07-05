// frontend/src/lib/__tests__/loom.test.ts
import { describe, expect, it } from "vitest";
import { flowToLoom, loomToFlow, nextNodeId, type Loom, type NodeDef } from "../loom";

const defs: Record<string, NodeDef> = {
  ema: { type: "ema", category: "indicator", inputs: { candle: "candle" },
         outputs: { value: "series" }, params: { period: "int" }, cost: {} },
  candle_feed: { type: "candle_feed", category: "data", inputs: {},
                 outputs: { out: "candle" }, params: {}, cost: {} },
};

const loom: Loom = {
  id: "t", name: "t",
  nodes: [{ id: "feed", type: "candle_feed", params: {} },
          { id: "ema1", type: "ema", params: { period: 12 } }],
  edges: [{ from: "feed.out", to: "ema1.candle" },
          { from: "ema1.value", to: "feed.out" as never, feedback: true } as never],
  meta: {},
};

describe("loom mapping", () => {
  it("roundtrips nodes, edges, feedback and positions", () => {
    const { nodes, edges } = loomToFlow(loom, defs);
    expect(nodes).toHaveLength(2);
    expect(edges[0].sourceHandle).toBe("out:out");
    expect(edges[0].targetHandle).toBe("in:candle");
    expect(edges[1].data.feedback).toBe(true);
    nodes[0].position = { x: 123.4, y: 56.6 };
    const back = flowToLoom(nodes, edges, loom);
    expect(back.nodes.map(n => n.id)).toEqual(["feed", "ema1"]);
    expect(back.edges[0]).toEqual({ from: "feed.out", to: "ema1.candle" });
    expect(back.edges[1].feedback).toBe(true);
    expect((back.meta.positions as Record<string, unknown>).feed).toEqual({ x: 123, y: 57 });
  });
  it("nextNodeId avoids collisions", () => {
    expect(nextNodeId(new Set(["ema_1"]), "ema")).toBe("ema_2");
    expect(nextNodeId(new Set(), "ema")).toBe("ema_1");
  });
});
