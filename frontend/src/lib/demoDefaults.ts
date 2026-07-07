import type { Loom } from "./loom";

export const PREFERRED_DEMO_BLUEPRINT_ID = "real_sol_breakout_demo_v1";

export type GalleryItem = { id: string; name: string; source: string };
export type RunDefaults = {
  inst: string;
  bar: string;
  start_ms?: number;
  end_ms?: number;
};

export function pickDefaultBlueprint(gallery: GalleryItem[]): string | null {
  return gallery.find((g) => g.id === PREFERRED_DEMO_BLUEPRINT_ID)?.id
    ?? gallery.find((g) => g.source === "preset")?.id
    ?? gallery[0]?.id
    ?? null;
}

export function runDefaultsFromLoom(loom: Loom): RunDefaults {
  const feed = loom.nodes.find((node) => node.type === "candle_feed");
  const inst = typeof feed?.params.inst === "string"
    ? feed.params.inst
    : "BTC-USDT-SWAP";
  const bar = typeof feed?.params.bar === "string"
    ? feed.params.bar
    : "1m";
  const start = loom.meta?.demo_start_ms;
  const end = loom.meta?.demo_end_ms;
  return {
    inst,
    bar,
    ...(typeof start === "number" ? { start_ms: start } : {}),
    ...(typeof end === "number" ? { end_ms: end } : {}),
  };
}
