# backend/alphaloom/backtest/runner.py
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import alphaloom.nodes  # noqa: F401  触发注册
from alphaloom.brokers.base import Order
from alphaloom.brokers.paper import PaperBroker
from alphaloom.data.source import DataSource, bar_to_ms
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.graph.model import BlueprintSpec
from alphaloom.nodes.registry import create_instance
from alphaloom.runtime.context import RunContext, SimClock
from alphaloom.runtime.engine import Engine
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.recorder import Recorder

class CompileFailed(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} compile error(s)")

@dataclass
class BacktestReport:
    run_id: str
    blueprint_id: str
    bars: int
    summary: dict
    certificate: dict
    equity_curve: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    recording_path: str | None = None

def run_backtest(bp: BlueprintSpec, source: DataSource, *, inst: str, bar: str,
                 start_ms: int | None = None, end_ms: int | None = None,
                 initial_cash: float = 10_000.0, fee_rate: float = 0.0005,
                 record_dir=None) -> BacktestReport:
    """时序契约：每根 bar 先 broker.on_bar（撮合上一根挂单/止损）再 engine.step（本根决策）
    —— 次 bar 开盘成交语义的另一半（见 PaperBroker.on_bar）。"""
    bar_ms = bar_to_ms(bar)
    compiled = compile_blueprint(bp, bars_per_day=86_400_000 // bar_ms)
    if not compiled.ok:
        raise CompileFailed(compiled.errors)
    run_id = uuid.uuid4().hex[:12]
    broker = PaperBroker(initial_cash=initial_cash, fee_rate=fee_rate)
    recorder = None
    rec_path = None
    if record_dir is not None:
        rec_path = str(Path(record_dir) / f"run_{run_id}.sqlite")
        recorder = Recorder(rec_path)
    ctx = RunContext(clock=SimClock(), run_id=run_id, broker=broker, recorder=recorder)
    instances = {nid: create_instance(spec) for nid, spec in compiled.nodes.items()}
    engine = Engine(compiled, instances, ctx)
    bars = 0
    last_candle = None
    try:
        for candle in source.iter_candles(inst, bar, start_ms, end_ms):
            broker.on_bar(candle)              # 先撮合上一根的挂单/止损并 mark
            engine.step(BarEvent(candle, bar_ms))
            last_candle = candle
            bars += 1
        # 收盘强平（回测惯例）：数据耗尽后残仓以最后收盘价结算成一笔完整回合，
        # 否则持仓到期末的策略 num_trades/win_rate 全部失真（Task 12 实测发现，sanctioned）
        if last_candle is not None and abs(broker.position().qty) > 1e-12 and not broker.halted:
            px = float(last_candle["close"])
            qty = broker.position().qty
            broker.submit(Order(side="sell" if qty > 0 else "buy", qty=abs(qty), tag="eod_close"))
            broker.on_bar({"ts": int(last_candle["ts"]) + bar_ms, "open": px, "high": px,
                           "low": px, "close": px, "volume": 0.0})
            del broker.equity_curve[bars:]     # 结算 bar 不入权益曲线（长度=数据根数）
    finally:
        if recorder:
            recorder.close()
    return BacktestReport(
        run_id=run_id, blueprint_id=bp.id, bars=bars,
        summary=broker.summary(), certificate=compiled.certificate.to_dict(),
        equity_curve=broker.equity_curve,
        fills=[f.__dict__ for f in broker.fills],
        recording_path=rec_path)
