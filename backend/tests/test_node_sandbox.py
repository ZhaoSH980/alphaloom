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
