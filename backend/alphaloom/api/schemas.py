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
