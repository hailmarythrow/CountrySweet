@echo off
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
    echo The dashboard Python environment is missing. Ask Codex to set it up again.
    pause
    exit /b 1
)
echo Opening Country Sweet Produce at http://127.0.0.1:8501
echo Keep this window open while using the dashboard. Press Ctrl+C to stop it.

rem Optional local-only Firebase authentication settings. This file lives under
rem .csub-local\ (ignored by Git) so local login survives launcher restarts
rem without placing Web App settings or feature flags in the repository.
if exist ".csub-local\firebase-auth.env.cmd" call ".csub-local\firebase-auth.env.cmd"

rem Development-only Firebase snapshot persistence. This uses the separate
rem bako_sweet_dev namespace and does not store workbook binaries.
if not defined BAKO_DEV_FIRESTORE_SNAPSHOTS set "BAKO_DEV_FIRESTORE_SNAPSHOTS=true"
if not defined BAKO_DEV_FIRESTORE_SNAPSHOT_NAMESPACE set "BAKO_DEV_FIRESTORE_SNAPSHOT_NAMESPACE=bako_sweet_dev"

rem The Codex/desktop environment may provide a local HTTP proxy that cannot
rem carry Google's Firestore gRPC traffic. Keep the proxy for other services,
rem but route Firebase/Google APIs directly so persistence cannot stall the UI.
if defined NO_PROXY (
    set "NO_PROXY=%NO_PROXY%,googleapis.com,.googleapis.com,firestore.googleapis.com,oauth2.googleapis.com"
) else (
    set "NO_PROXY=localhost,127.0.0.1,::1,googleapis.com,.googleapis.com,firestore.googleapis.com,oauth2.googleapis.com"
)
".venv312\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
