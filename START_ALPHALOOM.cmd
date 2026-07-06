@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%/#/studio"
set "PY=backend\.venv\Scripts\python.exe"

echo.
echo ============================================================
echo  AlphaLoom one-click offline demo
echo ============================================================
echo.

if not exist "%PY%" (
  echo [0/4] Backend virtualenv not found. Creating it now...
  pushd backend
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 (
    popd
    goto fail
  )
  .venv\Scripts\python.exe -m pip install -e .[dev]
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm.cmd was not found. Install Node.js, then reopen this window.
  echo.
  pause
  exit /b 1
)

echo [1/4] Ensuring deterministic demo market database...
"%PY%" scripts\ensure_demo_db.py
if errorlevel 1 goto fail

if not exist "frontend\node_modules" (
  echo [2/4] Installing frontend dependencies...
  pushd frontend
  call npm.cmd ci
  if errorlevel 1 (
    popd
    goto fail
  )
  popd
) else (
  echo [2/4] Frontend dependencies found.
)

echo [3/4] Building frontend bundle...
pushd frontend
call npm.cmd run build
if errorlevel 1 (
  popd
  goto fail
)
popd

if /I "%~1"=="--check" (
  echo.
  echo [OK] Startup check passed. Run START_ALPHALOOM.cmd to launch.
  echo.
  exit /b 0
)

set "ALPHALOOM_OFFLINE=1"

echo [4/4] Starting AlphaLoom in offline replay mode...
echo.
echo URL: %URL%
echo Stop: press Ctrl+C in this window.
echo.

powershell -NoProfile -Command "try { $c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo [INFO] Port %PORT% is already in use. Opening the existing service.
  start "" "%URL%"
  echo.
  pause
  exit /b 0
)

powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process '%URL%'" >nul 2>nul
"%PY%" -m uvicorn alphaloom.serve:app --host 127.0.0.1 --port %PORT% --app-dir backend

echo.
echo AlphaLoom server stopped.
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] Startup failed. Check the messages above.
echo.
pause
exit /b 1
