// frontend/src/components/CandleChart.tsx
import { createChart, type IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface Candle { ts: number; open: number; high: number; low: number; close: number; volume: number; }
export interface Fill { ts: number; side: string; qty: number; price: number; fee: number; tag: string; }

export default function CandleChart({ candles, fills, height = 320 }: {
  candles: Candle[];
  fills: Fill[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  useEffect(() => {
    if (!ref.current) return;
    const container = ref.current;
    chart.current = createChart(container, {
      height, layout: { background: { color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { color: "#101a33" }, horzLines: { color: "#101a33" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    const series = chart.current.addCandlestickSeries({
      upColor: "#34d399", downColor: "#ef4444", borderVisible: false,
      wickUpColor: "#34d399", wickDownColor: "#ef4444",
    });
    series.setData(candles.map((c) => ({ time: (c.ts / 1000) as never,
      open: c.open, high: c.high, low: c.low, close: c.close })));
    series.setMarkers(fills.map((f) => ({
      time: (f.ts / 1000) as never,
      position: f.side === "buy" ? "belowBar" : "aboveBar",
      color: f.tag === "eod_close" ? "#94a3b8" : f.side === "buy" ? "#34d399" : "#ef4444",
      shape: f.side === "buy" ? "arrowUp" : "arrowDown",
      text: markerText(f),
    })));
    chart.current.timeScale().fitContent();
    const ro = new ResizeObserver(() => {
      chart.current?.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      chart.current?.remove();
      chart.current = undefined;
    };
  }, [candles, fills, height]);
  return <div ref={ref} className="w-full" />;
}

function markerText(fill: Fill): string {
  if (fill.tag === "stop") return "STOP";
  if (fill.tag === "eod_close") return "EOD";
  return fill.side === "buy" ? "BUY" : "SELL";
}
