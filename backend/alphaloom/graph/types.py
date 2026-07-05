from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

class PinType(str, Enum):
    EXEC = "exec"
    CANDLE = "candle"
    SERIES = "series"
    SIGNAL = "signal"
    RISK_STAMPED_SIGNAL = "risk_stamped_signal"
    BOOL = "bool"

@dataclass(frozen=True)
class Stamped:
    """数据引脚上流动的值：value + as-of 毫秒时间戳（因果类型系统的载体）。"""
    value: Any
    as_of: int

@dataclass(frozen=True)
class CostAnnotation:
    llm_calls_per_bar: int = 0
    max_tokens_per_call: int = 0
    latency_class: str = "fast"   # fast | slow | llm
    deterministic: bool = True
