# backend/tests/test_backtest_e2e.py
import json
from pathlib import Path
import alphaloom.nodes  # noqa: F401
from alphaloom.data.sqlite_source import SQLiteMarketData
from alphaloom.graph.model import load_loom_file
from alphaloom.graph.compiler import compile_blueprint
from alphaloom.backtest.runner import run_backtest
from tests.fixtures.synth import gen_candles

REPO = Path(__file__).resolve().parents[2]   # alphaloom 仓库根

def _db(tmp_path, n=600):
    db = SQLiteMarketData(tmp_path / "m.sqlite")
    up = gen_candles(n // 2, seed=11, trend=0.002)
    down = gen_candles(n // 2, seed=12, trend=-0.002,
                       start_ts=up[-1]["ts"] + 60_000,
                       start_price=up[-1]["close"])
    db.insert_candles("BTC-USDT-SWAP", "1m", up + down)
    return db

def test_preset_blueprints_compile():
    for name in ("ema_cross.loom", "breakout_scenario.loom"):
        bp = load_loom_file(REPO / "blueprints" / name)
        r = compile_blueprint(bp)
        assert r.ok, (name, [e.to_dict() for e in r.errors])
        assert r.certificate.deterministic_ratio == 1.0

def test_ema_cross_end_to_end(tmp_path):
    db = _db(tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "ema_cross.loom")
    report = run_backtest(bp, db, inst="BTC-USDT-SWAP", bar="1m",
                          record_dir=tmp_path)
    assert report.bars == 600
    assert report.summary["num_trades"] >= 1
    assert report.certificate["deterministic_ratio"] == 1.0
    assert Path(report.recording_path).exists()
    assert len(report.equity_curve) == 600

def test_breakout_end_to_end(tmp_path):
    db = _db(tmp_path)
    bp = load_loom_file(REPO / "blueprints" / "breakout_scenario.loom")
    report = run_backtest(bp, db, inst="BTC-USDT-SWAP", bar="1m")
    assert report.bars == 600 and "net_pnl" in report.summary

def test_cli_run_and_compile(tmp_path, capsys):
    from alphaloom.cli import main
    db = _db(tmp_path)  # noqa: F841  路径在 tmp_path/m.sqlite
    rc = main(["compile", str(REPO / "blueprints" / "ema_cross.loom")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "certificate" in out
    rc2 = main(["run", str(REPO / "blueprints" / "ema_cross.loom"),
                "--db", str(tmp_path / "m.sqlite"),
                "--inst", "BTC-USDT-SWAP", "--bar", "1m"])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and "summary" in out2 and out2["summary"]["num_trades"] >= 1
