"""Background slicing / export so the UI stays responsive."""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    progress = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        try:
            result = self._fn(lambda stage, f: self.progress.emit(stage, float(f)), self.cancelled)
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
