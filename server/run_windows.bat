@echo off
rem ==== CYD Alert server for Windows ====
rem Run: double-click this file, or in cmd:  server\run_windows.bat
rem Dashboard: http://localhost:8080/   ESP setting "Server": http://<PC-IP>:8080
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%PORT%"=="" set PORT=8080
rem --- optional keys (uncomment and fill in) ---
rem set ALERTS_TOKEN=your_alerts_in_ua_token
rem set UKRAINEALARM_KEY=your_ukrainealarm_key
rem set TG_MODE=web
echo Starting server on port %PORT% ...
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 server.py
) else (
  python server.py
)
echo.
echo Server stopped (exit code %ERRORLEVEL%). If "Python not found" - install Python 3.9+ from python.org with "Add to PATH".
pause
