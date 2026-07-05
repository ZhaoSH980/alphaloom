from __future__ import annotations
import math

def sanitize(obj):
    """递归把 inf/-inf/nan 变 None，保证严格 RFC 8259 JSON（Carryover 15②）。"""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj
