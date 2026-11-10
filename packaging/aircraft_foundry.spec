# PyInstaller spec for Aircraft Foundry.
#   pyinstaller --noconfirm packaging/aircraft_foundry.spec
# Produces dist/Aircraft Foundry.app on macOS (dist/AircraftFoundry/ elsewhere).
# Build on the architecture you are targeting: an Intel Mac produces an
# x86_64 app.  A universal2 app is not possible because the VTK, NumPy and
# OpenCascade wheels are single-architecture.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
WITH_CAD = True
try:
    import cadquery  # noqa: F401
except Exception:
    WITH_CAD = False

hidden = (collect_submodules("aircraft_foundry")
          + collect_submodules("vtkmodules")  # VTK registers backends via module import side effects
          + ["PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"])
# game controllers (SDL2 via pygame)
hidden += ["pygame", "pygame._sdl2", "pygame._sdl2.controller", "aircraft_foundry.input.controller"]
datas, binaries = [], []
# real-world simulator maps (heights, imagery, runways) ship inside the app
from PyInstaller.utils.hooks import collect_data_files
datas += collect_data_files("aircraft_foundry", includes=["simulator/worlds/**/*", "versions.json"])
if WITH_CAD:
    hidden += collect_submodules("cadquery") + collect_submodules("OCP")
    datas += collect_data_files("cadquery")
    binaries += collect_dynamic_libs("OCP")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "trame", "trame_vtk", "trame_vuetify",
              "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AircraftFoundry", console=False,
          argv_emulation=False, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, name="AircraftFoundry")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Aircraft Foundry.app",
        bundle_identifier="com.aircraftfoundry.app",
        info_plist={
            "CFBundleShortVersionString": "0.12.0",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "CFBundleDocumentTypes": [{"CFBundleTypeName": "Aircraft Foundry Project",
                                       "CFBundleTypeExtensions": ["afproj"], "CFBundleTypeRole": "Editor"}],
        },
    )
