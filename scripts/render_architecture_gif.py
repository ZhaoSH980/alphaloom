from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
LANG = "zh" if any(arg.lower() in {"zh", "--zh", "--lang=zh"} for arg in sys.argv[1:]) else "en"
OUT = (
    ROOT / "docs" / "assets" / "zh" / "zh-architecture-loop.gif"
    if LANG == "zh"
    else ROOT / "docs" / "assets" / "architecture-loop.gif"
)

BASE_W, BASE_H = 1000, 384
OUT_W, OUT_H = 1600, 614
AA_SCALE = 2
W, H = OUT_W * AA_SCALE, OUT_H * AA_SCALE
X_SCALE = W / BASE_W
Y_SCALE = H / BASE_H
AVG_SCALE = (X_SCALE + Y_SCALE) / 2
FRAME_COUNT = 72
FRAME_MS = 70


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if LANG == "zh":
        candidates = [
            Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
            Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/Dengb.ttf" if bold else "C:/Windows/Fonts/Deng.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
        ]
    else:
        candidates = [
            Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


FONT_TITLE = font(round(21 * AVG_SCALE), True)
FONT_SUB = font(round(12 * AVG_SCALE))
FONT_LABEL = font(round(17 * AVG_SCALE), True)
FONT_TINY = font(round(10 * AVG_SCALE), True)


def sc(value: float) -> int:
    return max(1, round(value * AVG_SCALE))


def xy(point: tuple[float, float]) -> tuple[float, float]:
    return point[0] * X_SCALE, point[1] * Y_SCALE


def points_xy(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [xy(point) for point in points]


def rect_xy(rect: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    x, y, w, h = rect
    return x * X_SCALE, y * Y_SCALE, (x + w) * X_SCALE, (y + h) * Y_SCALE


def box_xy(cx: float, cy: float, r: float) -> tuple[float, float, float, float]:
    return (cx - r) * X_SCALE, (cy - r) * Y_SCALE, (cx + r) * X_SCALE, (cy + r) * Y_SCALE


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def gradient_bg() -> Image.Image:
    img = Image.new("RGB", (W, H), "#061014")
    pix = img.load()
    for y in range(H):
        yy = y / max(H - 1, 1)
        for x in range(W):
            xx = x / max(W - 1, 1)
            r = int(5 + 7 * xx + 4 * yy)
            g = int(15 + 14 * xx + 9 * (1 - yy))
            b = int(20 + 20 * xx + 15 * yy)
            pix[x, y] = (r, g, b)
    return img


BG = gradient_bg()


TEXT = {
    "en": {
        "title_1": "Compile-gated trading",
        "title_2": "agent loop",
        "subtitle": "Input becomes a visual graph only after cost, causality, and risk contracts compile.",
        "risk_line_1": "legal order path",
        "risk_line_2": "stamp required",
        "loop_note": "compile errors become repair hints; held-out winners feed the next blueprint",
        "cards": [
            ("Input", "", "intent + data", "cyan", (28, 112, 105, 98)),
            ("Blueprint", "Studio", "typed .loom graph", "cyan", (151, 112, 105, 98)),
            ("Compiler", "", "pins, cycles, cost", "cyan", (274, 112, 105, 98)),
            ("RiskGate", "", "risk stamp required", "risk", (397, 112, 105, 98)),
            ("Runtime", "Broker", "fills, stops, equity", "cyan", (520, 112, 105, 98)),
            ("Recorder", "", "node I/O replay", "cyan", (643, 112, 105, 98)),
            ("Eval Lab", "", "fidelity, baselines", "cyan", (766, 112, 105, 98)),
            ("Copilot", "Evolution", "mutate + repair", "cyan", (889, 112, 105, 98)),
        ],
        "chips": [
            ("TYPE CONTRACT", "raw signal cannot enter ExecuteOrder", (63, 312, 267, 45)),
            ("OFFLINE REPLAY", "recorded LLM calls, zero quota demo", (367, 312, 267, 45)),
            ("REAL DATA CHECK", "OKX candles, exact window, honest caveats", (670, 312, 267, 45)),
        ],
    },
    "zh": {
        "title_1": "编译门控交易",
        "title_2": "Agent 闭环",
        "subtitle": "输入只有通过成本、因果和风控合约编译后，才会进入可视化蓝图。",
        "risk_line_1": "合法下单路径",
        "risk_line_2": "需要风控盖章",
        "loop_note": "编译错误变成修复提示；留出集赢家进入下一版蓝图",
        "cards": [
            ("输入", "", "意图 + 数据", "cyan", (28, 112, 105, 98)),
            ("蓝图", "工坊", "类型 .loom 图", "cyan", (151, 112, 105, 98)),
            ("编译器", "", "pin / 环 / 成本", "cyan", (274, 112, 105, 98)),
            ("风控门", "", "需要 risk stamp", "risk", (397, 112, 105, 98)),
            ("运行时", "Broker", "成交/止损/权益", "cyan", (520, 112, 105, 98)),
            ("记录器", "", "节点 I/O 回放", "cyan", (643, 112, 105, 98)),
            ("评估室", "", "保真度/基线", "cyan", (766, 112, 105, 98)),
            ("Copilot", "进化", "变异 + 修复", "cyan", (889, 112, 105, 98)),
        ],
        "chips": [
            ("类型合约", "裸信号不能进入 ExecuteOrder", (63, 312, 267, 45)),
            ("离线回放", "已录制 LLM 调用，零配额演示", (367, 312, 267, 45)),
            ("真实数据检查", "OKX K线，精确窗口，诚实 caveat", (670, 312, 267, 45)),
        ],
    },
}[LANG]

CARDS = TEXT["cards"]
CHIPS = TEXT["chips"]


def center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def left_edge(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, _w, h = rect
    return x, y + h / 2


def right_edge(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = rect
    return x + w, y + h / 2


def append_line(points: list[tuple[float, float]], a: tuple[float, float], b: tuple[float, float], count: int) -> None:
    if not points:
        points.append(a)
    for i in range(1, count + 1):
        t = i / count
        points.append((lerp(a[0], b[0], t), lerp(a[1], b[1], t)))


def sample_curve(p0, p1, p2, p3, count: int) -> list[tuple[float, float]]:
    pts = []
    for i in range(count):
        t = i / max(count - 1, 1)
        x = (
            (1 - t) ** 3 * p0[0]
            + 3 * (1 - t) ** 2 * t * p1[0]
            + 3 * (1 - t) * t**2 * p2[0]
            + t**3 * p3[0]
        )
        y = (
            (1 - t) ** 3 * p0[1]
            + 3 * (1 - t) ** 2 * t * p1[1]
            + 3 * (1 - t) * t**2 * p2[1]
            + t**3 * p3[1]
        )
        pts.append((x, y))
    return pts


def build_path() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    centers = [center(c[4]) for c in CARDS]
    for i in range(len(CARDS) - 1):
        append_line(pts, centers[i], right_edge(CARDS[i][4]), 6)
        append_line(pts, right_edge(CARDS[i][4]), left_edge(CARDS[i + 1][4]), 10)
        append_line(pts, left_edge(CARDS[i + 1][4]), centers[i + 1], 6)
    last_drop = (centers[-1][0], centers[-1][1] + 70)
    first_drop = (centers[0][0], centers[0][1] + 70)
    append_line(pts, centers[-1], last_drop, 8)
    pts.extend(
        sample_curve(
            last_drop,
            (centers[-1][0], 292),
            (centers[0][0], 292),
            first_drop,
            92,
        )[1:]
    )
    append_line(pts, first_drop, centers[0], 8)
    return pts


PATH = build_path()


def active_card_index(point: tuple[float, float]) -> int | None:
    px, py = point
    for i, (_line1, _line2, _sub, _kind, rect) in enumerate(CARDS):
        x, y, w, h = rect
        if x - 8 <= px <= x + w + 8 and y - 8 <= py <= y + h + 8:
            return i
    return None


def distance_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def draw_glow(draw_img: Image.Image, rect: tuple[int, int, int, int], color: tuple[int, int, int], alpha: int) -> None:
    x1, y1, x2, y2 = rect_xy(rect)
    layer = Image.new("RGBA", draw_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pad = sc(4)
    d.rounded_rectangle((x1 - pad, y1 - pad, x2 + pad, y2 + pad), radius=sc(18), fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(sc(12)))
    draw_img.alpha_composite(layer)


def draw_arrow(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], color, width: int = 2) -> None:
    pa = xy(a)
    pb = xy(b)
    draw.line((pa, pb), fill=color, width=sc(width))
    angle = math.atan2(pb[1] - pa[1], pb[0] - pa[0])
    tip = pb
    head = sc(9)
    left = (tip[0] - head * math.cos(angle - 0.45), tip[1] - head * math.sin(angle - 0.45))
    right = (tip[0] - head * math.cos(angle + 0.45), tip[1] - head * math.sin(angle + 0.45))
    draw.polygon([tip, left, right], fill=color)


def draw_frame(frame: int) -> Image.Image:
    progress = frame / FRAME_COUNT
    img = BG.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Soft market-like contour lines.
    draw.line(
        points_xy([(34, 338), (160, 300 + 8 * math.sin(progress * 6)), (315, 315), (510, 292), (765, 305), (960, 264)]),
        fill=(20, 61, 53, 160),
        width=sc(2),
    )
    draw.line(
        points_xy([(42, 77), (158, 48), (300, 64), (490, 45), (724, 55), (965, 37)]),
        fill=(18, 61, 75, 150),
        width=sc(2),
    )
    draw.text(xy((48, 23)), TEXT["title_1"], fill="#F8FAFC", font=FONT_TITLE)
    draw.text(xy((48, 48)), TEXT["title_2"], fill="#F8FAFC", font=FONT_TITLE)
    draw.text(
        xy((48, 78)),
        TEXT["subtitle"],
        fill="#9FB4C0",
        font=FONT_SUB,
    )

    p_idx = int(progress * len(PATH)) % len(PATH)
    point = PATH[p_idx]
    active_idx = active_card_index(point)
    for i, (line1, line2, sub, kind, rect) in enumerate(CARDS):
        x, y, w, h = rect
        active = i == active_idx
        pulse = 0.5 + 0.5 * math.sin(progress * math.tau * 3 + i)
        if active:
            color = (48, 231, 213)
            draw_glow(img, rect, color, 72 + int(45 * pulse))
        fill = "#0B1C27" if active else "#08141C"
        outline = "#7DD3FC"
        outline_alpha = 255 if active else 98
        draw.rounded_rectangle(
            rect_xy(rect),
            radius=sc(14),
            fill=fill,
            outline=outline,
            width=sc(3 if active else 1),
        )
        if not active:
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rounded_rectangle(rect_xy(rect), radius=sc(14), fill=(0, 0, 0, 28))
            img.alpha_composite(overlay)
        # Re-draw outline after dim overlay.
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            rect_xy(rect),
            radius=sc(14),
            outline=(125, 211, 252, outline_alpha),
            width=sc(3 if active else 1),
        )
        text_y = y + 28 if line2 else y + 39
        draw.text(xy((x + 19, text_y)), line1, fill="#F8FAFC", font=FONT_LABEL)
        if line2:
            draw.text(xy((x + 19, y + 51)), line2, fill="#F8FAFC", font=FONT_LABEL)
        if kind == "risk":
            draw.text(xy((x + 14, y + 67)), TEXT["risk_line_1"], fill="#FDE68A", font=FONT_TINY)
            draw.text(xy((x + 14, y + 81)), TEXT["risk_line_2"], fill="#B6A56E", font=FONT_SUB)
        else:
            draw.text(xy((x + 14, y + 77)), sub, fill="#9FB4C0", font=FONT_SUB)

    centers = [center(c[4]) for c in CARDS]
    for i in range(len(CARDS) - 1):
        start = right_edge(CARDS[i][4])
        end = left_edge(CARDS[i + 1][4])
        near = distance_to_segment(point, start, end) < 18
        col = (118, 228, 247, 235) if near else (85, 117, 128, 105)
        draw_arrow(draw, start, end, col, 3 if near else 2)

    # Return loop.
    loop = sample_curve(
        (centers[-1][0], centers[-1][1] + 70),
        (centers[-1][0], 292),
        (centers[0][0], 292),
        (centers[0][0], centers[0][1] + 70),
        92,
    )
    on_loop = active_idx is None and point[1] > 224
    loop_color = (118, 228, 247, 210) if on_loop else (80, 112, 120, 95)
    for a, b in zip(loop, loop[1:]):
        draw.line((xy(a), xy(b)), fill=loop_color, width=sc(2))
    draw.text(
        xy((382, 282)),
        TEXT["loop_note"],
        fill=(182, 243, 238, 205),
        font=FONT_TINY,
    )

    # Moving packet and trail.
    trail = []
    for k in range(15):
        trail.append(PATH[(p_idx - k) % len(PATH)])
    for k, trail_point in enumerate(reversed(trail)):
        alpha = int(20 + 140 * (k / max(len(trail) - 1, 1)))
        r = int(2 + 4 * (k / max(len(trail) - 1, 1)))
        draw.ellipse(
            box_xy(trail_point[0], trail_point[1], r),
            fill=(48, 231, 213, alpha),
        )
    draw.ellipse(box_xy(point[0], point[1], 8), fill=(250, 204, 21, 245))
    draw.ellipse(box_xy(point[0], point[1], 3), fill=(255, 255, 255, 250))
    if active_idx is not None:
        line1, line2, sub, kind, rect = CARDS[active_idx]
        x, y, _w, _h = rect
        text_y = y + 28 if line2 else y + 39
        draw.text(xy((x + 19, text_y)), line1, fill="#F8FAFC", font=FONT_LABEL)
        if line2:
            draw.text(xy((x + 19, y + 51)), line2, fill="#F8FAFC", font=FONT_LABEL)
        if kind == "risk":
            draw.text(xy((x + 14, y + 67)), TEXT["risk_line_1"], fill="#FDE68A", font=FONT_TINY)
            draw.text(xy((x + 14, y + 81)), TEXT["risk_line_2"], fill="#B6A56E", font=FONT_SUB)
        else:
            draw.text(xy((x + 14, y + 77)), sub, fill="#9FB4C0", font=FONT_SUB)

    for i, (title, sub, rect) in enumerate(CHIPS):
        x, y, w, h = rect
        active = (
            (active_idx is not None and active_idx <= 3 and i == 0)
            or (active_idx is not None and 4 <= active_idx <= 5 and i == 1)
            or (active_idx is not None and active_idx >= 6 and i == 2)
        )
        outline = (125, 211, 252, 120 + (80 if active else 0))
        draw.rounded_rectangle(rect_xy(rect), radius=sc(12), fill=(5, 15, 20, 230), outline=outline, width=sc(1))
        draw.text(xy((x + 18, y + 12)), title, fill="#B6F3EE" if active else "#88A5AA", font=FONT_TINY)
        draw.text(xy((x + 18, y + 28)), sub, fill="#9FB4C0", font=FONT_SUB)

    return img.convert("RGB")


def main() -> None:
    frames = []
    for i in range(FRAME_COUNT):
        frame = draw_frame(i).resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)
        frames.append(frame)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
