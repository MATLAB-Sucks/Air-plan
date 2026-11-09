"""GUI entry point."""
from __future__ import annotations

import sys


_LOG = None  # self-test log, written line by line so a crash still leaves evidence


def _log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG is not None:
        _LOG.write(msg + "\n")
        _LOG.flush()


def main(argv: list[str] | None = None) -> int:
    global _LOG
    raw = sys.argv if argv is None else argv
    if "--self-test-log" in raw:
        import faulthandler
        _LOG = open(raw[raw.index("--self-test-log") + 1], "w", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_LOG, all_threads=True)  # native crashes dump the Python stack here
        _log("SELF-TEST STAGE: starting")
    import vtkmodules.qt
    vtkmodules.qt.PyQtImpl = "PySide6"
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setOrganizationName("Aircraft Foundry")
    QCoreApplication.setApplicationName("Aircraft Foundry")
    # The viewport uses VTK's default QWidget-based interactor, which creates its
    # own native OpenGL context (Cocoa on macOS); no Qt surface format is needed.
    app = QApplication(sys.argv if argv is None else argv)
    _log("SELF-TEST STAGE: Qt application created") if _LOG else None
    from .theme import apply_theme
    apply_theme(app)
    from .main_window import MainWindow
    _log("SELF-TEST STAGE: building main window") if _LOG else None
    window = MainWindow()
    _log("SELF-TEST STAGE: main window built") if _LOG else None
    args = [a for a in app.arguments()[1:] if not a.startswith("-psn_")]  # macOS Finder adds -psn_*
    if "--self-test" in args:
        log = args[args.index("--self-test-log") + 1] if "--self-test-log" in args else None
        _schedule_self_test(window, log)
    elif args and args[0].endswith(".afproj"):
        from ..core.project import Project
        window._load_project(Project.load(args[0]))
    window.show()
    _log("SELF-TEST STAGE: window shown (OpenGL context next)") if _LOG else None
    code = app.exec()
    # Skip Python's module teardown: on Windows, VTK/Qt native objects destroyed in the wrong order
    # at interpreter exit crash *after* everything has finished (access violation, no Python frame).
    # Settings and the library are already saved when the window closes.
    import os
    for stream in (sys.stdout, sys.stderr, _LOG):
        try:
            stream.flush()
        except Exception:
            pass
    if _LOG is not None:
        _LOG.close()
    if sys.platform.startswith("win"):
        # os._exit still runs ExitProcess, which calls every DLL's unload routine - VTK, Qt and SDL
        # crash in theirs (0xC0000005 after a clean finish). TerminateProcess skips them; everything
        # has already been saved and flushed.
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), code & 0xFFFFFFFF)
    os._exit(code)


def _schedule_self_test(window, log_path: str | None = None) -> None:
    """Verify a (packaged) install: build, render and evaluate a design, then quit."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    def say(msg: str) -> None:  # windowed builds on Windows have no stdout: written to the log file too
        _log(msg)

    def run():
        code = 0
        try:
            from ..core.design import AircraftDesign
            from ..core.evaluator import evaluate
            from ..core.mission import MissionRequirements
            from ..geometry.cad import cadquery_available
            d = AircraftDesign()
            if not window.viewport.opengl_supported():
                raise RuntimeError("OpenGL 3.2 is not available on this machine (the 3-D view is disabled)")
            say("SELF-TEST STAGE: rendering a design")
            stats = window.viewport.show_design(d)
            say("SELF-TEST STAGE: evaluating")
            ev = evaluate(d.params, MissionRequirements())
            from ..simulator.flightmodel import FlightSimulator, build_aero_model
            sim = FlightSimulator(build_aero_model(d.params, MissionRequirements(), ev))
            trimmed = sim.start_in_flight(ev.metrics["cruise_speed_ms"], MissionRequirements().cruise_altitude_m)
            for _ in range(1000):  # 5 s of simulated flight
                sim.step(1 / 200)
            say(f"SELF-TEST SIM: trimmed={trimmed}, altitude after 5 s {sim.telemetry()['altitude']:.0f} m")
            say("SELF-TEST STAGE: game-controller support (SDL)")
            from ..input.controller import make_backend
            pads = make_backend()
            say(f"SELF-TEST PADS: controller support {'available' if pads.available else 'unavailable'}"
                f"{'' if pads.available else ' (' + pads.error + ')'}; {len(pads.poll())} connected")
            n_lib = window.library.count()
            say(f"SELF-TEST OK: {len(d.components)} components, {stats.added} actors, "
                  f"MTOW {ev.metrics.get('mtow_kg', 0):,.0f} kg, wing box root cover "
                  f"{ev.structure.summary['root_upper_cover_mm']:.1f} mm, STEP export "
                f"{'available' if cadquery_available() else 'unavailable'}")
            say(f"SELF-TEST DATA: {'portable' if window.location.portable else 'per-user'} "
                  f"{window.location.root} (library: {n_lib} designs"
                f"{', read-only' if window.library.read_only else ''})")
        except Exception as exc:  # report and fail the process
            say(f"SELF-TEST FAILED: {exc!r}")
            code = 1
        window.dirty = False
        window.close()
        QApplication.exit(code)

    QTimer.singleShot(800, run)


if __name__ == "__main__":
    sys.exit(main())
