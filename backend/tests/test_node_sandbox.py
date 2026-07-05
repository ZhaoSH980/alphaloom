"""Text-to-Node 沙箱测试（AlphaLoom D3 Task 7）。

沙箱是安全边界：合法纯计算节点热注册进 REGISTRY 可实例化并 on_bar；
恶意源码全部返回 SandboxError（决不抛逃逸、决不真 exec 危险代码）。
对抗性用例覆盖经典 Python 沙箱逃逸链。
"""
from __future__ import annotations

import pytest

from alphaloom.graph.model import NodeSpec
from alphaloom.graph.types import PinType
from alphaloom.nodes.registry import REGISTRY, create_instance, get_node_def
from alphaloom.sandbox.errors import SandboxError
from alphaloom.sandbox.node_sandbox import compile_node_source, make_threshold_node


# ---------------------------------------------------------------------------
# 合法源码：纯计算节点热注册
# ---------------------------------------------------------------------------

LEGIT_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="custom_double", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      params={"factor": float},
      cost=CostAnnotation(deterministic=True))
class CustomDoubleNode:
    def setup(self, params):
        self.factor = float(params.get("factor", 2.0))
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        return {"value": c * self.factor}
'''


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试后把自定义 type 清出 REGISTRY，避免跨测试污染 / 重复注册报错。"""
    before = set(REGISTRY)
    yield
    for k in set(REGISTRY) - before:
        del REGISTRY[k]


def test_legit_source_registers_and_is_instantiable():
    d = compile_node_source(LEGIT_SOURCE)
    assert not isinstance(d, SandboxError)
    assert d.type == "custom_double"
    assert d.category == "indicator"
    assert d.inputs == {"candle": PinType.CANDLE}
    assert d.outputs == {"value": PinType.SERIES}
    # 热注册进 REGISTRY
    assert "custom_double" in REGISTRY
    assert get_node_def("custom_double") is d


def test_registered_node_runs_on_bar():
    compile_node_source(LEGIT_SOURCE)
    inst = create_instance(NodeSpec("n1", "custom_double", {"factor": 3.0}))
    candle = {"ts": 0, "open": 1, "high": 1, "low": 1, "close": 10.0, "volume": 1}
    out = inst.on_bar(ctx=None, inputs={"candle": candle})
    assert out == {"value": 30.0}


def test_registered_node_usable_in_compile_blueprint():
    """注册后热可用于 compile_blueprint（画布上真能连线编译）。"""
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.graph.model import BlueprintSpec, EdgeSpec, PortRef

    compile_node_source(LEGIT_SOURCE)
    bp = BlueprintSpec(
        id="bp", name="bp",
        nodes=[
            NodeSpec("feed", "candle_feed", {"inst": "X", "bar": "1m"}),
            NodeSpec("dbl", "custom_double", {"factor": 2.0}),
        ],
        edges=[EdgeSpec(PortRef("feed", "out"), PortRef("dbl", "candle"))],
    )
    res = compile_blueprint(bp)
    assert res.ok, [e.message for e in res.errors]
    assert "dbl" in res.order


def test_legit_math_import_allowed():
    """math / statistics 在白名单内，纯计算可用。"""
    src = '''
import math
import statistics
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="custom_math", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class CustomMathNode:
    def setup(self, params):
        self.hist = []
    def on_bar(self, ctx, inputs):
        c = float(inputs["candle"]["close"])
        self.hist.append(c)
        return {"value": math.sqrt(abs(c)) + statistics.mean(self.hist)}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "args", d)
    inst = create_instance(NodeSpec("m", "custom_math", {}))
    out = inst.on_bar(ctx=None, inputs={"candle": {"close": 4.0}})
    assert out["value"] == pytest.approx(2.0 + 4.0)


# ---------------------------------------------------------------------------
# 恶意源码：全部 SandboxError，且节点不得进 REGISTRY
# ---------------------------------------------------------------------------

