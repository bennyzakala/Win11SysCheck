#!/usr/bin/env python3
"""Double-click / run this file to start UniSlicer (also the entry point of the Windows build)."""
import multiprocessing
import os
import sys
import tempfile

if __name__ == "__main__":
    # in the packaged app, worker processes start this executable again: hand them over right away
    multiprocessing.freeze_support()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A windowed (no console) build has no stdout/stderr: send output to a log file instead.
if sys.stdout is None or sys.stderr is None:
    # a relaunch with software OpenGL appends to the log of the first attempt
    _mode = "a" if os.environ.get("UNISLICER_SOFTWARE_GL") else "w"
    _log = open(os.path.join(tempfile.gettempdir(), "unislicer.log"), _mode, encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from unislicer.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
