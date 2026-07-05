# backend/tests/test_final_seams.py
"""终审缝隙测试：bars_per_day 接线、子图全链路回测、CLI 时间窗口、CLI 失败路径。"""
import json
from pathlib import Path
import alphaloom.nodes  # noqa: F401  触发内置节点注册
from alphaloom.backtest.runner import run_backtest
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import loads_loom
from alphaloom.graph.types import PinType, CostAnnotation
from alphaloom.nodes.registry import node
from alphaloom.runtime.recorder import Recorder
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]   # alphaloom 仓库根

@node(type="tb_llm", category="test",
      inputs={}, outputs={"signal": PinType.SIGNAL},
      cost=CostAnnotation(llm_calls_per_bar=1, max_tokens_per_call=1000,
                          latency_class="llm", deterministic=False))
class TbLlm:
    def setup(self, params):
        pass
    def on_bar(self, ctx, inputs):
        return {"signal": {"side": "hold", "qty": 0.0, "stop": None, "reason": "llm idle"}}

TB_LLM_BP = {"id": "llm_solo", "name": "llm_solo",
             "nodes": [{"id": "brain", "type": "tb_llm"}], "edges": []}

# ---- 1. bars_per_day 必须从 runner 的 bar 参数接进成本证书 ----
def test_bars_per_day_wired_through_runner(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    db.insert_candles("X", "1H", gen_candles(10, bar_ms=3_600_000, seed=7))
    report = run_backtest(loads_loom(json.dumps(TB_LLM_BP)), db, inst="X", bar="1H")
    assert report.bars == 10
    # 1H → 24 bars/day：ceiling = 1 call * 1000 tokens * 24，而非默认 1440 的 1_440_000
    assert report.certificate["daily_token_ceiling"] == 1 * 1000 * 24

# ---- 2. 子图包裹的蓝图全链路可回测且录制含展开节点 ----
INNER = {
    "id": "trend_core", "name": "trend_core",
    "nodes": [
        {"id": "ema_fast", "type": "ema", "params": {"period": 12}},
        {"id": "ema_slow", "type": "ema", "params": {"period": 26}},
        {"id": "cross", "type": "cross_signal", "params": {"atr_mult": 2.0}},
    ],
    "edges": [
        {"from": "ema_fast.value", "to": "cross.fast"},
        {"from": "ema_slow.value", "to": "cross.slow"},
    ],
}

def _subgraph_bp():
    return loads_loom(json.dumps({
        "id": "sub_chain", "name": "sub_chain",
        "nodes": [
            {"id": "feed", "type": "candle_feed", "params": {"inst": "X", "bar": "1m"}},
            {"id": "atr", "type": "atr", "params": {"period": 14}},
            {"id": "sub", "type": "subgraph", "params": {
                "blueprint": INNER,
                "inputs": {"candle_fast": "ema_fast.candle",
                           "candle_slow": "ema_slow.candle",
                           "candle_x": "cross.candle",
                           "atr_in": "cross.atr"},
                "outputs": {"signal_out": "cross.signal"},
            }},
            {"id": "sizer", "type": "position_sizer", "params": {"risk_pct": 0.02}},
            {"id": "risk", "type": "risk_gate",
             "params": {"max_qty": 100.0, "require_stop": True}},
            {"id": "exec", "type": "execute_order", "params": {}},
        ],
        "edges": [
            {"from": "feed.out", "to": "atr.candle"},
            {"from": "feed.out", "to": "sub.candle_fast"},
            {"from": "feed.out", "to": "sub.candle_slow"},
            {"from": "feed.out", "to": "sub.candle_x"},
            {"from": "atr.value", "to": "sub.atr_in"},
            {"from": "sub.signal_out", "to": "sizer.signal"},
            {"from": "feed.out", "to": "sizer.candle"},
            {"from": "sizer.sized", "to": "risk.signal"},
            {"from": "risk.stamped", "to": "exec.signal"},
        ],
    }))

def test_subgraph_blueprint_full_chain(tmp_path):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    up = gen_candles(200, seed=11, trend=0.002)
    down = gen_candles(200, seed=12, trend=-0.002,
                       start_ts=up[-1]["ts"] + 60_000, start_price=up[-1]["close"])
    db.insert_candles("X", "1m", up + down)
    report = run_backtest(_subgraph_bp(), db, inst="X", bar="1m", record_dir=tmp_path)
    assert report.bars == 400
    rec = Recorder(report.recording_path)
    try:
        rows = rec.fetch(report.run_id, "sub/cross")
    finally:
        rec.close()
    assert len(rows) == 400          # 展开后的 sub/ 前缀节点逐 bar 录制在案

# ---- 3. CLI run 的 --start/--end 时间窗口 ----
def test_cli_run_window(tmp_path, capsys):
    from alphaloom.cli import main
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    db.insert_candles("BTC-USDT-SWAP", "1m", gen_candles(300, seed=5))
    rc = main(["run", str(REPO / "blueprints" / "ema_cross.loom"),
               "--db", str(tmp_path / "m.sqlite"), "--inst", "BTC-USDT-SWAP",
               "--bar", "1m",
               "--start", str(60_000 * 50), "--end", str(60_000 * 149)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["bars"] == 100        # [start, end] 闭区间共 100 根

# ---- 4. CLI compile 失败路径：结构化 JSON 错误 ----
def test_cli_compile_failure_json(tmp_path, capsys):
    from alphaloom.cli import main
    bypass = {
        "id": "bypass", "name": "bypass",
        "nodes": [
            {"id": "cross", "type": "cross_signal", "params": {}},
            {"id": "exec", "type": "execute_order", "params": {}},
        ],
        "edges": [{"from": "cross.signal", "to": "exec.signal"}],   # 绕过风控
    }
    p = tmp_path / "bypass.loom"
    p.write_text(json.dumps(bypass), encoding="utf-8")
    rc = main(["compile", str(p), "--bar", "1H"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False
    assert any(e["code"] == "TYPE_MISMATCH" for e in out["errors"])

# ---- 5. CLI compile 的 --bar 接进 bars_per_day（sanctioned addition，见计划 Task 14）----
def test_cli_compile_bar_wires_bars_per_day(tmp_path, capsys):
    from alphaloom.cli import main
    p = tmp_path / "llm_solo.loom"
    p.write_text(json.dumps(TB_LLM_BP), encoding="utf-8")
    rc = main(["compile", str(p), "--bar", "1H"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["certificate"]["daily_token_ceiling"] == 1 * 1000 * 24
