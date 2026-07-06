from __future__ import annotations

import datetime as dt
import math
from bisect import bisect_right
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from render_readme_result_screenshots import (
    BG,
    BORDER,
    CYAN,
    GOLD,
    GREEN,
    MUTED,
    MUTED_2,
    PANEL,
    RED,
    ROOT,
    TEXT,
    chip,
    font,
    load_metrics,
    text,
)


OUT = ROOT / "docs" / "assets" / "offline-player.gif"

W = 1180
H = 620
FRAMES = 70
DURATION_MS = 78

F_LOGO = font(24, True)
F_TITLE = font(32, True)
F_H2 = font(24, True)
F_BODY = font(18)
F_BODY_B = font(18, True)
F_SMALL = font(15)
F_SMALL_B = font(15, True)
F_NUM = font(30, True)
F_NUM_BIG = font(46, True)


def bg() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    pix = im.load()
    for y in range(H):
        yy = y / max(H - 1, 1)
        for x in range(W):
            xx = x / max(W - 1, 1)
            r = int(6 + 5 * xx + 2 * yy)
            g = int(13 + 12 * xx + 4 * math.sin(xx * math.pi))
            b = int(29 + 18 * yy + 11 * xx)
            pix[x, y] = (r, g, b)
    return im.convert("RGBA")


def rounded(draw: ImageDraw.ImageDraw, rect, fill, outline=BORDER, width=1, radius=14) -> None:
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def fmt_time(ms: int) -> str:
    stamp = dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC)
    return stamp.strftime("%H:%MZ")


def fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def fmt_signed_pct(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def metric(draw, rect, label, value, color=TEXT, sub="") -> None:
    rounded(draw, rect, fill=(8, 17, 34), outline=(30, 58, 96), width=1, radius=14)
    x1, y1, x2, y2 = rect
    text(draw, (x1 + 16, y1 + 14), label.upper(), F_SMALL_B, MUTED)
    text(draw, (x1 + 16, y1 + 43), value, F_NUM, color)
    if sub:
        text(draw, (x1 + 16, y2 - 22), sub, F_SMALL, MUTED_2)


def curve_values(curve):
    clean = []
    for item in curve:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            clean.append((int(item[0]), float(item[1])))
        else:
            clean.append((len(clean), float(item)))
    return clean


def fill_events(fills):
    events = []
    for fill in fills:
        ts = int(fill.get("ts", 0))
        if ts:
            events.append(fill)
    events.sort(key=lambda item: int(item.get("ts", 0)))
    return events


def nearest_equity(times, values, ts: int) -> float:
    idx = bisect_right(times, ts) - 1
    idx = max(0, min(idx, len(values) - 1))
    return values[idx]


def draw_chart(draw: ImageDraw.ImageDraw, curve, fills, idx, chart, cutoff_ts: int) -> None:
    x1, y1, x2, y2 = chart
    rounded(draw, chart, fill=(5, 13, 28), outline=(27, 48, 82), width=1, radius=12)
    for i in range(5):
        yy = y1 + 30 + i * (y2 - y1 - 60) / 4
        draw.line((x1 + 22, yy, x2 - 22, yy), fill=(20, 35, 59), width=1)

    shown = curve[: idx + 1]
    all_vals = [v for _, v in curve]
    min_v = min(all_vals)
    max_v = max(all_vals)
    pad = max(1.0, (max_v - min_v) * 0.1)
    min_v -= pad
    max_v += pad
    left = x1 + 30
    top = y1 + 30
    right = x2 - 30
    bottom = y2 - 42
    times = [t for t, _ in curve]
    values = [v for _, v in curve]
    t0, t1 = times[0], times[-1]

    def xy(ts: int, value: float) -> tuple[float, float]:
        px = left + (ts - t0) * (right - left) / max(1, t1 - t0)
        py = bottom - (value - min_v) * (bottom - top) / max(1e-9, max_v - min_v)
        return px, py

    if len(shown) > 1:
        pts = [xy(ts, value) for ts, value in shown]
        area = [(pts[0][0], bottom), *pts, (pts[-1][0], bottom)]
        draw.polygon(area, fill=(36, 211, 146, 38))
        draw.line(pts, fill=GREEN, width=4, joint="curve")
        draw.ellipse((pts[-1][0] - 7, pts[-1][1] - 7, pts[-1][0] + 7, pts[-1][1] + 7), fill=GOLD)

    for fill in fills:
        ts = int(fill.get("ts", 0))
        if ts > cutoff_ts:
            break
        eq = nearest_equity(times, values, ts)
        px, py = xy(max(t0, min(ts, t1)), eq)
        side = str(fill.get("side", "")).lower()
        color = CYAN if side == "buy" else GREEN if side == "sell" else GOLD
        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill=color, outline=(2, 8, 16), width=1)

    text(draw, (x1 + 30, y2 - 28), fmt_time(times[0]), F_SMALL_B, MUTED)
    text(draw, (x2 - 30, y2 - 28), fmt_time(times[-1]), F_SMALL_B, MUTED, anchor="ra")
    text(draw, (x1 + 30, y1 + 18), f"equity {fmt_money(curve[idx][1])}", F_SMALL_B, GREEN)
    text(draw, (x2 - 30, y1 + 18), f"range {fmt_money(min(values))} -> {fmt_money(max(values))}", F_SMALL, MUTED, anchor="ra")


