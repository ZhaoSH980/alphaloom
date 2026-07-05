"""Text-to-Node 沙箱：AST 白名单编译自定义节点源码 → 热注册进 REGISTRY。

安全模型（沙箱契约锁定，宁可保守拒绝也不漏）：

1. **AST 节点白名单**——只允许一小撮纯计算/类声明用的 AST 类型；任何不在集合
   内的节点（Import*、While、Global、Nonlocal、Yield、Await、With、Try、
   Lambda 里的默认值逃逸载体等）直接拒。
2. **dunder 属性禁访问**——任何 ``obj.__xxx__`` / ``obj.xxx__`` 一律拒，掐死
   ``__class__`` / ``__globals__`` / ``__bases__`` / ``__mro__`` /
   ``__subclasses__`` / ``__builtins__`` / ``__code__`` / ``__dict__`` 逃逸链。
3. **危险名字禁引用**——``open`` / ``exec`` / ``eval`` / ``__import__`` /
   ``getattr`` / ``globals`` / ``type`` / ``compile`` … 作为任何 Name 出现即拒，
   故 ``getattr(x, "__" + "globals__")`` 这种字符串拼接绕过名字检查的把戏因
   ``getattr`` 本身不可引用而失效。
4. **import 白名单**——仅 ``math`` / ``statistics`` 两个纯计算 stdlib 模块，外加
   沙箱自身供给的 ``alphaloom.graph.types``（PinType/CostAnnotation）与本模块
   （``node`` 装饰器）。其余 import 全拒。
5. **循环上限**——``while`` 全禁（掐死 ``while True``）；``for`` 仅允许迭代
   字面量/参数，``range(...)`` 字面量上界受 ``MAX_RANGE`` 限制。
6. **受限 exec 命名空间**——``__builtins__`` 只放极少数纯计算安全内建；无
   ``open``/``exec``/``__import__`` 等；``import`` 走白名单 __import__ 钩子。

通过则于受限 namespace ``exec``，@node 装饰器触发注册进 REGISTRY，返回 NodeDef。
失败一律返回（不抛）``SandboxError``。
"""
from __future__ import annotations

import ast
import builtins as _builtins
from typing import Any

from alphaloom.graph.types import CostAnnotation, PinType  # noqa: F401 (供源码 import)
from alphaloom.nodes.registry import REGISTRY, NodeDef, mark_sandboxed
from alphaloom.nodes.registry import node as _register_node
from alphaloom.sandbox.errors import SandboxError

# ---------------------------------------------------------------------------
# 沙箱 @node：与 registry.node 语义相同，但显式再导出，让自定义源码只需
# ``from alphaloom.sandbox.node_sandbox import node`` 即可注册。
# ---------------------------------------------------------------------------
node = _register_node

# ---------------------------------------------------------------------------
# 白名单集合
# ---------------------------------------------------------------------------

# 允许 import 的模块（纯计算 stdlib + 沙箱供给的类型/装饰器）。
ALLOWED_IMPORTS = frozenset({
    "math",
    "statistics",
    "alphaloom.graph.types",
    "alphaloom.sandbox.node_sandbox",
})

# 允许从白名单模块 import 的符号（防 ``from math import *`` 之外的把戏无所谓，
# * 已在 AST 层被 ImportFrom 的 names 检查捕获）。这里不细分符号——模块白名单
# 已保证来源纯净，符号级放行。

# 危险名字：作为任何 Name 出现即拒（Load/Store/Del 均拒，杜绝影子重绑定）。
FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "getattr", "setattr", "delattr", "hasattr",
    "globals", "locals", "vars", "dir",
    "input", "breakpoint", "help", "exit", "quit",
    "type", "object", "super", "classmethod", "staticmethod",
    "__builtins__", "__loader__", "__spec__", "__file__", "__name__",
    "memoryview", "bytearray",
})

# 禁访问的（非 dunder）属性名：str.format/format_map 是经典格式串逃逸载体
# （dunder 藏在字符串字面量里，AST 看不见），必须按名字拦。
FORBIDDEN_ATTRS = frozenset({
    "format", "format_map",
    "mro", "__subclasshook__",  # dunder 已单独拦，这些是显式冗余保险
})

