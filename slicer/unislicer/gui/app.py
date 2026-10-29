"""Application bootstrap."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from . import i18n, theme
from .main_window import SETTINGS_ORG, MainWindow
from .viewer import Viewer


def run(paths: list[str] | None = None) -> int:
    Viewer.configure_default_format()
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("UniSlicer")
    app.setOrganizationName(SETTINGS_ORG)
    theme.apply(app)
    app.setWindowIcon(theme.icon("layers", theme.ACCENT, 64))
    store = QSettings(SETTINGS_ORG, SETTINGS_ORG)

    state = {"window": None}

    def open_window(objects=None):
        i18n.set_language(store.value("language", "he"))
        app.setLayoutDirection(Qt.RightToLeft if i18n.LANG == "he" else Qt.LeftToRight)
        win = MainWindow(objects)
        win.languageSwitchRequested.connect(lambda: switch_language(win))
        state["window"] = win
        win.show()
        return win

    def switch_language(win):
        store.setValue("language", "en" if i18n.LANG == "he" else "he")
        objects = win.objects
        win.close()
        open_window(objects)

    win = open_window()
    if paths:
        win.load_files(paths)
    return app.exec()
