// frontend/src/components/EquityChart.tsx
import { createChart, type IChartApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

export default function EquityChart({ curve }: { curve: [number, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  useEffect(() => {
    if (!ref.current) return;
    chart.current = createChart(ref.current, {
      height: 160, layout: { background: { color: "transparent" }, textColor: "#94a3b8" },
      grid: { vertLines: { visible: false }, horzLines: { color: "#101a33" } },
      timeScale: { timeVisible: true },
    });
    const s = chart.current.addAreaSeries({
      lineColor: "#f59e0b", topColor: "rgba(245,158,11,0.25)", bottomColor: "rgba(245,158,11,0.02)",
    });
    s.setData(curve.map(([ts, eq]) => ({ time: (ts / 1000) as never, value: eq })));
    chart.current.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.current?.applyOptions({ width: ref.current!.clientWidth }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.current?.remove(); };
  }, [curve]);
  return <div ref={ref} className="w-full" />;
}