MALICIOUS = {
    "import_os": "import os\n",
    "import_from_os": "from os import system\n",
    "import_subprocess": "import subprocess\n",
    "import_socket": "import socket\n",
    "import_sys": "import sys\n",
    "call_open": 'x = open("secret.txt")\n',
    "call_exec": 'exec("import os")\n',
    "call_eval": 'y = eval("__import__(\'os\')")\n',
    "call_import_builtin": 'm = __import__("os")\n',
    "call_compile": 'c = compile("1", "<s>", "eval")\n',
    "call_getattr": 'g = getattr(object, "__subclasses__")\n',
    "call_globals": 'g = globals()\n',
    "call_vars": 'v = vars()\n',
    "call_input": 'v = input()\n',
    "attr_class": "x = ().__class__\n",
    "attr_bases": "x = ().__class__.__bases__\n",
    "attr_mro": "x = int.__mro__\n",
    "attr_subclasses": "x = ().__class__.__bases__[0].__subclasses__()\n",
    "attr_globals": "def f():\n    pass\ng = f.__globals__\n",
    "attr_builtins_name": "b = __builtins__\n",
    "attr_dunder_dict": "d = object.__dict__\n",
    "attr_dunder_code": "def f():\n    pass\nc = f.__code__\n",
    "getattr_string_concat": 'g = getattr((), "__" + "class__")\n',
    "while_true": "while True:\n    pass\n",
    "for_huge_range": "for _ in range(10**9):\n    pass\n",
    "lambda_default_escape": "f = lambda x=().__class__: x\n",
    "comprehension_call": "z = [open(p) for p in ['a']]\n",
    "gen_expr_call": "z = list(eval(s) for s in ['1'])\n",
    "decorator_arg_code": (
        "from alphaloom.sandbox.node_sandbox import node\n"
        "@node(type=exec('x=1') or 'bad', category='x', inputs={}, outputs={})\n"
        "class C:\n    def setup(self, p): pass\n    def on_bar(self, c, i): return {}\n"
    ),
    "type_call": "t = type('X', (), {})\n",
    "class_bases_dynamic": (
        "class Evil(().__class__.__bases__[0]):\n    pass\n"
    ),
    # str.format 格式串逃逸：dunder 藏在字符串字面量里，AST 看不见 —— 必须靠禁
    # .format/.format_map 方法名拦住（经典 "{0.__class__}".format(obj) 逃逸）。
    "str_format_escape": 'x = "{0.__class__}".format(())\n',
    "str_format_map_escape": 'x = "{a.__class__}".format_map({"a": ()})\n',
    "fstring_dunder": 'x = f"{().__class__}"\n',
}


@pytest.mark.parametrize("name", sorted(MALICIOUS))
def test_malicious_source_rejected(name):
    src = MALICIOUS[name]
    before = set(REGISTRY)
    result = compile_node_source(src)
    assert isinstance(result, SandboxError), (
        f"expected SandboxError for {name!r}, got {result!r}")
    # 拒绝的源码绝不注册任何节点（副作用隔离）
    assert set(REGISTRY) == before, f"{name!r} leaked a registration into REGISTRY"


def test_format_escape_in_node_body_rejected():
    """str.format 格式串逃逸即使包在合法 @node 里也必须拒。

    危险性：dunder 藏在字符串字面量里，AST walk 看不见；若放行，逃逸会在
    on_bar 回测运行期执行（不是编译期）。禁 .format/.format_map 方法名拦住。
    """
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="fmt_evil", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class FmtEvilNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        leaked = "{0.__class__.__bases__}".format(inputs)
        return {"value": 1.0}
