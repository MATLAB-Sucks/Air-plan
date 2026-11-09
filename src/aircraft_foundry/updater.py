"""Check GitHub for a newer release, download it, verify it and install it.

Releases are published by the GitHub Actions workflow when a version tag (``v0.9.0``) is pushed:
each release carries ``AircraftFoundry-Windows.zip``, ``AircraftFoundry-macOS.zip``,
``AircraftFoundry-Portable.zip`` and ``SHA256SUMS.txt``.

How an update is installed:

* **Packaged app (Windows / macOS, installed or on a USB stick)** - the right zip for this
  platform is downloaded and its SHA-256 checked against ``SHA256SUMS.txt``; it is unpacked next to
  the app; a small script then waits for the app to quit, keeps the old version as a backup,
  moves the new one into place and starts it.  If the swap fails the old version is put back.
* **Running from a git checkout** - ``git pull --ff-only`` (then restart).

Saved designs are never touched: the design library, settings and projects live in the data
folder (``AircraftFoundryData`` on a USB stick, or the user data folder), which is separate from
the program folder.  The updater refuses to run if the data folder is inside the program folder,
and copies the design library to ``backups/`` before installing.  Older designs keep working in
newer versions through :mod:`aircraft_foundry.core.compat`.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__

DEFAULT_REPO = "MATLAB-Sucks/Air-plan"
API = "https://api.github.com/repos/{repo}/releases/latest"
ASSET_NAMES = {"windows": "AircraftFoundry-Windows.zip", "macos": "AircraftFoundry-macOS.zip"}
SUMS_NAME = "SHA256SUMS.txt"


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""

    @property
    def installable(self) -> bool:
        return bool(self.asset_url and self.sha256)


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2); pre-release suffixes sort before the release."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)", text.strip())
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (3 - len(nums))
    return nums + ((0,) if m.group(2) else (1,))


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def platform_key() -> str:
    s = sys.platform
    return "windows" if s.startswith("win") else ("macos" if s == "darwin" else "linux")


def install_mode() -> str:
    """'frozen' (packaged app), 'git' (source checkout) or 'source' (source without git)."""
    if getattr(sys, "frozen", False):
        return "frozen"
    root = source_root()
    return "git" if root is not None and (root / ".git").exists() else "source"


def source_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def app_root() -> Path:
    """The folder (Windows) or .app bundle (macOS) that an update replaces."""
    exe = Path(sys.executable).resolve()
    if platform_key() == "macos":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
    return exe.parent


def _get(url: str, token: str | None = None, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": f"AircraftFoundry/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=timeout)


def _http_message(exc, repo: str) -> str:
    """Plain-language explanations of GitHub's answers."""
    if exc.code == 404:
        return (f"No release has been published on '{repo}' yet — or the repository name is wrong, or it is "
                "private (GitHub answers 'not found' for private repositories unless a token is set).")
    if exc.code in (403, 429):
        reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
        if exc.headers and exc.headers.get("X-RateLimit-Remaining") == "0" and reset:
            when = time.strftime("%H:%M", time.localtime(int(reset)))
            return (f"GitHub's limit of 60 update checks per hour for this internet connection is used up. "
                    f"Try again after {when} (or set a token in the update settings).")
        return f"GitHub refused the request ({exc.code}). If the repository is private, set a token."
    return f"GitHub answered with an error ({exc.code} {exc.reason})."


def check_for_update(repo: str = DEFAULT_REPO, current: str = __version__, token: str | None = None,
                     fetch=None) -> UpdateInfo | None:
    """The latest release if it is newer than ``current`` (None if up to date).

    ``fetch(url) -> bytes`` can be injected for tests.
    """
    fetch = fetch or (lambda u: _get(u, token).read())
    try:
        rel = json.loads(fetch(API.format(repo=repo)))
    except urllib.error.HTTPError as exc:
        raise UpdateError(_http_message(exc, repo)) from exc
    except Exception as exc:  # offline, DNS, TLS...
        raise UpdateError(f"Couldn't reach GitHub ({exc}). Check the internet connection, and that the "
                          f"repository '{repo}' is public (or a token is set in the update settings).") from exc
    if not isinstance(rel, dict) or "tag_name" not in rel:
        raise UpdateError(rel.get("message", "No releases found") if isinstance(rel, dict) else "No releases found")
    version = rel["tag_name"].lstrip("v")
    if not is_newer(version, current):
        return None
    info = UpdateInfo(version, rel.get("body") or "", rel.get("html_url", ""))
    assets = {a["name"]: a for a in rel.get("assets", [])}
    want = ASSET_NAMES.get(platform_key())
    if want and want in assets:
        a = assets[want]
        info.asset_name, info.asset_url, info.asset_size = want, a["browser_download_url"], int(a.get("size", 0))
        if SUMS_NAME in assets:
            sums = fetch(assets[SUMS_NAME]["browser_download_url"]).decode()
            for line in sums.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == want:
                    info.sha256 = parts[0].lower()
    return info


