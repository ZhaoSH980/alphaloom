// frontend/src/lib/__tests__/insights.test.ts
import { describe, expect, it } from "vitest";
import { parseInsights, VERDICT_META, type TraceRow } from "../insights";

// trace 序列化把 SERIES 值包成 {as_of, value}——测试用真实形状。
const wrap = (value: unknown) => ({ as_of: 1_700_000_000_000, value });

describe("parseInsights", () => {
  it("returns empty (hasAny=false) for a plain ema/breakout run with no LLM nodes", () => {
    const rows: TraceRow[] = [
      { event_idx: 0, ts: 1, node_id: "ema_1", outputs: { value: wrap(42.5) } },
      { event_idx: 0, ts: 1, node_id: "risk_gate_1", outputs: { out: wrap({ side: "hold" }) } },
    ];
    const r = parseInsights(rows);
    expect(r.hasAny).toBe(false);
    expect(r.committees).toHaveLength(0);
    expect(r.verdicts).toHaveLength(0);
    expect(r.citations).toHaveLength(0);
    expect(r.memoryUsed).toBe(false);
  });

  it("extracts committee trace, citations, verdicts and memory flag from a rich run", () => {
    // committee_trace 是后端真实形状 list[dict]（llm_nodes.py:257 `[strat_json, risk_json,
    // chair_json]`——解析后的角色 JSON 对象，非字符串）：
    //   策略师 {side, rationale, confidence} / 风控官 {veto, concern, confidence} /
    //   主席 {side, rationale, confidence}。
    const strat = { side: "long", rationale: "breakout", confidence: 0.8 };
    const risk = { veto: false, concern: "watch atr", confidence: 0.6 };
    const chair = { side: "long", rationale: "confirmed", confidence: 0.7 };
    const rows: TraceRow[] = [
      // 委员会节点：signal 输出含 committee_trace（早/晚各一条 → 取最后一条）。
      { event_idx: 0, ts: 1, node_id: "committee_1",
        outputs: { signal: wrap({ side: "hold", committee_trace: [
          { side: "hold", rationale: "flat", confidence: 0.3 },
          { veto: true, concern: "too choppy", confidence: 0.6 },
          { side: "hold", rationale: "stand down", confidence: 0.3 }] }) } },
      { event_idx: 5, ts: 6, node_id: "committee_1",
        outputs: { signal: wrap({ side: "long", confidence: 0.7, rationale: "trend up",
          committee_trace: [strat, risk, chair], citations: ["grid.md#1"] }) } },
      // 记忆节点：experience_retrieve 产 lessons。
      { event_idx: 5, ts: 6, node_id: "experience_retrieve_1", outputs: { lessons: wrap(["lesson a"]) } },
      // 反思节点：平仓那根产 verdict（非平仓根为 null → 应被跳过）。
      { event_idx: 4, ts: 5, node_id: "reflector_1", outputs: { verdict: wrap(null) } },
      { event_idx: 8, ts: 9, node_id: "reflector_1",
        outputs: { verdict: wrap({ verdict: "reasonable_but_wrong", bucket: "trend_up",
          pnl: -12.5, trade_key: "0:9:long", lesson: "process fine, don't over-correct" }) } },
    ];
    const r = parseInsights(rows);
    expect(r.hasAny).toBe(true);
    expect(r.memoryUsed).toBe(true);
    // 委员会取最后一条快照——trace 是解析出的三个角色对象（dict），不是字符串。
    expect(r.committees).toHaveLength(1);
    expect(r.committees[0].trace).toEqual([strat, risk, chair]);
    expect(r.committees[0].trace).toHaveLength(3);
    expect(r.committees[0].trace[0]).toMatchObject({ side: "long", confidence: 0.8 });
    expect(r.committees[0].trace[1]).toMatchObject({ veto: false, concern: "watch atr" });
    expect(r.committees[0].side).toBe("long");
    expect(r.committees[0].confidence).toBe(0.7);
    // 引用去重聚合。
    expect(r.citations).toEqual(["grid.md#1"]);
    // 只保留非 null 的 verdict。
    expect(r.verdicts).toHaveLength(1);
    expect(r.verdicts[0].verdict).toBe("reasonable_but_wrong");
    expect(r.verdicts[0].pnl).toBe(-12.5);
    // reasonable_but_wrong 招牌高亮元信息。
    expect(VERDICT_META.reasonable_but_wrong.signature).toBe(true);
  });

  it("detects memory via node_id convention even without a lessons key", () => {
    const rows: TraceRow[] = [
      { event_idx: 0, ts: 1, node_id: "experience_retrieve_2", outputs: {} },
    ];
    expect(parseInsights(rows).memoryUsed).toBe(true);
  });
});
