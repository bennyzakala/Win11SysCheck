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
    """The system language when UniSlicer has it (Hebrew, Russian, Arabic), otherwise English; the installer
    stores an explicit choice."""
    from PySide6.QtCore import QLocale
    return {QLocale.Hebrew: "he", QLocale.Russian: "ru", QLocale.Arabic: "ar"}.get(QLocale.system().language(), "en")


def _relaunch_with_software_opengl() -> int:
    """Restart the app using Qt's bundled software renderer (opengl32sw.dll on Windows)."""
    env = dict(os.environ, QT_OPENGL="software", UNISLICER_SOFTWARE_GL="1")
    cmd = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    _trace(f"OpenGL 3.3 unavailable, relaunching with software rendering: {cmd}")
    return subprocess.call(cmd, env=env)


def _relaunch_with_gpu_again() -> int:
    """The graphics driver gave no usable OpenGL context this time (it happens to a program started by the
    installer, with its elevated rights, or while the driver is busy): try the graphics card once more in a
    fresh process before settling for the slow software renderer."""
    env = dict(os.environ, UNISLICER_GL_RETRY="1")
    cmd = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    _trace(f"OpenGL context failed, retrying the graphics card: {cmd}")
    return subprocess.call(cmd, env=env)


_MUTEX = None


def _hold_app_mutex() -> None:
    """Windows: a named mutex the installer checks, so it can ask to close UniSlicer before updating."""
    global _MUTEX
    if sys.platform == "win32" and _MUTEX is None:
        try:
            import ctypes
            _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "UniSlicerAppMutex")
        except Exception:
            pass


SERVER_NAME = "UniSlicer-open-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")


def _hand_to_running(paths: list[str]) -> bool:
    """'Open with UniSlicer' / 'Send to' while UniSlicer is open: give the files to that window."""
    import json
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    sock.write(json.dumps([os.path.abspath(p) for p in paths]).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1500)
    sock.disconnectFromServer()
    return True


def _listen_for_files(app, window_getter):
    """Receive files from later 'Open with' launches and add them to the open plate."""
    import json
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer(app)
    if not server.listen(SERVER_NAME):
        return None

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        buf = bytearray()

        def read():
            buf.extend(bytes(sock.readAll()))

        def done():
            read()
            try:
                paths = [p for p in json.loads(buf.decode("utf-8")) if isinstance(p, str)]
            except ValueError:
                paths = []
            win = window_getter()
            if paths and win is not None:
                QTimer.singleShot(0, lambda: win.import_from_cad(paths))
            sock.deleteLater()
        sock.readyRead.connect(read)
        sock.disconnected.connect(done)
    server.newConnection.connect(on_connection)
    return server


def run(paths: list[str] | None = None) -> int:
    _hold_app_mutex()
    if SMOKE:
        import faulthandler
        faulthandler.dump_traceback_later(45, exit=True)  # print where it is stuck, then exit
        _trace("starting")
    Viewer.configure_default_format()
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    if paths and not SMOKE and _hand_to_running(paths):
        return 0                                   # the running UniSlicer took the files
    app.setApplicationName("UniSlicer")
    app.setOrganizationName(SETTINGS_ORG)
    store = QSettings(SETTINGS_ORG, SETTINGS_ORG)
    theme.set_theme(str(store.value("theme", "dark")), str(store.value("logo_file", "")))
    theme.apply(app)
    app.setWindowIcon(theme.icon("layers", theme.ACCENT, 64))
    if theme.brand_pixmap("mark", 64) is not None:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(theme.brand_pixmap("mark", 64)))
    _trace(f"QApplication ready, platform={app.platformName()}")
    from .crash_dialog import CrashReporter
    reporter = CrashReporter(lambda: state["window"], app)
    previous_crash = reporter.install()          # catches every error from here on and explains it
    reporter.reported.connect(lambda d: state["window"] is not None and state["window"].on_crash_reported(d))

    state = {"window": None, "software_gl": False, "retry_gpu": False}
    can_fall_back = sys.platform == "win32" and not os.environ.get("UNISLICER_SOFTWARE_GL")

    def on_gl_failed(_message):
        # No usable OpenGL 3.3 context: restart once on the graphics card, then on Qt's software renderer.
        if can_fall_back and not state["software_gl"] and not state["retry_gpu"]:
            if not os.environ.get("UNISLICER_GL_RETRY"):
                state["retry_gpu"] = True         # first: the graphics card again, in a fresh process
            else:
                state["software_gl"] = True       # it failed twice: the software renderer
            QTimer.singleShot(0, app.quit)

    def open_window(objects=None):
        i18n.set_language(store.value("language", default_language()))
        app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
        _trace("creating main window")
        win = MainWindow(objects)
        win.languageSwitchRequested.connect(lambda code: switch_language(win, code))
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
        mark = theme.brand_pixmap("mark", 64)
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(mark) if mark is not None else theme.icon("layers", theme.ACCENT, 64))
        objects = win.objects
        win.close()
        open_window(objects)

    def switch_language(win, code):
        if code == i18n.LANG:
            return
        store.setValue("language", code)
        objects = win.objects
        win.close()
        open_window(objects)

    win = open_window()
    state["file_server"] = _listen_for_files(app, lambda: state["window"])     # kept alive with the app
    if paths:
        win.load_files(paths)
    elif not SMOKE:
        QTimer.singleShot(400, win.offer_recovery)      # plate from autosave after a crash
    if previous_crash is not None and not SMOKE:
        reporter.show_previous(previous_crash)          # the last run closed suddenly: say why
    if SMOKE:
        QTimer.singleShot(0, lambda: _trace("event loop running"))
        QTimer.singleShot(4000, app.quit)  # used by the build pipeline to check the app starts
    code = app.exec()
    reporter.uninstall()
    _trace(f"exited with {code}")
    if state["retry_gpu"]:
        state["window"].close()
        return _relaunch_with_gpu_again()
    if state["software_gl"]:
        state["window"].close()
        return _relaunch_with_software_opengl()
    return code
