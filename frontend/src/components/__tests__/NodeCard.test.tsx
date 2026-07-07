import { afterEach, describe, expect, it } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ReactFlowProvider } from "@xyflow/react";
import NodeCard from "../NodeCard";
import type { NodeDef } from "../../lib/loom";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function render(el: ReactElement): HTMLDivElement {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(createElement(ReactFlowProvider, null, el)); });
  return container;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
});

const committeeDef: NodeDef = {
  type: "committee",
  category: "agent",
  inputs: { candle: "candle", atr: "series" },
  outputs: { signal: "signal" },
  params: {},
  cost: {},
};

describe("NodeCard params", () => {
  it("collapses long node params until explicitly expanded", () => {
    const longPersona = "an aggressive momentum trader with a very long committed persona";
    const view = render(createElement(NodeCard, {
      id: "committee",
      type: "loomNode",
      selected: false,
      dragging: false,
      selectable: true,
      deletable: true,
      draggable: true,
      zIndex: 0,
      isConnectable: true,
      positionAbsoluteX: 0,
      positionAbsoluteY: 0,
      data: {
        def: committeeDef,
        params: {
          atr_mult: 2,
          context_window: 12,
          strategist_persona: longPersona,
          risk_persona: "risk-tolerant officer",
          chair_persona: "decisive chair",
        },
      },
    }));

    expect(view.textContent).toContain("5 params");
    expect(view.textContent).toContain("strategist_persona");
    expect(view.textContent).not.toContain(longPersona);

    const toggle = view.querySelector("button[aria-label='Toggle node params']");
    expect(toggle).not.toBeNull();
    act(() => { (toggle as HTMLButtonElement).click(); });

    expect(view.textContent).toContain(longPersona);
  });
});
