// frontend/src/components/LeaderboardTable.tsx —— 基线排行榜表格
// 后端 Board.to_dict：{rows:[{name,kind,net_pnl,return_pct,max_dd,win_rate,num_trades,
// generalization_gap,in_sample_only}], sort_key, ranking_window}——rows 已按排序窗
// return_pct 降序。诚实：蓝图打不过基线如实排在下面；in_sample_only 行视觉降权 +
// 角标（T4 审查建议：防观众忽略未验证行）；运气基线（baseline_random）标 luck 角标。
import type { Board, BoardRow } from "../lib/eval";
import { useLang } from "../lib/i18n";

const num = (v: number, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "—");

export default function LeaderboardTable({ board }: { board: Board }) {
  const { t } = useLang();
  const rows = board.rows ?? [];
  if (!rows.length) return <div className="text-xs text-slate-500">{t("noData")}</div>;

  return (
    <div className="panel hud-frame p-3 space-y-2">
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="hud-label text-loom-green">{t("leaderboardTitle")}</div>
        <span className="text-[10px] text-slate-500">{t("leaderboardHint")}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-slate-500 text-[10px] uppercase tracking-wider">
              <th className="text-left py-1 pr-2">{t("rank")}</th>
              <th className="text-left py-1 pr-2">name</th>
              <th className="text-right py-1 px-2">return %</th>
              <th className="text-right py-1 px-2">net pnl</th>
              <th className="text-right py-1 px-2">max dd</th>
              <th className="text-right py-1 px-2">{t("winRate")}</th>
              <th className="text-right py-1 px-2">trades</th>
              <th className="text-right py-1 pl-2">{t("generalizationGap")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r: BoardRow, i) => {
              const luck = r.name === "baseline_random";
              const isBp = r.kind === "blueprint";
              // in_sample_only 行整体降透明度（视觉降权），蓝图行金色高亮以便对比。
              const rowCls = `${r.in_sample_only ? "opacity-55 " : ""}${isBp
                ? "bg-loom-gold/5" : ""} border-t border-edge/60`;
              const ret = r.return_pct;
              return (
                <tr key={r.name + i} className={rowCls}>
                  <td className="py-1 pr-2 text-slate-500">{i + 1}</td>
                  <td className="py-1 pr-2">
                    <span className={isBp ? "text-loom-gold" : "text-slate-300"}>{r.name}</span>
                    {luck && (
                      <span className="ml-1 px-1 rounded bg-loom-blue/20 text-loom-blue text-[9px]">
                        {t("luckBaseline")}
                      </span>)}
                    {r.in_sample_only && (
                      <span className="ml-1 px-1 rounded bg-loom-amber/20 text-loom-amber text-[9px]">
                        {t("inSampleOnly")}
                      </span>)}
                  </td>
                  <td className={`py-1 px-2 text-right ${ret >= 0 ? "text-loom-green" : "text-loom-red"}`}>
                    {num(ret)}
                  </td>
                  <td className="py-1 px-2 text-right text-slate-300">{num(r.net_pnl)}</td>
                  <td className="py-1 px-2 text-right text-slate-400">{num(r.max_dd * 100, 1)}%</td>
                  <td className="py-1 px-2 text-right text-slate-400">{num(r.win_rate * 100, 0)}%</td>
                  <td className="py-1 px-2 text-right text-slate-400">{r.num_trades}</td>
                  <td className="py-1 pl-2 text-right">
                    {r.generalization_gap == null
                      ? <span className="text-slate-600">—</span>
                      : <span className={r.generalization_gap > 0 ? "text-loom-red" : "text-loom-green"}>
                          {num(r.generalization_gap)}
                        </span>}
                  </td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
