"""Filament panel (FDM), in the style of Creality Print: a coloured card per filament with its CFS / AMS slot
and type, a colour palette, and the slots read from the printer's CFS."""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from .. import filaments as fil
from . import theme
from .i18n import tr

TYPES = ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "PVA", "HIPS", "PLA-CF", "PETG-CF")
# a filament colour palette (common spool colours)
PALETTE = ("#FFFFFF", "#F5F5DC", "#C0C0C0", "#808080", "#3C3C3C", "#000000",
           "#FF0000", "#C0392B", "#FF7F00", "#FFC0CB", "#FF69B4", "#8B4513",
           "#FFD700", "#FFFF00", "#ADFF2F", "#00A651", "#006400", "#7F975F",
           "#00FFFF", "#1E90FF", "#0000FF", "#000080", "#8A2BE2", "#800080")


def text_colour(hex_colour: str) -> str:
    r, g, b = fil.rgb(hex_colour)
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else "#FFFFFF"


def swatch_icon(hex_colour: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(hex_colour))
    return QIcon(pm)


class FilamentCard(QFrame):
    """One filament: number, slot button, menu (colour / remove) and type."""

    def __init__(self, panel: "FilamentPanel", f: fil.Filament):
        super().__init__()
        self.panel, self.f = panel, f
        fg = text_colour(f.colour)
        self.setObjectName("FilamentCard")
        self.setStyleSheet(f"QFrame#FilamentCard {{ background:{f.colour}; border:1px solid rgba(128,128,128,0.55);"
                           f" border-radius:6px; }} QFrame#FilamentCard QLabel {{ color:{fg}; background:transparent; }}")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("fil_card_tip"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        num = QLabel(f"<b>{f.index + 1:02d}</b>")
        top.addWidget(num)
        top.addStretch(1)
        self.slot_btn = QToolButton()
        self.slot_btn.setText(f"CFS {f.slot}" if f.slot else tr("fil_slot_auto"))
        self.slot_btn.setToolTip(tr("fil_slot_tip").format(fil.slot_name(f.t)))
        self.slot_btn.setPopupMode(QToolButton.InstantPopup)
        self.slot_btn.setStyleSheet("QToolButton { background:rgba(255,255,255,0.85); color:#111; border:1px solid #888;"
                                    " border-radius:4px; padding:0 4px; font-size:11px; }")
        slot_menu = QMenu(self.slot_btn)
        auto = slot_menu.addAction(tr("fil_slot_auto_long").format(fil.slot_name(f.index)))
        auto.triggered.connect(lambda: panel.set_slot(f.index, ""))
        slot_menu.addSeparator()
        cfs = panel.cfs_by_slot()
        for unit in range(1, 5):
            sub = slot_menu if unit == 1 else slot_menu.addMenu(f"CFS {unit}")
            for c in "ABCD":
                name = f"{unit}{c}"
                info = cfs.get(name)
                label = name + (f"  ·  {info['type']} {info['name']}".rstrip() if info and info.get("loaded") else "")
                act = sub.addAction(swatch_icon(info["colour"]) if info and info.get("colour") else QIcon(), label)
                act.setCheckable(True)
                act.setChecked(f.slot == name)
                act.triggered.connect(lambda _=False, n=name: panel.set_slot(f.index, n))
        self.slot_btn.setMenu(slot_menu)
        top.addWidget(self.slot_btn)
        more = QToolButton()
        more.setText("•••")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet("QToolButton { background:rgba(255,255,255,0.85); color:#111; border:1px solid #888;"
                           " border-radius:4px; padding:0 3px; font-size:10px; } QToolButton::menu-indicator { image:none; }")
        menu = QMenu(more)
        menu.addAction(tr("fil_colour") + "…", self.pick_colour)
        menu.addMenu(self._palette_menu(menu))
        menu.addSeparator()
        rm = menu.addAction(theme.icon("trash", theme.DANGER), tr("fil_remove"), lambda: panel.remove(f.index))
        rm.setEnabled(len(panel.flist) > 1)
        more.setMenu(menu)
        top.addWidget(more)
        lay.addLayout(top)
        self.type_combo = QComboBox()
        self.type_combo.setEditable(True)
        self.type_combo.addItems(TYPES)
        self.type_combo.setCurrentText(f.type)
        self.type_combo.setStyleSheet("QComboBox { background:rgba(255,255,255,0.92); color:#111; border:1px solid #888;"
                                      " border-radius:4px; padding:1px 4px; }")
        # only finished edits (not every keystroke: the cards are rebuilt after each change)
        self.type_combo.activated.connect(lambda _i: panel.set_type(f.index, self.type_combo.currentText()))
        self.type_combo.lineEdit().editingFinished.connect(lambda: panel.set_type(f.index, self.type_combo.currentText()))
        lay.addWidget(self.type_combo)

    def _palette_menu(self, parent):
        sub = QMenu(tr("fil_palette"), parent)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for k, c in enumerate(PALETTE):
            b = QToolButton()
            b.setFixedSize(22, 22)
            b.setToolTip(c)
            b.setStyleSheet(f"QToolButton {{ background:{c}; border:1px solid #777; border-radius:3px; }}")
            b.clicked.connect(lambda _=False, c=c: (sub.close(), parent.close(), self.panel.set_colour(self.f.index, c)))
            grid.addWidget(b, k // 6, k % 6)
        act = QWidgetAction(sub)
        act.setDefaultWidget(grid_w)
        sub.addAction(act)
        return sub

    def pick_colour(self):
        c = QColorDialog.getColor(QColor(self.f.colour), self, tr("fil_colour"))
        if c.isValid():
            self.panel.set_colour(self.f.index, c.name().upper())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pick_colour()
        super().mousePressEvent(e)


class FilamentPanel(QFrame):
    """``valuesChanged(dict)``: setting overrides to apply (filament_count / colors / types / slots)."""
    valuesChanged = Signal(dict)
    syncRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.flist: list[fil.Filament] = []
        self.cfg = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon("spool", theme.TEXT_DIM, 16).pixmap(16, 16))
        head.addWidget(ic)
        title = QLabel(tr("fil_panel"))
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        for icon, tip, slot in (("wifi", tr("fil_sync_tip"), self.syncRequested.emit),
                                ("plus", tr("fil_add"), self.add), ("settings", tr("fil_edit"), None)):
            b = QToolButton()
            b.setIcon(theme.icon(icon, theme.TEXT_DIM, 16))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            if slot is not None:
                b.clicked.connect(slot)
            else:
                self.edit_btn = b
            head.addWidget(b)
        lay.addLayout(head)
        self.cfs_row = QHBoxLayout()
        self.cfs_row.setSpacing(4)
        lay.addLayout(self.cfs_row)
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        lay.addLayout(self.grid)
        self.hint = QLabel(tr("fil_panel_hint"))
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

    # ---------------------------------------------------------------- CFS read from the printer
    def _store(self) -> QSettings:
        return QSettings("UniSlicer", "UniSlicer")

    def cfs_slots(self) -> list[dict]:
        try:
            v = json.loads(str(self._store().value("cfs_slots", "[]")))
            return v if isinstance(v, list) else []
        except ValueError:
            return []

    def cfs_by_slot(self) -> dict:
        return {s.get("slot"): s for s in self.cfs_slots() if isinstance(s, dict)}

    def set_cfs_slots(self, slots: list[dict]):
        self._store().setValue("cfs_slots", json.dumps(slots))
        self.refresh(self.cfg)

    # ---------------------------------------------------------------- show
    def refresh(self, cfg):
        if cfg is None:
            return
        self.cfg = cfg
        self.flist = fil.filament_list(cfg)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        cols = 3
        for k, f in enumerate(self.flist):
            self.grid.addWidget(FilamentCard(self, f), k // cols, k % cols)
        while self.cfs_row.count():
            w = self.cfs_row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        slots = [s for s in self.cfs_slots() if s.get("slot") != "EXT"]
        if slots:
            lab = QLabel("CFS")
            lab.setObjectName("Faint")
            self.cfs_row.addWidget(lab)
            for s in slots[:16]:
                chip = QLabel(s["slot"])
                colour = s.get("colour") or "#555555"
                chip.setAlignment(Qt.AlignCenter)
                chip.setFixedSize(30, 20)
                chip.setStyleSheet(f"background:{colour}; color:{text_colour(colour)}; border-radius:4px;"
                                   f" border:1px solid #777; font-size:11px; font-weight:bold;")
                chip.setToolTip(f"{s['slot']}: {s.get('vendor', '')} {s.get('type', '')} {s.get('name', '')}".strip()
                                if s.get("loaded") else f"{s['slot']}: {tr('fil_empty')}")
                self.cfs_row.addWidget(chip)
            self.cfs_row.addStretch(1)
        self.hint.setVisible(len(self.flist) == 1)

    # ---------------------------------------------------------------- edits
    def _emit(self, flist: list[fil.Filament]):
        slots = [f.slot for f in flist]
        self.valuesChanged.emit({
            "filament_count": len(flist),
            "filament_colors": ";".join(f.colour for f in flist),
            "filament_types": ";".join(f.type for f in flist),
            "filament_slots": "" if not any(slots) else ";".join(slots),
        })

    def _copy(self):
        return [fil.Filament(f.index, f.colour, f.type, f.temp, f.first_temp, f.slot) for f in self.flist]

    def set_colour(self, index: int, colour: str):
        fl = self._copy()
        fl[index].colour = fil.normalise_colour(colour)
        self._emit(fl)

    def set_type(self, index: int, ftype: str):
        ftype = (ftype or "").strip().upper()
        if not ftype or index >= len(self.flist) or self.flist[index].type == ftype:
            return
        fl = self._copy()
        fl[index].type = ftype
        self._emit(fl)

    def set_slot(self, index: int, slot: str):
        fl = self._copy()
        fl[index].slot = slot
        info = self.cfs_by_slot().get(slot)
        if info and info.get("loaded"):           # take the colour and type of what is loaded there
            if info.get("colour"):
                fl[index].colour = info["colour"]
            if info.get("type"):
                fl[index].type = info["type"]
        self._emit(fl)

    def add(self):
        if len(self.flist) >= 16:
            return
        fl = self._copy()
        k = len(fl)
        fl.append(fil.Filament(k, fil.DEFAULT_COLOURS[k % len(fil.DEFAULT_COLOURS)], fl[-1].type if fl else "PLA",
                               fl[-1].temp if fl else 210, fl[-1].first_temp if fl else 215))
        self._emit(fl)

    def remove(self, index: int):
        if len(self.flist) <= 1:
            return
        fl = self._copy()
        del fl[index]
        for k, f in enumerate(fl):
            f.index = k
        self._emit(fl)

    def use_printer_slots(self):
        """One filament per loaded CFS slot, in slot order, with its colour and type."""
        loaded = [s for s in self.cfs_slots() if s.get("loaded") and s.get("slot") != "EXT"]
        if not loaded:
            return False
        fl = [fil.Filament(k, s.get("colour") or "#FFFFFF", s.get("type") or "PLA", 0, 0, s["slot"])
              for k, s in enumerate(loaded)]
        self._emit(fl)
        return True
