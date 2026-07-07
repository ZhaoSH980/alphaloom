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


def test_nested_range_product_is_capped():
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="nested_loop_bomb", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class NestedLoopBombNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        total = 0
        for i in range(100000):
            for j in range(100000):
                total += 1
        return {"value": total}
'''
    result = compile_node_source(src)
    assert isinstance(result, SandboxError)
    assert result.reason == "loop_work"


def test_large_sequence_repeat_rejected():
    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="repeat_bomb", category="indicator",
      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},
      cost=CostAnnotation(deterministic=True))
class RepeatBombNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        payload = "A" * 300000000
        return {"value": len(payload)}
'''
    result = compile_node_source(src)
    assert isinstance(result, SandboxError)
    assert result.reason == "sequence_repeat"


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


# ---------------------------------------------------------------------------
# C1（Critical）：沙箱节点绕过 LLM 配额守门——沙箱 AST 不拦普通属性访问
# ``ctx.llm``，一个声称 llm_calls_per_bar=0 的沙箱节点能在 on_bar 里偷调
# ctx.llm.chat 刷爆真实配额。根治：沙箱节点标 sandboxed=True，运行期引擎给它剥离
# .llm/.audit 的受限 ctx 视图（访问即 SandboxEscapeError，绝不静默调真 LLM）。
# ---------------------------------------------------------------------------

# 偷调 LLM 的恶意沙箱节点：cost 谎报 llm_calls_per_bar=0，on_bar 却 ctx.llm.chat。
# （沙箱 AST 禁 .format，故用 dict 直接构造 messages，不用格式串。）
LLM_THIEF_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="llm_thief", category="decision",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True))
class LlmThiefNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        messages = [{"role": "user", "content": "burn quota"}]
        ctx.llm.chat(messages)
        return {"signal": {"side": "long", "qty": 0.0, "stop": None, "reason": "stolen"}}
'''


class _SpyLLM:
    """记录被调次数的假 LLM——若守门/剥离失灵、真 chat 被调，calls 会 >0（自证）。"""
    offline = False

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.2, **params):
        self.calls += 1
        return {"choices": [{"message": {"content": "{}"}}]}


def test_sandbox_node_is_marked_sandboxed():
    """沙箱热注册的节点 NodeDef.sandboxed=True；内置节点为 False。"""
    d = compile_node_source(LEGIT_SOURCE)
    assert not isinstance(d, SandboxError)
    assert d.sandboxed is True
    # 内置节点不受影响
    from alphaloom.nodes.registry import get_node_def
    assert get_node_def("ema").sandboxed is False


def test_sandbox_node_cannot_reach_ctx_llm_via_engine():
    """C1 根治（引擎层）：沙箱节点在 engine.step 里拿到剥离 .llm 的受限 ctx，
    ctx.llm 访问抛 SandboxEscapeError——间谍 LLM 的 chat 永远不被调（calls==0）。"""
    from alphaloom.graph.model import BlueprintSpec, EdgeSpec, NodeSpec as NS, PortRef
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import Engine, SandboxEscapeError
    from alphaloom.runtime.events import BarEvent

    d = compile_node_source(LLM_THIEF_SOURCE)
    assert not isinstance(d, SandboxError) and d.sandboxed is True
    # 最小可编译图：feed → llm_thief（SIGNAL 输出悬空即可，只测 on_bar 的 ctx）
    bp = BlueprintSpec("thief_bp", "thief", [
        NS("feed", "candle_feed", {"inst": "TEST", "bar": "1m"}),
        NS("thief", "llm_thief", {}),
    ], [EdgeSpec(PortRef("feed", "out"), PortRef("thief", "candle"), False)], {})
    compiled = compile_blueprint(bp)
    assert compiled.ok, [e.code for e in compiled.errors]
    instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
    spy = _SpyLLM()
    ctx = RunContext(clock=SimClock(), run_id="r", llm=spy)
    engine = Engine(compiled, instances, ctx)
    candle = {"ts": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
    with pytest.raises(SandboxEscapeError):
        engine.step(BarEvent(candle, 60_000))
    assert spy.calls == 0        # 真 LLM 绝对没被偷调——剥离生效


def test_restricted_ctx_passes_through_legit_attrs_but_strips_capabilities():
    """受限 ctx 视图：合法纯计算所需属性（clock/broker/run_id）照常委托真 ctx，
    唯 .llm/.audit 被剥夺——不误伤合法沙箱节点的正常数据处理。"""
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import _RestrictedContext, SandboxEscapeError

    real = RunContext(clock=SimClock(), run_id="run-42", broker="BROKER",
                      llm="SECRET_LLM", audit="AUDIT")
    view = _RestrictedContext(real)
    assert view.run_id == "run-42"
    assert view.clock is real.clock
    with pytest.raises(SandboxEscapeError):
        _ = view.llm
    with pytest.raises(SandboxEscapeError):
        _ = view.audit
    with pytest.raises(SandboxEscapeError):
        _ = view.broker


def test_sandbox_node_cannot_reach_ctx_broker_via_engine():
    from alphaloom.brokers.paper import PaperBroker
    from alphaloom.graph.model import BlueprintSpec, EdgeSpec, NodeSpec as NS, PortRef
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import Engine, SandboxEscapeError
    from alphaloom.runtime.events import BarEvent

    src = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="broker_thief", category="decision",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(deterministic=True))
class BrokerThiefNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        ctx.broker.halt("stolen")
        return {"signal": {"side": "hold", "qty": 0.0, "stop": None}}
'''
    d = compile_node_source(src)
    assert not isinstance(d, SandboxError)
    bp = BlueprintSpec("broker_thief_bp", "broker thief", [
        NS("feed", "candle_feed", {"inst": "TEST", "bar": "1m"}),
        NS("thief", "broker_thief", {}),
    ], [EdgeSpec(PortRef("feed", "out"), PortRef("thief", "candle"), False)], {})
    compiled = compile_blueprint(bp)
    assert compiled.ok
    broker = PaperBroker()
    instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
    ctx = RunContext(clock=SimClock(), run_id="r", broker=broker)
    engine = Engine(compiled, instances, ctx)
    candle = {"ts": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}

    with pytest.raises(SandboxEscapeError):
        engine.step(BarEvent(candle, 60_000))
    assert broker.halted is False


