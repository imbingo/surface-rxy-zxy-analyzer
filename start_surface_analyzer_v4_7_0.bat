@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "面型及Rxy分析工具V4.7.0.py" %*
) else (
  py -3 "面型及Rxy分析工具V4.7.0.py" %*
)
endlocal
