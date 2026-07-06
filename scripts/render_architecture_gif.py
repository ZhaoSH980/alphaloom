from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "architecture-loop.gif"

W, H = 1000, 384
SCALE = 2
FRAME_COUNT = 54
FRAME_MS = 78


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


FONT_TITLE = font(22, True)
FONT_SUB = font(11)
FONT_LABEL = font(15, True)
FONT_TINY = font(10, True)


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


CARDS = [
    ("Blueprint", "Studio", "typed .loom graph", "cyan", (40, 107, 117, 98)),
    ("Compiler", "", "pins, cycles, cost", "cyan", (175, 107, 117, 98)),
    ("RiskGate", "", "risk_stamped_signal", "gold", (310, 107, 117, 98)),
    ("Runtime", "Broker", "fills, stops, equity", "cyan", (445, 107, 117, 98)),
    ("Recorder", "", "full node I/O replay", "cyan", (580, 107, 117, 98)),
    ("Eval Lab", "", "fidelity, ablation", "cyan", (715, 107, 117, 98)),
    ("Copilot", "Evolution", "mutate and repair", "cyan", (850, 107, 117, 98)),
]

CHIPS = [
    ("TYPE CONTRACT", "raw signal cannot enter ExecuteOrder", (63, 312, 267, 45)),
    ("OFFLINE REPLAY", "recorded LLM calls, zero quota demo", (367, 312, 267, 45)),
    ("REAL DATA CHECK", "OKX candles, exact window, honest caveats", (670, 312, 267, 45)),
]


def center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = rect
    return x + w / 2, y + h / 2


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
    for a, b in zip(centers, centers[1:]):
        for i in range(18):
            t = i / 18
            pts.append((lerp(a[0] + 58, b[0] - 58, t), lerp(a[1], b[1], t)))
    pts.extend(
        sample_curve(
            (centers[-1][0], centers[-1][1] + 72),
            (centers[-1][0], 292),
            (centers[0][0], 292),
            (centers[0][0], centers[0][1] + 72),
            92,
        )
    )
    return pts


PATH = build_path()


def active_index(progress: float) -> int:
    centers = [center(c[4]) for c in CARDS]
    idx = 0
    for i, c in enumerate(centers):
        if progress * (len(PATH) - 1) >= i * 18:
            idx = i
    return min(idx, len(CARDS) - 1)


def draw_glow(draw_img: Image.Image, rect: tuple[int, int, int, int], color: tuple[int, int, int], alpha: int) -> None:
    x, y, w, h = rect
    layer = Image.new("RGBA", draw_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x - 4, y - 4, x + w + 4, y + h + 4), radius=18, fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(12))
    draw_img.alpha_composite(layer)


def draw_arrow(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], color, width: int = 2) -> None:
    draw.line((a, b), fill=color, width=width)
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    tip = b
    left = (tip[0] - 9 * math.cos(angle - 0.45), tip[1] - 9 * math.sin(angle - 0.45))
    right = (tip[0] - 9 * math.cos(angle + 0.45), tip[1] - 9 * math.sin(angle + 0.45))
    draw.polygon([tip, left, right], fill=color)