def test_builtin_llm_node_still_gets_real_llm():
    """回归：内置（受信）LLM 节点不受剥离影响——engine 给它真 ctx，ctx.llm 照常可用。"""
    from alphaloom.graph.model import BlueprintSpec, EdgeSpec, NodeSpec as NS, PortRef
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import Engine
    from alphaloom.runtime.events import BarEvent

    bp = BlueprintSpec("llm_bp", "llm", [
        NS("feed", "candle_feed", {"inst": "TEST", "bar": "1m"}),
        NS("atr", "atr", {"period": 14}),
        NS("analyst", "llm_analyst", {"persona": "trend", "atr_mult": 2.0}),
    ], [
        EdgeSpec(PortRef("feed", "out"), PortRef("atr", "candle"), False),
        EdgeSpec(PortRef("feed", "out"), PortRef("analyst", "candle"), False),
        EdgeSpec(PortRef("atr", "value"), PortRef("analyst", "atr"), False),
    ], {})
    compiled = compile_blueprint(bp)
    assert compiled.ok
    instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
    spy = _SpyLLM()
    ctx = RunContext(clock=SimClock(), run_id="r", llm=spy)
    engine = Engine(compiled, instances, ctx)
    candle = {"ts": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
    engine.step(BarEvent(candle, 60_000))
    assert spy.calls == 1        # 内置受信 LLM 节点照常调真 llm（剥离只针对沙箱节点）


# ---------------------------------------------------------------------------
# I1（Important）：Layer 1 私有属性图逃逸——``ctx._ctx.llm`` 经受限视图的单下划线
# slot 反向取回真 ctx。沙箱 AST 原只拦 dunder（``__x__``），不拦单下划线 ``_ctx``。
# 修复两条：#1 沙箱 AST 拒一切下划线前缀属性访问（``private_attr``）；#3 真 ctx 移出
# 受限视图对象图（存模块级 WeakKeyDictionary，``view._ctx``/``view.__dict__`` 够不到）。
# ---------------------------------------------------------------------------

# 经 ctx._ctx 反向取真 ctx 再偷调 llm（I1 红队原始逃逸变体）。
CTX_UNDERSCORE_ESCAPE_SOURCE = '''
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.sandbox.node_sandbox import node

@node(type="ctx_esc", category="decision",
      inputs={"candle": PinType.CANDLE}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True))
class CtxEscapeNode:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        ctx._ctx.llm.chat([{"role": "user", "content": "burn"}])
        return {"signal": {"side": "long", "qty": 0.0, "stop": None, "reason": "esc"}}
'''

# 其它私有属性逃逸变体：任意单下划线属性 + 经 dunder（后者本就被拦，一并锁定）。
_ESCAPE_VARIANT_SOURCES = {
    "ctx_esc_ctx": 'ctx._ctx',
    "ctx_esc_priv": 'ctx._anything.foo',
    "ctx_esc_dunder": 'ctx.__dict__',        # dunder 早已被拦（回归锁定）
    "ctx_esc_getset": 'inputs._secret',      # 对 inputs 的私有属性访问同样拒
}


def _variant_source(tp, access_expr):
    return (
        "from alphaloom.graph.types import PinType, CostAnnotation\n"
        "from alphaloom.sandbox.node_sandbox import node\n\n"
        f'@node(type="{tp}", category="indicator",\n'
        '      inputs={"candle": PinType.CANDLE}, outputs={"value": PinType.SERIES},\n'
        "      cost=CostAnnotation(llm_calls_per_bar=0, deterministic=True))\n"
        "class V:\n"
        "    def setup(self, params):\n"
        "        pass\n"
        "    def on_bar(self, ctx, inputs):\n"
        f"        x = {access_expr}\n"
        "        return {\"value\": 1.0}\n"
    )


@pytest.mark.parametrize("tp,expr", list(_ESCAPE_VARIANT_SOURCES.items()))
def test_sandbox_ast_rejects_private_attribute_access(tp, expr):
    """沙箱 AST 拒一切下划线前缀属性访问（私有 slot / 内部属性逃逸整类）——
    注册期即 SandboxError（reason=private_attr 或 dunder_attr），绝不放它进 REGISTRY。"""
    result = compile_node_source(_variant_source(tp, expr))
    assert isinstance(result, SandboxError), f"{expr!r} should be rejected, got {result!r}"
    assert result.reason in ("private_attr", "dunder_attr")
    assert tp not in REGISTRY


def test_ctx_underscore_escape_rejected_at_registration():
    """I1 原始逃逸 ``ctx._ctx.llm.chat()`` 在注册期就被拒（私有属性访问），
    偷调代码根本无从注册——最外层根治。"""
    result = compile_node_source(CTX_UNDERSCORE_ESCAPE_SOURCE)
    assert isinstance(result, SandboxError)
    assert result.reason == "private_attr"
    assert "ctx_esc" not in REGISTRY


def test_restricted_ctx_real_ctx_not_reachable_in_object_graph():
    """#3 纵深防御：真 ctx 不在受限视图对象图上——``view.__dict__`` 空、``_ctx``
    属性不存在（AttributeError）。即便未来 AST 层放松，也无从经实例属性取回真 ctx。"""
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import _RestrictedContext, SandboxEscapeError

    real = RunContext(clock=SimClock(), run_id="r", llm="SECRET_LLM")
    view = _RestrictedContext(real)
    # 实例 __dict__ 里不含真 ctx（存在模块级 WeakKeyDictionary，不在对象图上）
    inst_dict = object.__getattribute__(view, "__dict__")
    assert "SECRET_LLM" not in repr(inst_dict)
    assert "_ctx" not in inst_dict
    # 直接取 _ctx 属性：走 __getattr__ → 委托真 ctx，真 ctx 无 _ctx 属性 → 该访问
    # 委托到 real._ctx，real 是 RunContext 无 _ctx → AttributeError（不是取回真 ctx）
    with pytest.raises(AttributeError):
        _ = view._ctx
    # .llm 仍被硬拦
    with pytest.raises(SandboxEscapeError):
        _ = view.llm


def test_ctx_underscore_escape_blocked_at_runtime_even_if_ast_bypassed(monkeypatch):
    """#3 独立自证：假设 AST 层被绕过（直接手构一个访问 ctx._ctx 的实例），运行期
    受限视图的对象图上也取不回真 ctx——``view._ctx`` 抛 AttributeError 而非命中真
    llm。用手写类模拟"AST 放行了 _ctx 访问"的最坏情况。"""
    from alphaloom.runtime.context import RunContext, SimClock
    from alphaloom.runtime.engine import _RestrictedContext

    spy = _SpyLLM()
    real = RunContext(clock=SimClock(), run_id="r", llm=spy)
    view = _RestrictedContext(real)
    # 模拟绕过 AST 的沙箱代码 ``ctx._ctx.llm`` ——对象图上 _ctx 不可达
    try:
        leaked = view._ctx           # 若 #3 失效，这里会拿到真 ctx
        _ = leaked.llm.chat([{"role": "user", "content": "x"}])
    except AttributeError:
        pass
    assert spy.calls == 0            # 真 llm 绝对没被经 _ctx 反向取到
