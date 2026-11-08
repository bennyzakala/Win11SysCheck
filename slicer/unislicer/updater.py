"""Updates: is a newer build of UniSlicer published, and fetching its installer.

The Windows build stamps the commit it was built from into ``unislicer/_build.py``; the published build
("unislicer-latest" release on GitHub) names its commit too. A different commit there means a newer build.
Only the release's public description is read - nothing about the user or their files is sent.
"""
from __future__ import annotations

import json
import os
import urllib.request

REPO = "bennyzakala/Win11SysCheck"
TAG = "unislicer-latest"
API = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
ASSET = "UniSlicer-Setup.exe"


def build_commit() -> str:
    """The commit this program was built from ("" when run from source: never offered an update)."""
    try:
        from . import _build
        return str(getattr(_build, "COMMIT", "") or "")
    except Exception:
        return ""


def latest(timeout: float = 10.0) -> dict | None:
    """The published build: {"commit", "date", "url", "size"}, or None when it cannot be read."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "UniSlicer-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    if asset is None:
        return None
    digest = str(asset.get("digest") or "")
    return {"commit": str(data.get("target_commitish", "")), "date": str(data.get("published_at", ""))[:10],
            "url": asset.get("browser_download_url", ""), "size": int(asset.get("size") or 0),
            "sha256": digest[7:].lower() if digest.lower().startswith("sha256:") else ""}


def newer(info: dict | None, current: str) -> bool:
    """True when ``info`` is a different published build than this one (and this one knows its commit)."""
    if not info or not current or not info.get("commit") or not info.get("url"):
        return False
    a, b = info["commit"].lower(), current.lower()
    return not (a.startswith(b) or b.startswith(a))


def download(info: dict, dest_dir: str, progress=None, cancelled=None) -> str:
    """Fetch the installer into ``dest_dir``; returns its path. ``progress(fraction)`` while it comes. The file
    must have the SHA-256 fingerprint GitHub lists for it (when it lists one) and the listed size; with
    neither to check against it is refused - it is run with administrator rights afterwards."""
    import hashlib
    if not info.get("sha256") and not info.get("size"):
        raise OSError("the update cannot be checked (no size or fingerprint published)")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, ASSET)
    part = path + ".part"
    req = urllib.request.Request(info["url"], headers={"User-Agent": "UniSlicer-updater"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        sha = hashlib.sha256()
        while True:
            if cancelled is not None and cancelled():
                raise InterruptedError
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if progress is not None and total:
                progress(min(1.0, done / total))
    if info.get("size") and os.path.getsize(part) != info["size"]:
        os.remove(part)
        raise OSError("the download is incomplete")
    if info.get("sha256") and sha.hexdigest() != info["sha256"]:
        os.remove(part)
        raise OSError("the download does not match the published file")
    os.replace(part, path)
    return path
