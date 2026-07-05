# backend/alphaloom/cli.py
from __future__ import annotations
import argparse
import json
import sys

def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # cp1252 陷阱
    p = argparse.ArgumentParser(prog="alphaloom")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("compile", help="compile a .loom blueprint")
    pc.add_argument("blueprint")
    pc.add_argument("--bar", default="1m",
                    choices=["1m", "5m", "15m", "1H", "4H", "1D"])
    pr = sub.add_parser("run", help="backtest a .loom blueprint")
    pr.add_argument("blueprint")
    pr.add_argument("--db", required=True)
    pr.add_argument("--inst", required=True)
    pr.add_argument("--bar", default="1m",
                    choices=["1m", "5m", "15m", "1H", "4H", "1D"])
    pr.add_argument("--start", type=int, default=None)
    pr.add_argument("--end", type=int, default=None)
    pr.add_argument("--cash", type=float, default=10_000.0)
    args = p.parse_args(argv)

    import alphaloom.nodes  # noqa: F401
    from alphaloom.api.serialize import sanitize
    from alphaloom.data.source import bar_to_ms
    from alphaloom.graph.compiler import compile_blueprint
    from alphaloom.graph.model import load_loom_file

    bp = load_loom_file(args.blueprint)
    if args.cmd == "compile":
        r = compile_blueprint(bp, bars_per_day=86_400_000 // bar_to_ms(args.bar))
        print(json.dumps({
            "ok": r.ok,
            "errors": [e.to_dict() for e in r.errors],
            "certificate": sanitize(r.certificate.to_dict()) if r.certificate else None,
            "order": r.order,
        }, ensure_ascii=False, indent=2))
        return 0 if r.ok else 1

    from alphaloom.backtest.runner import run_backtest, CompileFailed
    from alphaloom.data.sqlite_source import SQLiteMarketData
    try:
        report = run_backtest(bp, SQLiteMarketData(args.db), inst=args.inst,
                              bar=args.bar, start_ms=args.start, end_ms=args.end,
                              initial_cash=args.cash)
    except CompileFailed as cf:
        print(json.dumps({"ok": False,
                          "errors": [e.to_dict() for e in cf.errors]},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "run_id": report.run_id, "bars": report.bars,
                      "certificate": report.certificate,
                      "summary": sanitize(report.summary)}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
