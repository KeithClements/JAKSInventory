@echo off
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYTHONPATH=%ROOT%"
cd /d "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m jaks_inventory %*
