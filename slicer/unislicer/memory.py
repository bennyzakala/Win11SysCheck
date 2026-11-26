"""Giving memory back to the system.

Python and numpy free memory into the C runtime's heap, which keeps much of it for later instead of handing it
back: after a plate was sliced and cleared, ~900 MB stayed "in use" in Task Manager that nothing held any more
(asked to give it back, the program went from 1.9 GB to 1.0 GB). The caches of a plate that is gone are
cleared, and the heap is asked to return what is free."""
from __future__ import annotations

import ctypes
import gc
import sys


def forget_plate_caches() -> None:
    """The work kept for parts no longer on the plate: ray casters, support structures, slice results."""
    from . import sla, sla_supports
    with sla_supports._MEMO_LOCK:
        sla_supports._CASTERS.clear()
        sla_supports._STRUCTURES.clear()
        sla_supports._OUTLINES.clear()
    sla.clear_cache()


def give_back() -> None:
    """Return the heap's free memory to the system (cheap: tens of ms). Never fails."""
    try:
        gc.collect()
        if sys.platform == "win32":
            for lib in ("ucrtbase", "msvcrt"):
                try:
                    getattr(ctypes.cdll, lib)._heapmin()
                    break
                except Exception:
                    continue
        elif sys.platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
