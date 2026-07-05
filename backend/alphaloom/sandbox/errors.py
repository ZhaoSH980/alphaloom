"""沙箱错误类型（AlphaLoom D3 Task 7）。

SandboxError 既是异常也是 ``compile_node_source`` 的返回值——AST 白名单拒绝
（禁 import/危险 Call/dunder 逃逸）或受限 exec 失败时返回它，而非抛出，方便
调用方（copilot / API）作为结构化反馈处理。
"""
from __future__ import annotations


class SandboxError(Exception):
    """自定义节点源码被沙箱拒绝。

    ``reason`` 是稳定的机器可读码（面向前端/LLM 修复提示），``message`` 是人读
    说明，``lineno`` 定位违规 AST 行（若可得）。
    """

    def __init__(self, message: str, *, reason: str = "rejected", lineno: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.lineno = lineno

    def to_dict(self) -> dict:
        return {"reason": self.reason, "message": self.message, "lineno": self.lineno}
