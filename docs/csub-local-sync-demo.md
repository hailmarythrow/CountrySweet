# CSUB local OneDrive-sync demonstration

This development-only path reads a workbook from a Windows folder that is
already synchronized by the OneDrive desktop client. It does not use
Microsoft Graph, Firebase shared-workbook metadata, or Country Sweet employee
authentication.

## Confirm OneDrive synchronization

1. Open the OneDrive folder in File Explorer.
2. Confirm the workbook shows a green checkmark (`Available on this device`),
   not a cloud-only icon or a red error badge.
3. Open the workbook in Excel, make a harmless change to the synthetic/demo
   workbook, save it, and wait until OneDrive reports `Up to date`.
4. Close Excel before pressing Refresh so the file is not in the middle of a
   write. The dashboard also checks the file size, modification time, and XLSX
   archive integrity before parsing.

## Enable the local demo

Set this process-local flag before starting Streamlit:

```powershell
$env:BAKO_LOCAL_WORKBOOK_DEMO = "true"
streamlit run app.py
```

The first `Select synced workbook` click opens the native Windows file picker.
The selected path is stored only in `%LOCALAPPDATA%\BakoSweet\csub_demo_workbook.json`.
The file contains no workbook bytes, tokens, or Firebase metadata. The
selection persists for later local sessions on the same computer.

Use `Refresh synced workbook` after OneDrive has finished synchronizing. The
dashboard validates the new bytes before activation; if the file is missing,
locked, incomplete, or invalid, the last validated dataset remains visible.

## Scope

This is a local single-computer demonstration. The selected path is not
available to other users or cloud hosting. Microsoft Graph remains prepared
but disabled, and no workbook is edited or written by the dashboard.
