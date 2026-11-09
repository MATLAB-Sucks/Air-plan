"""Self-update: version logic, verified download, staging, data safety, the swap script."""
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from aircraft_foundry import updater as up


def _zip_with_app(tmp_path, tamper=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AircraftFoundry/AircraftFoundry.exe", b"new version binary")
        z.writestr("AircraftFoundry/AircraftFoundry", "#!/bin/sh\necho started > \"$(dirname \"$0\")/started.txt\"\n")
        z.writestr("AircraftFoundry/_internal/lib.dat", b"x" * 1000)
    data = buf.getvalue()
    return data, hashlib.sha256(b"tampered" if tamper else data).hexdigest()


def _fake_github(tmp_path, version="99.0.0", tamper=False, monkeypatch=None):
    data, sha = _zip_with_app(tmp_path, tamper)
    name = up.ASSET_NAMES.get(up.platform_key(), up.ASSET_NAMES["windows"])
    if monkeypatch is not None:
        monkeypatch.setattr(up, "platform_key", lambda: "windows")
        name = up.ASSET_NAMES["windows"]
    rel = {"tag_name": f"v{version}", "body": "## What's new\n- things", "html_url": "https://example/rel",
           "assets": [{"name": name, "browser_download_url": "https://dl/app.zip", "size": len(data)},
                      {"name": "SHA256SUMS.txt", "browser_download_url": "https://dl/sums"}]}
    files = {"https://dl/sums": f"{sha}  {name}\n".encode(), "https://dl/app.zip": data}

    def fetch(url):
        return json.dumps(rel).encode() if "api.github.com" in url else files[url]

    def opener(url):
        return io.BytesIO(files[url])
    return fetch, opener, data


def test_version_ordering():
    assert up.parse_version("v0.10.0") > up.parse_version("0.9.9")
    assert up.parse_version("1.0.0") > up.parse_version("1.0.0-beta")
    assert up.is_newer("0.9.1", "0.9.0") and not up.is_newer("0.9.0", "0.9.0") and not up.is_newer("0.8", "0.9.0")


def test_check_finds_newer_release_with_checksum(tmp_path, monkeypatch):
    fetch, _, data = _fake_github(tmp_path, monkeypatch=monkeypatch)
    info = up.check_for_update("me/repo", current="0.9.0", fetch=fetch)
    assert info.version == "99.0.0" and info.installable
    assert info.sha256 == hashlib.sha256(data).hexdigest()
    old_fetch, _, _ = _fake_github(tmp_path, version="0.1.0", monkeypatch=monkeypatch)
    assert up.check_for_update("me/repo", current="0.9.0", fetch=old_fetch) is None  # up to date


def test_offline_or_private_repo_gives_a_clear_error():
    def fail(url):
        raise OSError("no network")
    with pytest.raises(up.UpdateError, match="public"):
        up.check_for_update("me/private", fetch=fail)


def test_download_is_verified(tmp_path, monkeypatch):
    fetch, opener, data = _fake_github(tmp_path, monkeypatch=monkeypatch)
    info = up.check_for_update("me/repo", current="0.9.0", fetch=fetch)
    seen = []
    path = up.download(info, tmp_path / "dl", progress=lambda d, t: seen.append(d), opener=opener)
    assert path.read_bytes() == data and seen[-1] == len(data)
    bad_fetch, bad_opener, _ = _fake_github(tmp_path, tamper=True, monkeypatch=monkeypatch)
    bad = up.check_for_update("me/repo", current="0.9.0", fetch=bad_fetch)
    with pytest.raises(up.UpdateError, match="checksum"):
        up.download(bad, tmp_path / "dl2", opener=bad_opener)
    assert not (tmp_path / "dl2" / bad.asset_name).exists()  # discarded


def test_stage_finds_the_app_and_refuses_unsafe_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "platform_key", lambda: "windows")
    data, _ = _zip_with_app(tmp_path)
    z = tmp_path / "u.zip"
    z.write_bytes(data)
    root = up.stage(z, tmp_path / "stage")
    assert (root / "AircraftFoundry.exe").exists()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zz:
        zz.writestr("../../escape.txt", "x")
    with pytest.raises(up.UpdateError, match="Unsafe"):
        up.stage(evil, tmp_path / "stage2")


def test_data_folder_must_be_outside_the_program(tmp_path):
    app = tmp_path / "USB" / "Windows" / "AircraftFoundry"
    up.ensure_data_is_separate(tmp_path / "USB" / "AircraftFoundryData", app)  # the portable layout: fine
    with pytest.raises(up.UpdateError):
        up.ensure_data_is_separate(app / "AircraftFoundryData", app)


