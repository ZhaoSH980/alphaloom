import { afterEach, describe, expect, it, vi } from "vitest";
import { getCandles, getMarketCatalog, stopLive } from "../api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("market API client", () => {
  it("fetches the market catalog used by the backtest selectors", async () => {
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);

    await getMarketCatalog();

    expect(fetchMock).toHaveBeenCalledWith("/api/market/catalog");
  });

  it("requests candles only for the selected backtest window", async () => {
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);

    await getCandles("SOL-USDT-SWAP", "1m", { start: 1000, end: 4000, limit: 2000 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/market/candles?inst=SOL-USDT-SWAP&bar=1m&limit=2000&start=1000&end=4000",
    );
  });

  it("requests live session stop through the REST control plane", async () => {
    const fetchMock = vi.fn(async () => json({ session_id: "live-1", status: "stopping" }));
    vi.stubGlobal("fetch", fetchMock);

    await stopLive("live-1");

    expect(fetchMock).toHaveBeenCalledWith("/api/live/live-1/stop", { method: "POST" });
  });
});
