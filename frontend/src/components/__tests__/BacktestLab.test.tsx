import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import BacktestLab from "../BacktestLab";
import type { BacktestConfig, MarketWindow } from "../../lib/backtestConfig";
import type { Loom } from "../../lib/loom";
import { setLang } from "../../lib/i18n";

vi.mock("../CandleChart", () => ({
  default: ({ candles }: { candles: unknown[] }) =>
    createElement("div", { "data-testid": "candles", "data-count": candles.length }),
}));
vi.mock("../EquityChart", () => ({
  default: ({ curve }: { curve: unknown[] }) =>
    createElement("div", { "data-testid": "equity", "data-count": curve.length }),
}));

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
  setLang("zh");
});

const blueprint: Loom = {
  id: "demo",
  name: "Demo Blueprint",
  nodes: [],
  edges: [],
  meta: {},
};
const catalog: MarketWindow[] = [
  { inst: "SOL-USDT-SWAP", bar: "1m", start_ms: 0, end_ms: 120_000, count: 3 },
];
const config: BacktestConfig = {
  inst: "SOL-USDT-SWAP",
  bar: "1m",
  start_ms: 0,
  end_ms: 120_000,
  cash: 10_000,
  fee_rate: 0.0005,
  speed: "4x",
};
const candles = [
  { ts: 0, open: 1, high: 2, low: 1, close: 2, volume: 1 },
  { ts: 60_000, open: 2, high: 3, low: 1, close: 2.5, volume: 1 },
  { ts: 120_000, open: 2.5, high: 4, low: 2, close: 3, volume: 1 },
];

describe("BacktestLab", () => {
  it("shows explicit blueprint and market controls and starts a run", () => {
    setLang("en");
    const onRun = vi.fn();
    const view = render(createElement(BacktestLab, {
      blueprint,
      catalog,
      config,
      onConfigChange: vi.fn(),
      candles,
      fills: [],
      equityCurve: [],
      cursor: undefined,
      status: undefined,
      disabled: false,
      onRun,
      onCommand: vi.fn(),
    }));

    expect(view.textContent).toContain("Backtest Lab");
    expect(view.textContent).toContain("Demo Blueprint");
    expect(view.textContent).toContain("SOL-USDT-SWAP");
    expect(view.textContent).toContain("1m");

    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start"));
    expect(start).toBeTruthy();
    act(() => { start!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("feeds only replayed candles to the chart while a run is streaming", () => {
    setLang("en");
    const view = render(createElement(BacktestLab, {
      blueprint,
      catalog,
      config,
      onConfigChange: vi.fn(),
      candles,
      fills: [],
      equityCurve: [[0, 10_000], [60_000, 10_010]],
      cursor: 1,
      status: "running",
      disabled: false,
      onRun: vi.fn(),
      onCommand: vi.fn(),
    }));

    expect(view.querySelector("[data-testid='candles']")?.getAttribute("data-count")).toBe("2");
    expect(view.querySelector("[data-testid='equity']")?.getAttribute("data-count")).toBe("2");
    expect(view.textContent).toContain("2 / 3");
  });

  it("disables the start button while a replay is already running", () => {
    setLang("en");
    const view = render(createElement(BacktestLab, {
      blueprint,
      catalog,
      config,
      onConfigChange: vi.fn(),
      candles,
      fills: [],
      equityCurve: [],
      cursor: 1,
      status: "running",
      disabled: false,
      onRun: vi.fn(),
      onCommand: vi.fn(),
    }));

    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start")) as HTMLButtonElement | undefined;

    expect(start).toBeTruthy();
    expect(start?.disabled).toBe(true);
  });
});
