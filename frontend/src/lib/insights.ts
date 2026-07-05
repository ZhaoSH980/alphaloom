// frontend/src/lib/insights.ts —— 从 run trace 解析 Agent 富信息（D3 Task 10）
// Terminal 只从 trace 端点读（getTrace(runId)），不依赖后端新增字段。
// 数据不存在时优雅返回空——调用方展示"此 run 无委员会/反思数据"。

/** 一条 trace 行（后端 /api/runs/{id}/trace 的元素）。 */
export interface TraceRow {
  event_idx: number;
  ts: number;
  node_id: string;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
}

/** 反思四象限判定（Reflector verdict 输出的载荷）。 */
export interface Verdict {
  verdict: string;         // reasonable_and_right | reasonable_but_wrong | lucky | bad_process
  bucket: string;          // 市场状态桶
  pnl: number;
  trade_key: string;
  config_summary?: string;
  lesson?: string;
}

/** 一个委员会节点在某根 bar 的三角色轨迹。 */
export interface CommitteeSnapshot {
  nodeId: string;
  eventIdx: number;
  side?: string;
  rationale?: string;
  confidence?: number;
  trace: string[];         // [strategist_json, risk_json, chair_json]
}

/** 整个 run 的 Agent 洞察汇总。 */
export interface RunInsights {
  committees: CommitteeSnapshot[];   // 每个 committee 节点最后一条快照
  verdicts: Verdict[];               // 所有平仓反思判定（按发生顺序）
  citations: string[];               // 去重后的全部引用来源
  memoryUsed: boolean;               // run 是否含 experience_retrieve（记忆开）
  hasAny: boolean;                   // 是否有任一富信息（否则展示"无"）
}

// trace 序列化把 SERIES 值包成 {as_of, value}；也可能直接是原值。解一层。
function unwrap(raw: unknown): unknown {
  if (raw && typeof raw === "object" && "value" in (raw as object) && "as_of" in (raw as object))
    return (raw as { value: unknown }).value;
  return raw;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

// 从一行 outputs 里找出含 signal 语义的值（committee/analyst/risk_gate 透传）。
function findSignal(outputs: Record<string, unknown>): Record<string, unknown> | null {
  for (const raw of Object.values(outputs)) {
    const v = asRecord(unwrap(raw));
    if (v && ("side" in v || "committee_trace" in v || "citations" in v || "rationale" in v))
      return v;
  }
  return null;
}

function toStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/**
 * 从整个 run 的 trace 行集合派生 Agent 洞察。纯函数、无副作用——可单测。
 * 优雅降级：无相关节点时 committees/verdicts/citations 空、memoryUsed=false、hasAny=false。
 */
export function parseInsights(rows: TraceRow[]): RunInsights {
  const committeeLatest = new Map<string, CommitteeSnapshot>();  // nodeId → 最后一条快照
  const verdicts: Verdict[] = [];
  const citationSet = new Set<string>();
  let memoryUsed = false;

  for (const row of rows) {
    const outputs = row.outputs ?? {};
    // 记忆使用：experience_retrieve 节点（按 id 约定或 lessons 输出）。
    if (row.node_id.startsWith("experience_retrieve") || "lessons" in outputs)
      memoryUsed = true;

    // 反思判定：verdict 输出，非平仓那根为 null → 跳过。
    if ("verdict" in outputs) {
      const v = asRecord(unwrap(outputs.verdict));
      if (v && typeof v.verdict === "string")
        verdicts.push({
          verdict: v.verdict,
          bucket: typeof v.bucket === "string" ? v.bucket : "?",
          pnl: typeof v.pnl === "number" ? v.pnl : 0,
          trade_key: typeof v.trade_key === "string" ? v.trade_key : "",
          config_summary: typeof v.config_summary === "string" ? v.config_summary : undefined,
          lesson: typeof v.lesson === "string" ? v.lesson : undefined,
        });
    }

    // signal 富字段：委员会轨迹 + 引用。
    const sig = findSignal(outputs);
    if (sig) {
      for (const c of toStringArray(sig.citations)) citationSet.add(c);
      const ctrace = toStringArray(sig.committee_trace);
      if (ctrace.length > 0)
        committeeLatest.set(row.node_id, {
          nodeId: row.node_id,
          eventIdx: row.event_idx,
          side: typeof sig.side === "string" ? sig.side : undefined,
          rationale: typeof sig.rationale === "string" ? sig.rationale : undefined,
          confidence: typeof sig.confidence === "number" ? sig.confidence : undefined,
          trace: ctrace,
        });
    }
  }

  const committees = [...committeeLatest.values()].sort((a, b) => a.nodeId.localeCompare(b.nodeId));
  const citations = [...citationSet];
  const hasAny =
    committees.length > 0 || verdicts.length > 0 || citations.length > 0 || memoryUsed;
  return { committees, verdicts, citations, memoryUsed, hasAny };
}

/** 四象限判定的展示元信息（颜色 + i18n key）。reasonable_but_wrong 招牌高亮。 */
export const VERDICT_META: Record<
  string,
  { cls: string; labelKey: "verdictReasonableRight" | "verdictReasonableWrong"
    | "verdictLucky" | "verdictBadProcess"; signature?: boolean }
> = {
  reasonable_and_right: { cls: "bg-loom-green/20 text-loom-green", labelKey: "verdictReasonableRight" },
  reasonable_but_wrong: {
    cls: "bg-loom-amber/25 text-loom-amber ring-1 ring-loom-amber/60",
    labelKey: "verdictReasonableWrong", signature: true,
  },
  lucky: { cls: "bg-loom-blue/20 text-loom-blue", labelKey: "verdictLucky" },
  bad_process: { cls: "bg-loom-red/20 text-loom-red", labelKey: "verdictBadProcess" },
};
