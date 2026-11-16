"""Performance log: every time the window does not answer for a while (a stall), one line in a small file
next to the crash log - when, how long, what the program was doing on the window's thread, what the user had
just done, how big the plate was and how much memory the program held. The lab sends the file; the stalls
are then fixed from its own computers' numbers, not guessed.

Nothing about the models or patients is written: no file or model names, no paths - only function names of
the program, counts and times. A button's text is kept with long digit runs (case numbers) blanked out.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import threading
import time

LOG_NAME = "performance.jsonl"
THRESHOLD = 0.2              # s without an answer from the window counts as a stall
MAX_BYTES = 1 << 20          # the file is cut to its newer half beyond this
_SAMPLE = 0.02               # s between looks at the window's thread during a stall


def _clean(text: str) -> str:
    """A button's text without case numbers or anything that looks like a name taken from a file."""
    text = re.sub(r"\d{3,}", "#", str(text or ""))
    text = re.sub(r"[A-Z]{2,}-\S*", "#", text)
    return text.strip()[:60]


def _frames(frame, n: int = 4) -> str | None:
    """The innermost few functions of the program on a stack (``file:function``), innermost first - after
    the innermost function of all in [brackets] when that is outside the program (a library, Qt)."""
    out, inner, depth = [], None, 0
    while frame is not None and depth < 60:
        code = frame.f_code
        path = code.co_filename.replace("\\", "/")
        name = f"{os.path.basename(path)}:{code.co_name}"
        if inner is None:
            inner = name
        # "/unislicer/" running from the sources; "unislicer/..." with no folder before it in the installed
        # program (its paths are relative: the first log from the lab had no chains at all)
        if ("/unislicer/" in path or path.startswith("unislicer/")) and len(out) < n:
            out.append(name)
        frame = frame.f_back
        depth += 1
    if inner is None:
        return None
    if out and out[0] == inner:
        return " < ".join(out)
    return f"[{inner}]" + (" < " + " < ".join(out) if out else "")


def memory_mb() -> int | None:
    """This process's resident memory (Windows, Linux, macOS), without extra packages."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            # the handle's type declared: left to ctypes, the 64-bit pseudo handle was cut to 32 bits and the
            # call failed (no memory in the log on Windows)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            get = getattr(k32, "K32GetProcessMemoryInfo", None)
            if get is None:
                get = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            get.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            get.restype = wintypes.BOOL
            if get(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return int(c.WorkingSetSize >> 20)
            return None
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) >> 10
    except Exception:
        pass
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(kb >> 20 if sys.platform == "darwin" else kb >> 10)
    except Exception:
        return None


def _cpu_times():
    """(idle, total) CPU time of the whole computer so far, or None."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
            return f(idle), f(kernel) + f(user)              # kernel time includes the idle time
        with open("/proc/stat", encoding="ascii") as fh:
            v = [int(x) for x in fh.readline().split()[1:]]
        return v[3] + (v[4] if len(v) > 4 else 0), sum(v)
    except Exception:
        return None


