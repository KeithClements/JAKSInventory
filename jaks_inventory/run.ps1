# JAKS Inventory Launcher
# Sets up Python path to include parent directory for db package access

$root = "C:\Users\keith\Inventory Program"
$env:PYTHONPATH = "$root;$root\jaks_inventory"
Set-Location $root
& "$root\.venv\Scripts\python" -m jaks_inventory @args
