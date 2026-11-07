# Build the Windows application folder (dist\AircraftFoundry\AircraftFoundry.exe).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& .\.venv\Scripts\python.exe -m pip install --only-binary :all: "pyinstaller==6.16.0"
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
& .\.venv\Scripts\pyinstaller.exe --noconfirm packaging\aircraft_foundry.spec
Write-Host "Built dist\AircraftFoundry\AircraftFoundry.exe"
