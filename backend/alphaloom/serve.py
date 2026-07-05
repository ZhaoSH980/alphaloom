"""单进程入口：uvicorn alphaloom.serve:app --port 8000（demo.bat 用）。"""
from __future__ import annotations
import sys
from pathlib import Path
from alphaloom.api.app import create_app

REPO = Path(__file__).resolve().parents[2]

def create_default_app():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = REPO / "data"
    runs_dir = REPO / "runs"
    runs_dir.mkdir(exist_ok=True)
    return create_app(db_path=data / "demo.sqlite", runs_db=data / "runs.sqlite",
                      record_dir=runs_dir, blueprints_dir=REPO / "blueprints",
                      user_blueprints_dir=REPO / "blueprints" / "user",
                      frontend_dist=REPO / "frontend" / "dist",
                      llm_db=data / "llm_calls.sqlite")

app = create_default_app()
