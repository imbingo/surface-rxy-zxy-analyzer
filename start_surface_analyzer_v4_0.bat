@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PY=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%requirements.txt"

pushd "%ROOT%" >nul

if not exist "%PY%" (
    echo [setup] Creating local Python environment: .venv
    call :find_python
    if errorlevel 1 goto :fail
    "!BASE_PY!" -m venv "%VENV%"
    if errorlevel 1 goto :fail
)

"%PY%" -c "import PyQt6, numpy, pandas, matplotlib, scipy, openpyxl, xlrd" >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing dependencies into .venv
    "%PY%" -m pip install --disable-pip-version-check --upgrade pip
    if errorlevel 1 goto :fail
    "%PY%" -m pip install --disable-pip-version-check -r "%REQ%"
    if errorlevel 1 goto :fail
) else (
    echo [ok] Dependencies already available in .venv
)

echo [ok] Python: %PY%
echo [run] Starting Surface Rxy/Zxy Analyzer V4.0
"%PY%" -m surface_analyzer %*
set "EXITCODE=%ERRORLEVEL%"
popd >nul
exit /b %EXITCODE%
:find_python
set "BASE_PY="

if exist "D:\python\python.exe" (
    set "BASE_PY=D:\python\python.exe"
    exit /b 0
)

py -3 -c "import sys; print(sys.executable)" > "%TEMP%\surface_analyzer_python_path.txt" 2>nul
if not errorlevel 1 (
    set /p BASE_PY=<"%TEMP%\surface_analyzer_python_path.txt"
    if defined BASE_PY exit /b 0
)

python -c "import sys; print(sys.executable)" > "%TEMP%\surface_analyzer_python_path.txt" 2>nul
if not errorlevel 1 (
    set /p BASE_PY=<"%TEMP%\surface_analyzer_python_path.txt"
    if defined BASE_PY exit /b 0
)

echo [error] Python 3 was not found. Install Python 3.10+ or update BASE_PY in this file.
exit /b 1

:fail
set "EXITCODE=%ERRORLEVEL%"
if "%EXITCODE%"=="0" set "EXITCODE=1"
echo.
echo [failed] Startup failed. Press any key to close.
pause >nul
popd >nul
exit /b %EXITCODE%
