@echo off
rem Local read-only web dashboard. Double-click to start; close this window to stop.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m stock_web --host 127.0.0.1 --port 8787
pause
