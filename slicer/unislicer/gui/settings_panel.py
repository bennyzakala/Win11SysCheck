"""Profile pickers and the schema-driven settings editor."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from .. import profiles
from ..gcode import default_end_gcode, default_start_gcode
from ..settings import CATEGORIES, SCHEMA_BY_KEY, SUPPORT_PRESETS, Config, applies_to_process, visible_settings

PROCESS_BADGE = {"lcd": "LCD", "dlp": "DLP", "laser": "SLA", "polyjet": "POLYJET"}
from . import theme
from . import i18n
from .i18n import category_label, label, option_label, tip, tr

PLATE_KEYS = ("plate_width", "plate_depth", "plate_offset_x", "plate_offset_y")
EXPANDED_BY_DEFAULT = {"quality", "walls", "infill", "support", "adhesion", "material", "exposure", "hollow"}


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
    valueEdited = Signal(str, object)      # a value the user typed or picked
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
            if key in ("printer", "material"):
                lib = QToolButton()
                lib.setIcon(theme.icon("settings", theme.TEXT_DIM, 16))
                lib.setToolTip(tr("manage_printers") if key == "printer" else tr("manage_materials"))
                lib.setCursor(Qt.PointingHandCursor)
                lib.clicked.connect(lambda _=False, k=key: self.open_library("printers" if k == "printer" else "materials"))
                row.addWidget(lib)
            if key == "printer":
                self.profile_menu_btn = QToolButton()
                self.profile_menu_btn.setIcon(theme.icon("save", theme.TEXT_DIM, 16))
                self.profile_menu_btn.setPopupMode(QToolButton.InstantPopup)
                self.profile_menu_btn.setCursor(Qt.PointingHandCursor)
                self.profile_menu_btn.setMenu(self._profile_menu())
                row.addWidget(self.profile_menu_btn)
            cl.addLayout(row)
            cl.addWidget(combo)
            if key == "printer":
                # build plate of the chosen printer (full plate or a smaller/other plate size)
                prow = QHBoxLayout()
                pl = QLabel(tr("plate"))
                pl.setObjectName("Faint")
                prow.addWidget(pl)
                self.plate_combo = NoWheelCombo()
                self.plate_combo.setFocusPolicy(Qt.StrongFocus)
                self.plate_combo.currentIndexChanged.connect(self._on_plate)
                prow.addWidget(self.plate_combo, 1)
                cl.addLayout(prow)
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
        self._default_plate()
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

    @property
    def process(self) -> str:
        return profiles.process_of(profiles.load("printers")[self.printer_id])

    def _fill_printers(self):
        self._building = True
        self.printer_combo.clear()
        items = sorted(profiles.load("printers").items(),
                       key=lambda kv: (profiles.technology_of(kv[1]) != "fdm", kv[1].get("vendor") != "Generic",
                                       kv[1].get("vendor", ""), kv[1]["name"]))
        last = None
        for pid, p in items:
            tech = profiles.technology_of(p)
            group = (tech, p.get("vendor", ""))
            if last is not None and group != last:
                self.printer_combo.insertSeparator(self.printer_combo.count())
            last = group
            badge = "FDM" if tech == "fdm" else PROCESS_BADGE.get(p.get("values", {}).get("sla_process", "lcd"), "RESIN")
            self.printer_combo.addItem(f"{p['name']}   ·  {badge}", pid)
            if p.get("note"):
                self.printer_combo.setItemData(self.printer_combo.count() - 1, p["note"], Qt.ToolTipRole)
        self.printer_combo.setCurrentIndex(max(0, self.printer_combo.findData(self.printer_id)))
        self._building = False

    def _default_plate(self):
        """A printer with manufacturer plates starts on its first one unless a plate was chosen."""
        if not any(k in self.overrides for k in PLATE_KEYS):
            first = (profiles.plates(self.printer_id) or [None])[0]
            if first:
                self.overrides.update({"plate_width": first["width"], "plate_depth": first["depth"]})

    def _fill_plates(self):
        self._building = True
        self.plate_combo.clear()
        base = self.base_config()
        cfg = self.config()
        self.plate_combo.addItem(tr("plate_full").format(base["bed_x"], base["bed_y"]), None)
        current = (cfg["plate_width"], cfg["plate_depth"], cfg["plate_offset_x"], cfg["plate_offset_y"])
        select = 0
        for p in profiles.plates(self.printer_id):
            label = tr("plate_item").format(p["name"], p["width"], p["depth"])
            self.plate_combo.addItem(label, p)
            if (p["width"], p["depth"], p.get("offset_x", 0.0), p.get("offset_y", 0.0)) == current:
                select = self.plate_combo.count() - 1
        self.plate_combo.insertSeparator(self.plate_combo.count())
        self.plate_combo.addItem(tr("plate_add"), "add")
        if select == 0 and current[0] > 0:   # a size typed in the settings, not in the list
            self.plate_combo.insertItem(1, tr("plate_item").format(tr("custom"), current[0], current[1]), "custom")
            select = 1
        self.plate_combo.setCurrentIndex(select)
        self._building = False

    def _on_plate(self, index):
        if self._building or index < 0:
            return
        data = self.plate_combo.itemData(index)
        if data == "custom":
            return
        if data == "add":
            self._add_plate()
            return
        values = {"plate_width": 0.0, "plate_depth": 0.0, "plate_offset_x": 0.0, "plate_offset_y": 0.0}
        if isinstance(data, dict):
            values = {"plate_width": float(data["width"]), "plate_depth": float(data["depth"]),
                      "plate_offset_x": float(data.get("offset_x", 0.0)), "plate_offset_y": float(data.get("offset_y", 0.0))}
        for k, v in values.items():
            if v:
                self.overrides[k] = v
            else:
                self.overrides.pop(k, None)
        self.rebuild()
        self.changed.emit()

    def _add_plate(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        base = self.base_config()
        dlg = QDialog(self.window())
        dlg.setWindowTitle(tr("plate_add_title"))
        form = QFormLayout(dlg)
        name = QLineEdit()
        name.setPlaceholderText(tr("plate_name_hint"))
        width, depth, ox, oy = (NoWheelDouble() for _ in range(4))
        for sb, value, hi in ((width, base["bed_x"], base["bed_x"]), (depth, base["bed_y"], base["bed_y"]),
                              (ox, 0.0, base["bed_x"] / 2), (oy, 0.0, base["bed_y"] / 2)):
            sb.setRange(-hi if sb in (ox, oy) else 1.0, hi)
            sb.setDecimals(2)
            sb.setSuffix(" mm")
            sb.setValue(value)
        form.addRow(tr("plate_name"), name)
        form.addRow(tr("plate_width"), width)
        form.addRow(tr("plate_depth"), depth)
        form.addRow(tr("plate_offset_x"), ox)
        form.addRow(tr("plate_offset_y"), oy)
        note = QLabel(tr("plate_add_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            self._fill_plates()
            return
        label = name.text().strip() or tr("plate_default_name")
        profiles.add_plate(self.printer_id, label, width.value(), depth.value(), ox.value(), oy.value())
        self.overrides.update({"plate_width": width.value(), "plate_depth": depth.value()})
        for k, sb in (("plate_offset_x", ox), ("plate_offset_y", oy)):
            if sb.value():
                self.overrides[k] = sb.value()
            else:
                self.overrides.pop(k, None)
        self.rebuild()
        self.changed.emit()

    def _fill_dependent(self):
        self._building = True
        tech = self.technology
        for combo, kind, attr in ((self.material_combo, "materials", "material_id"), (self.quality_combo, "qualities", "quality_id")):
            combo.clear()
            available = profiles.for_technology(kind, tech, self.process)
            def linked(prof):
                return self.printer_id in prof.get("printers", [])
            ordered = sorted(available.items(), key=lambda kv: (not linked(kv[1]), kv[1].get("vendor", "Generic") != "Generic",
                                                                kv[1].get("vendor", ""), profiles.display_name(kv[1])))
            last_group = None
            for pid, p in ordered:
                vendor = p.get("vendor", "")
                group = (linked(p), vendor)
                if kind == "materials" and last_group is not None and group != last_group:
                    combo.insertSeparator(combo.count())
                last_group = group
                label = profiles.display_name(p, i18n.LANG)
                if kind == "materials" and vendor and vendor != "Generic":
                    label = f"{vendor} · {label}"
                if kind == "materials" and linked(p):
                    label = "★ " + label
                combo.addItem(label, pid)
                if p.get("note"):
                    combo.setItemData(combo.count() - 1, p["note"], Qt.ToolTipRole)
            current = getattr(self, attr)
            if current not in available:
                linked_ids = [k for k, v in available.items() if self.printer_id in v.get("printers", [])]
                current = linked_ids[0] if linked_ids else next(iter(available), None)
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
        for k in PLATE_KEYS:              # a new printer starts on its first plate
            self.overrides.pop(k, None)
        first_plate = (profiles.plates(pid) or [None])[0]
        if first_plate:
            self.overrides.update({"plate_width": first_plate["width"], "plate_depth": first_plate["depth"]})
        # printers with their own validated resins start on one of them
        printer = profiles.load("printers")[pid]
        mats = profiles.for_technology("materials", profiles.technology_of(printer), profiles.process_of(printer))
        linked = [m for m, prof in mats.items() if pid in prof.get("printers", [])]
        if linked and self.material_id not in linked:
            self.material_id = linked[0]
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

    def open_library(self, kind: str):
        from .profile_manager import ProfileManager
        current = self.printer_id if kind == "printers" else self.material_id
        dlg = ProfileManager(kind, self.technology, current, self.window())
        dlg.exec()
        old_tech = self.technology
        if dlg.selected_id:
            if kind == "printers":
                self.printer_id = dlg.selected_id
                self._drop_overrides_for("printers", self.printer_id)
            else:
                self.material_id = dlg.selected_id
                self._drop_overrides_for("materials", self.material_id)
        if self.printer_id not in profiles.load("printers"):
            self.printer_id = "generic_fdm"
        self._fill_printers()
        self._fill_dependent()
        self.rebuild()
        if self.technology != old_tech:
            self.technologyChanged.emit(self.technology)
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

    def state(self) -> dict:
        return {"printer": self.printer_id, "material": self.material_id, "quality": self.quality_id,
                "overrides": dict(self.overrides)}

    def load_state(self, state: dict):
        printers = profiles.load("printers")
        old_tech = self.technology
        if state.get("printer") in printers:
            self.printer_id = state["printer"]
        self.material_id = state.get("material")
        self.quality_id = state.get("quality")
        self.overrides = {k: v for k, v in (state.get("overrides") or {}).items() if k in SCHEMA_BY_KEY}
        self._fill_printers()
        self._fill_dependent()
        self.rebuild()
        if self.technology != old_tech:
            self.technologyChanged.emit(self.technology)
        self.changed.emit()

    # -------------------------------------------------------------- values
    def base_config(self) -> Config:
        return profiles.build_config(self.printer_id, self.material_id, self.quality_id)

    def config(self) -> Config:
        return self.base_config().update(self.overrides)

    def set_value(self, key: str, value):
        """Programmatic override (e.g. the overhang toggle in the viewport)."""
        self._programmatic = True     # not a user habit: don't teach the assistant
        try:
            self._on_changed(key, value)
        finally:
            self._programmatic = False
        self.rebuild()

    def _on_changed(self, key, value):
        if self._building:
            return
        base = self.base_config()[key]
        if value == base or (isinstance(base, float) and abs(float(value) - base) < 1e-9):
            self.overrides.pop(key, None)
        else:
            self.overrides[key] = value
            if not getattr(self, "_programmatic", False):
                self.valueEdited.emit(key, value)
        if key == "sla_process":
            QTimer.singleShot(0, self.rebuild)       # the process decides which settings apply
        if key == "sla_support_preset":
            # the preset supplies tip/pillar/spacing: drop manual values and refresh the editors
            for k in SUPPORT_PRESETS["medium"]:
                self.overrides.pop(k, None)
            QTimer.singleShot(0, self.rebuild)
        elif key in SUPPORT_PRESETS["medium"] and self.config()["sla_support_preset"] != "custom":
            self.overrides["sla_support_preset"] = "custom"
            QTimer.singleShot(0, self.rebuild)
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

    # ------------------------------------------------- section in a large window
    def _section_menu(self, cat, global_pos):
        """Right-click on a section header: open it in a large window, or open / close all sections."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction(theme.icon("fit"), tr("section_open"), lambda: self.open_section_window(cat))
        menu.addSeparator()
        menu.addAction(theme.icon("chevron_down"), tr("sections_expand"), lambda: self._expand_all(True))
        menu.addAction(theme.icon("chevron"), tr("sections_collapse"), lambda: self._expand_all(False))
        menu.exec(global_pos)

    def _expand_all(self, on: bool):
        for sec in self._sections.values():
            sec.header.setChecked(on)

    def open_section_window(self, cat):
        """All settings of one section (advanced ones too) in a window 80% of the screen."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGridLayout, QScrollArea
        dlg = QDialog(self.window())
        dlg.setWindowTitle(category_label(cat))
        screen = (self.window().screen() or self.screen()).availableGeometry()
        dlg.resize(int(screen.width() * 0.8), int(screen.height() * 0.8))
        dlg.move(screen.x() + int(screen.width() * 0.1), screen.y() + int(screen.height() * 0.1))
        outer = QVBoxLayout(dlg)
        head = QHBoxLayout()
        title = QLabel(category_label(cat))
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        icon = QLabel()
        icon.setPixmap(theme.icon(cat[3], theme.ACCENT, 22).pixmap(22, 22))
        head.addWidget(icon)
        head.addWidget(title, 1)
        search = QLineEdit()
        search.setPlaceholderText(tr("search"))
        search.setFixedWidth(260)
        head.addWidget(search)
        outer.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)

        def fill():
            cfg = self.config()
            items = [x for x in visible_settings(cat[0], self.technology, True) if x.key != "technology"]
            if self.technology == "sla":
                items = [x for x in items if applies_to_process(x, cfg["sla_process"])]
            text = search.text().strip().lower()
            if text:
                items = [x for x in items if text in label(x).lower() or text in x.key or text in x.en.lower()]
            box = QWidget()
            grid = QGridLayout(box)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setHorizontalSpacing(28)
            grid.setVerticalSpacing(8)
            cols = 2 if dlg.width() > 1100 else 1
            per_col = (len(items) + cols - 1) // cols
            for k, setting in enumerate(items):
                row = SettingRow(setting, cfg[setting.key], setting.key in self.overrides, cfg)
                row.setToolTip(tip(setting))
                row.changed.connect(changed)
                row.reset.connect(reset)
                grid.addWidget(row, k % per_col if per_col else 0, k // per_col if per_col else 0)
            grid.setRowStretch(per_col + 1, 1)
            scroll.setWidget(box)

        def changed(key, value):
            self._on_changed(key, value)
            if key in ("sla_process", "sla_support_preset"):
                QTimer.singleShot(0, fill)

        def reset(key):
            self.overrides.pop(key, None)
            self.changed.emit()
            fill()

        search.textChanged.connect(lambda _t: fill())
        fill()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("close"))
        buttons.rejected.connect(dlg.reject)
        outer.addWidget(buttons)
        dlg.finished.connect(lambda _r: self.rebuild())      # the side panel shows the new values
        dlg.exec()

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
        self._fill_plates()
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
            if tech == "sla":
                items = [s for s in items if applies_to_process(s, cfg["sla_process"])]
            if self.filter_text:
                items = [s for s in items if self.filter_text in label(s).lower() or self.filter_text in s.key
                         or self.filter_text in s.en.lower()]
            if not items:
                continue
            expanded = bool(self.filter_text) or self._expanded.get(cat[0], cat[0] in EXPANDED_BY_DEFAULT)
            sec = Section(category_label(cat), cat[3], expanded)
            sec.header.setContextMenuPolicy(Qt.CustomContextMenu)
            sec.header.customContextMenuRequested.connect(
                lambda pos, c=cat, h=sec.header: self._section_menu(c, h.mapToGlobal(pos)))
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
