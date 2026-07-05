"""Audit log: every sandboxed data / LLM access leaves a row (AlphaLoom D3).

AlphaLoom keys market data by millisecond timestamps (not calendar dates), so
the causality high-water mark is ``data_max_ts: int | None`` rather than a
``date``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    data_max_ts: int | None = None
    note: str = ""


class AuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(
        self,
        tool: str,
        params: dict[str, Any],
        data_max_ts: int | None = None,
        note: str = "",
    ) -> None:
        self.entries.append(
            AuditEntry(tool=tool, params=params, data_max_ts=data_max_ts, note=note)
        )

    def as_dicts(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self.entries]