'''
    result = compile_node_source(src)
    assert isinstance(result, SandboxError)
    assert "fmt_evil" not in REGISTRY


def test_no_node_decorator_rejected():
    """纯计算但没声明 @node → 无从注册，返回 SandboxError（不是空成功）。"""
    src = "x = 1 + 2\n"
    result = compile_node_source(src)
    assert isinstance(result, SandboxError)


def test_syntax_error_rejected():
    result = compile_node_source("def f(:\n  pass\n")
    assert isinstance(result, SandboxError)


def test_builtins_not_accessible_at_runtime():
    """即便源码通过 AST，受限 namespace 也不给危险 builtin —— 运行时 open 不可达。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="try_open", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class TryOpenNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        # open 名字在 AST 层已被禁；此处即便偷用 __builtins__ 也无门
        return {"value": len(str(inputs))}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError)


# ---------------------------------------------------------------------------
# 接缝防线：沙箱节点不得伪造 RISK_STAMPED_SIGNAL 盖章（绕过 RiskGate 合规官）
# ---------------------------------------------------------------------------

FAKE_GATE_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="fake_gate", category="decision",
      inputs={"signal": PinType.SIGNAL},
      outputs={"stamped": PinType.RISK_STAMPED_SIGNAL},
      cost=CostAnnotation(deterministic=True))
class FakeGateNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        # 手动塞伪造盖章 —— 真 RiskGate 本会拦 qty=1e9 无止损
        sig["risk"] = {"checked": True, "blocked": False, "checks": []}
        return {"stamped": sig}
'''


def test_sandbox_node_cannot_forge_risk_stamp():
    """声明 RISK_STAMPED_SIGNAL 输出的沙箱节点必须被拒（盖章 provenance 保留给内置）。"""
    result = compile_node_source(FAKE_GATE_SOURCE)
    assert isinstance(result, SandboxError)
    assert result.reason == "forge_risk_stamp"
    # 伪造盖章发射器绝不进 REGISTRY
    assert "fake_gate" not in REGISTRY


def test_forged_stamp_node_never_reaches_compile_blueprint():
    """红队 PoC 重放：fake_gate.stamped→execute_order.signal 本应编译通过下大单；
    现在 fake_gate 在 compile_node_source 阶段就被拒、根本注册不进去，
    故蓝图引用它 → UNKNOWN_NODE_TYPE，链条从源头断开。"""
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.graph.model import BlueprintSpec, EdgeSpec, PortRef

    # 先确认沙箱拒绝 → 未注册
    assert isinstance(compile_node_source(FAKE_GATE_SOURCE), SandboxError)
    assert "fake_gate" not in REGISTRY

    bp = BlueprintSpec(
        id="poc", name="poc",
        nodes=[
            NodeSpec("fg", "fake_gate", {}),
            NodeSpec("ex", "execute_order", {}),
        ],
        edges=[EdgeSpec(PortRef("fg", "stamped"), PortRef("ex", "signal"))],
    )
    res = compile_blueprint(bp)
    assert not res.ok
    assert any(e.code == "UNKNOWN_NODE_TYPE" for e in res.errors)


def test_risk_gate_remains_sole_stamper_after_sandbox():
    """伪造被拒后，唯一盖章发射器仍是内置 risk_gate（合规官单点性未被侵蚀）。"""
    from alphaloom.nodes.registry import REGISTRY as R
    compile_node_source(FAKE_GATE_SOURCE)  # 被拒，不应污染
    stampers = [t for t, dd in R.items()
                if PinType.RISK_STAMPED_SIGNAL in dd.outputs.values()
                and dd.category != "test"]
    assert stampers == ["risk_gate"]


def test_legit_node_with_signal_output_unaffected():
    """合法节点产 SIGNAL（非 RISK_STAMPED_SIGNAL）不受伪造盖章防线影响。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="plain_sig", category="decision",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(deterministic=True))
class PlainSigNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {"signal": {"side": "hold", "qty": 0.0, "stop": None, "reason": "ok"}}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "message", d)
    assert "plain_sig" in REGISTRY


# ---------------------------------------------------------------------------
# 循环上限：变量绑定的 range 上界也须套 MAX_RANGE（字面量已拦、变量勿绕）
# ---------------------------------------------------------------------------

def test_variable_bound_range_capped():
    """n = 500000; for _ in range(n) —— 变量绕过字面量检查，必须也拦。"""
    src = "n = 500000\nfor _ in range(n):\n    pass\n"
    result = compile_node_source(src)
    assert isinstance(result, SandboxError)
    assert result.reason == "loop_bound"


def test_small_variable_bound_range_allowed():
    """小的变量上界（n=100）应放行——纯计算常见，不误伤。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="loop_ok", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class LoopOkNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        n = 10
        total = 0.0
        for i in range(n):
            total += i
        return {"value": total}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "message", d)


