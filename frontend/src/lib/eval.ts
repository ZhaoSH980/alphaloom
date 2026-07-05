// frontend/src/lib/eval.ts —— Eval/Evolve 端点响应的 TS 类型 + 纯解析辅助（D4-T7）
//
// 每个类型逐字段镜像后端 to_dict()（backend/alphaloom/eval/*.py + evolve/lab.py）——
// 字段名对不上就是纸面功能（D3-T10 教训）。诚实要求：负值/缺证据/垫底原样承载，
// 不在类型层美化。纯解析函数无副作用，可单测。

// ── 保真度阶梯 fidelity.py::LadderReport.to_dict ──────────────────────────
// { levels: [{level, net_pnl, max_dd, num_trades, profit_factor(inf→null)}],
//   optimism_gap: L0_pnl - L3_pnl }
export interface LadderLevel {
  level: string;               // "L0" | "L1" | "L2" | "L3"
  net_pnl: number;
  max_dd: number;
  num_trades: number;
  profit_factor: number | null;  // inf → null（to_dict 已转）
}
export interface LadderReport {
  levels: LadderLevel[];
  optimism_gap: number;        // ≥0；越大回测越乐观
}

// ── 基线排行榜 leaderboard.py::Board.to_dict ─────────────────────────────
// { rows: [{name, kind, net_pnl, return_pct, max_dd, win_rate, num_trades,
//           generalization_gap(null), in_sample_only}], sort_key, ranking_window }
export interface BoardRow {
  name: string;
  kind: string;                // "blueprint" | "baseline"
  net_pnl: number;
  return_pct: number;
  max_dd: number;
  win_rate: number;
  num_trades: number;
  generalization_gap: number | null;  // train - valid（无 valid = null）
  in_sample_only: boolean;
}
export interface Board {
  rows: BoardRow[];            // 已按排序窗 return_pct 降序
  sort_key: string;
  ranking_window: string;      // "valid_first"
}

// ── 委员会消融 ablation.py::AblationReport.to_dict ───────────────────────
export interface ArmResult {
  arm: string;                 // "full" | "no_risk_officer" | "no_rag"
  run_id: string;
  blueprint_id: string;
  bars: number;
  summary: Record<string, unknown>;   // net_pnl/return_pct/max_drawdown/... (BacktestReport summary)
  num_vetoes: number;
  num_trades: number;
  verdict_counts: Record<string, number>;
}
export interface GuardrailValue {
  arms_compared: string[];
  net_pnl_full: number;
  net_pnl_no_risk_officer: number;
  net_pnl_delta: number;       // full - no_risk_officer（正=护栏帮忙、负=帮倒忙，原样）
  return_pct_delta: number;
  max_drawdown_delta: number;
  num_vetoes_full: number;
  num_trades_full: number;
  num_trades_no_risk_officer: number;
  guardrail_helped: boolean;   // delta>0（负也如实展示）
  note: string;
}
export interface AblationReport {
  arms: ArmResult[];
  guardrail_value: GuardrailValue | null;   // 缺对照臂 = null
}

// ── 蓝图记分卡 scorecard.py::Scorecard.to_dict ───────────────────────────
export interface Scorecard {
  composite: number;           // 0-100 综合分
  components: Record<string, number>;   // valid_performance/generalization/fidelity/determinism
  weights: Record<string, number>;
  evidence_coverage: Record<string, boolean | number>;  // 各维度 bool + "ratio": 0-1
  generalization_gap: number | null;
  in_sample_only: boolean;
  train_summary: Record<string, unknown>;
  valid_summary: Record<string, unknown> | null;
  fidelity: LadderReport | null;
  cost: Record<string, unknown> | null;
  ablation: Record<string, unknown> | null;
  notes: string[];
}

// ── 进化谱系 lab.py::Genealogy.to_dict ───────────────────────────────────
export type CompileStatus = "ok" | "repaired" | "stillborn" | "runtime_error";
export interface GenealogyNode {
  id: string;
  gen: number;
  parent_id: string | null;
  mutation_summary: string;
  fitness: number | null;      // train 适应度；stillborn/runtime_error = null
  compile_status: CompileStatus;
  blueprint_json: Record<string, unknown> | null;   // loom dict（可直接开画布）
  survived: boolean;
  error: string | null;        // runtime_error 的错误摘要
}
export interface GenealogyWinner {
  id: string;
  train_fitness: number | null;
  valid_fitness: number | null;
  generalization_gap: number | null;
  train_summary: Record<string, unknown>;
  valid_summary: Record<string, unknown>;
}
export interface Genealogy {
  nodes: GenealogyNode[];
  winner: GenealogyWinner | Record<string, never>;   // 空 dict 兜底
  param_only: boolean;
  population: number;
  generations: number;
}

// ─────────────────────────────────────────────────────────────────────────
// 委员会角色推断（T4 审查遗留必修）
// ─────────────────────────────────────────────────────────────────────────
// 后端 committee_trace 是 list[dict]，元素为原始 LLM JSON、**不带 role 键**
// （llm_nodes.py: trace = [strategist_json] + [risk_json?] + [chair_json]）。
// 原 Terminal.tsx 按下标取 ROLE_LABELS[idx]——no_risk_officer 消融臂 trace 只有
// [策略师, 主席] 两项，会把主席误标成 "risk officer"。
//
// 修法（纯前端可靠推断，无需后端加字段）：
//  - 位置 0：策略师（strategist）——恒为首元素（含 side 提案）。
//  - 末元素：主席（chair）——恒为终案（含 side）。
//  - 中间元素：风控官（risk officer）——唯一带 `veto` 键的角色，仅在未消融时存在。
// veto 键是风控官的无歧义指纹（策略师/主席从不产 veto）；即便有人重排，带 veto 者
// 即风控官。full 臂 → [strategist, risk, chair]；no_risk_officer 臂 → [strategist, chair]。
export type CommitteeRoleLabel = "strategist" | "risk officer" | "chair" | string;

export function inferCommitteeRole(
  trace: ReadonlyArray<Record<string, unknown>>, idx: number,
): CommitteeRoleLabel {
  const n = trace.length;
  const el = trace[idx];
  // 风控官指纹：带 veto 键（策略师/主席不产 veto）——最强判据，优先。
  if (el && typeof el === "object" && "veto" in el) return "risk officer";
  if (idx === 0) return "strategist";        // 首元素恒策略师
  if (idx === n - 1) return "chair";         // 末元素恒主席
  return "risk officer";                     // 中间非首非末：风控官（无 veto 键的兜底）
}
