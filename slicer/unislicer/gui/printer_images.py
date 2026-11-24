"""Pictures of the printers for the printer picker, so a look-alike name (Sonic XL 4K 2020 / 2022 / Plus) is not
picked by mistake: a lab chose the 2020 profile for its XL 4K Plus and every part printed 4% too large.

Each printer shows, in this order: a photo the lab set for it (the maker's product photo, or one taken of the
machine itself - kept in the user folder), a photo shipped with the program (gui/brand/printers/<id>.png), or a
drawing made here - the machine's outline in its maker's colour, as wide as its build plate, with the model name
written on its front."""
from __future__ import annotations

import os
import re
import shutil

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap

PHOTO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand", "printers")

# the cover / accent colour of each maker's machines (Phrozen and Formlabs: amber and orange UV covers)
VENDOR_COLOURS = {"phrozen": "#f0a12c", "formlabs": "#f26b21", "elegoo": "#e8432f", "anycubic": "#2f7de8",
                  "asiga": "#4aa3df", "prusa": "#fa6831", "stratasys": "#8b8f99", "creality": "#2bb673"}
_cache: dict = {}


def photo_dir() -> str:
    from .. import profiles
    return os.path.join(profiles.USER_DIR, "printer_photos")


def _safe_id(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", pid or "")


def photo_path(pid: str) -> str | None:
    """The photo set for this printer (the lab's own first, then one shipped with the program), or None."""
    for folder in (photo_dir(), BUILTIN_DIR):
        for ext in PHOTO_EXTENSIONS:
            p = os.path.join(folder, _safe_id(pid) + ext)
            if os.path.isfile(p):
                return p
    return None


def set_photo(pid: str, source: str) -> str:
    """Keep a copy of ``source`` as this printer's photo (any earlier one goes). Returns the kept file."""
    ext = os.path.splitext(source)[1].lower()
    if ext not in PHOTO_EXTENSIONS or QPixmap(source).isNull():
        raise ValueError("not a picture")
    clear_photo(pid)
    os.makedirs(photo_dir(), exist_ok=True)
    dest = os.path.join(photo_dir(), _safe_id(pid) + ext)
    shutil.copyfile(source, dest)
    _cache.clear()
    return dest


def clear_photo(pid: str) -> None:
    for ext in PHOTO_EXTENSIONS:
        p = os.path.join(photo_dir(), _safe_id(pid) + ext)
        if os.path.isfile(p):
            os.remove(p)
    _cache.clear()


def has_own_photo(pid: str) -> bool:
    return any(os.path.isfile(os.path.join(photo_dir(), _safe_id(pid) + e)) for e in PHOTO_EXTENSIONS)


def short_label(printer: dict) -> str:
    """The model as written on the drawing: 'Phrozen Sonic XL 4K Plus (dental)' -> 'XL 4K PLUS',
    'Phrozen Sonic XL 4K (2020)' -> 'XL 4K 2020', 'Formlabs Form 4B (dental)' -> 'FORM 4B'."""
    name = str(printer.get("name") or "")
    vendor = str(printer.get("vendor") or "")
    if vendor and name.lower().startswith(vendor.lower()):
        name = name[len(vendor):]
    name = re.sub(r"\((dental|lcd|dlp)\)", "", name, flags=re.I)
    name = re.sub(r"\bSonic\b", "", name)
    name = name.replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", name).strip().upper() or vendor.upper()


def drawing(pid: str, printer: dict, size: int) -> QPixmap:
    """The machine's outline in its maker's colour, as wide as its plate, with its model on the front."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    vals = printer.get("values") or {}
    bed = float(vals.get("bed_x") or 150.0)
    width = max(0.58, min(0.96, 0.42 + bed / 420.0)) * size          # a bigger plate draws a wider machine
    x0 = (size - width) / 2
    colour = QColor(VENDOR_COLOURS.get(str(printer.get("vendor") or "").lower(), "#5b8def"))
    # the UV cover (translucent, the maker's colour) over the base
    cover = QRectF(x0 + width * 0.06, size * 0.06, width * 0.88, size * 0.50)
    path = QPainterPath()
    path.addRoundedRect(cover, size * 0.06, size * 0.06)
    c = QColor(colour)
    c.setAlpha(205)
    p.fillPath(path, c)
    p.setPen(QPen(colour.darker(140), max(1.0, size / 64)))
    p.drawPath(path)
    shine = QColor(255, 255, 255, 70)
    p.fillRect(QRectF(cover.x() + cover.width() * 0.12, cover.y() + size * 0.04, cover.width() * 0.10,
                      cover.height() * 0.72), shine)
    # the base, with the model written on its front
    base = QRectF(x0, size * 0.56, width, size * 0.38)
    bpath = QPainterPath()
    bpath.addRoundedRect(base, size * 0.05, size * 0.05)
    p.fillPath(bpath, QColor("#2a2e36"))
    p.setPen(QPen(QColor("#15171c"), max(1.0, size / 64)))
    p.drawPath(bpath)
    label = short_label(printer)
    font = QFont()
    font.setBold(True)
    px = max(6, int(size * 0.15))
    font.setPixelSize(px)
    p.setFont(font)
    room = base.adjusted(size * 0.04, size * 0.02, -size * 0.04, -size * 0.02)
    while px > 6 and p.fontMetrics().boundingRect(room.toRect(), Qt.AlignCenter | Qt.TextWordWrap, label).height() > room.height():
        px -= 1
        font.setPixelSize(px)
        p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(room, Qt.AlignCenter | Qt.TextWordWrap, label)
    p.end()
    return pm


def pixmap(pid: str, printer: dict, size: int) -> QPixmap:
    """This printer's picture at ``size`` px (square, transparent margins): its photo when it has one, else the
    drawing."""
    photo = photo_path(pid)
    stamp = os.path.getmtime(photo) if photo else None
    key = (pid, size, photo, stamp)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    out = None
    if photo:
        src = QPixmap(photo)
        if not src.isNull():
            out = QPixmap(size, size)
            out.fill(Qt.transparent)
            scaled = src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p = QPainter(out)
            p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
            p.end()
    if out is None:
        out = drawing(pid, printer, size)
    _cache[key] = out
    return out
