@echo off
rem TartanGrid: install server + worker packages into .venv (run once)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment .venv ...
  python -m venv .venv
)
echo Installing TartanGrid packages, this may take a few minutes...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt requests
if errorlevel 1 (
  echo Retrying without optional uvicorn extras...
  .venv\Scripts\python.exe -m pip install fastapi uvicorn "pydantic>=2" numpy pytest httpx anthropic openai python-dotenv requests
  if errorlevel 1 (
    echo [X] Package install failed. Check the internet connection and run tg_setup.bat again.
    pause
    exit /b 1
  )
)
echo Installing numba (optional, makes the native worker 2-3x faster)...
.venv\Scripts\python.exe -m pip install numba
if errorlevel 1 echo [!] numba is not available for this Python version. The native worker will use numpy instead. That is OK.
.venv\Scripts\python.exe -c "import fastapi, uvicorn, numpy, requests; print('[OK] TartanGrid setup done. Next: double-click tg_server.bat')"
pause
