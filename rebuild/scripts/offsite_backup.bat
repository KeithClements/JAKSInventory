@echo off
REM ============================================================================
REM  offsite_backup.bat - copy the newest JAKS backup + Fernet keyfile offsite.
REM
REM  The ERP already ships an offsite copy after every backup run (Settings ->
REM  System -> "Offsite backup folder"), but that only fires when the app takes
REM  a backup (startup / Back Up Now). This script is the belt-and-braces lane
REM  for a machine that stays up for days: register it once in Task Scheduler
REM  and the newest backup + keyfile land in the cloud-synced folder nightly.
REM
REM      schtasks /Create /SC DAILY /ST 21:30 /TN "JAKS Offsite Backup" ^
REM        /TR "\"C:\Users\keith\JAKSInventory\rebuild\scripts\offsite_backup.bat\""
REM
REM  Usage: offsite_backup.bat [destination-folder]
REM         (no argument -> %%OneDrive%%\JAKS Backups)
REM ============================================================================
setlocal

set "SRC=%~dp0..\backups"

if not "%~1"=="" (
    set "DEST=%~1"
) else if not "%OneDrive%"=="" (
    set "DEST=%OneDrive%\JAKS Backups"
) else (
    echo [ERROR] No destination given and %%OneDrive%% is not set.
    echo         Usage: offsite_backup.bat "D:\SomeCloudFolder\JAKS Backups"
    exit /b 1
)

if not exist "%DEST%" mkdir "%DEST%"

REM Newest ROUTINE dated backup only (jaks-YYYYMMDD_HHMMSS.db). The pattern
REM cannot match jaks-pre* snapshots, and /o-n puts the newest name first.
set "NEWEST="
for /f "delims=" %%F in ('dir /b /a-d /o-n "%SRC%\jaks-????????_??????.db" 2^>nul') do (
    if not defined NEWEST set "NEWEST=%%F"
)

if not defined NEWEST (
    echo [WARN] No dated backups found in "%SRC%" - start JAKS or use Back Up Now first.
) else (
    robocopy "%SRC%" "%DEST%" "%NEWEST%" /NJH /NJS /NDL /NP >nul
    if errorlevel 8 (
        echo [ERROR] robocopy failed copying "%NEWEST%" to "%DEST%".
        exit /b 1
    )
    echo Copied %NEWEST% to "%DEST%".
)

REM The Fernet keyfile: without it a restored DB has undecryptable secrets
REM (QBO tokens, API keys). 44 bytes - always refresh the offsite copy.
if exist "%USERPROFILE%\.jaks_fernet.key" (
    copy /y "%USERPROFILE%\.jaks_fernet.key" "%DEST%\" >nul
    echo Copied .jaks_fernet.key to "%DEST%".
) else (
    echo [WARN] %%USERPROFILE%%\.jaks_fernet.key not found - skipped.
)

exit /b 0
