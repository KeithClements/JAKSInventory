@echo off
title JAKS Inventory ERP (LAN)
cd /d "%~dp0"

REM ── One-time firewall rule (needs Administrator) ───────────────────────────
REM Lets phones/laptops on YOUR Wi-Fi reach the server. If the rule is missing
REM and this window isn't elevated, you'll see instructions instead of errors.
netsh advfirewall firewall show rule name="JAKS ERP 8000" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="JAKS ERP 8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>&1
    if errorlevel 1 (
        echo [SETUP NEEDED] Run this file ONCE as Administrator ^(right-click^>
        echo Run as administrator^) to open port 8000 for your shop Wi-Fi.
        echo Until then only this PC can connect.
        echo.
    ) else (
        echo Firewall rule added: phones/laptops on this network can now connect.
        echo.
    )
)

REM ── Find this PC's LAN address to show the URL for other devices ───────────
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))' } | Select-Object -First 1).IPAddress"') do set LANIP=%%i

echo ============================================
echo    JAKS Inventory ERP  --  shop network mode
echo ============================================
echo.
echo    This PC:        http://localhost:8000
if defined LANIP echo    Phone/laptop:   http://%LANIP%:8000   (same Wi-Fi only)
echo.
echo    Tip: open that address on your phone, then
echo    "Add to Home Screen" for an app-style icon.
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

start "" /b cmd /c "timeout /t 4 >nul && start http://localhost:8000/"

REM 0.0.0.0 = listen for the whole local network (firewall still gatekeeps).
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo Server stopped.
pause >nul
