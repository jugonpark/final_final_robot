@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m python_controller --ui dashboard %*
) else (
  python -m python_controller --ui dashboard %*
)
pause
