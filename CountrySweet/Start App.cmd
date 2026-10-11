@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The Python environment is missing. See APP_GUIDE.md for setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo Installing the app packages. This is needed only once.
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installation failed. Please send the error message for help.
    pause
    exit /b 1
  )
)
echo Opening Bako Sweet Farm Planning. Keep this window open while using the app.
".venv\Scripts\python.exe" -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=1).read().strip() == b'ok'" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8501"
  exit /b 0
)
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
