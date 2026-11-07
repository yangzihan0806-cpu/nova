@echo off
setlocal DisableDelayedExpansion
rem Optional public owner label for contribution records; leave blank to omit.
set "OWNER="
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
rem Read values from the environment so owner quotes and cmd metacharacters stay literal.
rem runpy keeps one Python process, including the worker's normal Ctrl+C handling.
.venv\Scripts\python.exe -c "import os, runpy, sys; owner = os.environ.get('OWNER', '').strip(); sys.argv = ['workers/native_worker.py', '--server', os.environ['SERVER'], '--name', 'Native-' + os.environ.get('COMPUTERNAME', 'Windows'), '--slots', os.environ['SLOTS']] + (['--owner', owner] if owner else []); runpy.run_path(sys.argv[0], run_name='__main__')"
pause
endlocal
