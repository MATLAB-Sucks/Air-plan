# Aircraft Foundry - one-command installer for Windows (PowerShell 5.1 or 7).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say($m) { Write-Host "  $m" }
function Die($m) { Write-Host ""; Write-Host "  Install failed: $m" -ForegroundColor Red; throw $m }
function Ask($question, $default) {
    if ($env:AF_YES -or $env:CI -or -not [Environment]::UserInteractive) { return $default }
    $a = Read-Host "  $question [$default]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a.Trim() }
}

$Headers = @{ 
    "User-Agent" = "aircraft-foundry-installer"
    "Accept"     = "application/vnd.github+json"
}
if ($env:GITHUB_TOKEN) { $Headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }

function Release($repo, $tag) {
    $u = if ($tag -eq "latest") { "https://api.github.com/repos/$repo/releases/latest" }
         else { "https://api.github.com/repos/$repo/releases/tags/$tag" }
    try { 
        $resp = Invoke-WebRequest -Uri $u -Headers $Headers -UseBasicParsing 
    } catch { 
        Die "couldn't read $repo ($tag) from GitHub: $($_.Exception.Message)" 
    }
    
    $text = $resp.Content
    if ($text -is [byte[]]) { $text = [Text.Encoding]::UTF8.GetString($text) }
    
    # FIX 1: Strip UTF-8 Byte Order Mark (U+FEFF) and whitespace before parsing
    $text = $text.Trim().Trim([char]0xFEFF)
    
    try { 
        $obj = $text | ConvertFrom-Json 
    } catch { 
        Die "GitHub's answer for $repo ($tag) couldn't be read: $($_.Exception.Message)" 
    }
    
    if (-not $obj.tag_name) { Die "no release information for $repo ($tag) - is a release published?" }
    return $obj
}

function Download($url, $file) { 
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing -Headers @{ "User-Agent" = "aircraft-foundry-installer" } 
}

function Verify($rel, $file, $name) {
    $sums = $rel.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" } | Select-Object -First 1
    if (-not $sums) { Die "release $($rel.tag_name) has no SHA256SUMS.txt" }
    $text = (Invoke-WebRequest -Uri $sums.browser_download_url -UseBasicParsing).Content
    if ($text -is [byte[]]) { $text = [Text.Encoding]::UTF8.GetString($text) }
    $line = $text -split "`n" | Where-Object { $_ -match [regex]::Escape($name) } | Select-Object -First 1
    if (-not $line) { Die "SHA256SUMS.txt has no entry for $name" }
    $want = ($line.Trim() -split "\s+")[0].ToLower()
    if ((Get-FileHash -Algorithm SHA256 $file).Hash.ToLower() -ne $want) { Die "$name didn't match its published checksum - not installed" }
    Say "checksum verified"
}

function Shortcut($lnkPath, $target, $workdir) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $workdir
    $lnk.Description = "Aircraft Foundry - conceptual aircraft design"
    $lnk.Save()
}

function Replace-Folder($new, $dest) {
    $prev = "$dest.previous"
    if (Test-Path $prev) { Remove-Item -Recurse -Force $prev }
    if (Test-Path $dest) { Move-Item $dest $prev }
    try { Move-Item $new $dest }
    catch { 
        if ((-not (Test-Path $dest)) -and (Test-Path $prev)) { Move-Item $prev $dest }
        Die "couldn't move the new version into place: $($_.Exception.Message)" 
    }
    Remove-Item -Recurse -Force $prev -ErrorAction SilentlyContinue
}

$Repo = if ($env:AF_REPO) { $env:AF_REPO } else { "MATLAB-Sucks/Air-plan" }
$Version = if ($env:AF_VERSION) { $env:AF_VERSION } else { "latest" }

Write-Host ""
Write-Host "  Aircraft Foundry installer" -ForegroundColor Cyan

# ---------------------------------------------------------------- where
$target = $env:AF_TARGET
if (-not $target) {
    Write-Host ""
    Say "Where should it go?"
    Say "  1) This computer, just for me   ($env:LOCALAPPDATA\Programs\AircraftFoundry)"
    Say "  2) A USB stick or other drive   (portable: planes are saved on that drive)"
    Say "  3) A folder I choose"
    $target = @{ "1" = "computer"; "2" = "drive"; "3" = "folder" }[(Ask "Choose 1, 2 or 3" "1")]
    if (-not $target) { Die "please answer 1, 2 or 3" }
}

