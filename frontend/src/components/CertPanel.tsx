// frontend/src/components/CertPanel.tsx
import { useLang } from "../lib/i18n";
export default function CertPanel({ cert }: { cert: Record<string, unknown> | null }) {
  const { t } = useLang();
  if (!cert) return null;
  const item = (k: string, v: unknown, accent = "") => (
    <div className="flex justify-between text-xs py-0.5">
      <span className="text-slate-500">{k}</span>
      <span className={`font-mono ${accent}`}>{String(v)}</span>
    </div>
  );
  const det = Number(cert.deterministic_ratio ?? 1);
  return (
    <div className="panel p-3">
      <div className="hud-label text-loom-gold mb-1">{t("cost")}</div>
      {item("llm calls/bar", cert.llm_calls_per_bar)}
      {item("daily token ceiling", Number(cert.daily_token_ceiling).toLocaleString())}
      {item("worst latency", cert.worst_latency_class,
            cert.worst_latency_class === "llm" ? "text-loom-amber" : "text-loom-green")}
      {item("deterministic", `${(det * 100).toFixed(1)}%`, det === 1 ? "text-loom-green" : "text-loom-amber")}
    </div>
  );
}
