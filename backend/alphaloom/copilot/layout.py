"""Copilot 自动布局（AlphaLoom D3 Task 6）。

按拓扑 order 分层分列给每节点 position（无重叠），供前端画布渲染。列（x）= 该节点在
依赖链上的深度（源在最左），行（y）= 同层内的次序。前端 loomToFlow 从 meta.positions
读它（见 frontend/src/lib/loom.ts）。

深度用节点上游边推算：depth(n) = 1 + max(depth(src) for 入边) ，无入边则 0。这样即使
拓扑 order 把互不依赖的节点交错排在一起，纯源节点仍全落在第 0 列（feed 在最左）。
"""
from __future__ import annotations

# 与前端 loom.ts 的 GRID_X/GRID_Y 同量级（画布网格间距），保证生成图与手绘图观感一致。
COL_WIDTH = 260
ROW_HEIGHT = 150
MARGIN_X = 40
MARGIN_Y = 40


def _depths(loom: dict, order: list[str]) -> dict[str, int]:
    """按上游依赖推每节点列深度。order 保证遍历时上游已定深度。"""
    incoming: dict[str, list[str]] = {n["id"]: [] for n in loom["nodes"]}
    for e in loom.get("edges", []):
        # feedback 边不计入深度（否则回边会把整层往右推乱布局）
        if e.get("feedback"):
            continue
        src = e["from"].split(".")[0]
        dst = e["to"].split(".")[0]
        if dst in incoming and src in incoming:
            incoming[dst].append(src)

    depth: dict[str, int] = {}
    for nid in order:
        preds = incoming.get(nid, [])
        depth[nid] = 0 if not preds else 1 + max(depth.get(p, 0) for p in preds)
    # order 可能不含孤立节点（无边）——兜底补 0
    for n in loom["nodes"]:
        depth.setdefault(n["id"], 0)
    return depth


def layout(loom: dict, order: list[str]) -> dict[str, dict]:
    """返回 {node_id: {"x": int, "y": int}}，分层分列无重叠。"""
    depth = _depths(loom, order)
    # 同列内按稳定顺序（order 优先，孤立节点补在后）排行号
    seq = list(order) + [n["id"] for n in loom["nodes"] if n["id"] not in order]
    row_of_col: dict[int, int] = {}
    positions: dict[str, dict] = {}
    for nid in seq:
        if nid in positions:
            continue
        col = depth[nid]
        row = row_of_col.get(col, 0)
        row_of_col[col] = row + 1
        positions[nid] = {
            "x": MARGIN_X + col * COL_WIDTH,
            "y": MARGIN_Y + row * ROW_HEIGHT,
        }
    return positions
