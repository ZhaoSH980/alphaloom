import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import LiveDesk from "../LiveDesk";
import { setLang } from "../../lib/i18n";
import type { Loom, NodeDef } from "../../lib/loom";

vi.mock("../../components/CandleChart", () => ({
  default: ({ candles }: { candles: unknown[] }) =>
    createElement("div", { "data-testid": "live-candles", "data-count": candles.length }),
}));
vi.mock("../../components/EquityChart", () => ({
  default: ({ curve }: { curve: unknown[] }) =>
    createElement("div", { "data-testid": "live-equity", "data-count": curve.length }),
}));
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes }: { nodes: { id: string }[] }) =>
    createElement("div", { "data-testid": "live-flow" }, nodes.map((node) => node.id).join(",")),
  Background: () => createElement("div", null),
  Controls: () => createElement("div", null),
}));

const mocks = vi.hoisted(() => {
  const nodeDefs: NodeDef[] = [
    { type: "candle_feed", category: "data", inputs: {}, outputs: { out: "candle" }, params: {}, cost: {} },
    { type: "committee", category: "decision", inputs: { candle: "candle" }, outputs: { signal: "signal" }, params: {}, cost: {} },
    { type: "risk_gate", category: "risk", inputs: { signal: "signal" }, outputs: { stamped: "risk_stamped_signal" }, params: {}, cost: {} },
    { type: "execute_order", category: "execution", inputs: { signal: "risk_stamped_signal" }, outputs: {}, params: {}, cost: {} },
  ];
  const blueprint: Loom = {
    id: "agent_committee_v1",
    name: "Agent Committee",
    nodes: [
      { id: "feed", type: "candle_feed", params: {} },
      { id: "committee", type: "committee", params: {} },
      { id: "risk", type: "risk_gate", params: {} },
      { id: "exec", type: "execute_order", params: {} },
    ],
    edges: [],
    meta: {
      run_defaults: { inst: "SOL-USDT-SWAP", bar: "1m" },
      positions: {
        feed: { x: 0, y: 0 },
        committee: { x: 240, y: 0 },
        risk: { x: 480, y: 0 },
        exec: { x: 720, y: 0 },
      },
      gateProtocol: {
        steps: [
          { id: "market", title: { en: "Market Snapshot" }, body: { en: "candles" }, nodes: ["feed"] },
          { id: "diagnosis", title: { en: "Diagnosis" }, body: { en: "committee" }, nodes: ["committee"] },
          { id: "risk", title: { en: "RiskGate" }, body: { en: "stamp" }, nodes: ["risk"] },
        ],
      },
    },
  };
  return {
    nodeDefs,
    blueprint,
    startLive: vi.fn(async (_body: Record<string, unknown>) =>
      ({ session_id: "live-session-1", run_id: "live-session-1" })),
    stopLive: vi.fn(async (_sessionId: string) =>
      ({ session_id: "live-session-1", status: "stopping" })),
    socket: { send: vi.fn(), close: vi.fn() },
    socketCallback: undefined as undefined | ((ev: Record<string, unknown>) => void),
    socketCloseCallback: undefined as undefined | (() => void),
  };
});

