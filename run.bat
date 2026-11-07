@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Starting app... your browser will open http://localhost:8501
echo Close this window to stop the app.
.venv\Scripts\python -m streamlit run app.py
pause
