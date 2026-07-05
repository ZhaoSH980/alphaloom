:: demo.bat —— 离线单进程全站（零配额录制回放：招牌 committee 回放 + Eval Lab demo 预设）
@echo off
cd /d %~dp0
:: 离线回放模式必须开：LLM 节点走 committed 录制、Eval Lab 的"Run offline demo"命中种子（不设则 LLM 节点无客户端、demo 预设 409）。
set ALPHALOOM_OFFLINE=1
backend\.venv\Scripts\python scripts\ensure_demo_db.py
cd frontend && call npm run build && cd ..
backend\.venv\Scripts\python -m uvicorn alphaloom.serve:app --port 8000 --app-dir backend
