from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from render_readme_result_screenshots import (
    BG,
    BORDER,
    BORDER_HOT,
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
    chip,
    font,
    text,
)


OUT = ROOT / "docs" / "screenshots" / "studio.png"
BLUEPRINT = ROOT / "blueprints" / "agent_committee.loom"

W = 2360
H = 1500

F_LOGO = font(38, True)
F_NAV = font(25, True)
F_TITLE = font(44, True)
F_SUB = font(24)
F_NODE = font(27, True)
F_TYPE = font(18, True)
F_PIN = font(16, True)
F_PARAM = font(16)
F_STAT = font(22, True)

NODE_COLORS = {
    "candle_feed": (45, 196, 255),
    "ema": (163, 128, 255),
    "atr": (163, 128, 255),
    "knowledge_retrieve": (65, 166, 255),
    "committee": (245, 178, 31),
    "require_citations": (245, 178, 31),
    "experience_retrieve": (132, 99, 255),
    "reflector": (46, 211, 190),
    "experience_write": (46, 211, 190),
    "position_sizer": (255, 89, 102),
    "risk_gate": (52, 211, 153),
    "execute_order": (52, 211, 153),
    "kill_switch": (255, 89, 102),
}

LAYOUT = {
    "feed": (90, 680, 350, 840),
    "ema": (430, 330, 700, 480),
    "atr": (430, 560, 700, 710),
    "kb": (430, 790, 700, 940),
    "kill": (430, 1050, 700, 1200),
    "committee": (780, 455, 1080, 635),
    "xp": (780, 805, 1080, 985),
    "cite_gate": (1160, 455, 1460, 635),
    "reflector": (1160, 805, 1460, 985),
    "sizer": (1540, 455, 1810, 635),
    "xp_write": (1540, 805, 1810, 985),
    "risk": (1880, 455, 2150, 635),
    "exec": (1880, 775, 2150, 955),
}

EDGE_COLOR_BY_PIN = {
    "out": CYAN,
    "value": PURPLE,
    "citations": (65, 166, 255),
    "signal": GOLD,
    "verdict": (46, 211, 190),
    "sized": RED,
    "stamped": GREEN,
}


def bg() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    pix = im.load()
    for y in range(H):
        yy = y / max(H - 1, 1)
        for x in range(W):
            xx = x / max(W - 1, 1)
            r = int(6 + 5 * xx + 2 * yy)
            g = int(12 + 10 * xx + 8 * math.sin(xx * math.pi))
            b = int(25 + 26 * yy + 8 * xx)
            pix[x, y] = (r, g, b)
    return im.convert("RGBA")


def rounded(draw: ImageDraw.ImageDraw, rect, fill, outline=BORDER, width=2, radius=18) -> None:
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def glow(im: Image.Image, rect, color, alpha=70) -> None:
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(rect, radius=22, fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(18))
    im.alpha_composite(layer)


def line_points(start, end):
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    if abs(dx) < 80:
        c1 = (sx + 95, sy)
        c2 = (ex + 95, ey)
    else:
        c1 = (sx + dx * 0.45, sy)
        c2 = (ex - dx * 0.45, ey)
    pts = []
    for i in range(28):
        t = i / 27
        mt = 1 - t
        x = mt**3 * sx + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * ex
        y = mt**3 * sy + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * ey
        pts.append((x, y))
    return pts


def draw_arrow(draw: ImageDraw.ImageDraw, pts, color, width=4, alpha=210) -> None:
    rgba = (*color, alpha)
    draw.line(pts, fill=rgba, width=width, joint="curve")
    if len(pts) < 2:
        return
    x1, y1 = pts[-2]
    x2, y2 = pts[-1]
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 13
    left = (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45))
    right = (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45))
    draw.polygon([(x2, y2), left, right], fill=rgba)


def pin_order(edges, side: str) -> dict[str, list[str]]:
    pins: dict[str, list[str]] = {}
    key = "from" if side == "out" else "to"
    for edge in edges:
        node, pin = edge[key].split(".", 1)
        pins.setdefault(node, [])
        if pin not in pins[node]:
            pins[node].append(pin)
    return pins


def pin_position(rect, pins, pin, side) -> tuple[int, int]:
    x1, y1, x2, y2 = rect
    names = pins or [pin]
    if pin not in names:
        names = [*names, pin]
    idx = names.index(pin)
    step = (y2 - y1 - 78) / max(1, len(names) - 1)
    y = int(y1 + 96 + step * idx)
    x = x2 if side == "out" else x1
    return x, y


def node_category(node_type: str) -> str:
    if node_type in {"candle_feed"}:
        return "DATA"
    if node_type in {"ema", "atr"}:
        return "INDICATOR"
    if node_type in {"knowledge_retrieve", "experience_retrieve"}:
        return "RAG / MEMORY"
    if node_type in {"committee", "require_citations", "reflector", "experience_write"}:
        return "AGENT"
    if node_type in {"position_sizer", "risk_gate", "execute_order", "kill_switch"}:
        return "RISK / EXECUTION"
    return "NODE"


