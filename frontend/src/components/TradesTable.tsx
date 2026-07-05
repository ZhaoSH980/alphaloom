// frontend/src/components/TradesTable.tsx
import type { Fill } from "./CandleChart";
export default function TradesTable({ fills }: { fills: Fill[] }) {
  return (
    <div className="panel overflow-auto max-h-64">
      <table className="w-full text-xs">
        <thead className="text-slate-500 sticky top-0 bg-panel">
          <tr>{["time", "side", "qty", "price", "fee", "tag"].map((h) =>
            <th key={h} className="text-left px-2 py-1 font-normal">{h}</th>)}</tr>
        </thead>
        <tbody className="font-mono">
          {fills.map((f, i) => (
            <tr key={i} className="border-t border-edge/50">
              <td className="px-2 py-0.5 text-slate-400">{new Date(f.ts).toISOString().slice(0, 16)}</td>
              <td className={`px-2 ${f.side === "buy" ? "text-loom-green" : "text-loom-red"}`}>{f.side}</td>
              <td className="px-2">{+f.qty.toFixed(6)}</td>
              <td className="px-2">{+f.price.toFixed(4)}</td>
              <td className="px-2 text-slate-500">{+f.fee.toFixed(4)}</td>
              <td className="px-2 text-slate-500">{f.tag}</td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}
