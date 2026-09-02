@echo off
rem Restart the always-on dashboard task (registered at logon as STOCK_WEB_DASHBOARD).
schtasks /End /TN STOCK_WEB_DASHBOARD >nul 2>&1
timeout /t 2 >nul
schtasks /Run /TN STOCK_WEB_DASHBOARD
echo started; open http://127.0.0.1:8787
pause
