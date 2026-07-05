# backend/alphaloom/runtime/engine.py
from __future__ import annotations
from alphaloom.graph.types import Stamped
from alphaloom.graph.compiler import CompileResult
from alphaloom.runtime.context import RunContext, check_stamped
from alphaloom.runtime.events import BarEvent

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

    def run(self, events) -> None:
        for ev in events:
            self.step(ev)

    def step(self, ev: BarEvent) -> None:
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
            outputs = inst.on_bar(self.ctx, raw_inputs) or {}
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
