"""Add parts: after choosing model files, set each part's type (orientation preset), supports, placement,
units and quantity before it goes on the plate. Types are recognised from the file name (3Shape / exocad
names, lab codes) and, when the name says nothing, from the shape."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QMenu, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import dental_cad
from . import i18n
from .i18n import tr

# part type -> (Hebrew, English) name; "model_hollow" is a model hollowed by itself, "asis" changes nothing
KINDS = ("model", "model_hollow", "die", "crown", "gingiva", "splint", "guide", "denture", "asis")
KIND_NAMES = {
    "model": ("מודל", "Model"), "model_hollow": ("מודל (חלול)", "Model (hollow)"), "die": ("Die (גדם)", "Die"),
    "crown": ("כתר / גשר", "Crown / bridge"), "gingiva": ("מסכת חניכיים", "Gingiva mask"),
    "splint": ("סד / Night guard", "Splint / night guard"), "guide": ("מדריך כירורגי", "Surgical guide"),
    "denture": ("תותבת", "Denture"), "asis": ("כמו בקובץ", "As in the file"),
}
SUPPORTS = ("auto", "generate", "none")
PLACEMENTS = ("auto", "keep")
UNITS = ("mm", "inch")
COLS = ("name", "kind", "supports", "placement", "unit", "qty")


def kind_name(kind: str) -> str:
    return i18n.pick(*KIND_NAMES.get(kind, (kind, kind)))


def classify(path: str, mesh=None, hollow_models: bool = False) -> str:
    """The part type of a file: from its name (and a CAD order file beside it), else from its shape."""
    kind = dental_cad.detect_indication(path, dental_cad.order_text_for(path))
    if kind == "unknown":
        kind = dental_cad.indication_from_shape(mesh) if mesh is not None else "asis"
    if kind == "model" and hollow_models:
        return "model_hollow"
    return kind if kind in KINDS else "asis"


class AddPartsDialog(QDialog):
    """``load(path)`` returns the file's first mesh (or None); used to recognise a part by its shape."""

    def __init__(self, paths: list[str], store, load=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("ap_title"))
        self.resize(940, 380)
        self.store = store
        self.load = load
        self.meshes: dict = {}                  # path -> mesh read while classifying (re-used on open)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([tr(f"ap_col_{c}") for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(COLS)):
            head.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("ap_select_files"))
        add.clicked.connect(self._select_files)
        recent = QPushButton(tr("ap_recent"))
        recent.clicked.connect(lambda: self._recent_menu(recent))
        self.orient = QComboBox()
        self.orient.addItem(tr("ap_set_selected"), None)
        for k in KINDS:
            self.orient.addItem(kind_name(k), k)
        self.orient.activated.connect(self._apply_to_selected)
        remove = QPushButton(tr("ap_remove"))
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton(tr("ap_clear"))
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        for w in (add, recent, self.orient, remove, clear):
            row.addWidget(w)
        lay.addLayout(row)
        bottom = QHBoxLayout()
        defaults = QPushButton(tr("ap_defaults") + "…")
        defaults.clicked.connect(self._defaults)
        self.auto = QCheckBox(tr("ap_auto_classify"))
        self.auto.setChecked(str(store.value("ap_auto", "true")) == "true")
        self.auto.toggled.connect(self._reclassify)
        open_btn = QPushButton(tr("ap_open"))
        open_btn.setObjectName("Primary")
        open_btn.setDefault(True)
        open_btn.clicked.connect(self.accept)
        close = QPushButton(tr("close"))
        close.clicked.connect(self.reject)
        bottom.addWidget(defaults)
        bottom.addWidget(self.auto)
        bottom.addStretch(1)
        bottom.addWidget(open_btn)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self.add_paths(paths)

    # ------------------------------------------------------------ defaults
    def _pref(self, key: str, default: str) -> str:
        return str(self.store.value(f"ap_{key}", default))

    def _defaults(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("ap_defaults"))
        form = QFormLayout(dlg)
        hollow = QCheckBox(tr("ap_hollow_models"))
        hollow.setChecked(self._pref("hollow", "false") == "true")
        place = QComboBox()
        for p in PLACEMENTS:
            place.addItem(tr(f"ap_place_{p}"), p)
        place.setCurrentIndex(max(0, place.findData(self._pref("placement", "auto"))))
        unit = QComboBox()
        for u in UNITS:
            unit.addItem(u, u)
        unit.setCurrentIndex(max(0, unit.findData(self._pref("unit", "mm"))))
        form.addRow(hollow)
        form.addRow(tr("ap_col_placement"), place)
        form.addRow(tr("ap_col_unit"), unit)
        ok = QPushButton(tr("ok") if "ok" in i18n.STRINGS else "OK")
        ok.clicked.connect(dlg.accept)
        form.addRow(ok)
        if dlg.exec() != QDialog.Accepted:
            return
        self.store.setValue("ap_hollow", "true" if hollow.isChecked() else "false")
        self.store.setValue("ap_placement", place.currentData())
        self.store.setValue("ap_unit", unit.currentData())
        for r in range(self.table.rowCount()):
            self._combo(r, "placement").setCurrentIndex(PLACEMENTS.index(place.currentData()))
            self._combo(r, "unit").setCurrentIndex(UNITS.index(unit.currentData()))
        self._reclassify()

    # ------------------------------------------------------------ rows
    def _combo(self, r: int, col: str) -> QComboBox:
        return self.table.cellWidget(r, COLS.index(col))

    def _kind_for(self, path: str) -> str:
        if not self.auto.isChecked():
            return "asis"
        kind = dental_cad.detect_indication(path, dental_cad.order_text_for(path))
        mesh = None
        if kind == "unknown" and self.load is not None:
            mesh = self.meshes.get(path)
            if mesh is None:
                try:
                    mesh = self.load(path)
                except Exception:                    # unreadable here: the loader reports it on Open
                    mesh = None
                self.meshes[path] = mesh
        return classify(path, mesh, self._pref("hollow", "false") == "true")

    def add_paths(self, paths: list[str]):
        have = {self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())}
        for path in paths:
            if path in have:
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QTableWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.table.setItem(r, 0, item)
            kind = QComboBox()
            for k in KINDS:
                kind.addItem(kind_name(k), k)
            kind.setCurrentIndex(KINDS.index(self._kind_for(path)))
            self.table.setCellWidget(r, 1, kind)
            sup = QComboBox()
            for s in SUPPORTS:
                sup.addItem(tr(f"ap_sup_{s}"), s)
            self.table.setCellWidget(r, 2, sup)
            place = QComboBox()
            for p in PLACEMENTS:
                place.addItem(tr(f"ap_place_{p}"), p)
            place.setCurrentIndex(max(0, place.findData(self._pref("placement", "auto"))))
            self.table.setCellWidget(r, 3, place)
            unit = QComboBox()
            for u in UNITS:
                unit.addItem(u, u)
            unit.setCurrentIndex(max(0, unit.findData(self._pref("unit", "mm"))))
            self.table.setCellWidget(r, 4, unit)
            qty = QSpinBox()
            qty.setRange(1, 200)
            self.table.setCellWidget(r, 5, qty)
            have.add(path)

    def _reclassify(self, *_):
        self.store.setValue("ap_auto", "true" if self.auto.isChecked() else "false")
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(Qt.UserRole)
            self._combo(r, "kind").setCurrentIndex(KINDS.index(self._kind_for(path)))

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def _apply_to_selected(self, index: int):
        kind = self.orient.itemData(index)
        if kind is not None:
            for r in self._selected_rows() or range(self.table.rowCount()):
                self._combo(r, "kind").setCurrentIndex(KINDS.index(kind))
        self.orient.setCurrentIndex(0)

    def _remove_selected(self):
        for r in reversed(self._selected_rows()):
            self.table.removeRow(r)

    def _select_files(self):
        last = str(self.store.value("last_dir", os.path.expanduser("~")))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("ap_select_files"), last, tr("file_filter"))
        if paths:
            self.store.setValue("last_dir", os.path.dirname(paths[0]))
            self.add_paths(paths)

    def _recent_menu(self, button):
        menu = QMenu(self)
        try:
            recent = [p for p in json.loads(self.store.value("recent_files", "[]")) if os.path.exists(p)]
        except ValueError:
            recent = []
        for p in recent:
            menu.addAction(os.path.basename(p), lambda p=p: self.add_paths([p])).setToolTip(p)
        if not recent:
            menu.addAction(tr("recent_empty")).setEnabled(False)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    # ------------------------------------------------------------ result
    def parts(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            out.append({"path": self.table.item(r, 0).data(Qt.UserRole),
                        "kind": self._combo(r, "kind").currentData(),
                        "supports": self._combo(r, "supports").currentData(),
                        "placement": self._combo(r, "placement").currentData(),
                        "unit": self._combo(r, "unit").currentData(),
                        "qty": self.table.cellWidget(r, 5).value()})
        return out