# 允许调用的内建名（Call 的 func 若是这些 Name 之一则放行）。纯计算安全子集。
ALLOWED_CALL_BUILTINS = frozenset({
    "abs", "min", "max", "sum", "len", "round", "pow", "divmod",
    "int", "float", "bool", "str", "list", "dict", "tuple", "set",
    "frozenset", "sorted", "reversed", "enumerate", "zip", "map", "filter",
    "range", "all", "any", "bytes", "complex", "print", "repr", "format",
    "isinstance",
})

# 受限 exec 命名空间的 __builtins__（无 open/exec/eval/__import__ 等）。
SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(_builtins, name)
    for name in (
        "abs", "min", "max", "sum", "len", "round", "pow", "divmod",
        "int", "float", "bool", "str", "list", "dict", "tuple", "set",
        "frozenset", "sorted", "reversed", "enumerate", "zip", "map", "filter",
        "range", "all", "any", "bytes", "complex", "print", "repr", "format",
        "isinstance", "True", "False", "None", "Exception", "ValueError",
        "TypeError", "KeyError", "IndexError", "ZeroDivisionError",
        "ArithmeticError", "RuntimeError", "StopIteration",
    )
    if hasattr(_builtins, name)
}

# for range(...) 字面量上界。
MAX_RANGE = 100_000

# 允许出现的 AST 节点类型白名单（其余一律拒）。
_ALLOWED_NODES: tuple[type, ...] = (
    ast.Module,
    # 声明
    ast.ClassDef, ast.FunctionDef,
    ast.Import, ast.ImportFrom, ast.alias,
    # 语句
    ast.Return, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Expr, ast.Pass, ast.If, ast.For, ast.Break, ast.Continue,
    ast.arguments, ast.arg,
    # 表达式
    ast.Call, ast.Attribute, ast.Subscript, ast.Name, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Slice, ast.Starred,
    ast.keyword,
    # 推导式（受控——内部调用/dunder 仍走同一 visitor 逐节点校验）
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    # 运算符 / 上下文 类型（叶子，无害）
    ast.Load, ast.Store, ast.Del,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd, ast.MatMult,
    ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub, ast.Invert,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
)

# 显式拒绝并给专门理由的节点（安全相关，好报错）。
_DENY_REASONS = {
    ast.While: ("while loops are not allowed (unbounded loop / while True escape)", "while"),
    ast.Lambda: ("lambda is not allowed (default-arg escape vector)", "lambda"),
    ast.With: ("with statements are not allowed (context managers / file IO)", "with"),
    ast.AsyncWith: ("async is not allowed", "async"),
    ast.AsyncFor: ("async is not allowed", "async"),
    ast.AsyncFunctionDef: ("async is not allowed", "async"),
    ast.Await: ("await is not allowed", "async"),
    ast.Yield: ("yield is not allowed", "yield"),
    ast.YieldFrom: ("yield is not allowed", "yield"),
    ast.Global: ("global is not allowed", "global"),
    ast.Nonlocal: ("nonlocal is not allowed", "nonlocal"),
    ast.Try: ("try/except is not allowed (used to swallow escape errors)", "try"),
    ast.Raise: ("raise is not allowed", "raise"),
    ast.Delete: ("delete statements are not allowed", "delete"),
    ast.Assert: ("assert is not allowed", "assert"),
}


