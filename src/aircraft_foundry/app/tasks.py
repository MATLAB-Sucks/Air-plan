"""Run long engine operations off the GUI thread with progress and cancel."""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any]):
        super().__init__()
        self._fn = fn
        self.cancelled = False

    def run(self) -> None:
        try:
            result = self._fn(progress=lambda f, m: self.progress.emit(float(f), str(m)),
                              cancel=lambda: self.cancelled)
            self.finished.emit(result)
        except Exception as exc:  # report every failure to the UI, never crash the thread
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class Task(QObject):
    """A single background job.  ``fn(progress=..., cancel=...)`` runs in a QThread."""

    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], parent: QObject | None = None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.finished.connect(self._done)
        self._worker.failed.connect(self._fail)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._worker.cancelled = True

    @property
    def running(self) -> bool:
        return self._thread.isRunning()

    def _stop(self) -> None:
        self._thread.quit()
        self._thread.wait()

    def _done(self, result) -> None:
        self._stop()
        self.finished.emit(result)

    def _fail(self, msg: str) -> None:
        self._stop()
        self.failed.emit(msg)
