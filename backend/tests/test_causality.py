import pytest
from alphaloom.graph.types import Stamped
from alphaloom.runtime.events import BarEvent
from alphaloom.runtime.context import SimClock, RunContext, CausalityError, check_stamped

CANDLE = {"ts": 60_000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}

def test_bar_event_close_ts():
    ev = BarEvent(candle=CANDLE, bar_ms=60_000)
    assert ev.ts_open == 60_000 and ev.ts_close == 120_000

def test_clock_monotonic():
    clk = SimClock()
    clk.advance(120_000)
    assert clk.now == 120_000
    with pytest.raises(ValueError):
        clk.advance(60_000)

def test_check_stamped_passes_and_blocks():
    check_stamped("n1", Stamped(1.0, as_of=120_000), now=120_000)
    check_stamped("n1", {"x": Stamped(1.0, 60_000)}, now=120_000)
    with pytest.raises(CausalityError, match="n1"):
        check_stamped("n1", Stamped(1.0, as_of=180_000), now=120_000)

def test_run_context_defaults():
    ctx = RunContext(clock=SimClock(), run_id="r1")
    assert ctx.halted is False and ctx.broker is None and ctx.current_event is None
