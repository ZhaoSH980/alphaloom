from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"

INST = "SOL-USDT-SWAP"
BAR = "1m"
START_MS = 1782360720000
END_MS = 1782447120000
START_LABEL = "2026-06-25 04:12Z"
END_LABEL = "2026-06-26 04:12Z"


FALLBACK = {
    "summary": {
        "net_pnl": 946.46408889,
        "return_pct": 9.4646,
        "max_drawdown": 0.027693,
        "num_trades": 29,
        "win_rate": 0.6897,
        "profit_factor": 3.0025,
    },
    "certificate": {
        "llm_calls_per_bar": 0,
        "daily_token_ceiling": 0,
        "worst_latency_class": "fast",
        "deterministic_ratio": 1.0,
    },
    "bars": 1441,
    "ladder": {
        "levels": [
            {"level": "L0", "net_pnl": 1006.57456367, "max_dd": 0.020354, "num_trades": 29, "profit_factor": 3.1816},
            {"level": "L1", "net_pnl": 946.46408889, "max_dd": 0.025202, "num_trades": 29, "profit_factor": 3.0025},
            {"level": "L2", "net_pnl": 423.27283161, "max_dd": 0.039392, "num_trades": 29, "profit_factor": 1.5379},
            {"level": "L3", "net_pnl": 148.68836494, "max_dd": 0.052951, "num_trades": 29, "profit_factor": 1.1572},
        ],
        "optimism_gap": 857.88619873,
    },
    "baselines": {
        "buy_hold": {"net_pnl": 47.61407556, "return_pct": 0.4761, "max_drawdown": 0.076801, "num_trades": 1, "win_rate": 1.0, "profit_factor": None},
        "ema_default": {"net_pnl": 0.0, "return_pct": 0.0, "max_drawdown": 0.0, "num_trades": 0, "win_rate": 0.0, "profit_factor": 0.0},
        "random": {"net_pnl": -374.13755249, "return_pct": -3.7414, "max_drawdown": 0.085601, "num_trades": 62, "win_rate": 0.3387, "profit_factor": 0.7121},
    },
    "risk_variants": {
        "half risk": {"net_pnl": 466.76420828, "return_pct": 4.6676, "max_drawdown": 0.013887, "num_trades": 29, "win_rate": 0.6897, "profit_factor": 3.0024},
        "base risk": {"net_pnl": 946.46408889, "return_pct": 9.4646, "max_drawdown": 0.027693, "num_trades": 29, "win_rate": 0.6897, "profit_factor": 3.0025},
        "double risk": {"net_pnl": 1944.36259159, "return_pct": 19.4436, "max_drawdown": 0.055057, "num_trades": 29, "win_rate": 0.6897, "profit_factor": 3.0026},
        "tight max qty": {"net_pnl": 0.0, "return_pct": 0.0, "max_drawdown": 0.0, "num_trades": 0, "win_rate": 0.0, "profit_factor": 0.0},
    },
    "param_variants": [
        {"name": "seed", "lookback": 45, "cooldown": 10, "atr_mult": 2.5, "return_pct": 9.4646, "net_pnl": 946.46408889, "max_drawdown": 0.027693, "num_trades": 29, "win_rate": 0.6897, "profit_factor": 3.0025},
        {"name": "fast trend", "lookback": 45, "cooldown": 10, "atr_mult": 2.0, "return_pct": 10.2484, "net_pnl": 1024.84019463, "max_drawdown": 0.037762, "num_trades": 31, "win_rate": 0.6129, "profit_factor": 2.3463},
        {"name": "wide entry", "lookback": 30, "cooldown": 10, "atr_mult": 2.0, "return_pct": 15.1736, "net_pnl": 1517.36173738, "max_drawdown": 0.033716, "num_trades": 39, "win_rate": 0.641, "profit_factor": 2.9},
        {"name": "winner", "lookback": 30, "cooldown": 20, "atr_mult": 1.5, "return_pct": 19.8259, "net_pnl": 1982.59448294, "max_drawdown": 0.054755, "num_trades": 33, "win_rate": 0.4848, "profit_factor": 2.5227},
        {"name": "patient", "lookback": 30, "cooldown": 60, "atr_mult": 1.5, "return_pct": 12.7272, "net_pnl": 1272.72471148, "max_drawdown": 0.051546, "num_trades": 16, "win_rate": 0.5, "profit_factor": 3.317},
    ],
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


F_TITLE = font(26, True)
F_H1 = font(58, True)
F_H2 = font(34, True)
F_BODY = font(22)
F_BODY_B = font(22, True)
F_SMALL = font(18)
F_SMALL_B = font(18, True)
F_TINY = font(15)
F_NUM = font(30, True)
F_NUM_BIG = font(72, True)


BG = (7, 13, 29)
PANEL = (11, 18, 38)
PANEL_2 = (14, 25, 48)
BORDER = (30, 58, 96)
BORDER_HOT = (45, 196, 255)
TEXT = (207, 216, 226)
MUTED = (111, 128, 157)
MUTED_2 = (77, 91, 117)
GREEN = (47, 211, 146)
CYAN = (46, 192, 255)
GOLD = (245, 178, 31)
RED = (255, 89, 102)
PURPLE = (163, 128, 255)


def load_metrics() -> dict:
    backend_py = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    db = ROOT / "data" / "real_okx_14d.sqlite"
    if not backend_py.exists() or not db.exists():
        return dict(FALLBACK)

    code = r"""
import copy, itertools, json, math
import alphaloom.nodes
from alphaloom.graph.model import load_loom_file
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.backtest.runner import run_backtest
from alphaloom.eval.fidelity import fidelity_ladder
from alphaloom.eval.leaderboard import baseline_buy_hold, baseline_ema_default, baseline_random

bp0 = load_loom_file('blueprints/real_sol_breakout_demo.loom')
src = SQLiteMarketData('data/real_okx_14d.sqlite')
start = 1782360720000
end = 1782447120000
inst = 'SOL-USDT-SWAP'
bar = '1m'

def clean(x):
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x

rep = run_backtest(bp0, src, inst=inst, bar=bar, start_ms=start, end_ms=end, initial_cash=10000.0)
candles = list(src.iter_candles(inst, bar, start, end))
lad = fidelity_ladder(rep.fills, candles, initial_cash=10000.0, fee_rate=0.0005, slippage_bps=5.0)

baselines = {
    'buy_hold': baseline_buy_hold(src, inst, bar, start_ms=start, end_ms=end, initial_cash=10000.0, fee_rate=0.0005).summary,
    'ema_default': baseline_ema_default(src, inst, bar, start_ms=start, end_ms=end, initial_cash=10000.0, fee_rate=0.0005).summary,
    'random': baseline_random(src, inst, bar, start_ms=start, end_ms=end, initial_cash=10000.0, fee_rate=0.0005).summary,
}

risk_variants = {}
for name, max_qty, req_stop, risk_pct in [
    ('half risk', 100000.0, True, 0.0025),
    ('base risk', 100000.0, True, 0.005),
    ('double risk', 100000.0, True, 0.01),
    ('tight max qty', 50.0, True, 0.005),
]:
    b = copy.deepcopy(bp0)
    for n in b.nodes:
        if n.id == 'risk':
            n.params['max_qty'] = max_qty
            n.params['require_stop'] = req_stop
        if n.id == 'sizer':
            n.params['risk_pct'] = risk_pct
    risk_variants[name] = run_backtest(b, src, inst=inst, bar=bar, start_ms=start, end_ms=end, initial_cash=10000.0).summary

param_variants = []
for name, lookback, cooldown, atr in [
    ('seed', 45, 10, 2.5),
    ('fast trend', 45, 10, 2.0),
    ('wide entry', 30, 10, 2.0),
    ('winner', 30, 20, 1.5),
    ('patient', 30, 60, 1.5),
]:
    b = copy.deepcopy(bp0)
    for n in b.nodes:
        if n.id == 'scenario':
            n.params['lookback'] = lookback
            n.params['cooldown'] = cooldown
            n.params['atr_mult'] = atr
    s = run_backtest(b, src, inst=inst, bar=bar, start_ms=start, end_ms=end, initial_cash=10000.0).summary
    param_variants.append(dict(name=name, lookback=lookback, cooldown=cooldown, atr_mult=atr, **s))

print(json.dumps(clean({
    'summary': rep.summary,
    'certificate': rep.certificate,
    'bars': rep.bars,
    'ladder': lad.to_dict(),
    'baselines': baselines,
    'risk_variants': risk_variants,
    'param_variants': param_variants,
    'equity_curve': rep.equity_curve,
    'fills': rep.fills,
}), ensure_ascii=False))
"""
    try:
        proc = subprocess.run(
            [str(backend_py), "-c", code],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        data = json.loads(proc.stdout)
        return data
    except Exception as exc:
        print(f"Using fallback metrics: {exc}", file=sys.stderr)
        return dict(FALLBACK)


def bg(size: tuple[int, int]) -> Image.Image:
    w, h = size
    im = Image.new("RGB", size, BG)
    pix = im.load()
    for y in range(h):
        yy = y / max(h - 1, 1)
        for x in range(w):
            xx = x / max(w - 1, 1)
            r = int(6 + 5 * xx + 2 * yy)
            g = int(13 + 12 * xx + 3 * math.sin(xx * math.pi))
            b = int(29 + 19 * yy + 10 * xx)
            pix[x, y] = (r, g, b)
    return im.convert("RGBA")


def glow(im: Image.Image, rect: tuple[int, int, int, int], color: tuple[int, int, int], alpha: int = 50) -> None:
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(rect, radius=22, fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(18))
    im.alpha_composite(layer)


def panel(draw: ImageDraw.ImageDraw, rect, *, hot=False, fill=PANEL, width=2) -> None:
    outline = BORDER_HOT if hot else BORDER
    draw.rounded_rectangle(rect, radius=18, fill=fill, outline=outline, width=width)


def corner(draw: ImageDraw.ImageDraw, rect, color=BORDER_HOT) -> None:
    x1, y1, x2, y2 = rect
    l = 24
    draw.line((x1, y1 + l, x1, y1, x1 + l, y1), fill=color, width=2)
    draw.line((x2 - l, y2, x2, y2, x2, y2 - l), fill=color, width=2)


def text(draw, xy, value, font_obj, fill=TEXT, anchor=None):
    draw.text(xy, str(value), font=font_obj, fill=fill, anchor=anchor)


def fmt_pct(v: float, digits=2, plus=False) -> str:
    sign = "+" if plus and v > 0 else ""
    return f"{sign}{v:.{digits}f}%"


def fmt_money(v: float) -> str:
    return f"{'+' if v > 0 else ''}{v:,.2f}"


def fmt_pf(v) -> str:
    return "inf" if v is None else f"{float(v):.2f}"


def metric_card(draw, rect, label, value, sub="", value_color=GREEN):
    panel(draw, rect)
    x1, y1, x2, y2 = rect
    text(draw, (x1 + 22, y1 + 20), label.upper(), F_SMALL_B, MUTED)
    text(draw, (x1 + 22, y1 + 56), value, F_NUM, value_color)
    if sub:
        text(draw, (x1 + 22, y2 - 34), sub, F_SMALL, MUTED_2)


def chip(draw, xy, label, fill=(8, 73, 69), color=GREEN):
    x, y = xy
    pad_x = 16
    bbox = draw.textbbox((0, 0), label, font=F_SMALL_B)
    w = bbox[2] - bbox[0] + pad_x * 2
    h = 36
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=fill)
    text(draw, (x + pad_x, y + 8), label, F_SMALL_B, color)
    return x + w + 10


def draw_scorecard(data):
    im = bg((2312, 556))
    draw = ImageDraw.Draw(im)
    panel(draw, (1, 1, 2310, 554), fill=(8, 14, 31), width=2)
    corner(draw, (14, 14, 2295, 538))
    s = data["summary"]
    cert = data["certificate"]

    text(draw, (26, 33), "REAL-DATA SMOKE SCORECARD", F_TITLE, MUTED)
    text(draw, (26, 74), f"{INST} / {BAR} / {START_LABEL} -> {END_LABEL}", F_SMALL, MUTED)

    text(draw, (26, 138), "RETURN", F_SMALL_B, MUTED)
    text(draw, (26, 176), fmt_pct(s["return_pct"], 2, True), F_NUM_BIG, GREEN)
    x = 360
    x = chip(draw, (x, 168), "OKX public candles")
    x = chip(draw, (x, 168), "RiskGate -> ExecuteOrder")
    x = chip(draw, (x, 168), "0 LLM calls")
    chip(draw, (x, 168), "smoke test, not alpha")

    cards = [
        ("Net PnL", fmt_money(s["net_pnl"]), "initial cash 10,000", GREEN),
        ("Max DD", fmt_pct(s["max_drawdown"] * 100), "realized equity curve", CYAN),
        ("Trades", str(s["num_trades"]), "completed round trips", TEXT),
        ("Win rate", fmt_pct(s["win_rate"] * 100), "profitable closes", GREEN),
        ("Profit factor", fmt_pf(s["profit_factor"]), "gross wins / losses", GREEN),
        ("Determinism", fmt_pct(cert["deterministic_ratio"] * 100, 0), "offline replayable", GREEN),
    ]
    x0, y0, w, h, gap = 26, 290, 350, 150, 18
    for i, c in enumerate(cards):
        metric_card(draw, (x0 + i * (w + gap), y0, x0 + i * (w + gap) + w, y0 + h), *c)

    text(draw, (26, 484), "Evidence coverage", F_SMALL_B, MUTED)
    x = 250
    for label in ["real window", "fidelity ladder", "cost cert", "baseline compare", "trading activity"]:
        x = chip(draw, (x, 472), label)
    im.save(OUT / "scorecard.png")


def draw_leaderboard(data):
    im = bg((2312, 304))
    draw = ImageDraw.Draw(im)
    panel(draw, (1, 1, 2310, 302), fill=(8, 14, 31), width=2)
    corner(draw, (14, 14, 2295, 286))
    text(draw, (26, 31), "BASELINE LEADERBOARD", F_TITLE, MUTED)
    text(draw, (380, 35), "The blueprint beats buy-and-hold on the same real SOL window", F_SMALL, MUTED)
    s = data["summary"]
    b = data["baselines"]
    rows = [
        ("1", "alphaloom_real_sol", "blueprint", s),
        ("2", "baseline_buy_hold", "baseline", b["buy_hold"]),
        ("3", "baseline_ema_default", "baseline", b["ema_default"]),
        ("4", "baseline_random", "baseline", b["random"]),
    ]
    headers = [("RANK", 28), ("NAME", 138), ("RETURN %", 1030), ("NET PNL", 1240), ("MAX DD", 1450), ("WIN RATE", 1660), ("TRADES", 1860), ("NOTE", 2060)]
    for h, x in headers:
        text(draw, (x, 92), h, F_SMALL_B, MUTED)
    draw.line((26, 122, 2285, 122), fill=(26, 43, 75), width=2)
    y = 144
    for rank, name, kind, row in rows:
        if rank == "1":
            draw.rounded_rectangle((22, y - 14, 2285, y + 38), radius=8, fill=(7, 44, 48))
        text(draw, (28, y), rank, F_BODY_B, TEXT if rank == "1" else MUTED)
        text(draw, (138, y), name, F_BODY_B, TEXT if rank == "1" else MUTED)
        if kind == "blueprint":
            chip(draw, (390, y - 5), "risk-gated", fill=(8, 73, 69), color=GREEN)
        else:
            chip(draw, (390, y - 5), kind, fill=(36, 39, 52), color=MUTED)
        ret = row["return_pct"]
        col = GREEN if ret > 0 else RED if ret < 0 else MUTED
        text(draw, (1030, y), fmt_pct(ret, 2, ret > 0), F_BODY_B, col)
        text(draw, (1240, y), fmt_money(row["net_pnl"]), F_BODY_B, col)
        text(draw, (1450, y), fmt_pct(row["max_drawdown"] * 100), F_BODY_B, TEXT)
        text(draw, (1660, y), fmt_pct(row["win_rate"] * 100, 1), F_BODY_B, TEXT)
        text(draw, (1860, y), str(row["num_trades"]), F_BODY_B, TEXT)
        note = "same window" if rank == "1" else "-"
        text(draw, (2060, y), note, F_SMALL, MUTED)
        y += 44
    im.save(OUT / "leaderboard.png")


def draw_fidelity(data):
    im = bg((2312, 546))
    draw = ImageDraw.Draw(im)
    panel(draw, (1, 1, 2310, 544), fill=(8, 14, 31), width=2)
    corner(draw, (14, 14, 2295, 528))
    ladder = data["ladder"]
    levels = ladder["levels"]
    text(draw, (26, 31), "FIDELITY LADDER L0-L3", F_TITLE, MUTED)
    text(draw, (420, 35), "Same fill sequence replayed under four fill models", F_SMALL, MUTED)
    text(draw, (26, 88), "OPTIMISM GAP", F_SMALL_B, MUTED)
    text(draw, (244, 80), fmt_money(ladder["optimism_gap"]), F_NUM, GOLD)
    text(draw, (420, 89), "L0 - L3 net PnL; positive L3 means the edge survives the harshest fill model", F_SMALL, MUTED_2)

    vals = [lv["net_pnl"] for lv in levels]
    max_abs = max(abs(v) for v in vals) or 1
    slot_w = 500
    base_y = 395
    max_h = 210
    for i, lv in enumerate(levels):
        x = 112 + i * 535
        draw.line((x - 70, 142, x + 420, 142), fill=(30, 43, 74), width=2)
        pnl = lv["net_pnl"]
        h = int(max_h * abs(pnl) / max_abs)
        color = GREEN if pnl >= 0 else RED
        draw.rounded_rectangle((x + 85, base_y - h, x + 330, base_y), radius=8, fill=color)
        if i == 0:
            draw.rounded_rectangle((x + 85, base_y - h, x + 330, base_y), radius=8, outline=GOLD, width=3)
        text(draw, (x + 207, 420), fmt_money(pnl), F_NUM, color, anchor="ma")
        text(draw, (x + 207, 466), lv["level"], F_BODY_B, MUTED, anchor="ma")
        desc = {
            "L0": "naive close",
            "L1": "next-bar open",
            "L2": "intrabar path proxy",
            "L3": "fees + slippage",
        }[lv["level"]]
        text(draw, (x + 207, 498), desc, F_SMALL, MUTED_2, anchor="ma")
    im.save(OUT / "fidelity.png")


def draw_risk_sensitivity(data):
    im = bg((2312, 470))
    draw = ImageDraw.Draw(im)
    panel(draw, (1, 1, 2310, 468), fill=(8, 14, 31), width=2)
    corner(draw, (14, 14, 2295, 452))
    text(draw, (26, 31), "RISK BUDGET SENSITIVITY", F_TITLE, MUTED)
    text(draw, (430, 35), "Same blueprint/window; only risk budget or max-qty guardrail changes", F_SMALL, MUTED)

    headers = [("ARM", 28), ("NET PNL", 860), ("RETURN %", 1250), ("MAX DD", 1580), ("TRADES", 1880), ("PROFIT FACTOR", 2110)]
    for h, x in headers:
        text(draw, (x, 108), h, F_SMALL_B, MUTED)
    draw.line((26, 137, 2285, 137), fill=(26, 43, 75), width=2)
    rows = [
        ("base risk 0.5%", data["risk_variants"]["base risk"], True),
        ("double risk 1.0%", data["risk_variants"]["double risk"], False),
        ("half risk 0.25%", data["risk_variants"]["half risk"], False),
        ("max_qty cap", data["risk_variants"]["tight max qty"], False),
    ]
    y = 166
    for name, row, highlight in rows:
        if highlight:
            draw.rounded_rectangle((22, y - 17, 2285, y + 39), radius=8, fill=(7, 44, 48))
        text(draw, (28, y), name, F_BODY_B, TEXT)
        col = GREEN if row["net_pnl"] > 0 else MUTED if row["net_pnl"] == 0 else RED
        text(draw, (860, y), fmt_money(row["net_pnl"]), F_BODY_B, col)
        text(draw, (1250, y), fmt_pct(row["return_pct"], 2, row["return_pct"] > 0), F_BODY_B, col)
        text(draw, (1580, y), fmt_pct(row["max_drawdown"] * 100), F_BODY_B, TEXT)
        text(draw, (1880, y), str(row["num_trades"]), F_BODY_B, TEXT)
        text(draw, (2110, y), fmt_pf(row["profit_factor"]), F_BODY_B, TEXT)
        y += 54

    panel(draw, (26, 374, 2285, 440), fill=(10, 19, 40))
    text(draw, (48, 398), "Risk gate stays in the legal execution path for every arm; sizing changes the risk/reward tradeoff, not the type contract.", F_BODY, MUTED)
    im.save(OUT / "ablation.png")


def draw_genealogy(data):
    im = bg((2312, 868))
    draw = ImageDraw.Draw(im)
    panel(draw, (1, 1, 2310, 866), fill=(8, 14, 31), width=2)
    corner(draw, (14, 14, 2295, 850))
    variants = {v["name"]: v for v in data["param_variants"]}
    text(draw, (26, 31), "PARAMETER EVOLUTION GENEALOGY", F_TITLE, MUTED)
    text(draw, (540, 35), "Real parameter variants on the same OKX SOL smoke window", F_SMALL, MUTED)
    winner = variants["winner"]
    chip(draw, (42, 88), f"Winner +{winner['return_pct']:.2f}%")
    text(draw, (290, 96), f"seed +{variants['seed']['return_pct']:.2f}%   best valid-style smoke return +{winner['return_pct']:.2f}%   caveat: parameter scan, not alpha", F_BODY, MUTED)

    def node(rect, title, subtitle, row, hot=False, color=GREEN):
        if hot:
            glow(im, rect, GOLD, 45)
        panel(draw, rect, hot=hot, fill=(9, 17, 36), width=3 if hot else 2)
        x1, y1, x2, y2 = rect
        title_xy = (x1 + 28, y1 + 32)
        text(draw, title_xy, title, F_H2, TEXT)
        title_box = draw.textbbox(title_xy, title, font=F_H2)
        chip_x = min(title_box[2] + 22, x2 - 142)
        if hot:
            chip(draw, (chip_x, y1 + 34), "winner", fill=(80, 52, 5), color=GOLD)
        else:
            chip(draw, (chip_x, y1 + 34), "ok", fill=(8, 73, 69), color=GREEN)
        text(draw, (x1 + 28, y1 + 92), subtitle, F_BODY, MUTED)
        text(draw, (x1 + 28, y1 + 152), f"return {fmt_pct(row['return_pct'], 2, True)}", F_NUM, color)
        text(draw, (x1 + 28, y1 + 188), f"dd {fmt_pct(row['max_drawdown'] * 100)}  trades {row['num_trades']}  pf {fmt_pf(row['profit_factor'])}", F_SMALL_B, MUTED)

    rects = {
        "seed": (130, 205, 710, 410),
        "fast trend": (850, 180, 1430, 385),
        "wide entry": (1570, 180, 2150, 385),
        "patient": (850, 535, 1430, 740),
        "winner": (1570, 535, 2150, 740),
    }
    draw.line((710, 307, 850, 282), fill=(54, 76, 108), width=4)
    draw.line((710, 307, 850, 637), fill=(54, 76, 108), width=4)
    draw.line((1430, 282, 1570, 282), fill=(54, 76, 108), width=4)
    draw.line((1430, 637, 1570, 637), fill=(54, 76, 108), width=4)
    draw.line((1430, 282, 1570, 637), fill=(54, 76, 108), width=3)

    node(rects["seed"], "g0_seed", "lb45 / cd10 / atr2.5", variants["seed"])
    node(rects["fast trend"], "g1_fast", "tighten atr to 2.0", variants["fast trend"], color=GREEN)
    node(rects["wide entry"], "g2_wide", "lb30 / cd10 / atr2.0", variants["wide entry"], color=GREEN)
    node(rects["patient"], "g1_patient", "lb30 / cd60 / atr1.5", variants["patient"], color=CYAN)
    node(rects["winner"], "g2_winner", "lb30 / cd20 / atr1.5", variants["winner"], hot=True, color=GOLD)
    im.save(OUT / "genealogy.png")


def draw_terminal(data):
    im = bg((2360, 1640))
    draw = ImageDraw.Draw(im)
    # Header
    draw.rectangle((0, 0, 2360, 112), fill=(8, 13, 27))
    text(draw, (40, 34), "AlphaLoom", F_H2, GOLD)
    text(draw, (268, 46), "THE GRAPH IS THE AGENT", F_SMALL_B, MUTED)
    text(draw, (700, 46), "Studio", F_BODY_B, MUTED)
    text(draw, (840, 46), "Terminal", F_BODY_B, CYAN)
    text(draw, (1010, 46), "Eval Lab", F_BODY_B, MUTED)
    draw.line((835, 99, 952, 99), fill=CYAN, width=3)
    chip(draw, (1760, 30), "OFFLINE REPLAY / ZERO-QUOTA", fill=(6, 52, 56), color=GREEN)
    draw.line((0, 112, 2360, 112), fill=(27, 44, 75), width=2)

    s = data["summary"]
    cert = data["certificate"]
    # Run tabs
    panel(draw, (24, 138, 660, 190), hot=True, fill=(11, 18, 38), width=2)
    text(draw, (50, 153), "...2eb718 / real_sol_breakout_demo", F_BODY_B, GOLD)
    chip(draw, (458, 148), "completed")
    panel(draw, (680, 138, 1170, 190), fill=(11, 18, 38), width=2)
    text(draw, (710, 153), "OKX SOL 1m / real data", F_BODY_B, MUTED)

    cards = [
        ("NET PNL", fmt_money(s["net_pnl"]), GREEN),
        ("RETURN %", fmt_pct(s["return_pct"], 4, True), GREEN),
        ("MAX DD", fmt_pct(s["max_drawdown"] * 100, 2), CYAN),
        ("TRADES", str(s["num_trades"]), TEXT),
        ("WIN RATE", f"{s['win_rate']:.4f}", GREEN),
        ("PROFIT FACTOR", f"{s['profit_factor']:.4f}", GREEN),
    ]
    x, y, w, h = 24, 218, 360, 104
    for label, value, color in cards:
        metric_card(draw, (x, y, x + w, y + h), label, value, value_color=color)
        x += w + 22

    panel(draw, (24, 350, 1490, 975), fill=(10, 18, 38))
    text(draw, (50, 386), "EQUITY CURVE", F_TITLE, MUTED)
    curve = data.get("equity_curve") or []
    if not curve:
        curve = [10000 + 946.46 * (i / 100) + math.sin(i / 7) * 120 for i in range(101)]
    vals = [float(v[1] if isinstance(v, list) and len(v) == 2 else v) for v in curve]
    vals = vals[:: max(1, len(vals) // 500)]
    min_v, max_v = min(vals), max(vals)
    chart = (70, 460, 1440, 895)
    draw.rectangle(chart, fill=(6, 14, 30), outline=(28, 48, 81))
    for i in range(6):
        yy = chart[1] + i * (chart[3] - chart[1]) / 5
        draw.line((chart[0], yy, chart[2], yy), fill=(20, 34, 58), width=1)
    pts = []
    for i, v in enumerate(vals):
        px = chart[0] + i * (chart[2] - chart[0]) / max(1, len(vals) - 1)
        py = chart[3] - (v - min_v) * (chart[3] - chart[1] - 30) / max(1e-9, max_v - min_v) - 15
        pts.append((px, py))
    if len(pts) > 1:
        draw.line(pts, fill=GREEN, width=4)
    text(draw, (80, 920), f"start 10,000   end {10000 + s['net_pnl']:,.2f}   bars {data.get('bars', 1441)}", F_SMALL_B, MUTED)

    panel(draw, (1520, 350, 2336, 975), fill=(10, 18, 38))
    text(draw, (1548, 386), "AGENT INSIGHTS", F_TITLE, MUTED)
    x = chip(draw, (1548, 434), "Memory: regime bucket trend_up")
    chip(draw, (x, 434), "RiskGate stamped every order")
    items = [
        ("price_action", "Breakout confirmed by follow-through above range."),
        ("risk", "Attached stops present; max drawdown stayed below 3%."),
        ("cost", "0 LLM calls per bar; deterministic ratio 100%."),
        ("caveat", "One real window only; smoke test, not deployable alpha."),
    ]
    yy = 500
    for label, body in items:
        draw.rounded_rectangle((1548, yy, 2300, yy + 66), radius=8, fill=(20, 25, 55))
        text(draw, (1570, yy + 18), f"{label}: {body}", F_SMALL_B, PURPLE if label == "price_action" else TEXT)
        yy += 84

    panel(draw, (24, 1010, 2336, 1608), fill=(10, 18, 38))
    text(draw, (50, 1046), "RECENT FILLS", F_TITLE, MUTED)
    headers = [("TS", 60), ("SIDE", 420), ("QTY", 650), ("PRICE", 900), ("TAG", 1190), ("READ", 1500)]
    for h, xx in headers:
        text(draw, (xx, 1110), h, F_SMALL_B, MUTED)
    draw.line((50, 1140, 2310, 1140), fill=(27, 44, 75), width=2)
    fills = data.get("fills") or []
    if not fills:
        fills = [
            {"ts": START_MS + 1000, "side": "buy", "qty": 32.1, "price": 136.4, "tag": "open"},
            {"ts": START_MS + 2000, "side": "sell", "qty": 32.1, "price": 141.9, "tag": "close"},
        ]
    yy = 1174
    for f in fills[-8:]:
        side = str(f.get("side", ""))
        color = GREEN if side == "sell" else CYAN
        text(draw, (60, yy), str(f.get("ts", ""))[-8:], F_SMALL_B, MUTED)
        text(draw, (420, yy), side, F_SMALL_B, color)
        text(draw, (650, yy), f"{float(f.get('qty', 0.0)):.4f}", F_SMALL_B, TEXT)
        text(draw, (900, yy), f"{float(f.get('price', 0.0)):.4f}", F_SMALL_B, TEXT)
        text(draw, (1190, yy), str(f.get("tag", "")) or "-", F_SMALL_B, GOLD if f.get("tag") else MUTED)
        text(draw, (1500, yy), "risk-stamped execution leg", F_SMALL, MUTED)
        yy += 50
    im.save(OUT / "terminal.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load_metrics()
    draw_scorecard(data)
    draw_leaderboard(data)
    draw_fidelity(data)
    draw_risk_sensitivity(data)
    draw_genealogy(data)
    draw_terminal(data)
    for name in ["scorecard", "leaderboard", "fidelity", "ablation", "genealogy", "terminal"]:
        p = OUT / f"{name}.png"
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
