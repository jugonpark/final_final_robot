@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m python_controller --ui web --web-control --web-port 8766 --agent-enabled --open-web %*
) else (
  python -m python_controller --ui web --web-control --web-port 8766 --agent-enabled --open-web %*
)
pause