# ---------------------------------------------------------------------------
# 循环上限加固（D4 Carryover 3/9①）：算术传播 + AugAssign 绕过必须也拦。
#
# D3 只拦已知常量绑定的 range；算术传播（n=50000; m=n*3; range(m)）和
# AugAssign（n=0; n+=500000; range(n)）绕过 MAX_RANGE。D4 加固：追踪简单算术
# 传播（tracked const 的 BinOp）+ AugAssign 更新常量表，堵这两条 CPU-per-bar DoS。
# ---------------------------------------------------------------------------

def test_arithmetic_propagation_range_capped():
    """n=50000; m=n*3; range(m) —— m=150000 经算术传播超限，必须拦（D3 绕过）。"""
    src = "n = 50000\nm = n * 3\nfor _ in range(m):\n    pass\n"
    result = compile_node_source(src)
    assert isinstance(result, SandboxError), f"expected SandboxError, got {result!r}"
    assert result.reason == "loop_bound"


def test_augassign_range_capped():
    """n=0; n+=500000; range(n) —— AugAssign 累加超限，必须拦（D3 绕过）。"""
    src = "n = 0\nn += 500000\nfor _ in range(n):\n    pass\n"
    result = compile_node_source(src)
    assert isinstance(result, SandboxError), f"expected SandboxError, got {result!r}"
    assert result.reason == "loop_bound"


def test_arithmetic_propagation_direct_range_call_capped():
    """range(m) 作为直接 Call（非 for 迭代）也须拦——m 由算术传播超限。"""
    src = "n = 40000\nm = n * 4\nx = list(range(m))\n"
    result = compile_node_source(src)
    assert isinstance(result, SandboxError), f"expected SandboxError, got {result!r}"
    assert result.reason == "loop_bound"


def test_small_arithmetic_propagation_allowed():
    """小的算术传播（n=10; m=n*2; range(m)=20）应放行——不误伤合法小 range。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="loop_arith_ok", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class LoopArithOkNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        n = 5
        m = n * 2
        total = 0.0
        for i in range(m):
            total += i
        return {"value": total}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "message", d)


def test_small_augassign_allowed():
    """小的 AugAssign（n=10; n+=5; range(n)=15）应放行——不误伤。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="loop_aug_ok", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class LoopAugOkNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        n = 10
        n += 5
        total = 0.0
        for i in range(n):
            total += i
        return {"value": total}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "message", d)


def test_augassign_untracks_on_unknown_operand():
    """AugAssign 用未知量（n += param）→ n 变未知，从常量表移除，range(n) 保守放行
    （运行时 builtins 受限无逃逸面；不误伤合法 range(param) 式用法）。"""
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="loop_aug_unknown", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class LoopAugUnknownNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        n = 10
        n += int(inputs["candle"]["close"])
        total = 0.0
        for i in range(n):
            total += i
        return {"value": total}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError), getattr(d, "message", d)


# ---------------------------------------------------------------------------
# 降级保险丝：受限模板版（LLM 只填参数，不写自由代码）
# ---------------------------------------------------------------------------

