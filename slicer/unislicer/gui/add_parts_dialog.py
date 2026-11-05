"""Add parts: after choosing model files, set each part's placement, units and quantity before it goes on
the plate. The parts keep their orientation and get no supports here: that stays with the user (and the
assistant), as with a plain Open."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QMenu, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from . import i18n
from .i18n import tr

PLACEMENTS = ("auto", "keep")
UNITS = ("mm", "inch")
COLS = ("name", "placement", "unit", "qty")


class AddPartsDialog(QDialog):
    """``load`` is accepted for compatibility and not used."""

    def __init__(self, paths: list[str], store, load=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("ap_title"))
        self.resize(720, 380)
        self.store = store
        self.load = load
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
        remove = QPushButton(tr("ap_remove"))
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton(tr("ap_clear"))
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        for w in (add, recent, remove, clear):
            row.addWidget(w)
        lay.addLayout(row)
        bottom = QHBoxLayout()
        defaults = QPushButton(tr("ap_defaults") + "…")
        defaults.clicked.connect(self._defaults)
        open_btn = QPushButton(tr("ap_open"))
        open_btn.setObjectName("Primary")
        open_btn.setDefault(True)
        open_btn.clicked.connect(self.accept)
        close = QPushButton(tr("close"))
        close.clicked.connect(self.reject)
        bottom.addWidget(defaults)
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
        place = QComboBox()
        for p in PLACEMENTS:
            place.addItem(tr(f"ap_place_{p}"), p)
        place.setCurrentIndex(max(0, place.findData(self._pref("placement", "auto"))))
        unit = QComboBox()
        for u in UNITS:
            unit.addItem(u, u)
        unit.setCurrentIndex(max(0, unit.findData(self._pref("unit", "mm"))))
        form.addRow(tr("ap_col_placement"), place)
        form.addRow(tr("ap_col_unit"), unit)
        ok = QPushButton(tr("ok") if "ok" in i18n.STRINGS else "OK")
        ok.clicked.connect(dlg.accept)
        form.addRow(ok)
        if dlg.exec() != QDialog.Accepted:
            return
        self.store.setValue("ap_placement", place.currentData())
        self.store.setValue("ap_unit", unit.currentData())
        for r in range(self.table.rowCount()):
            self._combo(r, "placement").setCurrentIndex(PLACEMENTS.index(place.currentData()))
            self._combo(r, "unit").setCurrentIndex(UNITS.index(unit.currentData()))

    # ------------------------------------------------------------ rows
    def _combo(self, r: int, col: str) -> QComboBox:
        return self.table.cellWidget(r, COLS.index(col))

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
            place = QComboBox()
            for p in PLACEMENTS:
                place.addItem(tr(f"ap_place_{p}"), p)
            place.setCurrentIndex(max(0, place.findData(self._pref("placement", "auto"))))
            self.table.setCellWidget(r, 1, place)
            unit = QComboBox()
            for u in UNITS:
                unit.addItem(u, u)
            unit.setCurrentIndex(max(0, unit.findData(self._pref("unit", "mm"))))
            self.table.setCellWidget(r, 2, unit)
            qty = QSpinBox()
            qty.setRange(1, 200)
            self.table.setCellWidget(r, 3, qty)
            have.add(path)

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

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
                        "placement": self._combo(r, "placement").currentData(),
                        "unit": self._combo(r, "unit").currentData(),
                        "qty": self.table.cellWidget(r, 3).value()})
        return out
