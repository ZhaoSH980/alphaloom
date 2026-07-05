:: dev.bat —— 双窗口热更新
@echo off
cd /d %~dp0
backend\.venv\Scripts\python scripts\ensure_demo_db.py
start "alphaloom-api" cmd /k backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --reload --app-dir backend
start "alphaloom-web" cmd /k "cd frontend && npm run dev"
echo Studio: http://localhost:5173  API: http://localhost:8000