def draw_node(draw: ImageDraw.ImageDraw, node: dict, rect, in_pins, out_pins, hot=False) -> None:
    x1, y1, x2, y2 = rect
    node_type = node["type"]
    color = NODE_COLORS.get(node_type, CYAN)
    fill = (9, 17, 34) if not hot else (8, 31, 28)
    rounded(draw, rect, fill=fill, outline=color, width=3 if hot else 2, radius=18)
    draw.rounded_rectangle((x1, y1, x2, y1 + 10), radius=18, fill=color)
    draw.rectangle((x1, y1 + 7, x2, y1 + 12), fill=color)

    text(draw, (x1 + 24, y1 + 28), node["id"], F_NODE, TEXT)
    text(draw, (x1 + 24, y1 + 66), node_type, F_TYPE, color)

    params = node.get("params") or {}
    param_text = ", ".join(f"{k}={v}" for k, v in list(params.items())[:2]) or "{}"
    if len(param_text) > 34:
        param_text = param_text[:31] + "..."
    text(draw, (x1 + 24, y1 + 100), param_text, F_PARAM, MUTED)

    for pin in in_pins:
        px, py = pin_position(rect, in_pins, pin, "in")
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(17, 24, 46), outline=CYAN, width=2)
        text(draw, (px + 14, py - 10), pin, F_PIN, MUTED_2)
    for pin in out_pins:
        px, py = pin_position(rect, out_pins, pin, "out")
        edge_color = EDGE_COLOR_BY_PIN.get(pin, color)
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=edge_color, outline=(0, 0, 0), width=1)
        bbox = draw.textbbox((0, 0), pin, font=F_PIN)
        text(draw, (px - 14 - (bbox[2] - bbox[0]), py - 10), pin, F_PIN, MUTED_2)

    text(draw, (x2 - 18, y2 - 22), node_category(node_type), F_PIN, (*MUTED, 255), anchor="ra")


def draw_grid(draw: ImageDraw.ImageDraw, rect) -> None:
    x1, y1, x2, y2 = rect
    rounded(draw, rect, fill=(5, 12, 26), outline=(22, 42, 72), width=2, radius=22)
    for x in range(x1 + 40, x2, 64):
        draw.line((x, y1, x, y2), fill=(12, 34, 50, 110), width=1)
    for y in range(y1 + 40, y2, 64):
        draw.line((x1, y, x2, y), fill=(12, 34, 50, 110), width=1)
    for x in range(x1 + 40, x2, 64):
        for y in range(y1 + 40, y2, 64):
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(38, 64, 88, 100))


def main() -> None:
    blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in blueprint["nodes"]}
    edges = blueprint["edges"]
    in_pins = pin_order(edges, "in")
    out_pins = pin_order(edges, "out")

    im = bg()
    draw = ImageDraw.Draw(im, "RGBA")

    draw.rectangle((0, 0, W, 112), fill=(6, 12, 26))
    text(draw, (40, 34), "AlphaLoom", F_LOGO, GOLD)
    text(draw, (268, 45), "THE GRAPH IS THE AGENT", F_PIN, MUTED)
    text(draw, (700, 43), "Studio", F_NAV, CYAN)
    text(draw, (840, 43), "Terminal", F_NAV, MUTED)
    text(draw, (1010, 43), "Eval Lab", F_NAV, MUTED)
    draw.line((690, 99, 780, 99), fill=CYAN, width=4)
    chip(draw, (1730, 31), "PRESET BLUEPRINT / ZERO-QUOTA VIEW", fill=(6, 52, 56), color=GREEN)
    draw.line((0, 112, W, 112), fill=(27, 44, 75), width=2)

    canvas = (34, 145, W - 34, H - 42)
    draw_grid(draw, canvas)
    text(draw, (76, 184), "PRESET BLUEPRINT: agent_committee_v1", F_TITLE, TEXT)
    text(draw, (76, 238), "Complete LLM committee + RAG + reflection graph; every order must pass RiskGate before ExecuteOrder.", F_SUB, MUTED)

    stats = [
        ("13 nodes", CYAN),
        ("19 typed edges", CYAN),
        ("RAG citations", PURPLE),
        ("Reflection memory", GREEN),
        ("RiskGate stamped path", GOLD),
    ]
    x = 76
    for label, color in stats:
        x = chip(draw, (x, 270), label, fill=(9, 28, 41), color=color)

    edge_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge_layer, "RGBA")
    for edge in edges:
        from_node, from_pin = edge["from"].split(".", 1)
        to_node, to_pin = edge["to"].split(".", 1)
        start = pin_position(LAYOUT[from_node], out_pins.get(from_node, []), from_pin, "out")
        end = pin_position(LAYOUT[to_node], in_pins.get(to_node, []), to_pin, "in")
        color = EDGE_COLOR_BY_PIN.get(from_pin, CYAN)
        width = 6 if edge["from"] == "risk.stamped" and edge["to"] == "exec.signal" else 3
        alpha = 245 if width == 6 else 175
        draw_arrow(ed, line_points(start, end), color, width=width, alpha=alpha)
    edge_layer = edge_layer.filter(ImageFilter.GaussianBlur(0.25))
    im.alpha_composite(edge_layer)

    for node_id, rect in LAYOUT.items():
        if node_id == "risk":
            glow(im, rect, GREEN, 55)
        elif node_id in {"committee", "cite_gate"}:
            glow(im, rect, GOLD, 35)
        draw_node(draw, nodes[node_id], rect, in_pins.get(node_id, []), out_pins.get(node_id, []), hot=node_id in {"risk", "exec"})

    legend = (76, 1310, W - 76, 1420)
    rounded(draw, legend, fill=(7, 15, 31), outline=(27, 52, 84), width=2, radius=18)
    text(draw, (106, 1340), "Typed execution invariant", F_STAT, TEXT)
    text(draw, (106, 1378), "committee.signal -> require_citations.signal -> position_sizer.sized -> risk_gate.stamped -> execute_order.signal", F_SUB, MUTED)
    text(draw, (W - 110, 1340), "raw LLM output cannot enter execution", F_STAT, GOLD, anchor="ra")
    text(draw, (W - 110, 1378), "legal order path is visible before runtime", F_SUB, MUTED, anchor="ra")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(OUT, quality=95)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
