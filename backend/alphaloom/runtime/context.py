from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from alphaloom.graph.types import Stamped

class CausalityError(Exception):
    """节点试图产出/传播 as_of 晚于当前时钟的数据 —— 图感知了未来。"""

class SimClock:
    def __init__(self) -> None:
        self.now: int = 0

    def advance(self, ts: int) -> None:
        if ts < self.now:
            raise ValueError(f"clock cannot go backwards: {ts} < {self.now}")
        self.now = ts

def check_stamped(node_id: str, obj: Any, now: int) -> None:
    if isinstance(obj, Stamped):
        if obj.as_of > now:
            raise CausalityError(
                f"node {node_id!r} emitted data stamped as_of={obj.as_of} "
                f"but clock is {now}: graphs must not perceive the future")
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, Stamped) and v.as_of > now:
                raise CausalityError(
                    f"node {node_id!r} emitted nested future data (as_of={v.as_of} > now={now})")

@dataclass
class RunContext:
    clock: SimClock
    run_id: str
    broker: Any = None
    recorder: Any = None
    current_event: Any = None
    halted: bool = False
    llm: Any = None      # RecordingLLMClient | None：LLM 节点在 None 时抛清晰错误
    audit: Any = None    # AuditLog：每次 LLM/检索调用留痕（provenance）
