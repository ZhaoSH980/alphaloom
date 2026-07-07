import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import CandleChart from "../CandleChart";
import EquityChart from "../EquityChart";

const mocks = vi.hoisted(() => ({
  resizeCallbacks: [] as ResizeObserverCallback[],
  applyOptions: vi.fn(),
  remove: vi.fn(),
  setData: vi.fn(),
  setMarkers: vi.fn(),
  fitContent: vi.fn(),
}));

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addAreaSeries: vi.fn(() => ({ setData: mocks.setData })),
    addCandlestickSeries: vi.fn(() => ({
      setData: mocks.setData,
      setMarkers: mocks.setMarkers,
    })),
    applyOptions: mocks.applyOptions,
    remove: mocks.remove,
    timeScale: vi.fn(() => ({ fitContent: mocks.fitContent })),
  })),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class MockResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    mocks.resizeCallbacks.push(callback);
  }

  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

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
  mocks.resizeCallbacks.length = 0;
  mocks.applyOptions.mockClear();
  mocks.remove.mockClear();
  mocks.setData.mockClear();
  mocks.setMarkers.mockClear();
  mocks.fitContent.mockClear();
});

describe("chart lifecycle", () => {
  it("ignores late candle chart resize callbacks after unmount", () => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);

    render(createElement(CandleChart, { candles: [], fills: [], height: 120 }));
    const callback = mocks.resizeCallbacks[0];
    act(() => { root?.unmount(); });

    expect(() => callback([], {} as ResizeObserver)).not.toThrow();
  });

  it("ignores late equity chart resize callbacks after unmount", () => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);

    render(createElement(EquityChart, { curve: [], height: 120 }));
    const callback = mocks.resizeCallbacks[0];
    act(() => { root?.unmount(); });

    expect(() => callback([], {} as ResizeObserver)).not.toThrow();
  });
});
