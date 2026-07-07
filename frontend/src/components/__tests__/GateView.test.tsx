import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import GateView from "../GateView";
import { setLang } from "../../lib/i18n";
import type { Loom } from "../../lib/loom";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function render(el: ReactElement): string {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(el); });
  return container.innerHTML;
}

beforeEach(() => { setLang("en"); });

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
});

describe("GateView", () => {
  it("renders the two-stage gate protocol instead of implementation node names", () => {
    const out = render(createElement(GateView));

    expect(out).toContain("Stage 1");
    expect(out).toContain("Diagnosis Gate");
    expect(out).toContain("Stage 2");
    expect(out).toContain("Order Validity Gate");
    expect(out).toContain("RiskGate Stamp");
    expect(out).toContain("Replay + Eval + Reflection");
    expect(out).not.toContain("ema");
    expect(out).not.toContain("atr");
  });

  it("renders a blueprint gateProtocol and focuses mapped graph nodes when clicked", () => {
    const focused: string[][] = [];
    const loom: Loom = {
      id: "committee_gate",
      name: "Committee Gate",
      nodes: [
        { id: "committee", type: "committee", params: {} },
        { id: "cite_gate", type: "require_citations", params: {} },
      ],
      edges: [],
      meta: {
        gateProtocol: {
          title: { en: "Live gate protocol", zh: "Live gate protocol" },
          subtitle: { en: "Rendered from loom metadata", zh: "Rendered from loom metadata" },
          evidence: { en: "bars + citations", zh: "bars + citations" },
          steps: [
            {
              id: "stage1",
              tone: "stage",
              eyebrow: { en: "Stage 1", zh: "Stage 1" },
              title: { en: "LLM Diagnosis", zh: "LLM Diagnosis" },
              body: { en: "committee reads market context", zh: "committee reads market context" },
              nodes: ["committee"],
            },
            {
              id: "gate1",
              tone: "gate",
              eyebrow: { en: "Gate 1", zh: "Gate 1" },
              title: { en: "Citation Gate", zh: "Citation Gate" },
              body: { en: "requires explicit evidence", zh: "requires explicit evidence" },
              nodes: ["cite_gate"],
            },
          ],
          sidecars: [],
          invariant: { en: "Only mapped nodes are focused.", zh: "Only mapped nodes are focused." },
        },
      },
    };

    render(createElement(GateView, { loom, onFocusNodes: (ids) => focused.push(ids) }));

    expect(container!.innerHTML).toContain("Live gate protocol");
    expect(container!.innerHTML).toContain("LLM Diagnosis");
    expect(container!.innerHTML).toContain("committee");
    expect(container!.innerHTML).toContain("Citation Gate");

    const button = Array.from(container!.querySelectorAll("button"))
      .find((el) => el.textContent?.includes("Citation Gate"));
    expect(button).toBeTruthy();
    act(() => { button!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    expect(focused).toEqual([["cite_gate"]]);
  });

  it("ignores gateProtocol node references that are not present in the blueprint", () => {
    const focused: string[][] = [];
    const loom: Loom = {
      id: "stale_protocol",
      name: "Stale Protocol",
      nodes: [{ id: "risk", type: "risk_gate", params: {} }],
      edges: [],
      meta: {
        gateProtocol: {
          steps: [
            {
              id: "risk_gate",
              tone: "risk",
              title: { en: "RiskGate Stamp", zh: "RiskGate Stamp" },
              body: { en: "real node only", zh: "real node only" },
              nodes: ["risk", "ghost_node"],
            },
          ],
        },
      },
    };

    render(createElement(GateView, { loom, onFocusNodes: (ids) => focused.push(ids) }));

    expect(container!.innerHTML).toContain("risk");
    expect(container!.innerHTML).not.toContain("ghost_node");

    const button = container!.querySelector("button");
    expect(button).toBeTruthy();
    act(() => { button!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    expect(focused).toEqual([["risk"]]);
  });
});
