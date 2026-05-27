@echo off
REM JAKS Inventory Launcher
REM Sets up Python path to include parent directory for db package access

set PYTHONPATH=%PYTHONPATH%;c:\Users\keith\Website\jaks_scraper
cd /d "%~dp0"
.\.venv\Scripts\python -m jaks_inventory %*