$Dir = $env:AF_INSTALL_DIR
if ($target -eq "computer") { $Dir = Join-Path $env:LOCALAPPDATA "Programs\AircraftFoundry" }
elseif ($target -eq "drive" -and -not $Dir) {
    $drives = Get-CimInstance Win32_LogicalDisk | Where-Object { $_.DriveType -in 2, 3 -and $_.DeviceID -ne $env:SystemDrive }
    foreach ($d in $drives) {
        $kind = if ($d.DriveType -eq 2) { "removable" } else { "drive" }
        Say ("    {0}  {1,-20} {2,6:N1} GB free  ({3})" -f $d.DeviceID, $d.VolumeName, ($d.FreeSpace / 1GB), $kind)
    }
    $pick = if ($drives) { $drives[0].DeviceID } else { "E:" }
    $Dir = ((Ask "Drive letter" $pick).Trim().TrimEnd("\").TrimEnd(":")).ToUpper() + ":\"
}
elseif ($target -eq "folder" -and -not $Dir) { $Dir = Ask "Folder" (Join-Path $env:USERPROFILE "AircraftFoundry") }
if (-not $Dir) { Die "no install location" }
$portable = $target -ne "computer"

# ---------------------------------------------------------------- how
$mode = $env:AF_MODE
if (-not $mode) {
    Write-Host ""
    Say "How?"
    Say "  a) Ready-to-run app (recommended - Python is built in)"
    Say "  b) From source (uses Python 3.11; installs its own copy here if needed)"
    $mode = @{ "a" = "app"; "b" = "source" }[(Ask "Choose a or b" "a")]
    if (-not $mode) { Die "please answer a or b" }
}

$rel = Release $Repo $Version
Write-Host ""
Say "release $($rel.tag_name) -> $Dir ($target, $mode)"

# FIX 2: Create temp directory on the TARGET drive (e.g. D:\) so C: never runs out of space
$targetDrive = Split-Path $Dir -Qualifier
if (-not $targetDrive) { $targetDrive = [IO.Path]::GetTempPath() }
$tmp = Join-Path $targetDrive ("aircraft-foundry-tmp-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null

try {
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Force $Dir | Out-Null }
    if ($mode -eq "app" -and -not $portable) {
        $name = "AircraftFoundry-Windows.zip"
        $asset = $rel.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
        if (-not $asset) { Die "release $($rel.tag_name) has no $name" }
        Say "downloading $([math]::Round($asset.size / 1MB)) MB..."
        Download $asset.browser_download_url "$tmp\$name"
        Verify $rel "$tmp\$name" $name
        Expand-Archive "$tmp\$name" "$tmp\new" -Force
        $exe = Get-ChildItem -Recurse "$tmp\new" -Filter AircraftFoundry.exe | Select-Object -First 1
        if (-not $exe) { Die "the archive doesn't contain AircraftFoundry.exe" }
        Get-ChildItem -Recurse $exe.DirectoryName | Unblock-File -ErrorAction SilentlyContinue
        if (Get-Process AircraftFoundry -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dir*" }) { Die "Aircraft Foundry is running - close it and run the installer again" }
        Replace-Folder $exe.DirectoryName $Dir
        $launch = Join-Path $Dir "AircraftFoundry.exe"
    }
    elseif ($mode -eq "app") {
        $name = "AircraftFoundry-Portable.zip"
        $asset = $rel.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
        if (-not $asset) { Die "release $($rel.tag_name) has no $name" }
        Say "downloading $([math]::Round($asset.size / 1MB)) MB (Windows + macOS versions, for use on any computer)..."
        Download $asset.browser_download_url "$tmp\$name"
        Verify $rel "$tmp\$name" $name
        Expand-Archive "$tmp\$name" "$tmp\usb" -Force
        $root = Get-ChildItem -Recurse "$tmp\usb" -Directory -Filter AircraftFoundryData | Select-Object -First 1
        if (-not $root) { Die "the portable archive has no AircraftFoundryData folder" }
        $root = $root.Parent.FullName
        foreach ($item in Get-ChildItem $root) {
            $dest = Join-Path $Dir $item.Name
            if ($item.Name -eq "AircraftFoundryData") {
                if (-not (Test-Path $dest)) { Copy-Item -Recurse $item.FullName $dest }
                elseif (-not (Test-Path (Join-Path $dest "portable.json"))) { Copy-Item (Join-Path $item.FullName "portable.json") $dest }
            } elseif ($item.PSIsContainer) { Replace-Folder $item.FullName $dest }
            else { Copy-Item -Force $item.FullName $dest }
        }
        Get-ChildItem -Recurse (Join-Path $Dir "Windows") | Unblock-File -ErrorAction SilentlyContinue
        $launch = (Get-ChildItem -Recurse (Join-Path $Dir "Windows") -Filter AircraftFoundry.exe | Select-Object -First 1).FullName
    }
    else {
        $app = Join-Path $Dir "AircraftFoundry-source"
        Say "downloading the source of $($rel.tag_name)..."
        Download "https://github.com/$Repo/archive/refs/tags/$($rel.tag_name).zip" "$tmp\src.zip"
        Expand-Archive "$tmp\src.zip" "$tmp\src" -Force
        $src = (Get-ChildItem "$tmp\src" -Directory | Select-Object -First 1).FullName
        $py = $null
        if (-not $portable) {
            foreach ($c in @(@("py", "-3.11"), @("python3.11"), @("python"))) {
                try {
                    $v = & $c[0] $c[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
                    if ($v -eq "3.11") { $py = $c; break }
                } catch {}
            }
        }
        $newApp = "$tmp\app"
        Copy-Item -Recurse $src $newApp
        if ($py) {
            Say "using this computer's Python 3.11"
            & $py[0] $py[1..9] -m venv "$newApp\.venv"
            $pyexe = "$newApp\.venv\Scripts\python.exe"
            $pyw = "$app\.venv\Scripts\pythonw.exe"
        } else {
            Say "Python 3.11 not found$(if ($portable) {' (or installing to a drive)'}): installing a self-contained Python into the install folder"
            $pbs = Release "astral-sh/python-build-standalone" "latest"
            $pa = $pbs.assets | Where-Object { $_.name -match '^cpython-3\.11\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz$' } | Select-Object -First 1
            if (-not $pa) { Die "couldn't find a Python 3.11 build to download" }
            Download $pa.browser_download_url "$tmp\python.tar.gz"
            New-Item -ItemType Directory -Force "$newApp" | Out-Null
            tar -xzf "$tmp\python.tar.gz" -C "$newApp"
            $pyexe = "$newApp\python\python.exe"
            $pyw = "$app\python\pythonw.exe"
        }
        Say "installing the Python packages (a few minutes)..."
        & $pyexe -m pip install -q --upgrade pip
        & $pyexe -m pip install -q --only-binary ":all:" -r "$newApp\requirements\lock-windows-x64-py311.txt"
        if ($LASTEXITCODE -ne 0) { Die "installing the packages failed" }
        & $pyexe -m pip install -q --no-deps "$newApp"
        Replace-Folder $newApp $app
        $rel_pyw = $pyw.Substring($Dir.TrimEnd("\").Length).TrimStart("\")
        $cmd = "@echo off`r`nset AF_HERE=%~dp0`r`n" +
               "if exist `"%AF_HERE%AircraftFoundryData\portable.json`" set AIRCRAFT_FOUNDRY_DATA=%AF_HERE%AircraftFoundryData`r`n" +
               "start `"`" `"%AF_HERE%$rel_pyw`" -m aircraft_foundry.app.main %*`r`n"
        Set-Content -Path (Join-Path $Dir "AircraftFoundry.cmd") -Value $cmd -Encoding ASCII
        if ($portable -and -not (Test-Path (Join-Path $Dir "AircraftFoundryData\portable.json"))) {
            New-Item -ItemType Directory -Force (Join-Path $Dir "AircraftFoundryData") | Out-Null
            '{"portable": true}' | Set-Content (Join-Path $Dir "AircraftFoundryData\portable.json")
        }
        $launch = Join-Path $Dir "AircraftFoundry.cmd"
    }
    Say "installed to $Dir"
    if (-not $env:AF_NO_SHORTCUTS -and $launch) {
        $where = if ($portable) { @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Aircraft Foundry ($(Split-Path $Dir -Qualifier)).lnk")) }
                 else { @((Join-Path ([Environment]::GetFolderPath("Programs")) "Aircraft Foundry.lnk"),
                          (Join-Path ([Environment]::GetFolderPath("Desktop")) "Aircraft Foundry.lnk")) }
        foreach ($l in $where) { Shortcut $l $launch (Split-Path $launch -Parent) }
        Say "shortcut$(if ($where.Count -gt 1) {'s'}) added"
    }
}
finally { 
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue 
}

Write-Host ""
Write-Host "  Done. Your saved planes and settings are kept between updates." -ForegroundColor Green
if ($portable -and $mode -eq "app") { Say "On another computer, open the drive and start Windows\AircraftFoundry\AircraftFoundry.exe (or the macOS app)." }
elseif ($portable) { Say "On another Windows computer, run AircraftFoundry.cmd on the drive." }
if (-not $env:AF_NO_LAUNCH -and $launch) { Start-Process $launch }
