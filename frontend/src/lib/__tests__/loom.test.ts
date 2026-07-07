// frontend/src/lib/__tests__/loom.test.ts
import { describe, expect, it } from "vitest";
import {
  flowToLoom, layoutLoomPositions, loomToFlow, nextNodeId, validateFlowConnection,
  type Loom, type NodeDef,
} from "../loom";

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
  it("prunes stale gateProtocol node references when roundtripping edited graphs", () => {
    const base: Loom = {
      ...loom,
      meta: {
        keep: true,
        gateProtocol: {
          title: "Protocol",
          steps: [
            { id: "s1", title: "Stage", body: "Body", nodes: ["feed", "deleted_node", "ema1"] },
          ],
          sidecars: [
            { id: "loop", title: "Loop", body: "Body", nodes: ["ghost"] },
          ],
        },
      },
    };
    const { nodes, edges } = loomToFlow(loom, defs);

    const back = flowToLoom(nodes, edges, base);
    const protocol = back.meta.gateProtocol as {
      steps: { nodes?: string[] }[];
      sidecars: { nodes?: string[] }[];
    };

    expect(back.meta.keep).toBe(true);
    expect(protocol.steps[0].nodes).toEqual(["feed", "ema1"]);
    expect(protocol.sidecars[0].nodes).toEqual([]);
  });
  it("creates an unknown placeholder definition when node defs are not loaded yet", () => {
    const partial: Loom = {
      id: "partial",
      name: "partial",
      nodes: [{ id: "mystery", type: "future_node", params: { x: 1 } }],
      edges: [],
      meta: {},
    };

    const { nodes } = loomToFlow(partial, {});

    expect(nodes[0].data.def.type).toBe("future_node");
    expect(nodes[0].data.def.category).toBe("unknown");
    expect(nodes[0].data.def.inputs).toEqual({});
    expect(nodes[0].data.def.outputs).toEqual({});
  });
  it("uses dependency layers for blueprints without saved canvas positions", () => {
    const layered: Loom = {
      id: "layered",
      name: "layered",
      nodes: [
        { id: "feed", type: "candle_feed", params: {} },
        { id: "atr", type: "atr", params: {} },
        { id: "scenario", type: "scenario_gate", params: {} },
        { id: "sizer", type: "position_sizer", params: {} },
        { id: "risk", type: "risk_gate", params: {} },
        { id: "exec", type: "execute_order", params: {} },
        { id: "kill", type: "kill_switch", params: {} },
      ],
      edges: [
        { from: "feed.out", to: "atr.candle" },
        { from: "feed.out", to: "scenario.candle" },
        { from: "atr.value", to: "scenario.atr" },
        { from: "scenario.signal", to: "sizer.signal" },
        { from: "sizer.sized", to: "risk.signal" },
        { from: "risk.stamped", to: "exec.signal" },
        { from: "feed.out", to: "kill.candle" },
      ],
      meta: {},
    };

    const { nodes } = loomToFlow(layered, defs);
    const positions = Object.fromEntries(nodes.map((n) => [n.id, n.position]));

    expect(positions.risk.x).toBeGreaterThan(positions.sizer.x);
    expect(positions.exec.x).toBeGreaterThan(positions.risk.x);
    expect(positions.kill.x).toBe(positions.atr.x);
    expect(positions.risk.x).toBeGreaterThan(positions.feed.x);
  });
  it("recomputes dependency layout even when old canvas positions are messy", () => {
    const messy: Loom = {
      id: "messy",
      name: "messy",
      nodes: [
        { id: "feed", type: "candle_feed", params: {} },
        { id: "sizer", type: "position_sizer", params: {} },
        { id: "risk", type: "risk_gate", params: {} },
        { id: "exec", type: "execute_order", params: {} },
      ],
      edges: [
        { from: "feed.out", to: "sizer.candle" },
        { from: "sizer.sized", to: "risk.signal" },
        { from: "risk.stamped", to: "exec.signal" },
      ],
      meta: { positions: {
        feed: { x: 900, y: 500 },
        sizer: { x: 40, y: 500 },
        risk: { x: 40, y: 650 },
        exec: { x: 40, y: 800 },
      } },
    };

    const positions = layoutLoomPositions(messy);

    expect(positions.sizer.x).toBeGreaterThan(positions.feed.x);
    expect(positions.risk.x).toBeGreaterThan(positions.sizer.x);
    expect(positions.exec.x).toBeGreaterThan(positions.risk.x);
  });
  it("validates manual canvas connections before adding edges", () => {
    const { nodes, edges } = loomToFlow(loom, defs);

    expect(validateFlowConnection(nodes, [], {
      source: "feed", sourceHandle: "out:out",
      target: "ema1", targetHandle: "in:candle",
    })).toMatchObject({ ok: true, sourceType: "candle", targetType: "candle" });

    expect(validateFlowConnection(nodes, [], {
      source: "ema1", sourceHandle: "out:value",
      target: "ema1", targetHandle: "in:candle",
    })).toMatchObject({ ok: false });

    expect(validateFlowConnection(nodes, [], {
      source: "ema1", sourceHandle: "out:value",
      target: "feed", targetHandle: "in:out",
    })).toMatchObject({ ok: false, reason: "Unknown source or target port." });

    expect(validateFlowConnection(nodes, [edges[0]], {
      source: "feed", sourceHandle: "out:out",
      target: "ema1", targetHandle: "in:candle",
    })).toMatchObject({ ok: false });
  });
});
