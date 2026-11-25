@echo off
rem ELOMER planner launcher. First run installs dependencies (Python 3.11+ and Node.js 18+ required).
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Installing server dependencies...
  python -m venv backend\.venv || goto err
  "backend\.venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt || goto err
)
rem new versions may add libraries: install them if missing
"backend\.venv\Scripts\python.exe" -c "import fastapi, openpyxl, numpy, pandas, reportlab" 2>nul || (
  echo Updating server dependencies...
  "backend\.venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt || goto err
)
if not exist "frontend\node_modules\.bin\vite.cmd" (
  echo Building interface...
  pushd frontend
  call npm install --no-audit --no-fund || goto err
  popd
)
pushd frontend
call npm run build
if errorlevel 1 (
  popd
  goto err
)
popd

curl.exe --fail --silent --output NUL --max-time 3 http://127.0.0.1:8000/api/projects
if not errorlevel 1 (
  echo ELOMER is already running: http://127.0.0.1:8000
  start "" http://127.0.0.1:8000
  exit /b 0
)

echo.
echo ELOMER is running: http://127.0.0.1:8000
echo Close this window to stop it.
echo.
start "" http://127.0.0.1:8000
"backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir backend/app
pause
goto :eof

:err
echo.
echo Setup failed. Check that Python and Node.js are installed.
pause
