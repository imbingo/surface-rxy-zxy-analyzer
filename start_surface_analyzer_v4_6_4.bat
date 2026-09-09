@echo off
setlocal
chcp 65001 >nul
rem Keep the existing local-environment bootstrap; it invokes the current package.
call "%~dp0start_surface_analyzer_v4_6_3.bat" %*
exit /b %ERRORLEVEL%
