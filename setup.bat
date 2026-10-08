@echo off
setlocal
cd /d "%~dp0"
echo === GX Simulator2 / Factory I/O OPC UA bridge: setup ===

where uv >nul 2>nul
if errorlevel 1 (
  echo [X] uv not found. Install it first, e.g.  winget install --id=astral-sh.uv -e
  echo     or see https://docs.astral.sh/uv/getting-started/installation/
  goto :fail
)

echo [1/3] 32-bit Python 3.12
uv python install cpython-3.12-windows-x86 || goto :fail

REM a copied .venv points at the old PC's Python; recreate it unless it runs and is 32-bit
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import struct,sys;sys.exit(0 if struct.calcsize('P')==4 else 1)" >nul 2>nul
  if errorlevel 1 (
    echo     existing .venv is broken or not 32-bit, recreating it
    rmdir /s /q .venv
  )
)
if exist ".venv\Scripts\python.exe" (
  echo [2/3] .venv already OK
) else (
  echo [2/3] creating .venv
  uv venv .venv --python cpython-3.12-windows-x86 || goto :fail
)

echo [3/3] installing packages
uv pip install --python ".venv\Scripts\python.exe" --only-binary cryptography -r requirements.txt || goto :fail
".venv\Scripts\python.exe" -c "import asyncua,comtypes,yaml,cryptography" || goto :fail

reg query "HKCR\ActUtlType.ActUtlType" >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] MX Component is not registered on this PC. Install it before running the bridge with driver mx.
) else (
  echo [ok] MX Component found
)

echo.
echo Done. Remaining manual steps, not done by this script:
echo   1. MX Component: Communication Setup Utility, create logical station 1 for GX Simulator2, Connection Test must pass
echo   2. GX Works2: write your ladder and start the simulation  Debug - Start/Stop Simulation
echo   3. Double-click start.bat, then connect Factory I/O  see README.md
if not defined NOPAUSE pause
exit /b 0

:fail
echo.
echo Setup failed, see the messages above.
if not defined NOPAUSE pause
exit /b 1
