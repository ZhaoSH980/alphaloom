// frontend/src/pages/Eval.tsx —— Runs & Eval 区（D4-T7）
// 诚实评估套件：从一个已完成 run 出发，逐块拉后端权威评估结果并可视化。
// 保真度阶梯 / 记分卡（后端唯一评分实现）/ 基线排行榜 / 委员会消融 / 进化谱系。
//
// 数据流（字段名对齐后端 to_dict，见 lib/eval.ts）：
//  - fidelity：evalFidelity(runId) → LadderReport（选中 run 的 fills+candles 重放，零 LLM）
//  - scorecard：run.report 作 train_report + 上一步 ladder → evalScorecard → Scorecard
//  - leaderboard：run.params 的 inst/bar/窗口 → evalLeaderboard → Board（三基线，零 LLM）
//  - ablation / evolve：**蓝图级实验**（run 不存蓝图，app.py:207 model_dump(exclude=blueprint)），
//    故从蓝图库选一张蓝图驱动（getBlueprint）——消融须 committee 蓝图 + offline LLM 录制
//    （T8 前会 409/422，如实显示错误，控制器 T8 录完可跑）。
import { useEffect, useState } from "react";
import { getRun, listRuns, listBlueprints, getBlueprint, evalFidelity, evalScorecard,
         evalLeaderboard, evalAblation, evolve } from "../lib/api";
import type { LadderReport, Scorecard, Board, AblationReport, Genealogy } from "../lib/eval";
import FidelityLadder from "../components/FidelityLadder";
import ScorecardPanel from "../components/ScorecardPanel";
import LeaderboardTable from "../components/LeaderboardTable";
import AblationTable from "../components/AblationTable";
import GenealogyTree from "../components/GenealogyTree";
import RunPicker, { type RunPickerItem } from "../components/RunPicker";
import { useLang } from "../lib/i18n";

function errText(e: unknown): string {
  const err = e as { status?: number; body?: string; message?: string };
  const body = err?.body;
  if (typeof body === "string" && body) {
    try { const j = JSON.parse(body); return typeof j.detail === "string" ? j.detail
      : (j.detail?.message ?? JSON.stringify(j.detail ?? j)); } catch { return body; }
  }
  return err?.message ?? String(e);
}

// 一个可折叠/带加载/错误态的评估区块壳。
function Block({ title, err, loading, children, onRun, runLabel }: {
  title: string; err?: string | null; loading?: boolean;
  children?: React.ReactNode; onRun?: () => void; runLabel?: string;
}) {
  const { t } = useLang();
  return (
    <section className="space-y-1">
      {onRun && (
        <button onClick={onRun} disabled={loading}
                className="px-2 py-1 text-xs rounded bg-loom-blue/15 text-loom-blue disabled:opacity-40">
          {loading ? t("loading") : (runLabel ?? title)}
        </button>)}
      {err && (
        <div className="panel p-2 text-[11px] text-loom-red font-mono border-loom-red/40">{err}</div>)}
      {children}
    </section>
  );
}

