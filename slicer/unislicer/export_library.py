"""The central print library: every exported print file goes to <library>/<file name>/<file name>.<ext>."""
from __future__ import annotations

import os
import re

FOLDER_NAME = "UniSlicer Prints"     # the fallback under Documents
APP_FOLDER = "Prints"                 # inside the folder UniSlicer is installed in
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def documents_dir() -> str:
    """The user's Documents folder (Windows: the real one, even when moved to OneDrive / another drive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # CSIDL_PERSONAL
                return buf.value
        except Exception:
            pass
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return docs if os.path.isdir(docs) else home


def app_dir() -> str:
    """The folder UniSlicer runs from: the install folder (UniSlicer.exe), or the source tree."""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def writable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".unislicer_write_test")
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def default_library() -> str:
    """<install folder>/Prints (the installer makes it writable); Documents/UniSlicer Prints if the
    install folder cannot be written to (e.g. a Program Files install from an older installer)."""
    folder = os.path.join(app_dir(), APP_FOLDER)
    return folder if writable(folder) else os.path.join(documents_dir(), FOLDER_NAME)


def safe_name(name: str, fallback: str = "print") -> str:
    """A file / folder name that works on Windows, macOS and Linux."""
    base = os.path.splitext(os.path.basename(str(name or "")))[0] if str(name or "").lower().endswith(
        (".stl", ".obj", ".3mf", ".ply", ".step", ".stp")) else os.path.basename(str(name or ""))
    base = _BAD.sub("_", base).strip().rstrip(". ")
    if not base:
        base = fallback
    if base.upper().split(".")[0] in _RESERVED:
        base = "_" + base
    return base[:120]


def target_path(library: str, name: str, ext: str) -> str:
    """<library>/<name>/<name><ext>; an existing file is kept and the new one becomes '<name> (2)<ext>'..."""
    name = safe_name(name)
    ext = ext if ext.startswith(".") else "." + ext
    folder = os.path.join(library, name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ext)
    k = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{name} ({k}){ext}")
        k += 1
    return path
