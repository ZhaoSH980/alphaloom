import { describe, expect, it } from "vitest";
import { buildLiveStageSnapshots, currentLiveCandle } from "../liveDesk";
import type { Candle } from "../../components/CandleChart";
import type { Loom } from "../loom";

const loom: Loom = {
  id: "agent_committee_v1",
  name: "Agent Committee",
  nodes: [
    { id: "feed", type: "candle_feed", params: {} },
    { id: "committee", type: "committee", params: {} },
    { id: "risk", type: "risk_gate", params: {} },
    { id: "exec", type: "execute_order", params: {} },
  ],
  edges: [],
  meta: {
    gateProtocol: {
      steps: [
        {
          id: "market",
          title: { en: "Market Snapshot", zh: "市场快照" },
          body: { en: "fresh candles", zh: "实时 K 线" },
          nodes: ["feed"],
        },
        {
          id: "diagnosis",
          title: { en: "Diagnosis", zh: "诊断" },
          body: { en: "committee decides", zh: "委员会判断" },
          nodes: ["committee", "missing_node"],
        },
        {
          id: "risk",
          title: { en: "RiskGate", zh: "风控门" },
          body: { en: "stamp order", zh: "订单盖章" },
          nodes: ["risk", "exec"],
        },
      ],
    },
  },
};

describe("live desk model", () => {
  it("maps a blueprint gate protocol into active and seen live stages", () => {
    const stages = buildLiveStageSnapshots({
      loom,
      activeNodeIds: ["committee"],
      traceRows: [
        { event_idx: 1, ts: 1000, node_id: "feed", outputs: {} },
        { event_idx: 2, ts: 2000, node_id: "committee", outputs: {} },
      ],
    });

    expect(stages.map((stage) => stage.id)).toEqual(["market", "diagnosis", "risk"]);
    expect(stages[0]).toMatchObject({ state: "seen", nodeIds: ["feed"] });
    expect(stages[1]).toMatchObject({ state: "active", nodeIds: ["committee"] });
    expect(stages[2]).toMatchObject({ state: "waiting", nodeIds: ["risk", "exec"] });
  });

  it("returns only the currently revealed candle while a run is streaming", () => {
    const candles: Candle[] = [
      { ts: 0, open: 10, high: 11, low: 9, close: 10.5, volume: 1 },
      { ts: 60_000, open: 10.5, high: 12, low: 10, close: 11.5, volume: 2 },
    ];

    expect(currentLiveCandle(candles, -1)).toBeNull();
    expect(currentLiveCandle(candles, 0)?.close).toBe(10.5);
    expect(currentLiveCandle(candles, 99)?.close).toBe(11.5);
  });
});