vi.mock("../../lib/api", () => ({
  getNodes: vi.fn(async () => mocks.nodeDefs),
  listBlueprints: vi.fn(async () => [
    { id: "agent_committee_v1", name: "Agent Committee", meta: {}, source: "builtin" },
  ]),
  getBlueprint: vi.fn(async () => mocks.blueprint),
  getMarketCatalog: vi.fn(async () => [
    { inst: "SOL-USDT-SWAP", bar: "1m", start_ms: 0, end_ms: 120_000, count: 3 },
  ]),
  getCandles: vi.fn(async () => [
    { ts: 0, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
    { ts: 60_000, open: 1.5, high: 2.5, low: 1, close: 2, volume: 11 },
  ]),
  compileLoom: vi.fn(async () => ({ ok: true, errors: [], certificate: {}, order: [] })),
  startLive: mocks.startLive,
  stopLive: mocks.stopLive,
  getRun: vi.fn(async () => ({ report: { fills: [], equity_curve: [] } })),
  getTrace: vi.fn(async () => []),
}));
vi.mock("../../lib/ws", () => ({
  openLiveSocket: vi.fn((_runId: string, cb: (ev: Record<string, unknown>) => void,
                         onClose?: () => void) => {
    mocks.socketCallback = cb;
    mocks.socketCloseCallback = onClose;
    return mocks.socket;
  }),
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

async function waitFor(assertion: () => void) {
  const deadline = Date.now() + 1000;
  let lastError: unknown = null;
  while (Date.now() < deadline) {
    await act(async () => { await Promise.resolve(); });
    try {
      assertion();
      return;
    } catch (err) {
      lastError = err;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  throw lastError;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
  mocks.startLive.mockClear();
  mocks.stopLive.mockClear();
  mocks.socket.send.mockClear();
  mocks.socket.close.mockClear();
  mocks.socketCallback = undefined;
  mocks.socketCloseCallback = undefined;
  setLang("zh");
});

describe("LiveDesk", () => {
  it("loads a blueprint, shows live market controls, and starts a paper-live run", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Live Desk"));
    expect(view.textContent).toContain("Agent Committee");
    expect(view.textContent).toContain("SOL-USDT-SWAP");
    expect(view.textContent).toContain("Market Snapshot");
    expect(view.querySelector("[data-testid='live-flow']")?.textContent).toContain("committee");

    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));
    expect(start).toBeTruthy();

    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(mocks.startLive).toHaveBeenCalledTimes(1);
    expect(mocks.startLive.mock.calls[0][0]).toMatchObject({
      blueprint: expect.objectContaining({ id: "agent_committee_v1" }),
      inst: "SOL-USDT-SWAP",
      bar: "1m",
      analysis: true,
    });
    await waitFor(() => {
      const currentStart = Array.from(view.querySelectorAll("button"))
        .find((button) => button.textContent?.includes("Start Live")) as HTMLButtonElement | undefined;
      expect(currentStart?.disabled).toBe(true);
    });
  });

  it("offers live OKX bar intervals even when the local catalog only has 1m", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Start Live"));
    const barSelect = view.querySelectorAll("select")[2] as HTMLSelectElement;
    expect(Array.from(barSelect.options).map((option) => option.value)).toContain("5m");

    await act(async () => {
      barSelect.value = "5m";
      barSelect.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));
    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(mocks.startLive.mock.calls[0][0]).toMatchObject({ bar: "5m" });
  });

  it("explains the active gate path while the live stream advances", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Start Live"));
    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));

    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      mocks.socketCallback?.({
        type: "bar",
        idx: 0,
        ts: 0,
        candle: { ts: 120_000, open: 2, high: 3, low: 1.5, close: 2.6, volume: 12 },
        equity: 10005,
        active: ["feed", "committee", "risk"],
        fills: [],
      });
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(view.textContent).toContain("Gate path live");
      expect(view.textContent).toContain("feed -> committee -> risk");
      expect(view.textContent).toContain("No fill yet");
    });
  });

  it("renders live LLM sidecar analysis events with prompt provenance", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Start Live"));
    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));

    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      mocks.socketCallback?.({
        type: "analysis",
        event_idx: 0,
        prompt_hash: "abc123def4567890",
        output: {
          market_state: "trend up",
          current_gate: "RiskGate observed",
          risk_reason: "stamped path is intact",
          suggestion: "keep parameters",
        },
      });
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(view.textContent).toContain("LLM live analysis");
      expect(view.textContent).toContain("trend up");
      expect(view.textContent).toContain("abc123def456");
    });
  });

  it("stops the backend live session when the desk unmounts", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Start Live"));
    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));

    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(() => expect(mocks.startLive).toHaveBeenCalledTimes(1));

    await act(async () => {
      root?.unmount();
      root = null;
      await Promise.resolve();
    });

    expect(mocks.socket.send).toHaveBeenCalledWith("stop");
    expect(mocks.socket.close).toHaveBeenCalled();
    expect(mocks.stopLive).toHaveBeenCalledWith("live-session-1");
  });

  it("shows a disconnected status when the live websocket closes mid-run", async () => {
    setLang("en");
    const view = render(createElement(LiveDesk));

    await waitFor(() => expect(view.textContent).toContain("Start Live"));
    const start = Array.from(view.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("Start Live"));

    await act(async () => {
      start!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      mocks.socketCloseCallback?.();
      await Promise.resolve();
    });

    await waitFor(() => expect(view.textContent).toContain("disconnected"));
  });
});
