@echo off
REM ============================================================================
REM  Axle ERP — supervised headless runner (C5, FULL_ERP_REVIEW_2026-07-04)
REM ----------------------------------------------------------------------------
REM  This is what the "Axle ERP" scheduled task launches (see service_install.py).
REM  It is HEADLESS (no browser window, runs unattended) and SUPERVISED: if uvicorn
REM  exits for any reason it is restarted after a short delay, so a mid-day crash or
REM  a transient error self-heals instead of stopping the shop. To stop it, end the
REM  scheduled task:  schtasks /End /TN "Axle ERP"
REM ============================================================================
title Axle ERP (service)

REM Run from the rebuild folder (this script lives in rebuild\scripts).
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found next to this folder. >&2
    exit /b 1
)

REM ── Encryption key for secrets at rest (QBO/Shopify/AI). Same keyfile the
REM interactive launcher uses; generated once, OUTSIDE the repo/DB. ──────────────
set "KEYFILE=%USERPROFILE%\.jaks_fernet.key"
if not exist "%KEYFILE%" (
    ".venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet;open(r'%KEYFILE%','wb').write(Fernet.generate_key())"
)
for /f "usebackq delims=" %%K in ("%KEYFILE%") do set "JAKS_FERNET_KEY=%%K"

REM ── Production posture: auth + default-password gate enforced, demo-wipe off. ──
set "JAKS_ENV=production"

if not exist "logs" mkdir "logs"

:loop
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
echo [%date% %time%] uvicorn exited (code %errorlevel%) — restarting in 5s>> "logs\service_supervisor.log"
timeout /t 5 /nobreak >nul
goto loop
