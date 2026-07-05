:: demo.bat —— 离线单进程全站
@echo off
cd /d %~dp0
backend\.venv\Scripts\python scripts\ensure_demo_db.py
cd frontend && call npm run build && cd ..
backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
