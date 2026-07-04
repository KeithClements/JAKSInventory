@echo off
REM ============================================================================
REM  Legacy launcher — kept only as a safe redirect (C5, FULL_ERP_REVIEW).
REM
REM  This file used to launch the old dev entrypoint, which starts uvicorn in
REM  auto-reload mode (documented to STALL on Windows: routes 404, zombie port
REM  8000) WITHOUT loading the Fernet key (QBO/Shopify token decryption breaks) and
REM  WITHOUT JAKS_ENV=production (which left the /admin/demo/reset DB-WIPE route
REM  reachable). One stray double-click was a live-data landmine. It now just hands
REM  off to the real production launcher so an old shortcut can't do the wrong thing.
REM ============================================================================
cd /d "%~dp0"
echo Redirecting to the current launcher: "Start JAKS ERP.bat" ...
call "%~dp0Start JAKS ERP.bat"
