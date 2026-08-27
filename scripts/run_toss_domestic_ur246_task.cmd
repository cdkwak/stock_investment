@echo off
setlocal
set "UR246_ROOT=%~dp0.."
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0manual\collect\run_toss_domestic_ur246_task.ps1" -ProjectRoot "%UR246_ROOT%"
exit /b %ERRORLEVEL%
