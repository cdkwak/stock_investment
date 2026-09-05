@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
"%REPO_ROOT%\.venv\Scripts\pythonw.exe" -m stock_data.orchestration.kbsec_transactions_daily --project-root "%REPO_ROOT%" --confirm-live
exit /b %ERRORLEVEL%
