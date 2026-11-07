# Create a Python 3.11 environment and install Aircraft Foundry from source (64-bit Windows 10/11).
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = "py"
& $py -3.11 -c "import sys, platform; assert sys.version_info[:2] == (3, 11); assert platform.machine() in ('AMD64', 'x86_64'), platform.machine()"
if ($LASTEXITCODE -ne 0) { throw "Install 64-bit Python 3.11 from https://www.python.org/downloads/windows/" }
& $py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --only-binary :all: -r requirements\lock-windows-x64-py311.txt
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
& .\.venv\Scripts\python.exe -m pip install "pytest>=8"
Write-Host "Installed. Run:  .\.venv\Scripts\python.exe -m aircraft_foundry"
