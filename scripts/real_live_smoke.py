from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from alphaloom.api.app import create_app


REPO = Path(__file__).resolve().parents[1]


def _load_blueprint(name: str) -> dict:
    path = REPO / "blueprints" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _make_client(out_dir: Path) -> TestClient:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "runs").mkdir(exist_ok=True)
    app = create_app(
        db_path=out_dir / "live_market.sqlite",
        runs_db=out_dir / "runs.sqlite",
        record_dir=out_dir / "runs",
        blueprints_dir=REPO / "blueprints",
        user_blueprints_dir=out_dir / "user_blueprints",
        frontend_dist=REPO / "frontend" / "dist",
        llm_db=out_dir / "llm_calls.sqlite",
    )
    return TestClient(app)


def run_smoke(*, inst: str, bar: str, max_bars: int, fetch_limit: int,
              poll_ms: int, blueprint: str, out_dir: Path) -> dict:
    client = _make_client(out_dir)
    status = client.get("/api/status").json()
    if status.get("llm_mode") != "live":
        raise RuntimeError(f"LLM mode is not live: {status}")

    bp = _load_blueprint(blueprint)
    response = client.post("/api/live", json={
        "blueprint": bp,
        "inst": inst,
        "bar": bar,
        "poll_ms": poll_ms,
        "max_bars": max_bars,
        "fetch_limit": fetch_limit,
        "analysis": True,
        "analysis_every": 1,
        "context_bars": 30,
        "ws_wait_ms": 500,
    })
    if response.status_code != 200:
        raise RuntimeError(f"POST /api/live failed: {response.status_code} {response.text}")
    session_id = response.json()["session_id"]

    events: list[dict] = []
    with client.websocket_connect(f"/ws/live/{session_id}") as ws:
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    error = next((event for event in events if event["type"] == "error"), None)
    if error is not None:
        raise RuntimeError(f"live session error: {error}")

    run = client.get(f"/api/runs/{session_id}").json()
    analyses = client.get(f"/api/live/{session_id}/analysis").json()
    candles = client.get("/api/market/candles", params={
        "inst": inst,
        "bar": bar,
        "fetch_limit": fetch_limit,
        "poll_ms": poll_ms,
        "limit": max_bars + 2,
    }).json()
    bar_events = [event for event in events if event["type"] == "bar"]
    analysis_events = [event for event in events if event["type"] == "analysis"]
    first_analysis = analyses[0] if analyses else {}
    output = first_analysis.get("output") or {}
    recording = out_dir / "runs" / f"live_{session_id}.sqlite"

    return {
        "session_id": session_id,
        "llm_status": status,
        "blueprint": bp.get("id"),
        "inst": inst,
        "bar": bar,
        "events": {
            "total": len(events),
            "bars": len(bar_events),
            "analysis": len(analysis_events),
            "types": [event["type"] for event in events],
        },
        "run_status": run.get("status"),
        "report": {
            "mode": (run.get("report") or {}).get("mode"),
            "bars": (run.get("report") or {}).get("bars"),
            "fills": len((run.get("report") or {}).get("fills") or []),
        },
        "analysis": {
            "rows": len(analyses),
            "model": first_analysis.get("model"),
            "prompt_hash": first_analysis.get("prompt_hash"),
            "output_keys": sorted(output.keys()),
            "market_state": output.get("market_state"),
            "current_gate": output.get("current_gate"),
            "confidence": output.get("confidence"),
        },
        "stored_candles": len(candles),
        "recording": str(recording) if recording.is_file() else run.get("recording_path"),
        "artifact_dir": str(out_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inst", default="SOL-USDT-SWAP")
    parser.add_argument("--bar", default="1m")
    parser.add_argument("--max-bars", type=int, default=2)
    parser.add_argument("--fetch-limit", type=int)
    parser.add_argument("--poll-ms", type=int, default=1000)
    parser.add_argument("--blueprint", default="real_sol_breakout_demo.loom")
    parser.add_argument("--out-dir", type=Path, default=REPO / "output" / "real_live_smoke")
    args = parser.parse_args()

    try:
        summary = run_smoke(
            inst=args.inst,
            bar=args.bar,
            max_bars=args.max_bars,
            fetch_limit=args.fetch_limit or args.max_bars,
            poll_ms=args.poll_ms,
            blueprint=args.blueprint,
            out_dir=args.out_dir,
        )
    except Exception as exc:  # noqa: BLE001 - CLI smoke should report one clean failure line
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
