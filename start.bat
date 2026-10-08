@echo off
cd /d "%~dp0"
echo GX Simulator2 / Factory I/O OPC UA bridge  -  Ctrl+C to stop (do not just close the window)
".venv\Scripts\python.exe" bridge.py tags.yaml
pause
