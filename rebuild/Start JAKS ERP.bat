@echo off
title Axle ERP
cd /d "%~dp0"
echo ============================================
echo    Axle ERP
echo ============================================
echo.
echo    On this PC:      http://localhost:8000
echo    Phone / laptop:  http://THIS-PC's-IP:8000  (IPv4 addresses below)
ipconfig | findstr /c:"IPv4"
echo.
echo    Login:   admin / admin
echo.
echo    Keep this window open while you work.
echo    Close it (or press Ctrl+C) to stop the server.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not find .venv\Scripts\python.exe
    echo Make sure this file sits in the "rebuild" folder next to the .venv folder.
    pause
    exit /b 1
)

REM ── Encryption key for secrets at rest (QBO client secret + OAuth tokens, AI ─
REM key). Generated ONCE into a file OUTSIDE the repo/DB so a code or jaks.db
REM copy alone can never decrypt them. Loaded into JAKS_FERNET_KEY each launch.
set "KEYFILE=%USERPROFILE%\.jaks_fernet.key"
if not exist "%KEYFILE%" (
    ".venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet;open(r'%KEYFILE%','wb').write(Fernet.generate_key())"
    echo Generated a new encryption key at "%KEYFILE%" ^(back this up safely^).
)
for /f "usebackq delims=" %%K in ("%KEYFILE%") do set "JAKS_FERNET_KEY=%%K"

REM Open the browser a few seconds after the server starts booting.
start "" /b cmd /c "timeout /t 4 >nul && start http://localhost:8000/"

REM Run the server (no --reload: stable for daily use). Closing this window stops it.
REM 0.0.0.0 = reachable from other devices on your network (login still required).
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo Server stopped.
pause >nul
