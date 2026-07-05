# backend/alphaloom/runtime/engine.py
from __future__ import annotations
import weakref
from alphaloom.graph.types import Stamped
from alphaloom.graph.compiler import CompileResult
from alphaloom.runtime.context import RunContext, check_stamped
from alphaloom.runtime.events import BarEvent

class EngineDead(RuntimeError):
    """引擎已崩溃（毒化契约 Carryover 14①）：实例必须弃用。"""


class SandboxEscapeError(RuntimeError):
    """沙箱节点试图访问被剥夺的 ctx 能力（.llm / .audit）——配额/证书信任缺口拦截。

    沙箱自定义节点可谎报 cost 证书 llm_calls_per_bar=0 却在 on_bar 里偷调
    ctx.llm.chat 刷爆真实配额（C1 红队 PoC）。根治：运行期给沙箱节点一个剥离
    LLM 句柄的受限 ctx 视图，任何 .llm/.audit 访问即抛此错——"沙箱节点声称
    确定性"由此成为真确定性（沙箱不能偷烧钱，正如不能伪造风控盖章）。
    """


# 沙箱节点被剥夺访问的 ctx 能力（LLM 句柄 + 其 provenance 审计钩子）。
_SANDBOX_DENIED_CTX_ATTRS = frozenset({"llm", "audit"})

# 受限视图 → 真 ctx 的映射存**类外的模块级 WeakKeyDictionary**（不在实例对象图上）。
# 这样真 ctx 在受限视图的对象图上根本不可达：``view.__dict__`` 为空、``view._ctx``
# 不存在（AttributeError 且已被沙箱 AST 单下划线拒绝双保险）——I1 红队"经私有 slot
# 反向取回真 ctx"的逃逸从对象图层面被彻底切断（防未来新增 slot 再犯）。弱引用键
# 使受限视图被回收时映射项自动清理，无泄漏。
_CTX_BACKING: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


class _RestrictedContext:
    """沙箱节点专用 ctx 视图：除 .llm/.audit 外全部委托真 ctx（合法纯计算所需的
    clock/broker/current_event/run_id/recorder/halted 照常可用），.llm/.audit
    访问即抛 SandboxEscapeError。

    **真 ctx 不在本对象图上**——存于模块级 WeakKeyDictionary（``_CTX_BACKING``），
    实例本身无任何指向真 ctx 的属性（``__dict__`` 空、无 ``_ctx`` slot）。故沙箱
    节点既够不到被剥夺的 .llm/.audit（被拒名单硬拦），也无从经任何私有属性反向
    取回真 ctx（对象图上不可达 + 沙箱 AST 拒单下划线属性访问，双重保险）。
    """

    # 无 __slots__ 声明（也就没有 _ctx slot）；实例 __dict__ 保持空。
    def __init__(self, ctx: RunContext) -> None:
        _CTX_BACKING[self] = ctx

    def __getattr__(self, name):
        # __getattr__ 只在常规查找失败时触发——本类无实例属性，故所有属性都走这里。
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(
                f"sandboxed node may not access ctx.{name}: the LLM handle is "
                "stripped from sandbox nodes (declared-deterministic must be truly "
                "deterministic; sandbox nodes cannot burn LLM quota)")
        ctx = _CTX_BACKING.get(self)
        if ctx is None:
            raise AttributeError(name)
        return getattr(ctx, name)

    def __setattr__(self, name, value):
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(
                f"sandboxed node may not set ctx.{name}")
        ctx = _CTX_BACKING.get(self)
        if ctx is None:                        # __init__ 期尚未登记 → 内部错误
            raise AttributeError(name)
        setattr(ctx, name, value)

class Engine:
    def __init__(self, compiled: CompileResult, instances: dict, ctx: RunContext,
                 breakpoints: set[str] | None = None, on_pause=None):
        self.compiled = compiled
        self.instances = dict(instances)
        self.ctx = ctx
        self.breakpoints = breakpoints or set()
        self.on_pause = on_pause
        self.after_node = None            # 测试/调试钩子
        self._prev: dict[tuple[str, str], Stamped | None] = {}
        self._event_idx = 0
        self._dead = False
        # 沙箱节点专用受限 ctx（剥离 .llm/.audit）——懒构造、全 run 复用。
        self._restricted_ctx = _RestrictedContext(ctx)

    def run(self, events) -> None:
        for ev in events:
            self.step(ev)

    def step(self, ev: BarEvent) -> None:
        if self._dead:
            raise EngineDead("engine crashed earlier; discard this instance (crash contract)")
        try:
            self._step_inner(ev)
        except Exception:
            self._dead = True
            raise

    def _step_inner(self, ev: BarEvent) -> None:
        self.ctx.clock.advance(ev.ts_close)
        self.ctx.current_event = ev
        wave: dict[tuple[str, str], Stamped] = {}
        for node_id in self.compiled.order:
            inst = self.instances[node_id]
            raw_inputs: dict = {}
            rec_inputs: dict = {}
            for b in self.compiled.bindings.get(node_id, []):
                key = (b.src_node, b.src_port)
                stamped = self._prev.get(key) if b.feedback else wave.get(key)
                rec_inputs[b.dst_port] = stamped
                raw_inputs[b.dst_port] = stamped.value if isinstance(stamped, Stamped) else stamped
            if node_id in self.breakpoints and self.on_pause:
                self.on_pause(node_id, ev, raw_inputs)
            # 沙箱节点拿剥离 LLM 句柄的受限 ctx（C1 根治）；内置受信节点拿真 ctx。
            node_ctx = self._restricted_ctx if getattr(inst, "sandboxed", False) \
                else self.ctx
            outputs = inst.on_bar(node_ctx, raw_inputs) or {}
            stamped_outputs: dict[str, Stamped] = {}
            for port, val in outputs.items():
                s = val if isinstance(val, Stamped) else Stamped(val, self.ctx.clock.now)
                check_stamped(node_id, s, self.ctx.clock.now)
                stamped_outputs[port] = s
                wave[(node_id, port)] = s
            if self.after_node:
                self.after_node(node_id, stamped_outputs)
            if self.ctx.recorder:
                self.ctx.recorder.record(self.ctx.run_id, self._event_idx, ev.ts_close,
                                         node_id, rec_inputs, stamped_outputs)
        self._prev = wave
        self._event_idx += 1
