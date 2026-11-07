@echo off
rem TartanGrid: run this laptop as ONE native worker. Press Ctrl+C to leave cleanly.
rem Run only one copy per computer. On another laptop, pass the tunnel address:
rem   tg_native.bat https://xxxx.trycloudflare.com
cd /d "%~dp0"
rem 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and adds ~2 s per request.
set "SERVER=http://127.0.0.1:8000"
if not "%~1"=="" set "SERVER=%~1"
set SLOTS=2
if %NUMBER_OF_PROCESSORS% GEQ 8 set SLOTS=4
echo Native worker connecting to %SERVER% with %SLOTS% slots
echo Press Ctrl+C to leave cleanly (its tiles are handed back right away).
.venv\Scripts\python.exe workers\native_worker.py --server %SERVER% --name Native-%COMPUTERNAME% --slots %SLOTS%
pause
