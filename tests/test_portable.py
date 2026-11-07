import os
import stat
import sys

import pytest

from aircraft_foundry import portable
from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.library import DesignLibrary, LibraryReadOnly


def test_portable_found_from_windows_and_mac_locations(tmp_path, monkeypatch):
    monkeypatch.delenv(portable.ENV_VAR, raising=False)
    portable.create_portable_marker(tmp_path)
    win = tmp_path / "Windows" / "AircraftFoundry"
    win.mkdir(parents=True)
    loc = portable.locate(win)
    assert loc.portable and loc.root == tmp_path / "AircraftFoundryData"
    assert portable.locate(tmp_path).root == loc.root  # Mac: .app sits at the stick root


def test_not_portable_without_marker(tmp_path, monkeypatch):
    monkeypatch.delenv(portable.ENV_VAR, raising=False)
    (tmp_path / "AircraftFoundryData").mkdir()  # folder without marker is not enough
    assert not portable.locate(tmp_path).portable


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(portable.ENV_VAR, str(tmp_path / "x"))
    assert portable.locate(tmp_path).root == tmp_path / "x"


def test_frozen_mac_bundle_resolves_to_bundle_parent(tmp_path, monkeypatch):
    exe = tmp_path / "Aircraft Foundry.app" / "Contents" / "MacOS" / "AircraftFoundry"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert portable.application_dir() == tmp_path


def test_translocated_app_finds_volume(tmp_path, monkeypatch):
    monkeypatch.delenv(portable.ENV_VAR, raising=False)
    vol = tmp_path / "Volumes" / "AIRCRAFT"
    portable.create_portable_marker(vol)
    monkeypatch.setattr(portable, "_mounted_volumes", lambda: [vol])
    loc = portable.locate(tmp_path / "private" / "AppTranslocation" / "ABC" / "d")
    assert loc.portable and loc.root == vol / "AircraftFoundryData"


def test_library_roundtrip(tmp_path, params, mission):
    lib = DesignLibrary(tmp_path / "lib.sqlite", "Windows 11 test")
    ev = evaluate(params, mission)
    did = lib.save(params, mission, "Jet A", configuration="cfg", evaluation=ev, tags="test", thumbnail_png=b"png")
    assert lib.count() == 1 and lib.integrity_ok()
    p2, m2, entry = lib.load(did)
    assert p2 == params and m2 == mission and entry.created_on == "Windows 11 test" and entry.feasible == ev.feasible
    lib.save(params, mission, "Jet A v2", design_id=did)  # overwrite keeps creation info
    assert lib.count() == 1 and lib.list()[0].name == "Jet A v2" and lib.list()[0].created_on == "Windows 11 test"
    lib.update(did, notes="good", tags="a,b")
    assert lib.list("good")[0].tags == "a,b" and lib.list("nomatch") == []
    assert [p.name for p in tmp_path.iterdir()] == ["lib.sqlite"]  # no WAL/journal files left behind
    lib.delete(did)
    assert lib.count() == 0


def test_library_newer_schema_is_never_written(tmp_path, params, mission):
    import sqlite3
    lib = DesignLibrary(tmp_path / "lib.sqlite")
    con = sqlite3.connect(tmp_path / "lib.sqlite")
    con.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    con.commit()
    con.close()
    lib2 = DesignLibrary(tmp_path / "lib.sqlite")
    assert lib2.read_only
    with pytest.raises(LibraryReadOnly):
        lib2.save(params, mission, "x")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="root ignores file permissions")
def test_library_read_only_media(tmp_path, params, mission):
    lib = DesignLibrary(tmp_path / "lib.sqlite")
    lib.save(params, mission, "A")
    os.chmod(tmp_path / "lib.sqlite", stat.S_IREAD)
    os.chmod(tmp_path, stat.S_IREAD | stat.S_IEXEC)
    try:
        ro = DesignLibrary(tmp_path / "lib.sqlite")
        assert ro.read_only and ro.count() == 1
        with pytest.raises(LibraryReadOnly):
            ro.save(params, mission, "B")
    finally:
        os.chmod(tmp_path, 0o755)


def test_assemble_usb_layout(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("assemble_usb", Path(__file__).parents[1] / "packaging" / "assemble_usb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    win = tmp_path / "win"
    win.mkdir()
    (win / "AircraftFoundry.exe").write_bytes(b"MZ")
    mac = tmp_path / "Aircraft Foundry.app" / "Contents" / "MacOS"
    mac.mkdir(parents=True)
    (mac / "AircraftFoundry").write_text("#!/bin/sh")
    out = mod.assemble(tmp_path / "USB", win, tmp_path / "Aircraft Foundry.app")
    assert (out / "Windows" / "AircraftFoundry" / "AircraftFoundry.exe").exists()
    assert (out / "Aircraft Foundry.app" / "Contents" / "MacOS" / "AircraftFoundry").exists()
    assert b"\r\n" in (out / "Start Aircraft Foundry (Windows).bat").read_bytes()
    assert os.access(out / "Mac first-run fix.command", os.X_OK)
    # both builds would resolve the same shared data folder
    assert portable.locate(out / "Windows" / "AircraftFoundry").root == portable.locate(out).root