def draw_frame(frame: int) -> Image.Image:
    progress = frame / FRAME_COUNT
    img = BG.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Soft market-like contour lines.
    wave_shift = progress * 36
    draw.line(
        [(34, 338), (160, 300 + 8 * math.sin(progress * 6)), (315, 315), (510, 292), (765, 305), (960, 264)],
        fill=(20, 61, 53, 160),
        width=2,
    )
    draw.line(
        [(42, 77), (158, 48), (300, 64), (490, 45), (724, 55), (965, 37)],
        fill=(18, 61, 75, 150),
        width=2,
    )
    draw.rectangle((0, int(80 + wave_shift) % H, W, int(81 + wave_shift) % H), fill=(48, 231, 213, 32))

    draw.text((48, 30), "Compile-gated trading agent loop", fill="#F8FAFC", font=FONT_TITLE)
    draw.text(
        (48, 58),
        "A visual graph runs only after cost, causality, and risk contracts compile.",
        fill="#9FB4C0",
        font=FONT_SUB,
    )

    idx = active_index(progress)
    for i, (line1, line2, sub, kind, rect) in enumerate(CARDS):
        x, y, w, h = rect
        reached = i <= idx
        pulse = 0.5 + 0.5 * math.sin(progress * math.tau * 3 + i)
        if reached:
            color = (250, 204, 21) if kind == "gold" else (48, 231, 213)
            draw_glow(img, rect, color, 50 + int(35 * pulse))
        fill = "#1F180A" if kind == "gold" else "#08141C"
        outline = "#FACC15" if kind == "gold" else "#7DD3FC"
        outline_alpha = 225 if reached else 82
        draw.rounded_rectangle(
            (x, y, x + w, y + h),
            radius=14,
            fill=fill,
            outline=outline,
            width=2 if reached else 1,
        )
        if not reached:
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rounded_rectangle((x, y, x + w, y + h), radius=14, fill=(0, 0, 0, 88))
            img.alpha_composite(overlay)
        # Re-draw outline after dim overlay.
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            (x, y, x + w, y + h),
            radius=14,
            outline=(*((250, 204, 21) if kind == "gold" else (125, 211, 252)), outline_alpha),
            width=2 if reached else 1,
        )
        text_y = y + 28 if line2 else y + 39
        draw.text((x + 19, text_y), line1, fill="#F8FAFC", font=FONT_LABEL)
        if line2:
            draw.text((x + 19, y + 51), line2, fill="#F8FAFC", font=FONT_LABEL)
        if kind == "gold":
            draw.text((x + 19, y + 67), "only legal order path", fill="#FDE68A", font=FONT_TINY)
            draw.text((x + 19, y + 81), "risk stamp required", fill="#B6A56E", font=FONT_SUB)
        else:
            draw.text((x + 19, y + 77), sub, fill="#9FB4C0", font=FONT_SUB)

    centers = [center(c[4]) for c in CARDS]
    for i, (a, b) in enumerate(zip(centers, centers[1:])):
        start = (a[0] + 58, a[1])
        end = (b[0] - 58, b[1])
        col = (118, 228, 247, 230) if i < idx else (85, 117, 128, 110)
        if i == 1:
            col = (250, 204, 21, 230) if i < idx else (126, 103, 41, 130)
        draw_arrow(draw, start, end, col, 3 if i < idx else 2)

    # Return loop.
    loop = sample_curve(
        (centers[-1][0], centers[-1][1] + 72),
        (centers[-1][0], 292),
        (centers[0][0], 292),
        (centers[0][0], centers[0][1] + 72),
        92,
    )
    loop_color = (118, 228, 247, 190) if idx >= len(CARDS) - 1 else (80, 112, 120, 90)
    for a, b in zip(loop, loop[1:]):
        draw.line((a, b), fill=loop_color, width=2)
    draw.text(
        (382, 282),
        "compile errors become repair hints; held-out winners feed the next blueprint",
        fill=(182, 243, 238, 205),
        font=FONT_TINY,
    )

    # Moving packet and trail.
    p_idx = int(progress * len(PATH)) % len(PATH)
    trail = []
    for k in range(15):
        trail.append(PATH[(p_idx - k) % len(PATH)])
    for k, point in enumerate(reversed(trail)):
        alpha = int(20 + 140 * (k / max(len(trail) - 1, 1)))
        r = int(2 + 4 * (k / max(len(trail) - 1, 1)))
        draw.ellipse((point[0] - r, point[1] - r, point[0] + r, point[1] + r), fill=(48, 231, 213, alpha))
    point = PATH[p_idx]
    draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=(250, 204, 21, 245))
    draw.ellipse((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=(255, 255, 255, 250))

    for i, (title, sub, rect) in enumerate(CHIPS):
        x, y, w, h = rect
        active = (idx <= 2 and i == 0) or (3 <= idx <= 4 and i == 1) or (idx >= 5 and i == 2)
        outline = (125, 211, 252, 120 + (80 if active else 0))
        draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=(5, 15, 20, 230), outline=outline, width=1)
        draw.text((x + 18, y + 12), title, fill="#B6F3EE" if active else "#88A5AA", font=FONT_TINY)
        draw.text((x + 18, y + 28), sub, fill="#9FB4C0", font=FONT_SUB)

    return img.convert("RGB")


def main() -> None:
    frames = []
    for i in range(FRAME_COUNT):
        big = Image.new("RGB", (W * SCALE, H * SCALE))
        frame = draw_frame(i).resize((W * SCALE, H * SCALE), Image.Resampling.NEAREST)
        big.paste(frame)
        frames.append(big.resize((W, H), Image.Resampling.LANCZOS))

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
