"""Application bootstrap."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication

from . import i18n, theme
from .main_window import SETTINGS_ORG, MainWindow
from .viewer import Viewer


def _trace(msg: str) -> None:
    if SMOKE:
        print(f"[smoke] {msg}", file=sys.stderr, flush=True)


SMOKE = bool(os.environ.get("UNISLICER_SMOKE_TEST"))


def default_language() -> str:
    """Hebrew when the system is set to Hebrew, otherwise English (the installer stores an explicit choice)."""
    from PySide6.QtCore import QLocale
    return "he" if QLocale.system().language() == QLocale.Hebrew else "en"


def _relaunch_with_software_opengl() -> int:
    """Restart the app using Qt's bundled software renderer (opengl32sw.dll on Windows)."""
    env = dict(os.environ, QT_OPENGL="software", UNISLICER_SOFTWARE_GL="1")
    cmd = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    _trace(f"OpenGL 3.3 unavailable, relaunching with software rendering: {cmd}")
    return subprocess.call(cmd, env=env)


def run(paths: list[str] | None = None) -> int:
    if SMOKE:
        import faulthandler
        faulthandler.dump_traceback_later(45, exit=True)  # print where it is stuck, then exit
        _trace("starting")
    Viewer.configure_default_format()
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("UniSlicer")
    app.setOrganizationName(SETTINGS_ORG)
    store = QSettings(SETTINGS_ORG, SETTINGS_ORG)
    theme.set_theme(str(store.value("theme", "dark")), str(store.value("logo_file", "")))
    theme.apply(app)
    app.setWindowIcon(theme.icon("layers", theme.ACCENT, 64))
    _trace(f"QApplication ready, platform={app.platformName()}")

    state = {"window": None, "software_gl": False}
    can_fall_back = sys.platform == "win32" and not os.environ.get("UNISLICER_SOFTWARE_GL")

    def on_gl_failed(_message):
        # The GPU driver gave the 3D view less than OpenGL 3.3: restart on Qt's software renderer.
        if can_fall_back and not state["software_gl"]:
            state["software_gl"] = True
            QTimer.singleShot(0, app.quit)

    def open_window(objects=None):
        i18n.set_language(store.value("language", default_language()))
        app.setLayoutDirection(Qt.RightToLeft if i18n.LANG == "he" else Qt.LeftToRight)
        _trace("creating main window")
        win = MainWindow(objects)
        win.languageSwitchRequested.connect(lambda: switch_language(win))
        win.themeChangeRequested.connect(lambda name, logo: switch_theme(win, name, logo))
        win.viewer.glFailed.connect(on_gl_failed)
        state["window"] = win
        win.show()
        _trace("main window shown")
        return win

    def switch_theme(win, name, logo):
        # every widget reads the theme colours when it is built, so rebuild the window (models are kept)
        store.setValue("theme", name)
        store.setValue("logo_file", logo)
        theme.set_theme(name, logo)
        theme.apply(app)
        objects = win.objects
        win.close()
        open_window(objects)

    def switch_language(win):
        store.setValue("language", "en" if i18n.LANG == "he" else "he")
        objects = win.objects
        win.close()
        open_window(objects)

    win = open_window()
    if paths:
        win.load_files(paths)
    if SMOKE:
        QTimer.singleShot(0, lambda: _trace("event loop running"))
        QTimer.singleShot(4000, app.quit)  # used by the build pipeline to check the app starts
    code = app.exec()
    _trace(f"exited with {code}")
    if state["software_gl"]:
        state["window"].close()
        return _relaunch_with_software_opengl()
    return code
