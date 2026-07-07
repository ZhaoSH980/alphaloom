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
export type LocalizedText = string | { en?: string; zh?: string };
export type GateProtocolTone = "data" | "stage" | "gate" | "risk" | "exec" | "fail" | "loop";
export interface GateProtocolCard {
  id: string;
  tone?: GateProtocolTone;
  eyebrow?: LocalizedText;
  title: LocalizedText;
  body: LocalizedText;
  nodes?: string[];
}
export interface GateProtocol {
  title?: LocalizedText;
  subtitle?: LocalizedText;
  evidence?: LocalizedText;
  steps?: GateProtocolCard[];
  sidecars?: GateProtocolCard[];
  invariant?: LocalizedText;
}

export function isGateProtocol(value: unknown): value is GateProtocol {
  return !!value && typeof value === "object" && Array.isArray((value as GateProtocol).steps);
}

export function localize(value: LocalizedText | undefined, lang: "zh" | "en", fallback = ""): string {
  if (typeof value === "string") return value;
  if (!value) return fallback;
  return value[lang] ?? value.en ?? value.zh ?? fallback;
}

function pruneGateProtocolNodeRefs(
  meta: Record<string, unknown>,
  validNodeIds: Set<string>,
): Record<string, unknown> {
  const protocol = meta.gateProtocol;
  if (!isGateProtocol(protocol)) return meta;
  const pruneCards = (cards: unknown) => Array.isArray(cards)
    ? cards.map((card) => {
        if (!card || typeof card !== "object") return card;
        const nodes = Array.isArray((card as GateProtocolCard).nodes)
          ? (card as GateProtocolCard).nodes!.filter((id) => validNodeIds.has(id))
          : (card as GateProtocolCard).nodes;
        return { ...card, ...(Array.isArray((card as GateProtocolCard).nodes) ? { nodes } : {}) };
      })
    : cards;
  return {
    ...meta,
    gateProtocol: {
      ...protocol,
      steps: pruneCards(protocol.steps),
      ...(protocol.sidecars === undefined ? {} : { sidecars: pruneCards(protocol.sidecars) }),
    },
  };
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

export interface ConnectionLike {
  source?: string | null;
  sourceHandle?: string | null;
  target?: string | null;
  targetHandle?: string | null;
}

export interface EdgeLike {
  source: string;
  sourceHandle?: string | null;
  target: string;
  targetHandle?: string | null;
}

export interface ConnectionValidation {
  ok: boolean;
  reason?: string;
  sourceType?: PinType;
  targetType?: PinType;
}

const GRID_X = 260, GRID_Y = 150;

export function unknownNodeDef(type: string): NodeDef {
  return {
    type,
    category: "unknown",
    inputs: {},
    outputs: {},
    params: {},
    cost: { unknown: true },
  };
}

function nodeIdOf(ref: string): string {
  return ref.split(".")[0];
}

export function layoutLoomPositions(loom: Pick<Loom, "nodes" | "edges">):
    Record<string, { x: number; y: number }> {
  const ids = loom.nodes.map((n) => n.id);
  const known = new Set(ids);
  const indegree = new Map(ids.map((id) => [id, 0]));
  const outgoing = new Map(ids.map((id) => [id, [] as string[]]));
  const depth = new Map(ids.map((id) => [id, 0]));

  for (const edge of loom.edges) {
    if (edge.feedback) continue;
    const source = nodeIdOf(edge.from);
    const target = nodeIdOf(edge.to);
    if (!known.has(source) || !known.has(target) || source === target) continue;
    outgoing.get(source)!.push(target);
    indegree.set(target, (indegree.get(target) ?? 0) + 1);
  }

  const queue = ids.filter((id) => (indegree.get(id) ?? 0) === 0);
  for (let i = 0; i < queue.length; i += 1) {
    const source = queue[i];
    const sourceDepth = depth.get(source) ?? 0;
    for (const target of outgoing.get(source) ?? []) {
      depth.set(target, Math.max(depth.get(target) ?? 0, sourceDepth + 1));
      indegree.set(target, (indegree.get(target) ?? 0) - 1);
      if ((indegree.get(target) ?? 0) === 0) queue.push(target);
    }
  }

  const rowByCol = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  for (const id of ids) {
    const col = depth.get(id) ?? 0;
    const row = rowByCol.get(col) ?? 0;
    rowByCol.set(col, row + 1);
    positions[id] = { x: 40 + col * GRID_X, y: 40 + row * GRID_Y };
  }
  return positions;
}

export function loomToFlow(loom: Loom, defs: Record<string, NodeDef>):
    { nodes: FlowNode[]; edges: FlowEdge[] } {
  const pos = (loom.meta?.positions ?? {}) as Record<string, { x: number; y: number }>;
  const fallback = layoutLoomPositions(loom);
  const nodes: FlowNode[] = loom.nodes.map((n, i) => ({
    id: n.id, type: "loomNode",
    position: pos[n.id] ?? fallback[n.id] ?? { x: i * GRID_X + 40, y: 40 },
    data: { def: defs[n.type] ?? unknownNodeDef(n.type), params: n.params },
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
  const meta = pruneGateProtocolNodeRefs(base.meta, new Set(nodes.map((n) => n.id)));
  return {
    ...base,
    nodes: nodes.map((n) => ({ id: n.id, type: n.data.def.type, params: n.data.params })),
    edges: edges.map((e) => ({
      from: `${e.source}.${(e.sourceHandle ?? "").replace(/^out:/, "")}`,
      to: `${e.target}.${(e.targetHandle ?? "").replace(/^in:/, "")}`,
      ...(e.data?.feedback ? { feedback: true } : {}),
    })),
    meta: { ...meta, positions },
  };
}

function portFromHandle(handle: string | null | undefined, side: "in" | "out"): string | null {
  const prefix = `${side}:`;
  if (!handle?.startsWith(prefix)) return null;
  const port = handle.slice(prefix.length);
  return port || null;
}

export function pinTypeForHandle(
  nodes: Pick<FlowNode, "id" | "data">[],
  nodeId: string | null | undefined,
  handle: string | null | undefined,
  side: "in" | "out",
): PinType | undefined {
  if (!nodeId) return undefined;
  const port = portFromHandle(handle, side);
  if (!port) return undefined;
  const node = nodes.find((n) => n.id === nodeId);
  const pins = side === "out" ? node?.data.def.outputs : node?.data.def.inputs;
  return pins?.[port];
}

export function validateFlowConnection(
  nodes: Pick<FlowNode, "id" | "data">[],
  edges: EdgeLike[],
  c: ConnectionLike,
): ConnectionValidation {
  const { source, sourceHandle, target, targetHandle } = c;
  if (!source || !target || !sourceHandle || !targetHandle) {
    return { ok: false, reason: "Choose an output port and an input port." };
  }
  if (source === target) {
    return { ok: false, reason: "A node cannot connect to itself." };
  }
  const sourceType = pinTypeForHandle(nodes, source, sourceHandle, "out");
  const targetType = pinTypeForHandle(nodes, target, targetHandle, "in");
  if (!sourceType || !targetType) {
    return { ok: false, reason: "Unknown source or target port." };
  }
  const targetTaken = edges.some((edge) =>
    edge.target === target && edge.targetHandle === targetHandle);
  if (targetTaken) {
    return { ok: false, sourceType, targetType,
      reason: `${target}.${targetHandle.replace(/^in:/, "")} already has an input.` };
  }
  if (sourceType !== targetType) {
    return { ok: false, sourceType, targetType,
      reason: `${sourceType} cannot connect to ${targetType}.` };
  }
  return { ok: true, sourceType, targetType };
}

export function nextNodeId(existing: Set<string>, type: string): string {
  let i = 1;
  while (existing.has(`${type}_${i}`)) i += 1;
  return `${type}_${i}`;
}
