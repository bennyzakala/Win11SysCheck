"""Dental shades window: pick the parts (teeth, gingiva, crowns, bridges) and give each a shade."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import dental_shades as ds
from . import i18n, theme
from .i18n import tr


def swatch(rgb, size: int = 18) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor.fromRgbF(*rgb) if rgb else QColor(200, 200, 200))
    return QIcon(pm)


class ShadeDialog(QDialog):
    applyRequested = Signal(str, list)        # shade code, [(object, region index or None)]
    splitRequested = Signal()

    def __init__(self, objects: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("shade_title"))
        self.resize(760, 560)
        lay = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel(f"<b>{tr('shade_parts')}</b>"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        left.addWidget(self.list, 1)
        quick = QHBoxLayout()
        for key, kind in (("shade_sel_teeth", "tooth"), ("shade_sel_gingiva", "gingiva"), ("shade_sel_all", None)):
            b = QPushButton(tr(key))
            b.clicked.connect(lambda _=False, k=kind: self._select_kind(k))
            quick.addWidget(b)
        left.addLayout(quick)
        split = QPushButton(theme.icon("grid", theme.TEXT, 16), tr("denture_split_btn"))
        split.setToolTip(tr("denture_split_tip"))
        split.clicked.connect(self.splitRequested.emit)
        left.addWidget(split)
        lay.addLayout(left, 2)

        right = QVBoxLayout()
        self.tabs = QTabWidget()
        # teeth: VITA rows
        teeth = QWidget()
        grid = QGridLayout(teeth)
        grid.setSpacing(6)
        rows = {"BL": [], "A": [], "B": [], "C": [], "D": []}
        for s in ds.TOOTH_SHADES:
            rows["BL" if s.code.startswith("TBL") else s.code[1]].append(s)
        for r, (group, shades) in enumerate(rows.items()):
            lab = QLabel(tr("shade_bleach") if group == "BL" else group)
            lab.setObjectName("Faint")
            grid.addWidget(lab, r, 0)
            for c, s in enumerate(shades):
                b = QToolButton()
                b.setText(s.code)
                b.setIcon(swatch(ds.display_rgb(s.code), 28))
                b.setIconSize(QSize(28, 28))
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setMinimumWidth(62)
                b.setToolTip(s.he if i18n.LANG == "he" else s.en)
                b.clicked.connect(lambda _=False, code=s.code: self._apply(code))
                grid.addWidget(b, r, c + 1)
        note = QLabel(tr("shade_teeth_note"))
        note.setWordWrap(True)
        note.setObjectName("Faint")
        grid.addWidget(note, len(rows), 0, 1, 7)
        grid.setRowStretch(len(rows) + 1, 1)
        self.tabs.addTab(teeth, tr("shade_tab_teeth"))
        # gingiva: shade list + translucency
        gum = QWidget()
        gl = QVBoxLayout(gum)
        trow = QHBoxLayout()
        trow.addWidget(QLabel(tr("shade_translucency")))
        self.trans = QComboBox()
        for t in ds.TRANSLUCENCY:
            self.trans.addItem(tr(f"shade_trans_{t}"), t)
        self.trans.setCurrentIndex(1)
        self.trans.currentIndexChanged.connect(self._fill_gingiva)
        trow.addWidget(self.trans, 1)
        gl.addLayout(trow)
        self.gum_grid = QGridLayout()
        gl.addLayout(self.gum_grid)
        gnote = QLabel(tr("shade_gingiva_note"))
        gnote.setWordWrap(True)
        gnote.setObjectName("Faint")
        gl.addWidget(gnote)
        gl.addStretch(1)
        self.tabs.addTab(gum, tr("shade_tab_gingiva"))
        right.addWidget(self.tabs, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        close = QPushButton(tr("close"))
        close.clicked.connect(self.accept)
        right.addWidget(close, 0, Qt.AlignRight)
        lay.addLayout(right, 3)

        self._fill_gingiva()
        self.set_objects(objects)

    # ---------------------------------------------------------------- parts list
    def set_objects(self, objects: list):
        """One row per shaded region of each model (gingiva, tooth 1, ...), or per model without regions."""
        self.objects = list(objects)
        self.entries = []
        self.list.clear()
        for o in self.objects:
            if o.regions:
                for k, r in enumerate(o.regions):
                    self.entries.append((o, k))
                    label = f"{o.name} › {self._region_label(r['name'])}   ·   {r['shade'] or tr('shade_none')}"
                    self.list.addItem(QListWidgetItem(swatch(ds.display_rgb(r["shade"])), label))
            else:
                self.entries.append((o, None))
                self.list.addItem(QListWidgetItem(swatch(ds.display_rgb(o.material)),
                                                  f"{o.name}   ·   {o.material or tr('shade_none')}"))
        self.list.selectAll()

    @staticmethod
    def _region_label(name: str) -> str:
        if name == "Gingiva":
            return tr("region_gingiva")
        if name.startswith("Tooth "):
            return tr("region_tooth").format(name.split(" ", 1)[1])
        return name

    def _entry_kind(self, o, r):
        shade = o.regions[r]["shade"] if r is not None else o.material
        name = o.regions[r]["name"] if r is not None else o.name
        s = ds.find(shade)
        if s is not None:
            return s.kind
        low = name.lower()
        return "gingiva" if "gingiva" in low or "base" in low else "tooth"

    def _select_kind(self, kind):
        self.list.clearSelection()
        for i, (o, r) in enumerate(self.entries):
            if kind is None or self._entry_kind(o, r) == kind:
                self.list.item(i).setSelected(True)

    def _chosen(self) -> list:
        rows = sorted(self.list.row(it) for it in self.list.selectedItems())
        return [self.entries[r] for r in rows]

    def _apply(self, code: str):
        objs = self._chosen()
        if not objs:
            self.info.setText(tr("shade_pick_parts"))
            return
        self.applyRequested.emit(code, objs)
        rows = [self.list.row(it) for it in self.list.selectedItems()]
        self.set_objects(self.objects)
        self.list.clearSelection()
        for r in rows:
            self.list.item(r).setSelected(True)
        self.info.setText(tr("shade_applied").format(code, len(objs)))

    def _fill_gingiva(self):
        while self.gum_grid.count():
            w = self.gum_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        level = self.trans.currentData() or 2
        for k, (name, he, _rgb) in enumerate(ds.GINGIVA_BASE):
            code = ds.gingiva_code(name, level)
            b = QPushButton(swatch(ds.display_rgb(code), 22), he if i18n.LANG == "he" else name)
            b.setIconSize(QSize(22, 22))
            b.setToolTip(code)
            b.clicked.connect(lambda _=False, c=code: self._apply(c))
            self.gum_grid.addWidget(b, k // 3, k % 3)
