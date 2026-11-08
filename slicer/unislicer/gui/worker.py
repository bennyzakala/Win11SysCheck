"""Background slicing / export so the UI stays responsive."""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QThread, Signal


_RUNNING: set = set()


def _wait_all_at_exit():
    """A thread still running when the program ends makes Qt abort ("QThread: Destroyed while thread is still
    running"): ask every background task to stop and let it finish first."""
    for t in list(_RUNNING):
        try:
            t.cancel()
            t.wait(10000)
        except RuntimeError:                 # its Qt object is already gone
            pass


import atexit as _atexit  # noqa: E402
_atexit.register(_wait_all_at_exit)


class Task(QThread):
    progress = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False
        self.started.connect(lambda: _RUNNING.add(self))
        self.finished.connect(lambda: _RUNNING.discard(self))

    def start(self, *a, **k):
        _RUNNING.add(self)                   # counted from the moment it is asked to start
        super().start(*a, **k)

    def cancel(self):
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        fn, self._fn = self._fn, None           # the work (and the plate it captured) is let go when done
        try:
            result = fn(lambda stage, f: self.progress.emit(stage, float(f)), self.cancelled)
        except InterruptedError:
            self.failed.emit("")
            return
        except Exception as exc:  # report to the UI instead of crashing the thread
            if sys.stderr is not None:          # the windowed Windows build has no console
                traceback.print_exc()
            try:                                 # the assistant keeps a record of background failures too
                from .. import diagnose
                diagnose.CrashLog().record(diagnose.diagnose(exc))
            except Exception:
                pass
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if not self._cancel:
            self.succeeded.emit(result)
