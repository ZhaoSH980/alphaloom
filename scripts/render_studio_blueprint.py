from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from render_readme_result_screenshots import (
    BG,
    BORDER,
    CYAN,
    GOLD,
    GREEN,
    MUTED,
    MUTED_2,
    PANEL,
    PURPLE,
    RED,
    ROOT,
    TEXT,
)


OUT_EN = ROOT / "docs" / "screenshots" / "studio.png"
OUT_ZH = ROOT / "docs" / "screenshots" / "studio-zh.png"

W = 2360
H = 1320


def font(size: int, bold: bool = False, *, zh: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if zh:
        candidates = [
            Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
    else:
        candidates = [
            Path("C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf"),
            Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def bg() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    pix = im.load()
    for y in range(H):
        yy = y / max(H - 1, 1)
        for x in range(W):
            xx = x / max(W - 1, 1)
            r = int(5 + 4 * xx + 2 * yy)
            g = int(12 + 9 * xx + 7 * math.sin(xx * math.pi))
            b = int(25 + 22 * yy + 7 * xx)
            pix[x, y] = (r, g, b)
    return im.convert("RGBA")


def text(draw: ImageDraw.ImageDraw, xy, value: str, font_obj, fill=TEXT, anchor=None) -> None:
    draw.text(xy, value, font=font_obj, fill=fill, anchor=anchor)


def rounded(draw: ImageDraw.ImageDraw, rect, fill, outline=BORDER, width=2, radius=18) -> None:
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def glow(im: Image.Image, rect, color, alpha=52, blur=24) -> None:
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(rect, radius=24, fill=(*color, alpha))
    im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def draw_grid(draw: ImageDraw.ImageDraw, rect) -> None:
    x1, y1, x2, y2 = rect
    rounded(draw, rect, fill=(5, 12, 26), outline=(22, 42, 72), width=2, radius=24)
    for x in range(x1 + 48, x2, 64):
        draw.line((x, y1, x, y2), fill=(16, 42, 61, 95), width=1)
    for y in range(y1 + 48, y2, 64):
        draw.line((x1, y, x2, y), fill=(16, 42, 61, 95), width=1)


def wrap(draw: ImageDraw.ImageDraw, value: str, font_obj, max_width: int, *, zh: bool) -> list[str]:
    if zh:
        lines: list[str] = []
        cur = ""
        for ch in value:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            test = cur + ch
            if draw.textbbox((0, 0), test, font=font_obj)[2] <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines

    lines = []
    cur = ""
    for word in value.split():
        test = word if not cur else f"{cur} {word}"
        if draw.textbbox((0, 0), test, font=font_obj)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def arrow(draw: ImageDraw.ImageDraw, start, end, color=CYAN, width=4, alpha=220) -> None:
    sx, sy = start
    ex, ey = end
    rgba = (*color, alpha)
    draw.line((sx, sy, ex, ey), fill=rgba, width=width)
    ang = math.atan2(ey - sy, ex - sx)
    size = 16
    left = (ex - size * math.cos(ang - 0.45), ey - size * math.sin(ang - 0.45))
    right = (ex - size * math.cos(ang + 0.45), ey - size * math.sin(ang + 0.45))
    draw.polygon([(ex, ey), left, right], fill=rgba)


def card(draw: ImageDraw.ImageDraw, im: Image.Image, rect, *, tag: str, title: str, body: str,
         color, zh: bool, hot: bool = False) -> None:
    x1, y1, x2, y2 = rect
    fill = (8, 17, 34) if not hot else (8, 29, 27)
    glow(im, rect, color, alpha=38 if hot else 22, blur=18)
    rounded(draw, rect, fill=fill, outline=color, width=3 if hot else 2, radius=18)
    draw.rounded_rectangle((x1, y1, x2, y1 + 10), radius=18, fill=color)
    draw.rectangle((x1, y1 + 7, x2, y1 + 12), fill=color)
    f_tag = font(19, True, zh=zh)
    f_title = font(27 if not zh else 29, True, zh=zh)
    f_body = font(19 if not zh else 21, False, zh=zh)
    text(draw, (x1 + 22, y1 + 28), tag.upper(), f_tag, color)
    yy = y1 + 60
    for line in wrap(draw, title, f_title, x2 - x1 - 44, zh=zh)[:2]:
        text(draw, (x1 + 22, yy), line, f_title, TEXT)
        yy += 34
    yy += 4
    for line in wrap(draw, body, f_body, x2 - x1 - 44, zh=zh)[:3]:
        text(draw, (x1 + 22, yy), line, f_body, MUTED)
        yy += 27


def callout(draw: ImageDraw.ImageDraw, rect, *, title: str, body: str, color, zh: bool) -> None:
    x1, y1, x2, y2 = rect
    rounded(draw, rect, fill=(7, 15, 31), outline=color, width=2, radius=16)
    f_title = font(23 if not zh else 25, True, zh=zh)
    f_body = font(19 if not zh else 21, False, zh=zh)
    text(draw, (x1 + 22, y1 + 20), title, f_title, color)
    yy = y1 + 58
    for line in wrap(draw, body, f_body, x2 - x1 - 44, zh=zh)[:2]:
        text(draw, (x1 + 22, yy), line, f_body, MUTED)
        yy += 27


def render(copy: dict[str, str], out: Path, *, zh: bool = False) -> None:
    im = bg()
    draw = ImageDraw.Draw(im, "RGBA")
    draw.rectangle((0, 0, W, 112), fill=(6, 12, 26))
    text(draw, (40, 34), "AlphaLoom", font(38, True), GOLD)
    text(draw, (268, 45), copy["tagline"], font(18, True, zh=zh), MUTED)
    text(draw, (700, 43), "Studio", font(25, True), CYAN)
    text(draw, (840, 43), "Gate View", font(25, True), GREEN)
    text(draw, (1010, 43), "Graph View", font(25, True), MUTED)
    draw.line((690, 99, 790, 99), fill=CYAN, width=4)
    draw.line((0, 112, W, 112), fill=(27, 44, 75), width=2)

    canvas = (34, 145, W - 34, H - 42)
    draw_grid(draw, canvas)
    text(draw, (76, 184), copy["title"], font(46 if not zh else 48, True, zh=zh), TEXT)
    text(draw, (76, 242), copy["subtitle"], font(24 if not zh else 25, False, zh=zh), MUTED)

    chips = [
        (copy["chip_stage"], PURPLE),
        (copy["chip_short"], GOLD),
        (copy["chip_risk"], GREEN),
        (copy["chip_replay"], CYAN),
    ]
    x = 76
    for label, color in chips:
        f_chip = font(21 if not zh else 22, True, zh=zh)
        bbox = draw.textbbox((0, 0), label, font=f_chip)
        w = bbox[2] - bbox[0] + 34
        rounded(draw, (x, 282, x + w, 322), fill=(9, 28, 41), outline=(9, 28, 41), width=1, radius=10)
        text(draw, (x + 17, 291), label, f_chip, color)
        x += w + 12

    evidence = (150, 360, 910, 430)
    rounded(draw, evidence, fill=(7, 20, 35), outline=(25, 86, 117), width=2, radius=16)
    text(draw, (176, 382), copy["evidence"], font(22 if not zh else 24, True, zh=zh), CYAN)
    text(draw, (176, 410), copy["evidence_body"], font(18 if not zh else 20, False, zh=zh), MUTED)

    y = 500
    h = 190
    gap = 28
    x0 = 76
    w = 290
    cards = [
        (copy["inputs_tag"], copy["market"], copy["market_body"], CYAN, False),
        (copy["stage1"], copy["diagnosis"], copy["diagnosis_body"], PURPLE, False),
        (copy["gate1_tag"], copy["diag_gate"], copy["diag_gate_body"], GOLD, True),
        (copy["stage2"], copy["proposal"], copy["proposal_body"], PURPLE, False),
        (copy["gate2_tag"], copy["order_gate"], copy["order_gate_body"], GOLD, True),
        (copy["gate3_tag"], copy["risk"], copy["risk_body"], GREEN, True),
        (copy["runtime_tag"], copy["execute"], copy["execute_body"], (226, 232, 240), False),
    ]
    rects = []
    for i, spec in enumerate(cards):
        rx = x0 + i * (w + gap)
        rect = (rx, y, rx + w, y + h)
        rects.append(rect)
        card(draw, im, rect, tag=spec[0], title=spec[1], body=spec[2], color=spec[3], zh=zh, hot=spec[4])
        if i:
            prev = rects[i - 1]
            arrow(draw, (prev[2] + 4, y + h // 2), (rx - 10, y + h // 2), color=CYAN, width=4, alpha=210)

    callout(draw, (rects[2][0] - 5, 745, rects[2][2] + 5, 850),
            title=copy["short"], body=copy["short_body"], color=RED, zh=zh)
    arrow(draw, ((rects[2][0] + rects[2][2]) // 2, y + h + 4),
          ((rects[2][0] + rects[2][2]) // 2, 735), color=RED, width=4, alpha=210)

    callout(draw, (rects[4][0] - 5, 745, rects[4][2] + 5, 850),
            title=copy["reject"], body=copy["reject_body"], color=RED, zh=zh)
    arrow(draw, ((rects[4][0] + rects[4][2]) // 2, y + h + 4),
          ((rects[4][0] + rects[4][2]) // 2, 735), color=RED, width=4, alpha=210)

    loop = (rects[5][0], 920, rects[6][2], 1038)
    callout(draw, loop, title=copy["reflection"], body=copy["reflection_body"], color=CYAN, zh=zh)
    arrow(draw, ((rects[6][0] + rects[6][2]) // 2, y + h + 4),
          ((rects[6][0] + rects[6][2]) // 2, 910), color=CYAN, width=4, alpha=210)
    legend = (76, 1120, W - 76, 1228)
    rounded(draw, legend, fill=(7, 15, 31), outline=(46, 71, 103), width=2, radius=18)
    text(draw, (110, 1152), copy["invariant"], font(26 if not zh else 28, True, zh=zh), GOLD)
    text(draw, (110, 1192), copy["invariant_body"], font(23 if not zh else 24, False, zh=zh), MUTED)
    text(draw, (W - 110, 1152), copy["raw"], font(23 if not zh else 24, True, zh=zh), GOLD, anchor="ra")
    text(draw, (W - 110, 1192), copy["raw_body"], font(20 if not zh else 21, False, zh=zh), MUTED, anchor="ra")

    out.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out, quality=95)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.1f} KiB)")


EN = {
    "tagline": "THE GATE PATH IS THE BLUEPRINT",
    "title": "PRESET BLUEPRINT: two-stage gate protocol",
    "subtitle": "A readable Gate View of agent_committee_v1: diagnose first, short-circuit weak setups, then stamp risk before execution.",
    "chip_stage": "Stage 1 -> Gate -> Stage 2",
    "chip_short": "gate short-circuit",
    "chip_risk": "RiskGate stamped path",
    "chip_replay": "replay + reflection loop",
    "evidence": "Evidence sidecar",
    "evidence_body": "market bars, indicators, RAG citations, experience memory",
    "inputs_tag": "INPUTS",
    "gate1_tag": "GATE 1",
    "gate2_tag": "GATE 2",
    "gate3_tag": "GATE 3",
    "runtime_tag": "RUNTIME",
    "market": "Market Snapshot",
    "market_body": "real candles and deterministic features",
    "stage1": "STAGE 1",
    "diagnosis": "Market Diagnosis",
    "diagnosis_body": "regime, direction, setup quality",
    "diag_gate": "Diagnosis Gate",
    "diag_gate_body": "proceed / wait / unknown",
    "stage2": "STAGE 2",
    "proposal": "Order Proposal",
    "proposal_body": "entry, stop, target, confidence",
    "order_gate": "Order Validity Gate",
    "order_gate_body": "contract, citations, prices, trace",
    "risk": "RiskGate Stamp",
    "risk_body": "size, stop, exposure contract",
    "execute": "Execute / Simulate",
    "execute_body": "only stamped orders enter runtime",
    "short": "wait / unknown",
    "short_body": "Stage 2 is skipped; the system records a no-trade reason.",
    "reject": "reject / repair",
    "reject_body": "Invalid order proposals cannot reach RiskGate.",
    "reflection": "Replay + Eval + Reflection",
    "reflection_body": "Every run is recorded, scored on real data, and written back as lessons.",
    "invariant": "Typed execution invariant",
    "invariant_body": "Raw LLM output cannot enter execution; every legal order path is visible before runtime.",
    "raw": "not a node soup",
    "raw_body": "the graph view remains editable underneath",
}

ZH = {
    "tagline": "门控路径就是蓝图",
    "title": "预设蓝图：两阶段门控协议",
    "subtitle": "agent_committee_v1 的可读门控视图：先诊断，弱机会短路，再通过 RiskGate 盖章后才能执行。",
    "chip_stage": "阶段一 -> 门控 -> 阶段二",
    "chip_short": "门控短路",
    "chip_risk": "RiskGate 盖章路径",
    "chip_replay": "回放 + 反思闭环",
    "evidence": "证据旁路",
    "evidence_body": "行情 K 线、指标、RAG 引用、经验记忆",
    "inputs_tag": "输入",
    "gate1_tag": "门 1",
    "gate2_tag": "门 2",
    "gate3_tag": "门 3",
    "runtime_tag": "运行时",
    "market": "市场快照",
    "market_body": "真实 K 线与确定性特征",
    "stage1": "阶段一",
    "diagnosis": "市场诊断",
    "diagnosis_body": "结构、方向、机会质量",
    "diag_gate": "诊断门控",
    "diag_gate_body": "通过 / 等待 / 未知",
    "stage2": "阶段二",
    "proposal": "订单提案",
    "proposal_body": "入场、止损、止盈、置信度",
    "order_gate": "订单合法性门控",
    "order_gate_body": "契约、引用、价格、路径追踪",
    "risk": "RiskGate 盖章",
    "risk_body": "仓位、止损、敞口契约",
    "execute": "执行 / 模拟",
    "execute_body": "只有盖章订单能进入运行时",
    "short": "wait / unknown",
    "short_body": "阶段二被跳过；系统记录不下单原因。",
    "reject": "reject / repair",
    "reject_body": "非法订单提案不能进入风控门。",
    "reflection": "回放 + 评估 + 反思",
    "reflection_body": "每次运行都会被记录、用真实数据评分，并把教训写回。",
    "invariant": "类型执行不变量",
    "invariant_body": "裸 LLM 输出不能直接执行；每条合法订单路径在运行前都可见。",
    "raw": "不是节点大杂烩",
    "raw_body": "底层节点图仍然保留可编辑",
}


def main() -> None:
    render(EN, OUT_EN)
    render(ZH, OUT_ZH, zh=True)


if __name__ == "__main__":
    main()
