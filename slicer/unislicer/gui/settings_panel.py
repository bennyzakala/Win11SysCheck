"""Profile pickers and the schema-driven settings editor."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from .. import profiles
from ..gcode import default_end_gcode, default_start_gcode
from ..settings import CATEGORIES, SCHEMA_BY_KEY, Config, visible_settings
from . import theme
from . import i18n
from .i18n import category_label, label, option_label, tip, tr

EXPANDED_BY_DEFAULT = {"quality", "walls", "infill", "support", "adhesion", "material", "exposure"}


class _NoWheelMixin:
    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class NoWheelDouble(_NoWheelMixin, QDoubleSpinBox):
    pass


class NoWheelInt(_NoWheelMixin, QSpinBox):
    pass


class NoWheelCombo(_NoWheelMixin, QComboBox):
    pass


class Section(QFrame):
    """Collapsible card with an icon header."""

    def __init__(self, title: str, icon_name: str, expanded: bool):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 8)
        lay.setSpacing(0)
        self.header = QToolButton()
        self.header.setObjectName("SectionHeader")
        self.header.setText("  " + title.replace("&", "&&"))
        self.header.setIcon(theme.icon(icon_name, theme.ACCENT, 18))
        self.header.setIconSize(QSize(18, 18))
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 2, 10, 2)
        self.body_layout.setSpacing(6)
        lay.addWidget(self.body)
        self.body.setVisible(expanded)
        self.header.toggled.connect(self.body.setVisible)

    def add_row(self, widget):
        self.body_layout.addWidget(widget)


class SettingRow(QWidget):
    changed = Signal(str, object)
    reset = Signal(str)

    def __init__(self, setting, value, modified: bool, cfg: Config):
        super().__init__()
        self.setting = setting
        s = setting
        self.key = s.key
        tall = s.kind == "text" and s.key.endswith("gcode")
        outer = QVBoxLayout(self) if tall else QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.label = QLabel(label(s))
        self.label.setWordWrap(True)
        self.label.setToolTip(tip(s) or f"{s.key}")
        self.label.setObjectName("Dim")
        head.addWidget(self.label, 1)
        self.reset_btn = QToolButton()
        self.reset_btn.setObjectName("Reset")
        self.reset_btn.setIcon(theme.icon("reset", theme.ACCENT, 14))
        self.reset_btn.setToolTip(tr("reset_setting"))
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(lambda: self.reset.emit(self.key))
        self.reset_btn.setVisible(modified)
        head.addWidget(self.reset_btn)
        self.editor = self._make_editor(s, value, cfg)
        if tall:
            outer.addLayout(head)
            outer.addWidget(self.editor)
        else:
            outer.addLayout(head, 1)
            outer.addWidget(self.editor)
        if modified:
            self.label.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600;")

    def _make_editor(self, s, value, cfg: Config):
        if s.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.setCursor(Qt.PointingHandCursor)
            w.toggled.connect(lambda v: self.changed.emit(self.key, v))
            return w
        if s.kind == "enum":
            w = NoWheelCombo()
            w.setFocusPolicy(Qt.StrongFocus)
            for opt in s.options:
                w.addItem(option_label(opt), opt[0])
            w.setCurrentIndex(max(0, [o[0] for o in s.options].index(value) if value in [o[0] for o in s.options] else 0))
            w.setMinimumWidth(150)
            w.currentIndexChanged.connect(lambda i: self.changed.emit(self.key, w.itemData(i)))
            return w
        if s.kind == "text":
            if s.key.endswith("gcode"):
                w = QPlainTextEdit()
                w.setPlainText(value)
                default = default_start_gcode(cfg) if s.key == "start_gcode" else default_end_gcode(cfg)
                w.setPlaceholderText(default)
                w.setFixedHeight(150)
                w.setLayoutDirection(Qt.LeftToRight)
                w.textChanged.connect(lambda: self.changed.emit(self.key, w.toPlainText()))
                return w
            w = QLineEdit(str(value))
            w.setFixedWidth(150)
            w.editingFinished.connect(lambda: self.changed.emit(self.key, w.text()))
            return w
        if s.kind == "int":
            w = NoWheelInt()
            w.setFocusPolicy(Qt.StrongFocus)
            w.setRange(int(s.minimum if s.minimum is not None else -10 ** 6), int(s.maximum if s.maximum is not None else 10 ** 6))
            w.setValue(int(value))
        else:
            w = NoWheelDouble()
            w.setFocusPolicy(Qt.StrongFocus)
            decimals = 3 if s.step < 0.01 else 2 if s.step < 1 else 1
            w.setDecimals(decimals)
            w.setRange(s.minimum if s.minimum is not None else -1e6, s.maximum if s.maximum is not None else 1e6)
            w.setSingleStep(s.step)
            w.setValue(float(value))
        if s.unit:
            w.setSuffix(f" {s.unit}")
        w.setKeyboardTracking(False)
        w.setFixedWidth(118)
        w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        w.setButtonSymbols(QSpinBox.NoButtons)
        w.valueChanged.connect(lambda v: self.changed.emit(self.key, v))
        return w


class SettingsPanel(QWidget):
    """Printer / material / quality pickers plus all editable settings."""
    changed = Signal()
    technologyChanged = Signal(str)

    def __init__(self, printer: str, material: str | None, quality: str | None, overrides: dict, show_advanced: bool):
        super().__init__()
        self.printer_id = printer if printer in profiles.load("printers") else "generic_fdm"
        self.material_id = material
        self.quality_id = quality
        self.overrides = {k: v for k, v in (overrides or {}).items() if k in SCHEMA_BY_KEY}
        self.show_advanced = show_advanced
        self.filter_text = ""
        self._building = False
        self._expanded: dict[str, bool] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(6)
        self.printer_combo = self._profile_combo()
        self.material_combo = self._profile_combo()
        self.quality_combo = self._profile_combo()
        for key, icon_name, combo in (("printer", "printer", self.printer_combo), ("material", "spool", self.material_combo),
                                      ("quality", "layers", self.quality_combo)):
            row = QHBoxLayout()
            ic = QLabel()
            ic.setPixmap(theme.icon(icon_name, theme.TEXT_DIM, 16).pixmap(16, 16))
            row.addWidget(ic)
            t = QLabel(tr(key))
            t.setObjectName("SectionTitle")
            row.addWidget(t)
            row.addStretch(1)
            if key == "printer":
                self.profile_menu_btn = QToolButton()
                self.profile_menu_btn.setIcon(theme.icon("save", theme.TEXT_DIM, 16))
                self.profile_menu_btn.setPopupMode(QToolButton.InstantPopup)
                self.profile_menu_btn.setCursor(Qt.PointingHandCursor)
                self.profile_menu_btn.setMenu(self._profile_menu())
                row.addWidget(self.profile_menu_btn)
            cl.addLayout(row)
            cl.addWidget(combo)
        lay.addWidget(card)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        tools.addWidget(self.search, 1)
        self.reset_all_btn = QToolButton()
        self.reset_all_btn.setIcon(theme.icon("reset", theme.TEXT_DIM, 18))
        self.reset_all_btn.setToolTip(tr("reset_all"))
        self.reset_all_btn.clicked.connect(self.reset_all)
        tools.addWidget(self.reset_all_btn)
        lay.addLayout(tools)
        self.advanced = QCheckBox(tr("show_advanced"))
        self.advanced.setChecked(show_advanced)
        self.advanced.toggled.connect(self._on_advanced)
        lay.addWidget(self.advanced)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)

        self.printer_combo.currentIndexChanged.connect(self._on_printer)
        self.material_combo.currentIndexChanged.connect(self._on_material)
        self.quality_combo.currentIndexChanged.connect(self._on_quality)
        self._fill_printers()
        self._fill_dependent()
        self.rebuild()

    # -------------------------------------------------------------- profiles
    def _profile_combo(self):
        c = NoWheelCombo()
        c.setObjectName("ProfileCombo")
        c.setFocusPolicy(Qt.StrongFocus)
        c.setMaxVisibleItems(20)
        return c

    def _profile_menu(self):
        m = QMenu(self)
        m.addAction(theme.icon("printer"), tr("save_printer"), lambda: self._save_profile("printers"))
        m.addAction(theme.icon("spool"), tr("save_material"), lambda: self._save_profile("materials"))
        m.addSeparator()
        m.addAction(theme.icon("trash"), tr("delete_profile"), self._delete_profile)
        return m

    @property
    def technology(self) -> str:
        return profiles.technology_of(profiles.load("printers")[self.printer_id])

    def _fill_printers(self):
        self._building = True
        self.printer_combo.clear()
        items = sorted(profiles.load("printers").items(),
                       key=lambda kv: (profiles.technology_of(kv[1]) != "fdm", kv[1].get("vendor") != "Generic",
                                       kv[1].get("vendor", ""), kv[1]["name"]))
        last_tech = None
        for pid, p in items:
            tech = profiles.technology_of(p)
            if last_tech is not None and tech != last_tech:
                self.printer_combo.insertSeparator(self.printer_combo.count())
            last_tech = tech
            badge = "FDM" if tech == "fdm" else "RESIN"
            self.printer_combo.addItem(f"{p['name']}   ·  {badge}", pid)
        self.printer_combo.setCurrentIndex(max(0, self.printer_combo.findData(self.printer_id)))
        self._building = False

    def _fill_dependent(self):
        self._building = True
        tech = self.technology
        for combo, kind, attr in ((self.material_combo, "materials", "material_id"), (self.quality_combo, "qualities", "quality_id")):
            combo.clear()
            available = profiles.for_technology(kind, tech)
            for pid, p in available.items():
                combo.addItem(profiles.display_name(p, i18n.LANG), pid)
            current = getattr(self, attr)
            if current not in available:
                current = next(iter(available), None)
                if kind == "qualities":
                    current = "standard" if "standard" in available else ("sla_normal" if "sla_normal" in available else current)
                setattr(self, attr, current)
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self._building = False

    def _drop_overrides_for(self, kind: str, pid: str | None):
        if not pid:
            return
        keys = profiles.load(kind).get(pid, {}).get("values", {}).keys()
        for k in keys:
            self.overrides.pop(k, None)

    def _on_printer(self, index):
        if self._building or index < 0:
            return
        pid = self.printer_combo.itemData(index)
        if not pid:
            return
        old_tech = self.technology
        self.printer_id = pid
        self._drop_overrides_for("printers", pid)
        self._fill_dependent()
        self.rebuild()
        if self.technology != old_tech:
            self.technologyChanged.emit(self.technology)
        self.changed.emit()

    def _on_material(self, index):
        if self._building or index < 0:
            return
        self.material_id = self.material_combo.itemData(index)
        self._drop_overrides_for("materials", self.material_id)
        self.rebuild()
        self.changed.emit()

    def _on_quality(self, index):
        if self._building or index < 0:
            return
        self.quality_id = self.quality_combo.itemData(index)
        self._drop_overrides_for("qualities", self.quality_id)
        self.rebuild()
        self.changed.emit()

    def _save_profile(self, kind: str):
        name, ok = QInputDialog.getText(self, tr("save_printer" if kind == "printers" else "save_material"), tr("profile_name"))
        if not ok or not name.strip():
            return
        cfg = self.config()
        cats = {"printers": {"printer", "gcode", "speed", "retraction", "exposure"}, "materials": {"material"}}[kind]
        values = {s: cfg[s] for s, meta in SCHEMA_BY_KEY.items() if meta.category in cats}
        if kind == "materials":
            values.update({k: cfg[k] for k in ("exposure_time", "bottom_exposure_time")} if self.technology == "sla" else {})
        pid = profiles.save_user_profile(kind, name.strip(), values, self.technology)
        for k in values:
            self.overrides.pop(k, None)
        if kind == "printers":
            self.printer_id = pid
            self._fill_printers()
        else:
            self.material_id = pid
            self._fill_dependent()
        self.rebuild()
        self.changed.emit()

    def _delete_profile(self):
        for kind, pid in (("printers", self.printer_id), ("materials", self.material_id)):
            if pid and pid.startswith("user_"):
                if QMessageBox.question(self, tr("delete_profile"), f"{tr('delete_profile')}: {pid}?") == QMessageBox.Yes:
                    profiles.delete_user_profile(kind, pid)
                    if kind == "printers":
                        self.printer_id = "generic_fdm"
                        self._fill_printers()
                    else:
                        self.material_id = None
                    self._fill_dependent()
                    self.rebuild()
                    self.changed.emit()
                return

    # -------------------------------------------------------------- values
    def base_config(self) -> Config:
        return profiles.build_config(self.printer_id, self.material_id, self.quality_id)

    def config(self) -> Config:
        return self.base_config().update(self.overrides)

    def set_value(self, key: str, value):
        """Programmatic override (e.g. the overhang toggle in the viewport)."""
        self._on_changed(key, value)
        self.rebuild()

    def _on_changed(self, key, value):
        if self._building:
            return
        base = self.base_config()[key]
        if value == base or (isinstance(base, float) and abs(float(value) - base) < 1e-9):
            self.overrides.pop(key, None)
        else:
            self.overrides[key] = value
        row = self._rows.get(key)
        if row is not None:
            modified = key in self.overrides
            row.reset_btn.setVisible(modified)
            row.label.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600;" if modified else "")
        self.changed.emit()

    def _on_reset(self, key):
        self.overrides.pop(key, None)
        self.rebuild()
        self.changed.emit()

    def reset_all(self):
        self.overrides.clear()
        self.rebuild()
        self.changed.emit()

    def _on_advanced(self, v):
        self.show_advanced = v
        self.rebuild()

    def _on_search(self, text):
        self.filter_text = text.strip().lower()
        self.rebuild()

    def rebuild(self):
        """(Re)create the settings sections for the current technology."""
        old = self.scroll.widget()
        if old is not None:
            for cat, sec in getattr(self, "_sections", {}).items():
                self._expanded[cat] = sec.header.isChecked()
        self._building = True
        cfg = self.config()
        tech = self.technology
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(10)
        self._rows: dict[str, SettingRow] = {}
        self._sections = {}
        for cat in CATEGORIES:
            items = visible_settings(cat[0], tech, self.show_advanced or bool(self.filter_text))
            items = [s for s in items if s.key != "technology"]
            if self.filter_text:
                items = [s for s in items if self.filter_text in label(s).lower() or self.filter_text in s.key
                         or self.filter_text in s.en.lower()]
            if not items:
                continue
            expanded = bool(self.filter_text) or self._expanded.get(cat[0], cat[0] in EXPANDED_BY_DEFAULT)
            sec = Section(category_label(cat), cat[3], expanded)
            for s in items:
                row = SettingRow(s, cfg[s.key], s.key in self.overrides, cfg)
                row.changed.connect(self._on_changed)
                row.reset.connect(self._on_reset)
                sec.add_row(row)
                self._rows[s.key] = row
            v.addWidget(sec)
            self._sections[cat[0]] = sec
        v.addStretch(1)
        self.scroll.setWidget(container)
        self._building = False
