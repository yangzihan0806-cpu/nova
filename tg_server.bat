@echo off
rem TartanGrid: start the server on port 8000. Keep this window open.
cd /d "%~dp0backend"
echo Starting TartanGrid server... open http://localhost:8000 in a browser on THIS laptop to check.
echo Keep this window open. Press Ctrl+C to stop.
..\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
