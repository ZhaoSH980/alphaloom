import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import RunPicker, { type RunPickerItem } from "../RunPicker";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function render(el: ReactElement): HTMLDivElement {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(el); });
  return container;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
});

const runs: RunPickerItem[] = [
  { run_id: "run-alpha-111111", blueprint_id: "real_sol_breakout_demo_v1", status: "completed" },
  { run_id: "run-beta-222222", blueprint_id: "agent_committee_v1", status: "failed" },
  { run_id: "run-gamma-333333", blueprint_id: "agent_committee_v1", status: "running" },
];

describe("RunPicker", () => {
  it("shows the selected run summary without expanding every run into the page", () => {
    const view = render(createElement(RunPicker, {
      runs,
      selectedId: "run-alpha-111111",
      label: "Run",
      onSelect: vi.fn(),
    }));

    expect(view.textContent).toContain("Run");
    expect(view.textContent).toContain("3 runs");
    expect(view.textContent).toContain("completed");
    expect(view.textContent).toContain("real_sol_breakout_demo_v1");
    expect(view.querySelectorAll("button").length).toBe(0);
    expect(view.querySelectorAll("option").length).toBe(3);
  });

  it("notifies when a different run is selected", () => {
    const onSelect = vi.fn();
    const view = render(createElement(RunPicker, {
      runs,
      selectedId: "run-alpha-111111",
      label: "Run",
      onSelect,
    }));

    const select = view.querySelector("select")!;
    act(() => {
      select.value = "run-beta-222222";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(onSelect).toHaveBeenCalledWith("run-beta-222222");
  });
});
