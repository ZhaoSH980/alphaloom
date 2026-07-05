// frontend/src/lib/loom.ts —— 锁定 TS 类型 + 映射 + 色板（契约区，见计划头）
export type PinType = "exec" | "candle" | "series" | "signal" | "risk_stamped_signal" | "bool";
export interface NodeDef {
  type: string; category: string;
  inputs: Record<string, PinType>; outputs: Record<string, PinType>;
  params: Record<string, string>; cost: Record<string, unknown>;
}
export interface LoomNode { id: string; type: string; params: Record<string, unknown>; }
export interface LoomEdge { from: string; to: string; feedback?: boolean; }
export interface Loom {
  id: string; name: string; nodes: LoomNode[]; edges: LoomEdge[];
  meta: Record<string, unknown>;
}

export const PIN_COLORS: Record<PinType, string> = {
  exec: "#e2e8f0", candle: "#38bdf8", series: "#a78bfa",
  signal: "#fbbf24", risk_stamped_signal: "#f59e0b", bool: "#34d399",
};
export const CATEGORY_COLORS: Record<string, string> = {
  data: "#0ea5e9", indicator: "#8b5cf6", decision: "#f59e0b",
  risk: "#ef4444", execution: "#22c55e", reflection: "#14b8a6",
};

export interface FlowNode {
  id: string; type: "loomNode"; position: { x: number; y: number };
  data: { def: NodeDef; params: Record<string, unknown> };
}
export interface FlowEdge {
  id: string; source: string; sourceHandle: string;
  target: string; targetHandle: string;
  data: { feedback: boolean };
}

const GRID_X = 260, GRID_Y = 150, COLS = 4;

export function loomToFlow(loom: Loom, defs: Record<string, NodeDef>):
    { nodes: FlowNode[]; edges: FlowEdge[] } {
  const pos = (loom.meta?.positions ?? {}) as Record<string, { x: number; y: number }>;
  const nodes: FlowNode[] = loom.nodes.map((n, i) => ({
    id: n.id, type: "loomNode",
    position: pos[n.id] ?? { x: (i % COLS) * GRID_X + 40, y: Math.floor(i / COLS) * GRID_Y + 40 },
    data: { def: defs[n.type], params: n.params },
  }));
  const edges: FlowEdge[] = loom.edges.map((e, i) => {
    const [sn, sp] = e.from.split(".");
    const [tn, tp] = e.to.split(".");
    return { id: `e${i}`, source: sn, sourceHandle: `out:${sp}`,
             target: tn, targetHandle: `in:${tp}`, data: { feedback: !!e.feedback } };
  });
  return { nodes, edges };
}

export function flowToLoom(nodes: FlowNode[], edges: FlowEdge[], base: Loom): Loom {
  const positions: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) positions[n.id] = { x: Math.round(n.position.x), y: Math.round(n.position.y) };
  return {
    ...base,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.def.type, params: n.data.params })),
    edges: edges.map((e) => ({
      from: `${e.source}.${(e.sourceHandle ?? "").replace(/^out:/, "")}`,
      to: `${e.target}.${(e.targetHandle ?? "").replace(/^in:/, "")}`,
      ...(e.data?.feedback ? { feedback: true } : {}),
    })),
    meta: { ...base.meta, positions },
  };
}

export function nextNodeId(existing: Set<string>, type: string): string {
  let i = 1;
  while (existing.has(`${type}_${i}`)) i += 1;
  return `${type}_${i}`;
}