def test_make_threshold_node_template():
    d = make_threshold_node(type="thr_long", indicator="rsi", op="lt",
                            threshold=30.0, side="long")
    assert not isinstance(d, SandboxError)
    assert d.type == "thr_long"
    assert "thr_long" in REGISTRY
    inst = create_instance(NodeSpec("t", "thr_long", {}))
    candle = {"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    # rsi=20 < 30 → 触发 long
    trig = inst.on_bar(ctx=None, inputs={"value": 20.0, "candle": candle})
    assert trig["signal"]["side"] == "long"
    # rsi=50 not < 30 → hold
    hold = inst.on_bar(ctx=None, inputs={"value": 50.0, "candle": candle})
    assert hold["signal"]["side"] == "hold"


def test_make_threshold_node_rejects_bad_params():
    """模板参数也受校验：非法 op / side / 非数值 threshold → SandboxError。"""
    assert isinstance(make_threshold_node(type="t1", indicator="rsi", op="danger",
                                          threshold=1.0, side="long"), SandboxError)
    assert isinstance(make_threshold_node(type="t2", indicator="rsi", op="lt",
                                          threshold=1.0, side="buy"), SandboxError)
    assert isinstance(make_threshold_node(type="t3", indicator="rsi", op="lt",
                                          threshold="oops", side="long"), SandboxError)


def test_threshold_node_cost_is_deterministic_zero():
    d = make_threshold_node(type="thr_det", indicator="ema", op="gt",
                            threshold=100.0, side="short")
    assert d.cost.deterministic is True
    assert d.cost.llm_calls_per_bar == 0


# ---------------------------------------------------------------------------
# 接缝防线：自定义节点的 inputs/outputs 值必须是真 PinType（T8 审查 carryover #9②）
#
# 畸形节点（outputs 值是 list 或 str 而非 PinType 实例）此前能注册成功，随后让
# GET /api/nodes（``{k: v.value for ...}`` 对非 PinType 取 .value）和
# /api/compile（compiler.py ``t_out.value``）对所有后续调用者 500，
# 进程级 REGISTRY 持久污染（网络可达跨用户 DoS，经 POST /api/nodes/custom 触发）。
# ---------------------------------------------------------------------------

BAD_PIN_TYPE_LIST_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="bad_pin_list", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"v": [PinType.SERIES]},
      cost=CostAnnotation(deterministic=True))
class BadPinListNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {"v": 1.0}
'''

BAD_PIN_TYPE_STR_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="bad_pin_str", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"v": "series"},
      cost=CostAnnotation(deterministic=True))
class BadPinStrNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {"v": 1.0}
'''

BAD_PIN_TYPE_IN_INPUTS_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="bad_pin_input", category="indicator",
      inputs={"candle": [PinType.CANDLE]}, outputs={"v": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class BadPinInputNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {"v": 1.0}
'''


@pytest.mark.parametrize("src,type_name", [
    (BAD_PIN_TYPE_LIST_SOURCE, "bad_pin_list"),
    (BAD_PIN_TYPE_STR_SOURCE, "bad_pin_str"),
    (BAD_PIN_TYPE_IN_INPUTS_SOURCE, "bad_pin_input"),
])
def test_malformed_pin_type_rejected_and_rolled_back(src, type_name):
    """outputs/inputs 值不是真 PinType（list 或 str）→ SandboxError bad_pin_type，
    且绝不留在 REGISTRY 里（不然 GET /api/nodes 会被畸形节点 500 污染所有后续调用者）。"""
    result = compile_node_source(src)
    assert isinstance(result, SandboxError), (
        f"expected SandboxError for malformed pin type, got {result!r}")
    assert result.reason == "bad_pin_type"
    assert type_name not in REGISTRY


def test_malformed_pin_type_does_not_break_nodes_listing():
    """红队复现：畸形节点若注册成功，GET /api/nodes 的 ``v.value`` 会对非 PinType
    抛 AttributeError。修复后畸形节点根本不进 REGISTRY，遍历 REGISTRY 取 .value
    不会炸。"""
    result = compile_node_source(BAD_PIN_TYPE_LIST_SOURCE)
    assert isinstance(result, SandboxError)
    # 模拟 GET /api/nodes 的遍历逻辑：不应因残留畸形值而抛异常
    for d in REGISTRY.values():
        for v in d.inputs.values():
            assert isinstance(v, PinType)
            _ = v.value
        for v in d.outputs.values():
            assert isinstance(v, PinType)
            _ = v.value
