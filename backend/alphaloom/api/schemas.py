# backend/alphaloom/api/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field

class CompileIn(BaseModel):
    blueprint: dict
    bar: str = "1m"

class SaveBlueprintIn(BaseModel):
    blueprint: dict

class RunIn(BaseModel):
    blueprint: dict
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)   # ≤2100 年，防 int64 溢出穿到 sqlite
    end_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    breakpoints: list[str] = Field(default_factory=list)
    playback_ms: int = 15
    ws_wait_ms: int = 0
    # D3：backtest（默认）| replay（走真实 LLM/录制，加速由 playback_ms 控制）。
    # 两种模式都绑注入的 LLM 客户端——D3 replay 语义先等同 backtest 但确保 LLM 节点能跑。
    mode: str = "backtest"


class CopilotBlueprintIn(BaseModel):
    nl: str


class CopilotExplainIn(BaseModel):
    blueprint: dict


class CopilotOptimizeIn(BaseModel):
    blueprint: dict
    report: dict = Field(default_factory=dict)


class CustomNodeIn(BaseModel):
    source: str
