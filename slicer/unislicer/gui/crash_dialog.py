"""The assistant's crash report: catches errors anywhere in the program, keeps it running, and explains
what went wrong and how to fix it (see ``unislicer.diagnose``)."""
from __future__ import annotations

import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QToolButton,
                               QVBoxLayout)

from .. import diagnose
from . import i18n, theme
from .i18n import tr


class CrashDialog(QDialog):
    def __init__(self, d: diagnose.Diagnosis, parent=None):
        super().__init__(parent)
        self.d = d
        t = d.text(i18n.LANG)
        self.setWindowTitle(tr("crash_title"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(theme.icon("bulb", theme.WARNING, 36).pixmap(36, 36))
        head.addWidget(icon)
        title = QLabel(f"<b style='font-size:15px'>{tr('crash_agent')}</b><br>{t['title']}")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        lay.addLayout(head)

        def section(label, body):
            w = QLabel(f"<b>{label}</b><br>{body}")
            w.setWordWrap(True)
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(w)

        if t["area"]:
            section(tr("crash_where"), t["area"])
        section(tr("crash_cause"), t["cause"])
        section(tr("crash_fix"), "<ol style='margin:0'>" + "".join(f"<li>{s}</li>" for s in t["fixes"]) + "</ol>")
        if d.repeat > 1:
            note = QLabel(tr("crash_repeat").format(d.repeat))
            note.setStyleSheet(f"color:{theme.WARNING}")
            note.setWordWrap(True)
            lay.addWidget(note)

        more = QToolButton()
        more.setText(tr("crash_details"))
        more.setCheckable(True)
        lay.addWidget(more)
        self.details = QPlainTextEdit(diagnose.report_text(d, i18n.LANG))
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(180)
        self.details.setVisible(False)
        more.toggled.connect(self.details.setVisible)
        lay.addWidget(self.details)

        row = QHBoxLayout()
        copy = QPushButton(tr("crash_copy"))
        copy.clicked.connect(self._copy)
        folder = QPushButton(tr("crash_folder"))
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(diagnose.CrashLog().folder)))
        ok = QPushButton(tr("crash_continue"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addWidget(folder)
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)

    def _copy(self):
        QGuiApplication.clipboard().setText(diagnose.report_text(self.d, i18n.LANG))
        self.sender().setText(tr("crash_copied"))


class CrashReporter(QObject):
    """Installs sys.excepthook / threading.excepthook; errors are diagnosed, logged and shown once."""
    reported = Signal(object)            # Diagnosis (the main window passes it to the assistant)
    _from_thread = Signal(object)

    def __init__(self, window_getter, parent=None):
        super().__init__(parent)
        self.window_getter = window_getter
        self.log = diagnose.CrashLog()
        self._shown: dict[str, float] = {}
        self._open = False
        self._from_thread.connect(self._show)
        self._prev_hook = sys.excepthook

    def install(self) -> diagnose.Diagnosis | None:
        sys.excepthook = self._hook
        threading.excepthook = lambda a: self._hook(a.exc_type, a.exc_value, a.exc_traceback)
        return self.log.arm()

    def uninstall(self):
        sys.excepthook = self._prev_hook
        self.log.disarm()

    def _hook(self, exc_type, exc, tb):
        if exc_type is KeyboardInterrupt:
            return
        try:
            if sys.stderr is not None:                 # windowed build: no console
                import traceback
                traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
            d = self.log.record(diagnose.diagnose(exc, tb))
        except Exception:                              # the reporter itself must never fail
            return
        if threading.current_thread() is threading.main_thread():
            # not from inside the painting / event code that failed: show it right after
            QTimer.singleShot(0, lambda: self._show(d))
        else:
            self._from_thread.emit(d)

    def _show(self, d: diagnose.Diagnosis):
        now = time.time()
        # an error in drawing repeats on every frame: one window per problem, at most once a minute
        if self._open or now - self._shown.get(d.signature, 0.0) < 60.0:
            return
        self._shown[d.signature] = now
        self.reported.emit(d)
        self._open = True
        try:
            CrashDialog(d, self.window_getter()).exec()
        finally:
            self._open = False

    def show_previous(self, d: diagnose.Diagnosis):
        QTimer.singleShot(600, lambda: self._show(d))


def recent_crashes(days: float = 7.0) -> list[dict]:
    """Problems logged in the last few days, newest per signature (for the assistant panel)."""
    out: dict[str, dict] = {}
    limit = time.time() - days * 86400
    for e in diagnose.CrashLog().entries():
        if e.get("time", 0) >= limit:
            out[e["signature"]] = e
    return sorted(out.values(), key=lambda e: -e.get("time", 0))


def entry_diagnosis(e: dict) -> diagnose.Diagnosis:
    keys = diagnose.Diagnosis.__dataclass_fields__
    return diagnose.Diagnosis(**{k: (tuple(v) if isinstance(v, list) and k in ("title", "cause", "area") else v)
                                 for k, v in e.items() if k in keys})