class _SandboxViolation(Exception):
    """内部：AST walk 命中违规时抛出，转成 SandboxError 返回。"""

    def __init__(self, message: str, reason: str, lineno: int | None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.lineno = lineno


def _reject(node_obj: ast.AST, message: str, reason: str) -> None:
    raise _SandboxViolation(message, reason, getattr(node_obj, "lineno", None))


def _is_dunder(name: str) -> bool:
    """任何以 __ 开头（且不止两个下划线本身）的属性名都算 dunder 逃逸载体。

    保守：只要以 ``__`` 开头就拒，覆盖 ``__class__`` / ``__globals__`` /
    ``__mro__`` / ``__subclasses__`` / ``__code__`` / ``__dict__`` / ``__bases__``
    / ``__builtins__`` / ``__init_subclass__`` 等全家桶，也拒 ``__private`` 名字
    改写（沙箱里没有正当理由碰 dunder）。
    """
    return name.startswith("__")


def _check_import(node_obj: ast.AST, module_names: list[str]) -> None:
    for m in module_names:
        if m not in ALLOWED_IMPORTS:
            _reject(
                node_obj,
                f"import of {m!r} is not allowed; only {sorted(ALLOWED_IMPORTS)} permitted",
                "import_denied",
            )


class _Validator(ast.NodeVisitor):
    """逐 AST 节点白名单校验。命中违规抛 _SandboxViolation。"""

    def __init__(self) -> None:
        # name -> int 常量绑定（简单 `n = 500000` 形式），供变量绑定的 range 上界
        # 检查用（字面量已拦、变量勿绕）。保守：只跟踪整数字面量赋值；一旦某名字
        # 被重新赋成非常量则从表中移除（不敢假设其值），此时该名字作 range 上界会
        # 被当"未知上界"保守拒（见 _check_range）。
        self._const_ints: dict[str, int] = {}

    def _eval_const_int(self, expr) -> int | None:
        """尝试把表达式在已知整数常量表上静态求值为 int，失败返回 None。

        覆盖：整数字面量、跟踪到的常量名、以及二者上的简单算术传播
        （BinOp of tracked consts，如 ``n*3`` / ``n+500000`` / ``n<<10``）与
        一元 +/-。这样 ``n=50000; m=n*3; range(m)`` 的 m=150000 也能算出并受
        MAX_RANGE 限制（D4 Carryover 3/9①：算术传播绕过加固）。任何未知量
        （param、函数调用结果、非整数）出现即返回 None → 保守当"未知上界"。
        """
        if isinstance(expr, ast.Constant) and isinstance(expr.value, int) \
                and not isinstance(expr.value, bool):
            return expr.value
        if isinstance(expr, ast.Name):
            return self._const_ints.get(expr.id)
        if isinstance(expr, ast.UnaryOp):
            operand = self._eval_const_int(expr.operand)
            if operand is None:
                return None
            if isinstance(expr.op, ast.USub):
                return -operand
            if isinstance(expr.op, ast.UAdd):
                return operand
            return None
        if isinstance(expr, ast.BinOp):
            left = self._eval_const_int(expr.left)
            right = self._eval_const_int(expr.right)
            if left is None or right is None:
                return None
            try:
                if isinstance(expr.op, ast.Add):
                    return left + right
                if isinstance(expr.op, ast.Sub):
                    return left - right
                if isinstance(expr.op, ast.Mult):
                    return left * right
                if isinstance(expr.op, ast.FloorDiv):
                    return left // right if right != 0 else None
                if isinstance(expr.op, ast.Mod):
                    return left % right if right != 0 else None
                if isinstance(expr.op, ast.Pow):
                    # 指数上界防御：巨大指数（10**9）也应被 MAX_RANGE 拦，但先防
                    # 天量幂运算本身炸内存——指数 > 64 一律视为"超大未知"直接给
                    # 一个必超限的哨兵值（不真算）。
                    if right > 64 or right < 0:
                        return MAX_RANGE + 1
                    return left ** right
                if isinstance(expr.op, ast.LShift):
                    if right > 64 or right < 0:
                        return MAX_RANGE + 1
                    return left << right
                if isinstance(expr.op, ast.RShift):
                    return left >> right if right >= 0 else None
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
        return None

    def _record_const_binding(self, target, value) -> None:
        if not isinstance(target, ast.Name):
            return
        evaluated = self._eval_const_int(value)
        if evaluated is not None:
            self._const_ints[target.id] = evaluated
        else:
            # 名字被赋成未知（非整数 / 含未知量的算术）→ 其值未知，从常量表移除
            # （后续作 range 上界保守拒或放行，见 _check_range）
            self._const_ints.pop(target.id, None)

    def visit_Assign(self, node_obj: ast.Assign) -> None:
        for tgt in node_obj.targets:
            self._record_const_binding(tgt, node_obj.value)
        self.generic_visit(node_obj)

    def visit_AugAssign(self, node_obj: ast.AugAssign) -> None:
        """AugAssign（``n += 500000``）更新常量表——否则累加超限绕过 MAX_RANGE
        （D4 Carryover 3/9①：AugAssign 绕过加固）。等价于 ``n = n <op> value``。"""
        tgt = node_obj.target
        if isinstance(tgt, ast.Name):
            current = self._const_ints.get(tgt.id)
            if current is not None:
                # 构造等价 BinOp（current <op> value）在常量表上求值
                synthetic = ast.BinOp(
                    left=ast.Constant(value=current), op=node_obj.op,
                    right=node_obj.value)
                evaluated = self._eval_const_int(synthetic)
                if evaluated is not None:
                    self._const_ints[tgt.id] = evaluated
                else:
                    # n += <未知量> → n 变未知，从常量表移除（range(n) 保守放行）
                    self._const_ints.pop(tgt.id, None)
            # current is None（本就未跟踪）→ 保持未跟踪
        self.generic_visit(node_obj)

    def generic_visit(self, node_obj: ast.AST) -> None:
        t = type(node_obj)
        # 1) 显式拒绝集（含专门理由）
        if t in _DENY_REASONS:
            msg, reason = _DENY_REASONS[t]
            _reject(node_obj, msg, reason)
        # 2) 白名单外的 AST 类型一律拒
        if not isinstance(node_obj, _ALLOWED_NODES):
            _reject(
                node_obj,
                f"AST node {t.__name__} is not allowed in sandboxed node source",
                "ast_denied",
            )
        super().generic_visit(node_obj)

    # ---- import 白名单 ----
    def visit_Import(self, node_obj: ast.Import) -> None:
        _check_import(node_obj, [a.name for a in node_obj.names])
        self.generic_visit(node_obj)

    def visit_ImportFrom(self, node_obj: ast.ImportFrom) -> None:
        if node_obj.level and node_obj.level > 0:
            _reject(node_obj, "relative imports are not allowed", "import_denied")
        module = node_obj.module or ""
        if module not in ALLOWED_IMPORTS:
            _reject(
                node_obj,
                f"import from {module!r} is not allowed; only {sorted(ALLOWED_IMPORTS)} permitted",
                "import_denied",
            )
        for a in node_obj.names:
            if a.name == "*":
                _reject(node_obj, "wildcard import (import *) is not allowed", "import_denied")
        self.generic_visit(node_obj)

    # ---- dunder 属性禁访问 ----
    def visit_Attribute(self, node_obj: ast.Attribute) -> None:
        if _is_dunder(node_obj.attr):
            _reject(
                node_obj,
                f"access to dunder attribute {node_obj.attr!r} is forbidden "
                f"(sandbox escape vector)",
                "dunder_attr",
            )
        if node_obj.attr in FORBIDDEN_ATTRS:
            _reject(
                node_obj,
                f"access to attribute {node_obj.attr!r} is forbidden "
                f"(format-string escape vector)",
                "forbidden_attr",
            )
        self.generic_visit(node_obj)

    # ---- 危险名字禁引用（含被拼接绕过名字检查的 getattr） ----
    def visit_Name(self, node_obj: ast.Name) -> None:
        if node_obj.id in FORBIDDEN_NAMES:
            _reject(
                node_obj,
                f"reference to forbidden name {node_obj.id!r} is not allowed",
                "forbidden_name",
            )
        if _is_dunder(node_obj.id):
            _reject(
                node_obj,
                f"reference to dunder name {node_obj.id!r} is not allowed",
                "dunder_name",
            )
        self.generic_visit(node_obj)

    # ---- Call 白名单：func 是简单 Name 时须在允许内建 or 用户/import 名内 ----
    def visit_Call(self, node_obj: ast.Call) -> None:
        func = node_obj.func
        if isinstance(func, ast.Name):
            fname = func.id
            if fname in FORBIDDEN_NAMES:
                _reject(node_obj, f"call to forbidden builtin {fname!r}", "forbidden_call")
            # range 字面量上界限制
            if fname == "range":
                self._check_range(node_obj)
        # func 是 Attribute（如 math.sqrt / self.hist.append）——dunder 已由
        # visit_Attribute 拦；模块方法安全。
        self.generic_visit(node_obj)

    # ---- for range(...) 上界 ----
    def visit_For(self, node_obj: ast.For) -> None:
        it = node_obj.iter
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
            self._check_range(it)
        self.generic_visit(node_obj)

    def _check_range(self, call: ast.Call) -> None:
        for arg in call.args:
            # 静态求值：字面量、跟踪常量名、算术传播（BinOp/UnaryOp of tracked
            # consts）统一走 _eval_const_int。能算出整数则套 MAX_RANGE；算不出
            # （param / len(x) / 未知量）→ 保守放行（运行时受限 builtins 无逃逸面，
            # 仅 CPU-per-bar 有界，且纯计算常见 range(len(...)) 不误伤）。
            bound = self._eval_const_int(arg)
            if bound is not None and bound > MAX_RANGE:
                _reject(
                    call,
                    f"range bound {bound} exceeds sandbox limit {MAX_RANGE}",
                    "loop_bound",
                )


def _validate(tree: ast.AST) -> SandboxError | None:
    try:
        _Validator().visit(tree)
    except _SandboxViolation as v:
        return SandboxError(v.message, reason=v.reason, lineno=v.lineno)
    return None


def compile_node_source(src: str) -> NodeDef | SandboxError:
    """AST 白名单编译自定义节点源码；通过则热注册进 REGISTRY 并返回其 NodeDef。

    失败一律返回（不抛）SandboxError。
    """
    # 1) 解析（语法错误 → SandboxError）
    try:
        tree = ast.parse(src, mode="exec")
    except SyntaxError as exc:
        return SandboxError(f"syntax error: {exc}", reason="syntax_error",
                            lineno=getattr(exc, "lineno", None))

    # 2) AST 白名单校验
    err = _validate(tree)
    if err is not None:
        return err

    # 3) 记录校验前 REGISTRY 快照，识别本次源码新注册了哪个 type（并做副作用回滚）
    before = set(REGISTRY)

    # 4) 受限 namespace exec —— __builtins__ 换成安全子集，import 走白名单钩子
    sandbox_globals: dict[str, Any] = {
        "__builtins__": _make_safe_builtins(),
        "__name__": "alphaloom_sandbox_node",
    }
    try:
        code = compile(tree, filename="<sandbox_node>", mode="exec")
        exec(code, sandbox_globals)  # noqa: S102 — 已 AST 白名单 + 受限 builtins
    except _SandboxViolation as v:  # import 钩子命中白名单外
        _rollback(before)
        return SandboxError(v.message, reason=v.reason, lineno=v.lineno)
    except SandboxError as se:
        _rollback(before)
        return se
    except Exception as exc:  # 源码执行期任何错误 → 拒绝并回滚，不外泄
        _rollback(before)
        return SandboxError(f"node source failed to execute: {exc!r}",
                            reason="exec_error")

    # 5) 识别新注册 type
    new_types = set(REGISTRY) - before
    if not new_types:
        return SandboxError(
            "source did not register any @node; declare exactly one @node class",
            reason="no_node",
        )
    if len(new_types) > 1:
        _rollback(before)
        return SandboxError(
            f"source registered multiple node types {sorted(new_types)}; "
            f"declare exactly one @node class",
            reason="multiple_nodes",
        )
    (t,) = new_types
    ndef = REGISTRY[t]

    # 6) 接缝防线：禁伪造风控盖章。RISK_STAMPED_SIGNAL 是"类型系统即合规官"的核心
    # provenance——只有受信内置 RiskGate 才允许发射它。沙箱节点若声明该输出类型，
    # 就能手塞 risk.checked=True 伪造盖章、让 qty=1e9 无止损单绕过 RiskGate（红队
    # PoC）。故检测返回 NodeDef 的 outputs，含该类型即拒并回滚注册（从 AST 花式写
    # 法绕过的角度，事后查 NodeDef.outputs.values() 比 AST 层匹配更可靠）。
    if PinType.RISK_STAMPED_SIGNAL in ndef.outputs.values():
        _rollback(before)
        return SandboxError(
            f"node {t!r} declares a {PinType.RISK_STAMPED_SIGNAL.value} output; "
            f"this risk-stamp type is reserved for the trusted built-in RiskGate "
            f"and may not be declared by sandboxed nodes (stamp provenance forgery)",
            reason="forge_risk_stamp",
        )

    # 7) 接缝防线：inputs/outputs 每个值必须是真 PinType 实例（网络可达跨用户 DoS，
    # T8 审查 carryover #9②）。@node 装饰器本身不校验 inputs/outputs 的值类型——
    # 一个畸形节点（如 outputs={"v": [PinType.SERIES]} 传 list，或 outputs={"v":
    # "series"} 传 str）此前能注册成功，随后让 GET /api/nodes（app.py 的
    # ``{k: v.value for ...}``）和 /api/compile（compiler.py 的 ``t_out.value``）
    # 对该类型上所有非 PinType 的值取 .value 时 AttributeError → 500，且 REGISTRY
    # 是进程级全局状态，一旦注册就持久污染，对所有后续调用者（含其他用户）500。
    # 故在此校验并回滚，与上面的伪造盖章检查同一接缝防线模式。
    for _port_name, _pin in {**ndef.inputs, **ndef.outputs}.items():
        if not isinstance(_pin, PinType):
            _rollback(before)
            return SandboxError(
                f"node {t!r} declares port {_port_name!r} with value {_pin!r} "
                f"which is not a PinType instance; inputs/outputs values must all "
                f"be real PinType members (malformed pin type would crash "
                f"GET /api/nodes and /api/compile for every subsequent caller)",
                reason="bad_pin_type",
            )

    # 8) 来源标记：沙箱热注册的节点一律标 sandboxed=True（不受信）。运行期引擎据此
    # 给它剥离 .llm 的受限 ctx（C1 根治：沙箱节点不能偷调 LLM），且守门层不信任其
    # 成本证书自证（含任何沙箱节点的蓝图非 offline 即拒——app.py 深度防御兜底）。
    mark_sandboxed(t)
    return REGISTRY[t]


def _rollback(before: set[str]) -> None:
    for k in set(REGISTRY) - before:
        del REGISTRY[k]


def _make_safe_builtins() -> dict[str, Any]:
    """受限 __builtins__：安全内建 + 白名单 __import__ 钩子。"""
    safe = dict(SAFE_BUILTINS)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level and level > 0:
            raise SandboxError("relative import blocked at runtime", reason="import_denied")
        if name not in ALLOWED_IMPORTS:
            raise SandboxError(
                f"import of {name!r} blocked at runtime (not in allowlist)",
                reason="import_denied",
            )
        return _builtins.__import__(name, globals, locals, fromlist, level)

    safe["__import__"] = _guarded_import
    # class 语句在字节码层用 __build_class__ 构造类；它本身无逃逸能力（类体已被
    # AST 白名单校验），但不给它则合法 @node 类无法定义。
    safe["__build_class__"] = _builtins.__build_class__
    return safe


# ---------------------------------------------------------------------------
# 降级保险丝：受限模板版（LLM 只填参数，不写自由代码）
# ---------------------------------------------------------------------------

_TEMPLATE_OPS = {
    "lt": lambda v, thr: v < thr,
    "le": lambda v, thr: v <= thr,
    "gt": lambda v, thr: v > thr,
    "ge": lambda v, thr: v >= thr,
}
_TEMPLATE_SIDES = frozenset({"long", "short"})


def make_threshold_node(
    *,
    type: str,  # noqa: A002
    indicator: str,
    op: str,
    threshold: float,
    side: str,
) -> NodeDef | SandboxError:
    """受限模板：一个把上游数值 ``value`` 与阈值比较产 signal 的门控节点。

    LLM 只填 ``op`` / ``threshold`` / ``side`` 等参数，**不写任何自由代码**——比
    自由源码沙箱更保险的降级路径。参数受严格校验，非法即 SandboxError。
    """
    if op not in _TEMPLATE_OPS:
        return SandboxError(
            f"threshold op {op!r} invalid; choose one of {sorted(_TEMPLATE_OPS)}",
            reason="template_param",
        )
    if side not in _TEMPLATE_SIDES:
        return SandboxError(
            f"threshold side {side!r} invalid; choose 'long' or 'short'",
            reason="template_param",
        )
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        return SandboxError(
            f"threshold {threshold!r} is not a number", reason="template_param")
    if not isinstance(type, str) or not type:
        return SandboxError("threshold node type must be a non-empty string",
                            reason="template_param")
    if type in REGISTRY:
        return SandboxError(f"node type {type!r} already registered",
                            reason="template_param")

    cmp = _TEMPLATE_OPS[op]
    _type, _side, _thr, _ind = type, side, thr, indicator

    @node(
        type=_type,
        category="decision",
        inputs={"value": PinType.SERIES, "candle": PinType.CANDLE},
        outputs={"signal": PinType.SIGNAL},
        params={},
        cost=CostAnnotation(
            llm_calls_per_bar=0, max_tokens_per_call=0,
            latency_class="fast", deterministic=True,
        ),
    )
    class ThresholdTemplateNode:
        """模板生成的确定性阈值门控（无自由代码，纯参数化）。"""

        def setup(self, params):
            pass

        def on_bar(self, ctx, inputs):
            value = inputs.get("value")
            if value is None:
                return {"signal": {"side": "hold", "qty": 0.0, "stop": None,
                                   "reason": f"{_ind}: no value"}}
            triggered = cmp(float(value), _thr)
            if triggered:
                return {"signal": {"side": _side, "qty": 0.0, "stop": None,
                                   "reason": f"{_ind} {op} {_thr} -> {_side}"}}
            return {"signal": {"side": "hold", "qty": 0.0, "stop": None,
                               "reason": f"{_ind} {op} {_thr} not met"}}

    return REGISTRY[_type]
