from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class CompileError:
    code: str
    message: str
    node_id: str | None = None
    port: str | None = None
    fix_hint: str | None = None   # 面向 LLM 的修复提示（结构化反馈环境的一部分）

    def to_dict(self) -> dict:
        return asdict(self)
