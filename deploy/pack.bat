@echo off
rem Pack ELOMER for the server: code + data, without local libraries. Result: elomer_server.tar.gz next to run.bat
cd /d "%~dp0\.."
tar -czf elomer_server.tar.gz --exclude=backend/.venv --exclude=frontend/node_modules --exclude=frontend/dist --exclude=__pycache__ --exclude=deploy/Caddyfile --exclude=deploy/users.caddy backend frontend deploy README.md
if errorlevel 1 (
  echo Packing failed.
) else (
  echo Done: %cd%\elomer_server.tar.gz
)
pause
