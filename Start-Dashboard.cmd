@echo off
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
    echo The dashboard Python environment is missing. Ask Codex to set it up again.
    pause
    exit /b 1
)
echo Opening Country Sweet Produce at http://127.0.0.1:8501
echo Keep this window open while using the dashboard. Press Ctrl+C to stop it.
".venv312\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
