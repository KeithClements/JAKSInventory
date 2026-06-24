@echo off
title Backfill SEO (generate all)
cd /d "%~dp0"

REM ============================================================================
REM  Generate AI SEO descriptions for EVERY product that lacks one, and save
REM  them. Uses your Anthropic API key and COSTS tokens. Resumable (commits per
REM  product) - close and re-run anytime; already-done products are skipped.
REM  The next Shopify sync pushes the new SEO to the storefront automatically.
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
echo This GENERATES AI SEO descriptions for every product that lacks one.
echo It uses your Anthropic API key and COSTS tokens. It is resumable.
echo (Tip: run 'Backfill SEO (preview)' first to see how many that is.)
echo.
set /p OK="Type Y then Enter to continue (anything else cancels): "
if /i not "%OK%"=="Y" ( echo Cancelled. & pause & exit /b 0 )
echo.
".venv\Scripts\python.exe" -m scripts.backfill_seo --apply
echo.
pause
