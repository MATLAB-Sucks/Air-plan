"""GUI entry point."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    import vtkmodules.qt
    vtkmodules.qt.PyQtImpl = "PySide6"
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setOrganizationName("Aircraft Foundry")
    QCoreApplication.setApplicationName("Aircraft Foundry")
    # The viewport uses VTK's default QWidget-based interactor, which creates its
    # own native OpenGL context (Cocoa on macOS); no Qt surface format is needed.
    app = QApplication(sys.argv if argv is None else argv)
    from .theme import apply_theme
    apply_theme(app)
    from .main_window import MainWindow
    window = MainWindow()
    args = [a for a in app.arguments()[1:] if not a.startswith("-psn_")]  # macOS Finder adds -psn_*
    if "--self-test" in args:
        log = args[args.index("--self-test-log") + 1] if "--self-test-log" in args else None
        _schedule_self_test(window, log)
    elif args and args[0].endswith(".afproj"):
        from ..core.project import Project
        window._load_project(Project.load(args[0]))
    window.show()
    return app.exec()


def _schedule_self_test(window, log_path: str | None = None) -> None:
    """Verify a (packaged) install: build, render and evaluate a design, then quit."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    lines: list[str] = []

    def say(msg: str) -> None:  # windowed builds on Windows have no stdout: also write a log file
        lines.append(msg)
        print(msg, flush=True)

    def run():
        code = 0
        try:
            from ..core.design import AircraftDesign
            from ..core.evaluator import evaluate
            from ..core.mission import MissionRequirements
            from ..geometry.cad import cadquery_available
            d = AircraftDesign()
            stats = window.viewport.show_design(d)
            ev = evaluate(d.params, MissionRequirements())
            from ..simulator.flightmodel import FlightSimulator, build_aero_model
            sim = FlightSimulator(build_aero_model(d.params, MissionRequirements(), ev))
            trimmed = sim.start_in_flight(ev.metrics["cruise_speed_ms"], MissionRequirements().cruise_altitude_m)
            for _ in range(1000):  # 5 s of simulated flight
                sim.step(1 / 200)
            say(f"SELF-TEST SIM: trimmed={trimmed}, altitude after 5 s {sim.telemetry()['altitude']:.0f} m")
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
        if log_path:
            from pathlib import Path
            Path(log_path).write_text("\n".join(lines) + "\n")
        window.dirty = False
        window.close()
        QApplication.exit(code)

    QTimer.singleShot(800, run)


if __name__ == "__main__":
    sys.exit(main())
