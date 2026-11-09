"""'Check for updates' window: shows what's new, downloads, verifies and installs."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QTextBrowser, QVBoxLayout)

from .. import __version__
from .. import updater as up
from .settings import settings as app_settings


def update_settings() -> dict:
    s = app_settings()
    return {"repo": s.value("update/repo", up.DEFAULT_REPO) or up.DEFAULT_REPO,
            "token": s.value("update/token", "") or "",
            "auto": str(s.value("update/auto", "true")).lower() == "true",
            "last": float(s.value("update/last_check", 0) or 0)}


class _Worker(QObject):
    finished = Signal(object, str)  # result, error
    progress = Signal(int, int)

    def run(self, fn):
        def go():
            try:
                self.finished.emit(fn(), "")
            except Exception as exc:  # reported in the dialog
                self.finished.emit(None, str(exc))
        threading.Thread(target=go, daemon=True).start()


def check_in_background(callback) -> None:
    """Startup check (at most once a day, if enabled): callback(UpdateInfo | None)."""
    cfg = update_settings()
    if not cfg["auto"] or time.time() - cfg["last"] < 20 * 3600:
        return
    w = _Worker()
    w.finished.connect(lambda info, err: (app_settings().setValue("update/last_check", time.time()),
                                          callback(info if not err else None)))
    w.run(lambda: up.check_for_update(cfg["repo"], token=cfg["token"] or None))
    check_in_background._keep = w  # keep the QObject alive until it reports


class UpdateDialog(QDialog):
    def __init__(self, location, library_path=None, parent=None, info: up.UpdateInfo | None = None):
        super().__init__(parent)
        self.setWindowTitle("Check for updates")
        self.resize(620, 520)
        self.location, self.library_path, self.info = location, library_path, info
        lay = QVBoxLayout(self)
        self.headline = QLabel()
        self.headline.setObjectName("pageTitle")
        lay.addWidget(self.headline)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        lay.addWidget(self.notes, 1)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        cfg = update_settings()
        form = QFormLayout()
        self.repo = QLineEdit(cfg["repo"])
        self.token = QLineEdit(cfg["token"])
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("only needed if the repository is private")
        self.auto = QCheckBox("Check automatically when the app starts (once a day)")
        self.auto.setChecked(cfg["auto"])
        form.addRow("GitHub repository", self.repo)
        form.addRow("Access token", self.token)
        form.addRow(self.auto)
        lay.addLayout(form)
        note = QLabel("Your saved planes, projects and settings live in the data folder, which updates never "
                      "touch; the design library is also backed up before installing.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.check_btn = QPushButton("Check now")
        self.install_btn = QPushButton("Download and install")
        self.install_btn.setObjectName("primary")
        self.install_btn.setEnabled(False)
        close = QPushButton("Close")
        self.check_btn.clicked.connect(self.check)
        self.install_btn.clicked.connect(self.install)
        close.clicked.connect(self.reject)
        row.addWidget(self.check_btn)
        row.addStretch(1)
        row.addWidget(self.install_btn)
        row.addWidget(close)
        lay.addLayout(row)
        self._worker = _Worker()
        self._worker.progress.connect(self._progress)
        if info is not None:
            self._show(info, "")
        else:
            self.headline.setText(f"Aircraft Foundry {__version__}")
            QTimer.singleShot(50, self.check)

    def _save_settings(self):
        s = app_settings()
        s.setValue("update/repo", self.repo.text().strip() or up.DEFAULT_REPO)
        s.setValue("update/token", self.token.text().strip())
        s.setValue("update/auto", "true" if self.auto.isChecked() else "false")

    def check(self):
        self._save_settings()
        self.headline.setText("Checking GitHub…")
        self.check_btn.setEnabled(False)
        w = _Worker()
        w.finished.connect(self._show)
        repo, token = self.repo.text().strip(), self.token.text().strip() or None
        w.run(lambda: up.check_for_update(repo, token=token))
        self._check_worker = w

    def _show(self, info, err):
        self.check_btn.setEnabled(True)
        app_settings().setValue("update/last_check", time.time())
        if err:
            self.headline.setText("Couldn't check for updates")
            self.notes.setPlainText(err)
            return
        self.info = info
        if info is None:
            self.headline.setText(f"You're up to date — Aircraft Foundry {__version__}")
            self.notes.setPlainText("No newer release on GitHub.")
            self.install_btn.setEnabled(False)
            return
        self.headline.setText(f"Version {info.version} is available (you have {__version__})")
        self.notes.setMarkdown((info.notes or "No release notes.") + f"\n\n[Release page]({info.page_url})")
        mode = up.install_mode()
        if mode == "git":
            self.install_btn.setText("Update with git pull")
            self.install_btn.setEnabled(True)
        elif mode == "frozen" and info.installable:
            self.install_btn.setText(f"Download and install ({info.asset_size / 1e6:.0f} MB)")
            self.install_btn.setEnabled(True)
        elif mode == "source":
            self.install_btn.setText("Update the program files")
            self.install_btn.setEnabled(True)
        else:
            self.install_btn.setEnabled(False)
            self.headline.setText(self.headline.text() + " — no verified download for this computer")
            self.notes.append("\nThis release has no verified download for this computer yet (it may still be "
                              "building on GitHub). Try again in a few minutes, or use the one-line installer.")

    def _progress(self, done, total):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)

    def install(self):
        self._save_settings()
        if self.info is None:
            return
        mode = up.install_mode()
        self.install_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        if mode in ("git", "source"):
            w = _Worker()
            w.finished.connect(lambda out, err: self._done_git(out, err))
            repo, token = self.repo.text().strip() or up.DEFAULT_REPO, self.token.text().strip() or None
            info = self.info
            w.run(up.git_update if mode == "git" else (lambda: up.source_update(info, repo, token=token)))
            self._git_worker = w
            return
        self.bar.show()
        token = self.token.text().strip() or None
        w = _Worker()
        w.finished.connect(self._ready)
        prog = self._worker.progress
        w.run(lambda: up.prepare_install(self.info, self.location.root, self.library_path,
                                         progress=lambda d, t: prog.emit(d, t), token=token))
        self._install_worker = w

    def _done_git(self, out, err):
        if err:
            QMessageBox.warning(self, "Update", err)
            self.install_btn.setEnabled(True)
            return
        QMessageBox.information(self, "Update", f"Updated:\n{out}\n\nRestart Aircraft Foundry to use the new "
                                                "version. If requirements changed, run the setup script again.")
        self.accept()

    def _ready(self, cmd, err):
        if err:
            QMessageBox.warning(self, "Update", err)
            self.install_btn.setEnabled(True)
            self.check_btn.setEnabled(True)
            return
        if QMessageBox.question(self, "Install update",
                                f"Version {self.info.version} is ready. Aircraft Foundry will close, update and "
                                "restart. Unsaved changes will be asked about first.") != QMessageBox.Yes:
            return
        self.apply_command = cmd
        self.accept()
