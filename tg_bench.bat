@echo off
rem TartanGrid: single-device speed test of the native kernel on the demo preset (samples 8 tiles).
cd /d "%~dp0"
echo Measuring native speed on the demo preset (8 sample tiles)... keep this window in front.
.venv\Scripts\python.exe workers\benchmark.py --preset demo --tile-sample 8
pause
