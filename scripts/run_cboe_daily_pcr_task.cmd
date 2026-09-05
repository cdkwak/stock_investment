@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
"%REPO_ROOT%\.venv\Scripts\pythonw.exe" -m stock_data.orchestration.cboe_daily_pcr --project-root "%REPO_ROOT%" --confirm-live --personal-mode
exit /b %ERRORLEVEL%
