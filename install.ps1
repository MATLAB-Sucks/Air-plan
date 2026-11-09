# Aircraft Foundry - one-command installer for Windows (PowerShell 5.1 or 7).
#
#   irm https://raw.githubusercontent.com/MATLAB-Sucks/Air-plan/main/install.ps1 | iex
#
# Downloads the latest release from GitHub, checks its SHA-256 against the release's SHA256SUMS.txt,
# installs to %LOCALAPPDATA%\Programs\AircraftFoundry and adds Start-menu and desktop shortcuts.
# Run it again to update: the old version is kept as "AircraftFoundry.previous" until the new one is
# in place. Saved planes, projects and settings live in your user data folder and are never touched.
#
# Options (environment variables, set before running):
#   $env:AF_VERSION = "v0.9.3"     install a specific release (default: latest)
#   $env:AF_INSTALL_DIR = "D:\Apps\AircraftFoundry"
#   $env:AF_NO_SHORTCUTS = "1"     $env:AF_NO_LAUNCH = "1"     $env:AF_REPO = "owner/repo"

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # PowerShell 5.1's progress bar slows downloads 10x
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say($m) { Write-Host "  $m" }
function Die($m) { Write-Host ""; Write-Host "  Install failed: $m" -ForegroundColor Red; throw $m }

$Repo = if ($env:AF_REPO) { $env:AF_REPO } else { "MATLAB-Sucks/Air-plan" }
$Dir = if ($env:AF_INSTALL_DIR) { $env:AF_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\AircraftFoundry" }
$Version = if ($env:AF_VERSION) { $env:AF_VERSION } else { "latest" }
$Asset = "AircraftFoundry-Windows.zip"

Write-Host ""
Write-Host "  Aircraft Foundry installer" -ForegroundColor Cyan
Say "from github.com/$Repo ($Version) to $Dir"

$api = if ($Version -eq "latest") { "https://api.github.com/repos/$Repo/releases/latest" }
       else { "https://api.github.com/repos/$Repo/releases/tags/$Version" }
$headers = @{ "User-Agent" = "aircraft-foundry-installer" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }   # optional: avoids rate limits
try { $rel = Invoke-RestMethod -Uri $api -Headers $headers }
catch { Die "couldn't read the release from GitHub ($($_.Exception.Message)). Is the repository public and released?" }
$zip = $rel.assets | Where-Object { $_.name -eq $Asset } | Select-Object -First 1
$sums = $rel.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" } | Select-Object -First 1
if (-not $zip -or -not $sums) { Die "release $($rel.tag_name) has no $Asset / SHA256SUMS.txt" }
Say "release $($rel.tag_name): $([math]::Round($zip.size / 1MB)) MB"

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("aircraft-foundry-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
    $zipPath = Join-Path $tmp $Asset
    Say "downloading..."
    Invoke-WebRequest -Uri $zip.browser_download_url -OutFile $zipPath -UseBasicParsing
    $sumText = (Invoke-WebRequest -Uri $sums.browser_download_url -UseBasicParsing).Content
    if ($sumText -is [byte[]]) { $sumText = [Text.Encoding]::UTF8.GetString($sumText) }
    $expected = ($sumText -split "`n" | Where-Object { $_ -match [regex]::Escape($Asset) } | Select-Object -First 1)
    if (-not $expected) { Die "SHA256SUMS.txt has no entry for $Asset" }
    $expected = ($expected.Trim() -split "\s+")[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLower()
    if ($actual -ne $expected) { Die "the download didn't match its published checksum - not installed" }
    Say "checksum verified"

    Say "unpacking..."
    $unpack = Join-Path $tmp "new"
    Expand-Archive -Path $zipPath -DestinationPath $unpack -Force
    $exe = Get-ChildItem -Recurse $unpack -Filter AircraftFoundry.exe | Select-Object -First 1
    if (-not $exe) { Die "the archive doesn't contain AircraftFoundry.exe" }
    $newApp = $exe.DirectoryName
    Get-ChildItem -Recurse $newApp | Unblock-File -ErrorAction SilentlyContinue   # downloaded-file warning

    $running = Get-Process AircraftFoundry -ErrorAction SilentlyContinue |
               Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) { Die "Aircraft Foundry is running from $Dir - close it and run the installer again" }

    $parent = Split-Path $Dir -Parent
    New-Item -ItemType Directory -Force $parent | Out-Null
    $previous = "$Dir.previous"
    if (Test-Path $previous) { Remove-Item -Recurse -Force $previous }
    if (Test-Path $Dir) { Move-Item $Dir $previous }
    try { Move-Item $newApp $Dir }
    catch {
        if ((-not (Test-Path $Dir)) -and (Test-Path $previous)) { Move-Item $previous $Dir }
        Die "couldn't move the new version into place: $($_.Exception.Message)"
    }
    Say "installed $($rel.tag_name) to $Dir"

    if (-not $env:AF_NO_SHORTCUTS) {
        $shell = New-Object -ComObject WScript.Shell
        $targets = @((Join-Path ([Environment]::GetFolderPath("Programs")) "Aircraft Foundry.lnk"),
                     (Join-Path ([Environment]::GetFolderPath("Desktop")) "Aircraft Foundry.lnk"))
        foreach ($lnkPath in $targets) {
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = Join-Path $Dir "AircraftFoundry.exe"
            $lnk.WorkingDirectory = $Dir
            $lnk.Description = "Aircraft Foundry - conceptual aircraft design"
            $lnk.Save()
        }
        Say "shortcuts added to the Start menu and desktop"
    }
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Write-Host ""
Write-Host "  Done. Your saved planes and settings are kept between updates." -ForegroundColor Green
if (-not $env:AF_NO_LAUNCH) { Start-Process (Join-Path $Dir "AircraftFoundry.exe") }
