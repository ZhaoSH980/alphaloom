# backend/alphaloom/runtime/engine.py
from __future__ import annotations
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


class _RestrictedContext:
    """沙箱节点专用 ctx 视图：除 .llm/.audit 外全部委托真 ctx（合法纯计算所需的
    clock/broker/current_event/run_id/recorder/halted 照常可用），.llm/.audit
    访问即抛 SandboxEscapeError。

    受限视图**不是共享活 ctx**——沙箱节点拿不到真 ctx 引用，故也无从旁路取回
    llm 句柄（属性委托只读放行白名单外的普通属性，被拒名单硬拦 llm/audit）。
    """

    __slots__ = ("_ctx",)

    def __init__(self, ctx: RunContext) -> None:
        object.__setattr__(self, "_ctx", ctx)

    def __getattr__(self, name):
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(
                f"sandboxed node may not access ctx.{name}: the LLM handle is "
                "stripped from sandbox nodes (declared-deterministic must be truly "
                "deterministic; sandbox nodes cannot burn LLM quota)")
        return getattr(object.__getattribute__(self, "_ctx"), name)

    def __setattr__(self, name, value):
        if name in _SANDBOX_DENIED_CTX_ATTRS:
            raise SandboxEscapeError(
                f"sandboxed node may not set ctx.{name}")
        setattr(object.__getattribute__(self, "_ctx"), name, value)

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