def test_library_is_backed_up(tmp_path):
    lib = tmp_path / "data" / "library.sqlite"
    lib.parent.mkdir()
    lib.write_bytes(b"designs")
    out = up.backup_library(tmp_path / "data", lib, "0.9.0")
    assert out.read_bytes() == b"designs" and out.parent.name == "backups"
    assert lib.read_bytes() == b"designs"  # original untouched


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash swap script")
def test_swap_script_waits_swaps_keeps_backup_and_relaunches(tmp_path):
    old = tmp_path / "AircraftFoundry"
    old.mkdir()
    (old / "version.txt").write_text("old")
    new = tmp_path / "upd" / "new" / "AircraftFoundry"
    new.mkdir(parents=True)
    (new / "version.txt").write_text("new")
    exe = new / "AircraftFoundry"
    exe.write_text("#!/bin/sh\necho started > \"$(dirname \"$0\")/started.txt\"\n")
    exe.chmod(0o755)
    waiter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
    script, cmd = up.swap_script(old, new, waiter.pid, key="linux")
    t0 = time.time()
    swapper = subprocess.Popen(cmd)
    time.sleep(0.3)
    assert swapper.poll() is None and (old / "version.txt").read_text() == "old"  # still waiting for the app
    waiter.wait()  # the app quits
    swapper.wait(timeout=30)
    assert time.time() - t0 >= 0.9
    time.sleep(0.5)
    assert (old / "version.txt").read_text() == "new"
    assert (tmp_path / "AircraftFoundry.previous" / "version.txt").read_text() == "old"  # kept as backup
    assert (old / "started.txt").exists()  # relaunched


def test_windows_script_is_generated(tmp_path):
    old, new = tmp_path / "AircraftFoundry", tmp_path / "w" / "new" / "AircraftFoundry"
    new.mkdir(parents=True)
    script, cmd = up.swap_script(old, new, 1234, key="windows")
    text = script.read_text()
    assert "Get-Process -Id 1234" in text and "Move-Item" in text and "AircraftFoundry.exe" in text
    assert cmd[0] == "powershell"


def test_github_errors_are_explained():
    import urllib.error
    from email.message import Message

    def raiser(code, headers=None):
        h = Message()
        for k, v in (headers or {}).items():
            h[k] = v

        def fetch(url):
            raise urllib.error.HTTPError(url, code, "x", h, None)
        return fetch
    with pytest.raises(up.UpdateError, match="No release has been published"):
        up.check_for_update("me/repo", fetch=raiser(404))
    with pytest.raises(up.UpdateError, match="60 update checks per hour"):
        up.check_for_update("me/repo", fetch=raiser(403, {"X-RateLimit-Remaining": "0",
                                                          "X-RateLimit-Reset": "1800000000"}))


def test_source_copies_update_from_the_release_tarball(tmp_path):
    import tarfile
    root = tmp_path / "app"
    (root / "src" / "aircraft_foundry").mkdir(parents=True)
    (root / "pyproject.toml").write_text("old")
    (root / "src" / "aircraft_foundry" / "__init__.py").write_text('__version__ = "0.9.0"')
    (root / ".venv").mkdir()
    (root / ".venv" / "keep.txt").write_text("env")
    (root / "AircraftFoundryData").mkdir()
    (root / "AircraftFoundryData" / "library.sqlite").write_text("my planes")
    new = tmp_path / "Air-plan-0.9.9"
    (new / "src" / "aircraft_foundry").mkdir(parents=True)
    (new / "pyproject.toml").write_text("new")
    (new / "src" / "aircraft_foundry" / "__init__.py").write_text('__version__ = "0.9.9"')
    (new / "AircraftFoundryData").mkdir()
    (new / "AircraftFoundryData" / "library.sqlite").write_text("EMPTY")  # must never overwrite user data
    tgz = tmp_path / "s.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(new, arcname="Air-plan-0.9.9")
    info = up.UpdateInfo("0.9.9", "", "")
    msg = up.source_update(info, "me/repo", root=root, fetch=lambda url: tgz.read_bytes())
    assert "0.9.9" in msg
    assert '"0.9.9"' in (root / "src" / "aircraft_foundry" / "__init__.py").read_text()
    assert (root / ".venv" / "keep.txt").read_text() == "env"
    assert (root / "AircraftFoundryData" / "library.sqlite").read_text() == "my planes"
