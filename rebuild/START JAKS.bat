@echo off
title JAKS Inventory
color 0A
echo.
echo  ============================================
echo   JAKS Inventory — Starting...
echo  ============================================
echo.
echo  Opening browser to http://localhost:8000
echo  Keep this window open while you work.
echo  Close this window to stop the server.
echo.
cd /d "%~dp0"
start "" http://localhost:8000
".venv\Scripts\python.exe" run.py
echo.
echo  Server stopped. Press any key to close.
pause > nul