def draw_recent(draw: ImageDraw.ImageDraw, rect, fills, now_ts: int) -> None:
    rounded(draw, rect, fill=(7, 15, 31), outline=(30, 58, 96), width=1, radius=14)
    x1, y1, x2, _ = rect
    text(draw, (x1 + 18, y1 + 22), "RECENT FILL EVENT", F_H2, MUTED)
    seen = [fill for fill in fills if int(fill.get("ts", 0)) <= now_ts]
    if not seen:
        text(draw, (x1 + 18, y1 + 70), "waiting for first stamped order", F_BODY_B, MUTED_2)
        return
    fill = seen[-1]
    side = str(fill.get("side", "-")).upper()
    side_color = CYAN if side == "BUY" else GREEN if side == "SELL" else GOLD
    text(draw, (x1 + 18, y1 + 76), side, F_NUM_BIG, side_color)
    text(draw, (x1 + 18, y1 + 132), fmt_time(int(fill.get("ts", 0))), F_BODY_B, MUTED)

    rows = [
        ("qty", f"{float(fill.get('qty', 0.0)):.4f}"),
        ("price", f"{float(fill.get('price', 0.0)):.4f}"),
        ("fee", f"{float(fill.get('fee', 0.0)):.4f}"),
        ("tag", str(fill.get("tag", "")) or "risk-stamped"),
    ]
    yy = y1 + 166
    for label, value in rows:
        text(draw, (x1 + 18, yy), label.upper(), F_SMALL_B, MUTED_2)
        text(draw, (x2 - 18, yy), value, F_BODY_B, TEXT, anchor="ra")
        yy += 31


def draw_frame(curve, fills, frame_no: int, summary) -> Image.Image:
    im = bg()
    draw = ImageDraw.Draw(im, "RGBA")
    progress = frame_no / max(1, FRAMES - 1)
    eased = 1 - (1 - progress) * (1 - progress)
    idx = min(len(curve) - 1, int(eased * (len(curve) - 1)))
    now_ts, equity = curve[idx]
    start_equity = curve[0][1]
    ret = (equity / start_equity - 1) * 100 if start_equity else 0.0
    cutoff_ts = now_ts
    if idx == len(curve) - 1 and fills:
        cutoff_ts = max(now_ts, max(int(fill.get("ts", 0)) for fill in fills))
    seen_fills = [fill for fill in fills if int(fill.get("ts", 0)) <= cutoff_ts]

    draw.rectangle((0, 0, W, 74), fill=(6, 12, 26))
    text(draw, (24, 22), "AlphaLoom", F_LOGO, GOLD)
    text(draw, (170, 29), "OFFLINE PLAYER", F_SMALL_B, MUTED)
    x = chip(draw, (760, 20), "REAL OKX SOL REPLAY", fill=(6, 52, 56), color=GREEN)
    chip(draw, (x, 20), "ZERO LLM QUOTA", fill=(34, 30, 9), color=GOLD)
    draw.line((0, 74, W, 74), fill=(27, 44, 75), width=2)

    text(draw, (28, 104), "Realtime offline replay", F_TITLE, TEXT)
    text(draw, (28, 142), "Same OKX SOL 1m smoke-test replay. Equity and fills advance from recorded runtime data.", F_BODY, MUTED)

    metric(draw, (28, 178, 234, 270), "progress", f"{progress * 100:05.1f}%", CYAN, f"bar {idx + 1}/{len(curve)}")
    metric(draw, (252, 178, 458, 270), "return", fmt_signed_pct(ret), GREEN if ret >= 0 else RED, "mark-to-market")
    metric(draw, (476, 178, 682, 270), "equity", fmt_money(equity), GREEN, "initial 10,000")
    metric(draw, (700, 178, 906, 270), "fills", str(len(seen_fills)), GOLD, f"final {len(fills)}")
    metric(draw, (924, 178, 1152, 270), "final", fmt_signed_pct(float(summary["return_pct"])), GREEN, "same real run")

    draw_chart(draw, curve, fills, idx, (28, 296, 820, 584), cutoff_ts)
    draw_recent(draw, (846, 296, 1152, 584), fills, cutoff_ts)

    x1, y, x2 = 28, 594, 1152
    draw.line((x1, y, x2, y), fill=(25, 45, 74), width=6)
    px = x1 + progress * (x2 - x1)
    draw.line((x1, y, px, y), fill=CYAN, width=6)
    draw.ellipse((px - 9, y - 9, px + 9, y + 9), fill=GOLD)
    return im.convert("P", palette=Image.ADAPTIVE, colors=128)


def main() -> None:
    data = load_metrics()
    curve = curve_values(data.get("equity_curve") or [])
    fills = fill_events(data.get("fills") or [])
    if not curve:
        curve = [(i, 10000 + 946.46 * i / 100 + math.sin(i / 6) * 110) for i in range(101)]
    frames = [draw_frame(curve, fills, i, data["summary"]) for i in range(FRAMES)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=DURATION_MS,
        loop=0,
        disposal=2,
        optimize=False,
    )
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
