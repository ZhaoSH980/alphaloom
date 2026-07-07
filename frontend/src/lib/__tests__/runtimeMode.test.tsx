import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import App from "../../App";
import { setRuntimeMode } from "../api";
import { setLang } from "../i18n";

vi.mock("../../pages/Studio", () => ({ default: () => createElement("div", null, "studio page") }));
vi.mock("../../pages/Terminal", () => ({ default: () => createElement("div", null, "terminal page") }));
vi.mock("../../pages/Eval", () => ({ default: () => createElement("div", null, "eval page") }));
vi.mock("../../pages/LiveDesk", () => ({ default: () => createElement("div", null, "live desk page") }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function render(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(el); });
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

beforeEach(() => {
  location.hash = "#/studio";
  setLang("en");
});

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

describe("runtime mode API", () => {
  it("posts the requested mode and returns the refreshed backend status", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      json({ llm_mode: "live", model: "astron-code-latest" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await setRuntimeMode("live");

    expect(result).toEqual({ llm_mode: "live", model: "astron-code-latest" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/runtime-mode");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(String(init?.body))).toEqual({ mode: "live" });
  });
});

describe("App runtime mode switcher", () => {
  it("switches from offline to live from the header", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/status") {
        return json({ llm_mode: "offline", model: "spark-x1" });
      }
      if (String(input) === "/api/runtime-mode" && init?.method === "POST") {
        return json({ llm_mode: "live", model: "astron-code-latest" });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(createElement(App));
    await waitFor(() => expect(container!.textContent?.toLowerCase()).toContain("offline"));

    const liveButton = Array.from(container!.querySelectorAll("button"))
      .find((button) => button.textContent?.toLowerCase().includes("live"));
    expect(liveButton).toBeTruthy();

    await act(async () => {
      liveButton!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitFor(() => expect(container!.textContent?.toLowerCase()).toContain("live"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runtime-mode",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ mode: "live" }) }),
    );
  });

  it("clears a failed live-mode error when the user returns to offline", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/status") {
        return json({ llm_mode: "offline", model: "spark-x1" });
      }
      if (String(input) === "/api/runtime-mode" && init?.method === "POST") {
        return json({ detail: "live mode requires LLM_BASE_URL" }, 422);
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(createElement(App));
    await waitFor(() => expect(container!.textContent?.toLowerCase()).toContain("offline"));

    const buttons = () => Array.from(container!.querySelectorAll("button"));
    const liveButton = () => buttons()
      .find((button) => button.textContent?.toLowerCase().includes("live"));
    const offlineButton = () => buttons()
      .find((button) => button.textContent?.toLowerCase().includes("offline"));

    await act(async () => {
      liveButton()!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(() => expect(container!.textContent).toContain("LLM_BASE_URL"));

    await act(async () => {
      offlineButton()!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await waitFor(() => expect(container!.textContent).not.toContain("LLM_BASE_URL"));
  });

  it("routes to the Live Desk from the header", async () => {
    const fetchMock = vi.fn(async () => json({ llm_mode: "offline", model: "spark-x1" }));
    vi.stubGlobal("fetch", fetchMock);

    render(createElement(App));
    await waitFor(() => expect(container!.textContent?.toLowerCase()).toContain("studio"));

    const liveDeskTab = Array.from(container!.querySelectorAll("a"))
      .find((link) => link.textContent?.toLowerCase().includes("live desk"));
    expect(liveDeskTab).toBeTruthy();

    await act(async () => {
      location.hash = "#/live";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
      await Promise.resolve();
    });

    await waitFor(() => expect(container!.textContent).toContain("live desk page"));
  });
});
