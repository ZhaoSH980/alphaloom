// frontend/src/lib/__tests__/eval.test.ts —— Eval 解析/布局纯函数单测（D4-T7）
import { describe, expect, it } from "vitest";
import { inferCommitteeRole } from "../eval";
import { genealogyToFlow } from "../../components/GenealogyTree";
import type { Genealogy, GenealogyNode } from "../eval";

// ── 委员会角色推断（T4 审查遗留必修：不按下标，按形状）──────────────────────
describe("inferCommitteeRole", () => {
  // 后端 committee_trace 元素不带 role 键——按形状/位置推断。
  const strat = { side: "long", rationale: "momentum", confidence: 0.9 };
  const risk = { veto: false, concern: "watch atr", confidence: 0.6 };
  const chair = { side: "long", rationale: "confirmed", confidence: 0.7 };

  it("full arm [strategist, risk, chair] labels all three correctly", () => {
    const trace = [strat, risk, chair];
    expect(inferCommitteeRole(trace, 0)).toBe("strategist");
    expect(inferCommitteeRole(trace, 1)).toBe("risk officer");
    expect(inferCommitteeRole(trace, 2)).toBe("chair");
  });

  it("no_risk_officer arm [strategist, chair] does NOT mislabel chair as risk officer", () => {
    // 这是原 ROLE_LABELS[idx] 的 bug：idx=1 → "risk officer"，但此臂 idx=1 是主席。
    const trace = [strat, chair];
    expect(inferCommitteeRole(trace, 0)).toBe("strategist");
    expect(inferCommitteeRole(trace, 1)).toBe("chair");   // ← 修复前会是 "risk officer"
  });

  it("uses the veto key as the unambiguous risk-officer fingerprint", () => {
    // 即便风控官带 veto:true（真否决），仍应识别为 risk officer（而非因位置误判）。
    const vetoRisk = { veto: true, concern: "bubble", confidence: 0.2 };
    const trace = [strat, vetoRisk, chair];
    expect(inferCommitteeRole(trace, 1)).toBe("risk officer");
  });
});

// ── 谱系树 → React Flow 布局（gen 分层 / parent 连边 / winner 标记）─────────
describe("genealogyToFlow", () => {
  const mk = (over: Partial<GenealogyNode>): GenealogyNode => ({
    id: "n", gen: 0, parent_id: null, mutation_summary: "", fitness: 0,
    compile_status: "ok", blueprint_json: null, survived: false, error: null, ...over });

  const g: Genealogy = {
    nodes: [
      mk({ id: "g0_seed", gen: 0, parent_id: null, fitness: 1.0, survived: true }),
      mk({ id: "g1_c0", gen: 1, parent_id: "g0_seed", fitness: 2.0, survived: true }),
      mk({ id: "g1_c1", gen: 1, parent_id: "g0_seed", compile_status: "stillborn",
           fitness: null, blueprint_json: null }),
      mk({ id: "g1_c2", gen: 1, parent_id: "g0_seed", compile_status: "runtime_error",
           fitness: null, error: "ValueError: bad period" }),
    ],
    winner: { id: "g1_c0", train_fitness: 2.0, valid_fitness: 0.5,
              generalization_gap: 1.5, train_summary: {}, valid_summary: { num_trades: 3 } },
    param_only: true, population: 4, generations: 3,
  };

  it("lays nodes by gen on the x axis and marks the winner", () => {
    const { nodes } = genealogyToFlow(g, "g1_c0");
    expect(nodes).toHaveLength(4);
    const seed = nodes.find((n) => n.id === "g0_seed")!;
    const child = nodes.find((n) => n.id === "g1_c0")!;
    expect(child.position.x).toBeGreaterThan(seed.position.x);   // gen 1 在 gen 0 右侧
    expect((child.data as { isWinner: boolean }).isWinner).toBe(true);
    expect((seed.data as { isWinner: boolean }).isWinner).toBe(false);
  });

  it("connects each child to its parent via parent_id", () => {
    const { edges } = genealogyToFlow(g, "g1_c0");
    // 三个孩子都连回 seed（含 stillborn / runtime_error——失败态也留在谱系里）。
    expect(edges).toHaveLength(3);
    expect(edges.every((e) => e.source === "g0_seed")).toBe(true);
    expect(edges.map((e) => e.target).sort()).toEqual(["g1_c0", "g1_c1", "g1_c2"]);
  });

  it("keeps stillborn and runtime_error nodes (failures not hidden)", () => {
    const { nodes } = genealogyToFlow(g, "g1_c0");
    const statuses = nodes.map((n) => (n.data as { gnode: GenealogyNode }).gnode.compile_status);
    expect(statuses).toContain("stillborn");
    expect(statuses).toContain("runtime_error");
  });
});
