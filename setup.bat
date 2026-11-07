@echo off
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo [X] Python not found. Install it from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" during install. Then run setup.bat again.
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating virtual environment .venv ...
  %PY% -m venv .venv
)
echo Installing packages, this may take a minute...
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [X] Package install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2>nul
  (echo [general]& echo email = "")> "%USERPROFILE%\.streamlit\credentials.toml"
)
if not exist .env copy .env.example .env >nul
echo.
echo [OK] Setup done.
echo Next: paste your OpenRouter key into .env (opening it in Notepad now), save, then double-click test.bat
start notepad .env
pause