def download(info: UpdateInfo, dest_dir: Path, progress=None, token: str | None = None, opener=None) -> Path:
    """Stream the asset to ``dest_dir`` and verify its SHA-256."""
    if not info.installable:
        raise UpdateError("This release has no verified download for this computer.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / info.asset_name
    h = hashlib.sha256()
    resp = (opener or (lambda u: _get(u, token, timeout=60)))(info.asset_url)
    total = info.asset_size or int(getattr(resp, "headers", {}).get("Content-Length", 0) or 0)
    done = 0
    with open(path, "wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != info.sha256:
        path.unlink(missing_ok=True)
        raise UpdateError("The download didn't match its published checksum, so it was discarded.")
    return path


def stage(zip_path: Path, staging: Path) -> Path:
    """Unpack the downloaded zip and return the new app folder / bundle inside it."""
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if platform_key() == "macos" and shutil.which("ditto"):
        subprocess.run(["ditto", "-x", "-k", str(zip_path), str(staging)], check=True)  # keeps symlinks/permissions
    else:
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():  # refuse paths escaping the staging folder
                target = (staging / member).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    raise UpdateError("Unsafe path in the update archive.")
            z.extractall(staging)
    if platform_key() == "macos":
        apps = list(staging.rglob("*.app"))
        if apps:
            return min(apps, key=lambda p: len(p.parts))
    exes = list(staging.rglob("AircraftFoundry.exe")) or list(staging.rglob("AircraftFoundry"))
    if not exes:
        raise UpdateError("The update archive doesn't contain the application.")
    return min(exes, key=lambda p: len(p.parts)).parent


def ensure_data_is_separate(data_root: Path, root: Path) -> None:
    data_root, root = data_root.resolve(), root.resolve()
    if data_root == root or root in data_root.parents:
        raise UpdateError(f"Your data folder ({data_root}) is inside the program folder ({root}); "
                          "updating could overwrite it, so the update was stopped.")


def backup_library(data_root: Path, library_path: Path | None, version: str = __version__) -> Path | None:
    if library_path is None or not Path(library_path).exists():
        return None
    out = Path(data_root) / "backups"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"library-before-update-from-{version}-{time.strftime('%Y%m%d-%H%M%S')}{Path(library_path).suffix}"
    shutil.copy2(library_path, dest)
    return dest


def swap_script(old_root: Path, new_root: Path, pid: int, key: str | None = None) -> tuple[Path, list[str]]:
    """Write the script that swaps the app after this process exits; returns (script, command)."""
    key = key or platform_key()
    backup = old_root.with_name(old_root.name + ".previous")
    work = new_root.parent
    if key == "windows":
        exe = old_root / "AircraftFoundry.exe"
        script = work / "apply_update.ps1"
        script.write_text(f'''$ErrorActionPreference = "Stop"
while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 300 }}
Start-Sleep -Milliseconds 500
$old = "{old_root}"; $new = "{new_root}"; $bak = "{backup}"
if (Test-Path $bak) {{ Remove-Item -Recurse -Force $bak }}
try {{
  Move-Item $old $bak
  Move-Item $new $old
}} catch {{
  if (-not (Test-Path $old) -and (Test-Path $bak)) {{ Move-Item $bak $old }}
}}
Start-Process -FilePath "{exe}"
''', encoding="utf-8")
        return script, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                        "-File", str(script)]
    script = work / "apply_update.sh"
    relaunch = 'open "$OLD"' if key == "macos" else '"$OLD/AircraftFoundry" &'
    script.write_text(f'''#!/bin/bash
# wait until the app has exited (a finished-but-unreaped "zombie" process counts as exited)
while ps -p {pid} -o stat= 2>/dev/null | grep -qv Z; do sleep 0.3; done
sleep 0.5
OLD="{old_root}"; NEW="{new_root}"; BAK="{backup}"
rm -rf "$BAK"
if mv "$OLD" "$BAK" && mv "$NEW" "$OLD"; then
  xattr -dr com.apple.quarantine "$OLD" 2>/dev/null || true
else
  [ -e "$OLD" ] || mv "$BAK" "$OLD"
fi
{relaunch}
''')
    script.chmod(0o755)
    return script, ["/bin/bash", str(script)]


def launch_detached(cmd: list[str]) -> None:
    if platform_key() == "windows":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(cmd, start_new_session=True, close_fds=True)


KEEP_ON_SOURCE_UPDATE = {".venv", "venv", ".git", "AircraftFoundryData", "build", "dist", "__pycache__"}


def source_update(info: UpdateInfo, repo: str = DEFAULT_REPO, root: Path | None = None, token: str | None = None,
                  fetch=None, run=subprocess.run) -> str:
    """Update a source copy that isn't a git checkout: replace the program files with the release's source.

    Python environments, git data, build output and data folders are left alone; afterwards the
    dependencies are re-installed from the lock file for this platform if it changed.
    """
    import tarfile
    import tempfile
    root = root or source_root()
    if root is None:
        raise UpdateError("Couldn't find the program folder.")
    fetch = fetch or (lambda u: _get(u, token, timeout=120).read())
    data = fetch(f"https://github.com/{repo}/archive/refs/tags/v{info.version}.tar.gz")
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "src.tar.gz"
        tar_path.write_bytes(data)
        with tarfile.open(tar_path) as tf:
            for m in tf.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise UpdateError("Unsafe path in the source archive.")
            tf.extractall(tmp)
        new_root = next(p for p in Path(tmp).iterdir() if p.is_dir())
        if not (new_root / "pyproject.toml").exists():
            raise UpdateError("The download doesn't look like Aircraft Foundry's source.")
        lock = {"windows": "lock-windows-x64-py311.txt", "linux": "lock-linux-x86_64-py311.txt"}.get(
            platform_key(), "lock-macos-arm64-py311.txt" if platform.machine() == "arm64" else "lock-macos-intel-py311.txt")
        old_lock = (root / "requirements" / lock).read_text() if (root / "requirements" / lock).exists() else ""
        for item in new_root.iterdir():
            if item.name in KEEP_ON_SOURCE_UPDATE:
                continue
            dest = root / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    msg = f"Updated the program files to {info.version}."
    new_lock = (root / "requirements" / lock).read_text() if (root / "requirements" / lock).exists() else ""
    if new_lock and new_lock != old_lock:
        out = run([sys.executable, "-m", "pip", "install", "-q", "--only-binary", ":all:", "-r",
                   str(root / "requirements" / lock)], capture_output=True, text=True)
        msg += " Dependencies updated." if out.returncode == 0 else \
            f" Updating the dependencies failed - run: python -m pip install -r requirements/{lock}"
    return msg


def git_update(root: Path | None = None) -> str:
    root = root or source_root()
    if root is None or not (root / ".git").exists():
        raise UpdateError("This copy isn't a git checkout.")
    out = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"], capture_output=True, text=True)
    if out.returncode != 0:
        raise UpdateError(out.stderr.strip() or "git pull failed")
    return out.stdout.strip()


def prepare_install(info: UpdateInfo, data_root: Path, library_path: Path | None, progress=None,
                    token: str | None = None) -> list[str]:
    """Download, verify, back up the library and stage; returns the command that applies the update."""
    root = app_root()
    ensure_data_is_separate(Path(data_root), root)
    work = root.parent / f".aircraft-foundry-update-{info.version}"
    zip_path = download(info, work, progress, token)
    backup_library(Path(data_root), library_path)
    new_root = stage(zip_path, work / "new")
    _, cmd = swap_script(root, new_root, os.getpid())
    return cmd