export default function Eval() {
  const { t } = useLang();
  const [runs, setRuns] = useState<Record<string, any>[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [run, setRun] = useState<Record<string, any> | null>(null);

  const [ladder, setLadder] = useState<LadderReport | null>(null);
  const [ladderErr, setLadderErr] = useState<string | null>(null);
  const [card, setCard] = useState<Scorecard | null>(null);
  const [cardErr, setCardErr] = useState<string | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [boardErr, setBoardErr] = useState<string | null>(null);
  const [ablation, setAblation] = useState<AblationReport | null>(null);
  const [ablErr, setAblErr] = useState<string | null>(null);
  const [genealogy, setGenealogy] = useState<Genealogy | null>(null);
  const [evoErr, setEvoErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // 蓝图库（消融/进化用——它们是蓝图级实验，run 不带蓝图）。
  const [gallery, setGallery] = useState<{ id: string; name: string }[]>([]);
  const [bpSel, setBpSel] = useState<string>("");

  useEffect(() => {
    listRuns().then((rs) => {
      const completed = rs.filter((r) => r.status === "completed" && r.report);
      setRuns(rs);
      setSel(completed[0]?.run_id ?? rs[0]?.run_id ?? null);
    });
    listBlueprints().then((bps) => {
      setGallery(bps);
      setBpSel(bps[0]?.id ?? "");
    }).catch(() => {});
  }, []);

  // 换 run：清空全部派生结果，拉 run 详情，自动跑保真度阶梯（零 LLM，恒可用）。
  useEffect(() => {
    if (!sel) return;
    setLadder(null); setCard(null); setBoard(null); setAblation(null); setGenealogy(null);
    setLadderErr(null); setCardErr(null); setBoardErr(null); setAblErr(null); setEvoErr(null);
    getRun(sel).then((r) => {
      setRun(r);
      if (r.status === "completed" && r.report) {
        setBusy("fidelity");
        evalFidelity(sel).then(setLadder).catch((e) => setLadderErr(errText(e)))
          .finally(() => setBusy(null));
      }
    });
  }, [sel]);

  const params = (run?.params ?? {}) as Record<string, unknown>;
  const inst = (params.inst as string) ?? "BTC-USDT-SWAP";
  const bar = (params.bar as string) ?? "1m";

  const buildScorecard = () => {
    if (!run?.report) return;
    setBusy("scorecard"); setCardErr(null);
    // train_report = run.report（BacktestReport 形状：summary+certificate+equity_curve）；
    // ladder 为可选证据碎片。评分数学只在后端一份（防口径漂移）。
    evalScorecard({ train_report: run.report, ladder: ladder ?? undefined })
      .then(setCard).catch((e) => setCardErr(errText(e))).finally(() => setBusy(null));
  };

  const runLeaderboard = () => {
    setBusy("leaderboard"); setBoardErr(null);
    evalLeaderboard({
      inst, bar,
      start_ms: params.start_ms ?? undefined, end_ms: params.end_ms ?? undefined,
    }).then(setBoard).catch((e) => setBoardErr(errText(e))).finally(() => setBusy(null));
  };

  const runAblation = () => {
    if (!bpSel) { setAblErr("pick a blueprint to ablate"); return; }
    setBusy("ablation"); setAblErr(null); setAblation(null);
    // 蓝图级实验：从蓝图库取选中蓝图（run 不带蓝图）。须含 committee 节点 + offline LLM。
    // 数据窗口沿用选中 run 的 inst/bar/窗口（同数据对比）。
    getBlueprint(bpSel).then((blueprint) =>
      evalAblation({ blueprint, inst, bar,
        start_ms: params.start_ms ?? undefined, end_ms: params.end_ms ?? undefined })
        .then(setAblation))
      .catch((e) => setAblErr(errText(e))).finally(() => setBusy(null));
  };

  const runEvolve = () => {
    if (!bpSel) { setEvoErr("pick a blueprint to evolve"); return; }
    setBusy("evolve"); setEvoErr(null); setGenealogy(null);
    // train/valid 窗须不重叠：默认拿选中 run 窗口前 70% train、后 30% valid（若有边界）。
    const s = Number(params.start_ms ?? NaN), e = Number(params.end_ms ?? NaN);
    let win: Record<string, unknown> = {};
    if (Number.isFinite(s) && Number.isFinite(e) && e > s) {
      const mid = s + Math.floor((e - s) * 0.7);
      win = { train_start_ms: s, train_end_ms: mid, valid_start_ms: mid + 1, valid_end_ms: e };
    }
    getBlueprint(bpSel).then((blueprint) =>
      evolve({ blueprint, inst, bar, population: 4, generations: 3, param_only: true, ...win })
        .then(setGenealogy))
      .catch((e2) => setEvoErr(errText(e2))).finally(() => setBusy(null));
  };

  // —— 离线 Demo 预设（D4-T8）——
  // 不依赖选中 run 的坐标：直接对端点发 {demo:true}，服务端硬用固定 demo 坐标（后端
  // demo_coords）的**确定性录制回放**。离线（ALPHALOOM_OFFLINE=1）零配额即可渲染出
  // 消融表和进化谱系树。诚实标注：这是固定坐标的录制回放，不是任意配置实时算。
  const runAblationDemo = () => {
    setBusy("ablation"); setAblErr(null); setAblation(null);
    evalAblation({ demo: true }).then(setAblation)
      .catch((e) => setAblErr(errText(e))).finally(() => setBusy(null));
  };

  const runEvolveDemo = () => {
    setBusy("evolve"); setEvoErr(null); setGenealogy(null);
    evolve({ demo: true }).then(setGenealogy)
      .catch((e2) => setEvoErr(errText(e2))).finally(() => setBusy(null));
  };

  if (!runs.length) return <div className="p-10 text-slate-500">{t("evalNoRuns")}</div>;

  return (
    <div className="h-full overflow-auto p-3 space-y-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="font-semibold text-loom-gold">{t("evalLab")}</div>
        <span className="text-[10px] text-slate-500">{t("evalIntro")}</span>
      </div>

      <RunPicker
        runs={runs as RunPickerItem[]}
        selectedId={sel}
        label={t("evalRun")}
        onSelect={setSel}
      />

      {/* 1. 保真度阶梯（自动跑，零 LLM） */}
      <Block title={t("fidelityTitle")} err={ladderErr}
             loading={busy === "fidelity"}>
        {ladder && <FidelityLadder report={ladder} />}
      </Block>

      {/* 2. 记分卡（run.report + ladder → 后端权威综合分） */}
      <Block title={t("scorecardTitle")} err={cardErr} loading={busy === "scorecard"}
             onRun={buildScorecard} runLabel={t("runScorecard")}>
        {card && <ScorecardPanel card={card} />}
      </Block>

      {/* 3. 基线排行榜（三基线 + 同窗，零 LLM） */}
      <Block title={t("leaderboardTitle")} err={boardErr} loading={busy === "leaderboard"}
             onRun={runLeaderboard} runLabel={t("leaderboardTitle")}>
        {board && <LeaderboardTable board={board} />}
      </Block>

      {/* 蓝图库选择器（消融/进化用——它们是蓝图级实验，run 不带蓝图） */}
      {gallery.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="hud-label">blueprint</span>
          <select value={bpSel} onChange={(ev) => setBpSel(ev.target.value)}
                  className="bg-panel border border-edge rounded text-xs px-2 py-1 text-slate-300">
            {gallery.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        </div>)}

      {/* 4. 委员会消融（选中 run 坐标：须 committee 蓝图 + offline LLM 录制；
             或点"跑离线 Demo"用固定 demo 坐标的确定性录制回放，离线零配额可渲染） */}
      <Block title={t("ablationTitle")} err={ablErr} loading={busy === "ablation"}
             onRun={runAblation} runLabel={t("ablationTitle")}>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={runAblationDemo} disabled={busy === "ablation"}
                  title={t("demoPresetHint")}
                  className="px-2 py-1 text-xs rounded bg-loom-gold/15 text-loom-gold disabled:opacity-40">
            {busy === "ablation" ? t("loading") : t("demoPreset")}
          </button>
          <span className="text-[10px] text-slate-500">{t("demoPresetHint")}</span>
        </div>
        {ablation && <AblationTable report={ablation} />}
      </Block>

      {/* 5. 进化谱系树（选中 run 坐标：LLM 变异算子须 offline 录制；
             或点"跑离线 Demo"用固定 demo 坐标的确定性录制回放，离线零配额可渲染） */}
      <Block title={t("evolutionTitle")} err={evoErr} loading={busy === "evolve"}
             onRun={runEvolve} runLabel={t("evolutionTitle")}>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={runEvolveDemo} disabled={busy === "evolve"}
                  title={t("demoPresetHint")}
                  className="px-2 py-1 text-xs rounded bg-loom-gold/15 text-loom-gold disabled:opacity-40">
            {busy === "evolve" ? t("loading") : t("demoPreset")}
          </button>
          <span className="text-[10px] text-slate-500">{t("demoPresetHint")}</span>
        </div>
        {genealogy && <GenealogyTree genealogy={genealogy} />}
      </Block>
    </div>
  );
}
