@echo off
title Backfill SEO (preview)
cd /d "%~dp0"

REM ============================================================================
REM  Count how many products still need an AI SEO description. PREVIEW ONLY -
REM  changes nothing and costs nothing. Run 'Backfill SEO (generate all)' to
REM  actually create them.
REM ============================================================================

set "KEYFILE=%USERPROFILE%\.jaks_fernet.key"
if not exist "%KEYFILE%" (
    echo [ERROR] Encryption key not found at "%KEYFILE%". Start the ERP once first.
    pause & exit /b 1
)
for /f "usebackq delims=" %%K in ("%KEYFILE%") do set "JAKS_FERNET_KEY=%%K"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Could not find .venv\Scripts\python.exe in "%~dp0".
    pause & exit /b 1
)
echo Counting products that need an SEO description (no changes, no cost)...
echo.
".venv\Scripts\python.exe" -m scripts.backfill_seo
echo.
pause