class StallLog:
    """Watches the window's thread from a thread of its own (a heartbeat on a short timer); a stall is
    written when the window answers again."""

    def __init__(self, folder: str, state=None, threshold: float = THRESHOLD):
        self.path = os.path.join(folder, LOG_NAME)
        self.threshold = threshold
        self.state = state                  # () -> dict of plate numbers (models, faces, ...), window thread
        self._beat = time.monotonic()
        self._main = threading.main_thread().ident
        self._click = ("", 0.0)             # (what was clicked, when)
        self._snapshot: dict = {}
        self._stop = threading.Event()
        self._timer = None
        self._lock = threading.Lock()

    # -------------------------------------------------------------- window thread
    def start(self, parent) -> None:
        from PySide6.QtCore import QCoreApplication, QTimer
        self._timer = QTimer(parent)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._heartbeat)
        self._timer.start()
        app = QCoreApplication.instance()
        if app is not None:
            self._filter = _ClickFilter(self)
            app.installEventFilter(self._filter)
        threading.Thread(target=self._watch, name="stall-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._timer is not None:
            self._timer.stop()

    def clicked(self, what: str) -> None:
        self._click = (_clean(what), time.monotonic())

    def _heartbeat(self) -> None:
        self._beat = time.monotonic()
        # what the plate is like now, for the stall lines (read here, on the window's thread)
        if self.state is not None:
            try:
                self._snapshot = dict(self.state())
            except Exception:
                pass

    # -------------------------------------------------------------- watching thread
    def _cpu_busy(self) -> int | None:
        """The whole computer's CPU use over the last second or so, in percent (other programs too: a
        computer at 100% - left-over processes, a scan - stalls the window whatever it does)."""
        now = _cpu_times()
        prev, self._cpu_prev = getattr(self, "_cpu_prev", None), now
        if now is None or prev is None or now[1] <= prev[1]:
            return getattr(self, "_cpu_last", None)
        self._cpu_last = int(round(100.0 * (1.0 - (now[0] - prev[0]) / (now[1] - prev[1]))))
        return self._cpu_last

    def _watch(self) -> None:
        stalled_since = None
        next_cpu = 0.0
        where: collections.Counter = collections.Counter()
        others: collections.Counter = collections.Counter()
        while not self._stop.wait(_SAMPLE):
            now = time.monotonic()
            if now >= next_cpu:                     # the computer's load, every second
                self._cpu = self._cpu_busy()
                next_cpu = now + 1.0
            gap = now - self._beat
            if gap > self.threshold:
                if stalled_since is None:
                    stalled_since = self._beat
                    where = collections.Counter()
                    others = collections.Counter()
                frames = sys._current_frames()
                frame = frames.get(self._main)
                k = _frames(frame) if frame is not None else None
                if k:
                    where[k] += 1
                # what the other threads run meanwhile: one of them holding the interpreter stalls the window
                # while its own thread is in Qt (the first log: 43 s of stalls "in app.run")
                names = {t.ident: t.name for t in threading.enumerate()}
                for tid, fr in frames.items():
                    if tid in (self._main, threading.get_ident()):
                        continue
                    o = _frames(fr, 3)
                    if o and not o.startswith("[threading.py:wait]") and "queue.py:get" not in o:
                        others[f"{names.get(tid, '?')}: {o}"] += 1
            elif stalled_since is not None:
                length = self._beat - stalled_since
                if length < 120:                    # not a computer that slept
                    self._write(stalled_since, length, where, others)
                stalled_since = None

    def _write(self, since: float, length: float, where: collections.Counter,
               others: collections.Counter | None = None) -> None:
        click, at = self._click
        total = sum(where.values()) or 1
        line = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - (time.monotonic() - since))),
            "ms": int(length * 1000),
            "doing": [{"share": round(n / total, 2), "where": k} for k, n in where.most_common(3)],
            "meanwhile": [{"share": round(n / total, 2), "where": k} for k, n in (others if others is not None else collections.Counter()).most_common(3)],
            "last_click": click if click and since - at < 30 else "",
            "click_age_s": round(since - at, 1) if click and since - at < 30 else None,
            "mem_mb": memory_mb(),
            "cpu_pct": getattr(self, "_cpu", None),
            **self._snapshot,
        }
        try:
            from .. import __version__
            line["version"] = __version__
        except Exception:
            pass
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                if os.path.getsize(self.path) > MAX_BYTES:
                    with open(self.path, encoding="utf-8") as fh:
                        lines = fh.readlines()
                    with open(self.path, "w", encoding="utf-8") as fh:
                        fh.writelines(lines[len(lines) // 2:])
            except OSError:
                pass


class _ClickFilter:
    """Notes the button or menu item the user pressed (its text only), for the stall lines."""

    def __new__(cls, log):
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QAbstractButton

        class Filter(QObject):
            def eventFilter(self, obj, ev):
                try:
                    t = ev.type()
                    if t == QEvent.MouseButtonRelease and isinstance(obj, QAbstractButton):
                        log.clicked(obj.text() or obj.toolTip() or obj.objectName())
                    elif t == QEvent.MouseButtonPress and type(obj).__name__ == "Viewer":
                        log.clicked("3D view")
                    elif t == QEvent.KeyPress:
                        from PySide6.QtGui import QKeySequence
                        seq = QKeySequence(ev.keyCombination()).toString()
                        bare = seq.replace("Shift+", "")
                        if len(bare) > 1:             # a shortcut (Ctrl+Z, Delete, F1) - never typed text
                            log.clicked("key " + seq)
                except Exception:
                    pass
                return False
        return Filter()
