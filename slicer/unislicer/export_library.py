"""The central print library: every exported print file goes to <library>/<file name>/<file name>.<ext>."""
from __future__ import annotations

import os
import re

FOLDER_NAME = "UniSlicer Prints"
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


def default_library() -> str:
    return os.path.join(documents_dir(), FOLDER_NAME)


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
