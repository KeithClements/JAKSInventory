# JAKS Inventory Launcher
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = $root
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m jaks_inventory @args
