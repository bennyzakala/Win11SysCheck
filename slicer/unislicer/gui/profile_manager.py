"""Library window for printer and material (resin / filament) profiles: browse, edit, add, import, export."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import profiles
from ..settings import SCHEMA, Config, defaults
from . import i18n, theme
from .i18n import tr
from .settings_panel import SettingRow

# settings that belong to each kind of profile
KEYS = {
    ("materials", "sla"): ("material", "exposure"),
    ("materials", "fdm"): ("material",),
    ("printers", "sla"): ("printer",),
    ("printers", "fdm"): ("printer", "gcode"),
}


class ProfileManager(QDialog):
    def __init__(self, kind: str, technology: str, current: str | None, parent=None):
        super().__init__(parent)
        self.kind, self.technology = kind, technology
        self.selected_id: str | None = None
        self._pid: str | None = None
        self._values: dict = {}
        self._dirty = False
        title = {"materials": tr("resin_library") if technology == "sla" else tr("filament_library"),
                 "printers": tr("printer_library")}[kind]
        self.setWindowTitle(title)
        self.resize(1040, 720)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_tree)
        left.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(300)
        self.tree.currentItemChanged.connect(self._on_pick)
        self.tree.setStyleSheet(f"QTreeWidget {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
                                f"border-radius: 10px; padding: 6px; }} QTreeWidget::item {{ padding: 5px 4px; }} "
                                f"QTreeWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; border-radius: 6px; }}")
        left.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for text, icon_name, slot in ((tr("pm_new"), "plus", self._new), (tr("pm_duplicate"), "copy", self._duplicate),
                                      (tr("pm_import"), "open", self._import), (tr("pm_export"), "download", self._export)):
            b = QPushButton(theme.icon(icon_name, theme.TEXT, 15), text)
            b.clicked.connect(slot)
            row.addWidget(b)
        left.addLayout(row)
        root.addLayout(left, 0)

        right = QVBoxLayout()
        head = QFrame()
        head.setObjectName("Card")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(14, 12, 14, 12)
        names = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("pm_name"))
        self.name_edit.textEdited.connect(self._mark_dirty)
        self.vendor_edit = QLineEdit()
        self.vendor_edit.setPlaceholderText(tr("pm_vendor"))
        self.vendor_edit.setFixedWidth(220)
        self.vendor_edit.textEdited.connect(self._mark_dirty)
        names.addWidget(self.name_edit, 1)
        names.addWidget(self.vendor_edit)
        hl.addLayout(names)
        self.badge = QLabel("")
        self.badge.setObjectName("Faint")
        self.badge.setWordWrap(True)
        hl.addWidget(self.badge)
        right.addWidget(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        right.addWidget(self.scroll, 1)
        note = QLabel(tr("pm_note_resin") if (kind, technology) == ("materials", "sla") else "")
        note.setObjectName("Faint")
        note.setWordWrap(True)
        note.setVisible(bool(note.text()))
        right.addWidget(note)
        actions = QHBoxLayout()
        self.delete_btn = QPushButton(theme.icon("trash", theme.TEXT, 15), tr("pm_delete"))
        self.delete_btn.clicked.connect(self._delete)
        actions.addWidget(self.delete_btn)
        actions.addStretch(1)
        self.save_btn = QPushButton(theme.icon("save", theme.TEXT, 15), tr("pm_save"))
        self.save_btn.clicked.connect(self._save)
        actions.addWidget(self.save_btn)
        use = QPushButton(tr("pm_use"))
        use.setObjectName("Primary")
        use.clicked.connect(self._use)
        actions.addWidget(use)
        close = QPushButton(tr("pm_close"))
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        right.addLayout(actions)
        root.addLayout(right, 1)

        self._fill_tree()
        self._select(current)

    # ---------------------------------------------------------------- list
    def _profiles(self) -> dict:
        return profiles.for_technology(self.kind, self.technology)

    def _fill_tree(self, *_):
        text = self.search.text().strip().lower()
        keep = self._pid
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        builtin = profiles.builtin_ids(self.kind)
        items = sorted(self._profiles().items(),
                       key=lambda kv: (kv[1].get("vendor", "") not in ("Generic",), kv[1].get("vendor", ""), kv[1].get("name", "")))
        for pid, p in items:
            vendor = p.get("vendor") or "User"
            label = profiles.display_name(p, i18n.LANG)
            if text and text not in label.lower() and text not in vendor.lower():
                continue
            if vendor not in groups:
                g = QTreeWidgetItem([vendor])
                g.setFlags(Qt.ItemIsEnabled)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[vendor] = g
            if p.get("user"):
                label += "  ✎" if pid in builtin else "  ★"
            it = QTreeWidgetItem([label])
            it.setData(0, Qt.UserRole, pid)
            groups[vendor].addChild(it)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if keep:
            self._select(keep, quiet=True)

    def _select(self, pid: str | None, quiet: bool = False):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                if g.child(j).data(0, Qt.UserRole) == pid:
                    if quiet:
                        self.tree.blockSignals(True)
                    self.tree.setCurrentItem(g.child(j))
                    self.tree.blockSignals(False)
                    if not quiet:
                        self._load(pid)
                    return

    def _on_pick(self, item, _prev):
        pid = item.data(0, Qt.UserRole) if item else None
        if not pid or pid == self._pid:
            return
        if self._dirty and not self._confirm_discard():
            self._select(self._pid, quiet=True)
            return
        self._load(pid)

    # ---------------------------------------------------------------- editor
    def _load(self, pid: str):
        p = self._profiles()[pid]
        self._pid = pid
        self._values = dict(p.get("values", {}))
        self.name_edit.setText(profiles.display_name(p, i18n.LANG))
        self.vendor_edit.setText(p.get("vendor", ""))
        builtin = pid in profiles.builtin_ids(self.kind)
        if builtin and p.get("user"):
            self.badge.setText(tr("pm_badge_modified"))
        elif builtin:
            self.badge.setText(tr("pm_badge_builtin"))
        else:
            self.badge.setText(tr("pm_badge_user"))
        if p.get("note"):
            self.badge.setText(self.badge.text() + "\n" + p["note"])
        self.delete_btn.setText(tr("pm_restore") if builtin else tr("pm_delete"))
        self.delete_btn.setEnabled(not builtin or bool(p.get("user")))
        self._build_form()
        self._dirty = False

    def _build_form(self):
        cats = KEYS[(self.kind, self.technology)]
        cfg = Config(defaults()).update(self._values)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(4)
        from ..settings import CATEGORIES
        for cat in CATEGORIES:
            if cat[0] not in cats:
                continue
            items = [s for s in SCHEMA if s.category == cat[0] and s.tech in (self.technology, "both") and s.key != "technology"]
            if not items:
                continue
            t = QLabel(i18n.category_label(cat))
            t.setObjectName("SectionTitle")
            t.setContentsMargins(0, 10, 0, 4)
            v.addWidget(t)
            for s in items:
                row = SettingRow(s, cfg[s.key], s.key in self._values, cfg)
                row.changed.connect(self._on_value)
                row.reset.connect(self._on_reset)
                v.addWidget(row)
        v.addStretch(1)
        theme.replace_scroll_widget(self.scroll, box)

    def _on_value(self, key, value):
        self._values[key] = value
        self._mark_dirty()

    def _on_reset(self, key):
        self._values.pop(key, None)
        self._mark_dirty()
        self._build_form()

    def _mark_dirty(self, *_):
        self._dirty = True
        self.save_btn.setText(tr("pm_save") + " *")

    def _confirm_discard(self) -> bool:
        return QMessageBox.question(self, self.windowTitle(), tr("pm_discard")) == QMessageBox.Yes

    # ---------------------------------------------------------------- actions
    def _save(self) -> bool:
        if not self._pid:
            return False
        name = self.name_edit.text().strip() or "Profile"
        builtin = self._pid in profiles.builtin_ids(self.kind)
        pid = profiles.save_user_profile(self.kind, name, self._values, self.technology, pid=self._pid,
                                         vendor=self.vendor_edit.text().strip() or ("Generic" if builtin else "User"))
        self._dirty = False
        self.save_btn.setText(tr("pm_save"))
        self._pid = None
        self._fill_tree()
        self._select(pid)
        return True

    def _new(self):
        base = Config(defaults())
        cats = KEYS[(self.kind, self.technology)]
        values = {s.key: base[s.key] for s in SCHEMA if s.category in cats and s.tech in (self.technology, "both")}
        values["technology"] = self.technology
        name = tr("pm_new_name")
        pid = profiles.save_user_profile(self.kind, name, values, self.technology, vendor="User")
        self._fill_tree()
        self._select(pid)

    def _duplicate(self):
        if not self._pid:
            return
        name = f"{self.name_edit.text().strip()} ({tr('pm_copy')})"
        pid = profiles.save_user_profile(self.kind, name, self._values, self.technology, vendor="User")
        self._fill_tree()
        self._select(pid)

    def _delete(self):
        if not self._pid:
            return
        builtin = self._pid in profiles.builtin_ids(self.kind)
        question = tr("pm_restore_q") if builtin else tr("pm_delete_q")
        if QMessageBox.question(self, self.windowTitle(), question.format(self.name_edit.text())) != QMessageBox.Yes:
            return
        profiles.delete_user_profile(self.kind, self._pid)
        pid = self._pid if builtin else None
        self._pid = None
        self._dirty = False
        self._fill_tree()
        if pid:
            self._select(pid)
        elif self.tree.topLevelItemCount():
            g = self.tree.topLevelItem(0)
            if g.childCount():
                self._select(g.child(0).data(0, Qt.UserRole))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("pm_import"), "", "UniSlicer profiles (*.json)")
        if not path:
            return
        try:
            added = profiles.import_profiles(self.kind, path)
        except Exception as exc:
            QMessageBox.warning(self, tr("pm_import"), str(exc))
            return
        self._fill_tree()
        if added:
            self._select(added[0])

    def _export(self):
        if not self._pid:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("pm_export"), f"{self.name_edit.text().strip() or 'profile'}.json",
                                              "UniSlicer profiles (*.json)")
        if path:
            if self._dirty:
                self._save()
            profiles.export_profiles(self.kind, [self._pid], path)

    def _use(self):
        if self._dirty and not self._save():
            return
        self.selected_id = self._pid
        self.accept()

    def reject(self):
        if self._dirty and not self._confirm_discard():
            return
        super().reject()
