@echo off
setlocal
chcp 65001 >nul
call "%~dp0start_surface_analyzer_v4_6_3.bat" %*
exit /b %ERRORLEVEL%
