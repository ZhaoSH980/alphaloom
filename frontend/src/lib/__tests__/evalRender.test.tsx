// frontend/src/lib/__tests__/evalRender.test.tsx —— Eval 组件诚实渲染分支（D4-T7）
// 用 react-dom/client 在 jsdom 里客户端渲染（无新依赖；useLang 的 useSyncExternalStore
// 需真实客户端渲染）断言关键诚实分支：负护栏值、缺证据、负净利、蓝图垫底如实呈现。
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import AblationTable from "../../components/AblationTable";
import ScorecardPanel from "../../components/ScorecardPanel";
import LeaderboardTable from "../../components/LeaderboardTable";
import FidelityLadder from "../../components/FidelityLadder";
import { setLang } from "../i18n";
import type { AblationReport, Scorecard, Board, LadderReport } from "../eval";

// React 18 act() 支持标志：告知 React 处于测试环境（消 "not configured to support act" 警告）。
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

// 断言英文文案——强制 en（默认 zh 会渲染中文角标，断言失配）。
beforeEach(() => { setLang("en"); });

function render(el: ReactElement): string {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(el); });
  return container.innerHTML;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null; root = null;
});

// ── 消融表：负护栏值（护栏帮倒忙）如实展示，不藏 ─────────────────────────
describe("AblationTable honest rendering", () => {
  it("renders a NEGATIVE guardrail delta as-is (guardrail hurt, not hidden)", () => {
    const report: AblationReport = {
      arms: [
        { arm: "full", run_id: "r1", blueprint_id: "bp", bars: 20,
          summary: { net_pnl: -510.14, return_pct: -5.1, max_drawdown: 0.12 },
          num_vetoes: 3, num_trades: 2, verdict_counts: {} },
        { arm: "no_risk_officer", run_id: "r2", blueprint_id: "bp", bars: 20,
          summary: { net_pnl: 4243.8, return_pct: 42.4, max_drawdown: 0.2 },
          num_vetoes: 0, num_trades: 5, verdict_counts: {} },
      ],
      guardrail_value: {
        arms_compared: ["full", "no_risk_officer"],
        net_pnl_full: -510.14, net_pnl_no_risk_officer: 4243.8,
        net_pnl_delta: -4753.94, return_pct_delta: -47.5, max_drawdown_delta: -0.08,
        num_vetoes_full: 3, num_trades_full: 2, num_trades_no_risk_officer: 5,
        guardrail_helped: false, note: "computed from paired runs; sign is as-is",
      },
    };
    const out = render(createElement(AblationTable, { report }));
    expect(out).toContain("-4753.94");           // 负 delta 数值原样
    expect(out.toLowerCase()).toContain("hurt");  // guardrail hurt 标签（en）——不美化为 helped
    expect(out.toLowerCase()).not.toContain("helped");
  });

  it("shows guardrail_value missing when a comparison arm is absent", () => {
    const report: AblationReport = {
      arms: [{ arm: "full", run_id: "r1", blueprint_id: "bp", bars: 10,
               summary: { net_pnl: 5 }, num_vetoes: 0, num_trades: 1, verdict_counts: {} }],
      guardrail_value: null,
    };
    const out = render(createElement(AblationTable, { report }));
    expect(out.toLowerCase()).toContain("missing");
  });
});

// ── 记分卡：缺证据如实标注（红叉），不假装满分 ──────────────────────────
describe("ScorecardPanel honest rendering", () => {
  it("renders missing-evidence dimensions with a cross (not a checkmark)", () => {
    const card: Scorecard = {
      composite: 36.25,
      components: { valid_performance: 25, generalization: 25, fidelity: 100, determinism: 100 },
      weights: { valid_performance: 0.4, generalization: 0.25, fidelity: 0.2, determinism: 0.15 },
      evidence_coverage: { valid_window: false, fidelity_ladder: true, cost_certificate: true,
        ablation: false, trading_activity: false, ratio: 0.4 },
      generalization_gap: null, in_sample_only: true,
      train_summary: { num_trades: 0 }, valid_summary: null, fidelity: null, cost: null,
      ablation: null,
      notes: ["zero trades in ranking window: treated as missing evidence"],
    };
    const out = render(createElement(ScorecardPanel, { card }));
    expect(out).toContain("✗");                 // 缺证据红叉存在
    expect(out).toContain("valid window");      // 缺失的 valid_window 维度被展示
    expect(out).toContain("36.3");              // 综合分低分如实（36.25→.toFixed(1)，不封满）
    expect(out).toContain("in-sample only");    // in_sample_only 角标
  });
});

// ── 排行榜：蓝图垫底 + 运气基线角标 + 泛化差距列 ────────────────────────
describe("LeaderboardTable honest rendering", () => {
  it("renders blueprint below a winning baseline (loser shown, not hidden)", () => {
    const board: Board = {
      sort_key: "return_pct", ranking_window: "valid_first",
      rows: [
        { name: "baseline_buy_hold", kind: "baseline", net_pnl: 800, return_pct: 8.0,
          max_dd: 0.1, win_rate: 1.0, num_trades: 1, generalization_gap: null,
          in_sample_only: false },
        { name: "my_blueprint", kind: "blueprint", net_pnl: -120, return_pct: -1.2,
          max_dd: 0.3, win_rate: 0.4, num_trades: 5, generalization_gap: 12.5,
          in_sample_only: false },
        { name: "baseline_random", kind: "baseline", net_pnl: -300, return_pct: -3.0,
          max_dd: 0.4, win_rate: 0.3, num_trades: 8, generalization_gap: null,
          in_sample_only: false },
      ],
    };
    const out = render(createElement(LeaderboardTable, { board }));
    const bpIdx = out.indexOf("my_blueprint");
    const bhIdx = out.indexOf("baseline_buy_hold");
    expect(bpIdx).toBeGreaterThan(bhIdx);       // 蓝图排在 buy_hold 之后（垫底如实）
    expect(out.toLowerCase()).toContain("luck"); // 运气基线角标
    expect(out).toContain("12.5");               // 泛化差距列
  });
});

// ── 保真度阶梯：负净利如实向下（不美化） ────────────────────────────────
describe("FidelityLadder honest rendering", () => {
  it("renders a negative L3 net_pnl and the optimism gap", () => {
    const report: LadderReport = {
      levels: [
        { level: "L0", net_pnl: 200, max_dd: 0.05, num_trades: 3, profit_factor: 2.0 },
        { level: "L1", net_pnl: 150, max_dd: 0.06, num_trades: 3, profit_factor: 1.8 },
        { level: "L2", net_pnl: 20, max_dd: 0.1, num_trades: 3, profit_factor: 1.1 },
        { level: "L3", net_pnl: -80, max_dd: 0.15, num_trades: 3, profit_factor: null },
      ],
      optimism_gap: 280,
    };
    const out = render(createElement(FidelityLadder, { report }));
    expect(out).toContain("-80");                // L3 负净利数值原样
    expect(out).toContain("280");                // 乐观差距
    expect(out).toContain("text-loom-red");      // 负值红色（诚实上色）
  });
});
