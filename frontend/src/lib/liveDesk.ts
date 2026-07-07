import type { Candle } from "../components/CandleChart";
import type { TraceRow } from "./insights";
import {
  isGateProtocol,
  type GateProtocolCard,
  type LocalizedText,
  type Loom,
} from "./loom";

export type LiveStageState = "waiting" | "seen" | "active";

export interface LiveStageSnapshot {
  id: string;
  title: LocalizedText;
  body: LocalizedText;
  tone: GateProtocolCard["tone"];
  nodeIds: string[];
  state: LiveStageState;
  lastEventIdx?: number;
}

export function currentLiveCandle(candles: Candle[], cursor?: number): Candle | null {
  if (!candles.length || cursor === undefined || cursor < 0) return null;
  return candles[Math.min(cursor, candles.length - 1)] ?? null;
}

export function buildLiveStageSnapshots({
  loom,
  activeNodeIds,
  traceRows,
}: {
  loom: Loom;
  activeNodeIds: string[];
  traceRows: TraceRow[];
}): LiveStageSnapshot[] {
  const validNodeIds = new Set(loom.nodes.map((node) => node.id));
  const active = new Set(activeNodeIds);
  const seen = new Map<string, number>();
  for (const row of traceRows) {
    if (!validNodeIds.has(row.node_id)) continue;
    seen.set(row.node_id, Math.max(seen.get(row.node_id) ?? -1, row.event_idx));
  }

  return liveStageCards(loom).map((card) => {
    const nodeIds = Array.isArray(card.nodes)
      ? card.nodes.filter((id): id is string => typeof id === "string" && validNodeIds.has(id))
      : [];
    const activeHere = nodeIds.some((id) => active.has(id));
    const seenEventIdx = Math.max(-1, ...nodeIds.map((id) => seen.get(id) ?? -1));
    return {
      id: card.id,
      title: card.title,
      body: card.body,
      tone: card.tone,
      nodeIds,
      state: activeHere ? "active" : seenEventIdx >= 0 ? "seen" : "waiting",
      ...(seenEventIdx >= 0 ? { lastEventIdx: seenEventIdx } : {}),
    };
  });
}

function liveStageCards(loom: Loom): GateProtocolCard[] {
  const protocol = loom.meta?.gateProtocol;
  if (isGateProtocol(protocol) && protocol.steps?.length) return protocol.steps;

  const groups = [
    { id: "data", title: "Market input", body: "candles and raw features", tone: "data" as const },
    { id: "decision", title: "Signal logic", body: "strategy and agent decisions", tone: "stage" as const },
    { id: "risk", title: "Risk gate", body: "position and stop contracts", tone: "risk" as const },
    { id: "execution", title: "Execution", body: "orders only after risk stamp", tone: "exec" as const },
    { id: "reflection", title: "Reflection", body: "replay evidence and lessons", tone: "loop" as const },
  ];

  return groups
    .map((group) => ({
      ...group,
      nodes: loom.nodes
        .filter((node) => node.type.includes(group.id) || categoryHint(node.type) === group.id)
        .map((node) => node.id),
    }))
    .filter((group) => group.nodes.length > 0);
}

function categoryHint(type: string): string {
  if (type.includes("feed") || type.includes("candle")) return "data";
  if (type.includes("risk") || type.includes("sizer") || type.includes("kill")) return "risk";
  if (type.includes("execute") || type.includes("broker")) return "execution";
  if (type.includes("reflect") || type.includes("experience")) return "reflection";
  return "decision";
}
