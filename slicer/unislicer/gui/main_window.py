"""Main application window."""
from __future__ import annotations

import json
import os

import numpy as np
from PySide6.QtCore import QItemSelectionModel, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QCheckBox, QDoubleSpinBox, QFileDialog, QInputDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QScrollArea, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, dental_shades, fdm, gcode, orient, primitives, profiles, project, sla, sla_supports
from ..assistant import Assistant, hollow_saving_ml
from ..mesh import SUPPORTED_EXTENSIONS, Mesh, load_mesh, save_stl
from ..scene import SceneObject, out_of_bounds, plate_area, plate_rect, plate_region
from ..settings import SCHEMA_BY_KEY
from ..slicing import MultiPolygon
from . import i18n, theme
from .assistant_panel import AssistantPanel
from .i18n import tr
from .preview_data import toolpath_vertices
from .settings_panel import SettingsPanel
from .viewer import Viewer
from .worker import Task

SETTINGS_ORG = "UniSlicer"

# engine messages shown in the UI language
ENGINE_TEXT = {
    "The cavity is too small for the drain holes.": "note_cavity_small",
    "Hollowed without drain holes: uncured resin will stay trapped inside.": "note_no_drain",
    "The model is too thin to hollow with this wall thickness.": "note_too_thin",
    sla.UNIFORM_FORMAT_NOTE: "note_uniform_format",
}


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("Card")
    return f


def _title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("SectionTitle")
    return lab


def _spin(lo, hi, step, decimals, suffix="", width=72) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setButtonSymbols(QDoubleSpinBox.NoButtons)
    s.setAlignment(Qt.AlignCenter)
    s.setFixedWidth(width)
    return s


class StatTile(QFrame):
    def __init__(self, icon_name: str, caption: str):
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: {theme.SURFACE_2}; border-radius: 10px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(theme.icon(icon_name, theme.ACCENT, 18).pixmap(18, 18))
        lay.addWidget(ic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = QLabel("—")
        self.value.setObjectName("StatValue")
        self.caption = QLabel(caption)
        self.caption.setObjectName("StatLabel")
        col.addWidget(self.value)
        col.addWidget(self.caption)
        lay.addLayout(col, 1)

    def set(self, value: str, caption: str | None = None):
        self.value.setText(value)
        if caption:
            self.caption.setText(caption)


class MainWindow(QMainWindow):
    languageSwitchRequested = Signal(str)
    themeChangeRequested = Signal(str, str)     # theme name, logo file

    def __init__(self, objects: list[SceneObject] | None = None):
        super().__init__()
        self.setWindowTitle(f"UniSlicer {__version__}")
        self.setAcceptDrops(True)
        self.store = QSettings(SETTINGS_ORG, SETTINGS_ORG)
        self.objects: list[SceneObject] = list(objects or [])
        self.result = None          # ("fdm", plan, text, stats) | ("sla", job, cfg)
        self.result_cfg = None
        self.task: Task | None = None
        self._syncing = False
        self._undo: list = []
        self._redo: list = []
        self._dragging = False
        self._support_cache: dict = {}
        self._islands: dict = {}
        self.assistant = Assistant()
        self._orient_gain: dict = {}          # uid -> (orientation key, gain)
        self._orient_task: Task | None = None
        self._dismissed_session: set = set()
        self._seen_problems: set = set()
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(600)
        self._suggest_timer.timeout.connect(self._refresh_suggestions)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.left_panel = self._build_left_panel()
        self.right_panel = self._build_right_panel()
        body.addWidget(self.left_panel)
        body.addWidget(self._build_viewport(), 1)
        body.addWidget(self.right_panel)
        root.addLayout(body, 1)
        self.setCentralWidget(central)
        self.status = QLabel(tr("ready"))
        self.statusBar().addWidget(self.status, 1)
        if os.environ.get("UNISLICER_SOFTWARE_GL"):
            sw = QLabel("⚠ " + tr("software_gl"))
            sw.setObjectName("Warning")
            sw.setToolTip(tr("software_gl_tip"))
            self.statusBar().addPermanentWidget(sw)
        ver = QLabel(f"UniSlicer {__version__}")
        ver.setObjectName("Faint")
        self.statusBar().addPermanentWidget(ver)
        self._install_shortcuts()

        for side in ("left", "right"):
            self.set_panel_visible(side, self.store.value(f"{side}_panel", "true") != "false", save=False)
        geo = self.store.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1560, 940)
        self._apply_printer()
        self._refresh_objects(select=self.objects[0].uid if self.objects else None)
        self.viewer.view_preset("iso")

    # ================================================================ layout
    def _build_top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(6)
        logo = QLabel()
        mark = theme.brand_pixmap("mark", 38) if not theme.LOGO_FILE else None
        logo.setPixmap(mark or theme.logo_pixmap(32, 36))
        lay.addWidget(logo)
        word = theme.brand_pixmap("wordmark", 22) if not theme.LOGO_FILE else None
        if word is not None:
            wl = QLabel()
            wl.setPixmap(word)
            lay.addWidget(wl)
            lay.addSpacing(8)
        names = QVBoxLayout()
        names.setSpacing(0)
        t = QLabel("UniSlicer")
        t.setObjectName("AppTitle")
        s = QLabel(tr("app_subtitle"))
        s.setObjectName("AppSubtitle")
        names.addStretch(1)
        names.addWidget(t)
        names.addWidget(s)
        names.addStretch(1)
        lay.addLayout(names)
        lay.addSpacing(24)

        def tool(icon_name, text, tip, slot):
            b = QToolButton()
            b.setObjectName("TextTool")
            b.setIcon(theme.icon(icon_name))
            b.setIconSize(QSize(18, 18))
            b.setText(text)
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setCursor(Qt.PointingHandCursor)
            if slot:
                b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        tool("open", tr("open"), tr("open_tip"), lambda: self._act("open", "button", self.open_files))
        shapes = tool("shapes", tr("shapes"), tr("shapes"), None)
        menu = QMenu(self)
        menu.addAction(theme.icon("cube"), tr("shape_cube"), lambda: self.add_mesh(primitives.box(20, 20, 20, "Cube")))
        menu.addAction(tr("shape_cylinder"), lambda: self.add_mesh(primitives.cylinder(10, 25, 64, "Cylinder")))
        menu.addAction(tr("shape_sphere"), lambda: self.add_mesh(primitives.sphere(12, 32, 64, "Sphere")))
        menu.addAction(tr("shape_bridge"), lambda: self.add_mesh(primitives.demo_bridge()))
        menu.addSeparator()
        menu.addAction(theme.icon("code"), tr("add_text"), self.add_text)
        shapes.setMenu(menu)
        shapes.setPopupMode(QToolButton.InstantPopup)
        tool("sparkle", tr("auto_prepare"), tr("auto_prepare_tip"), self.auto_prepare)
        arr = tool("arrange", tr("arrange"), tr("arrange_tip"), None)
        am = QMenu(self)
        am.addAction(theme.icon("arrange"), tr("layout_quick") + "\tA", lambda: self._act("arrange", "button", self.arrange_objects))
        am.addAction(theme.icon("settings"), tr("auto_layout") + "…\tShift+A", lambda: self.auto_layout())
        am.addAction(theme.icon("copy"), tr("lay_fill"), self._fill_plate_quick)
        arr.setMenu(am)
        arr.setPopupMode(QToolButton.MenuButtonPopup)
        arr.clicked.connect(lambda: self._act("arrange", "button", self.arrange_objects))
        tool("copy", tr("duplicate"), tr("duplicate"), lambda: self._act("duplicate", "button", self.duplicate_selected))
        tool("trash", tr("delete"), tr("delete"), self.delete_selected)
        self.undo_btn = tool("undo", "", tr("undo") + " (Ctrl+Z)", lambda: self._act("undo", "button", self.undo))
        self.redo_btn = tool("redo", "", tr("redo") + " (Ctrl+Y)", self.redo)
        more = tool("more", tr("more"), tr("more"), None)
        mm = QMenu(self)
        mm.addAction(theme.icon("open"), tr("open_project"), self.open_project)
        self.recent_menu = mm.addMenu(theme.icon("clock"), tr("recent_files"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        mm.addAction(theme.icon("save"), tr("save_project"), lambda: self._act("save_project", "button", self.save_project))
        mm.addSeparator()
        mm.addAction(theme.icon("download"), tr("export_stl"), self.export_stl)
        mm.addAction(theme.icon("wifi"), tr("net_title") + "…", self.network_printers)
        mm.addAction(theme.icon("wifi"), tr("remote_title") + "…", self.remote_control)
        mm.addAction(theme.icon("layers"), tr("fil_title") + "…", self.filaments_dialog)
        mm.addAction(theme.icon("sparkle"), tr("cad_title") + "…", self.dental_cad_dialog)
        mm.addAction(theme.icon("copy"), tr("multiply"), self.multiply_selected)
        mm.addSeparator()
        mm.addAction(theme.icon("square"), tr("cut_height"), self.cut_selected)
        mm.addAction(theme.icon("grid"), tr("split_parts"), self.split_selected)
        mm.addAction(theme.icon("settings"), tr("repair_mesh"), self.repair_selected)
        mm.addAction(theme.icon("settings"), tr("repair_advanced") + "…", self.repair_advanced_dialog)
        mm.addAction(theme.icon("grid"), tr("pixel_edit") + "…", self.open_layer_editor)
        more.setMenu(mm)
        more.setPopupMode(QToolButton.InstantPopup)
        lay.addStretch(1)

        seg = QFrame()
        seg.setObjectName("Segmented")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.prepare_btn = QPushButton(tr("prepare"))
        self.preview_btn = QPushButton(tr("preview"))
        for b in (self.prepare_btn, self.preview_btn):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            sl.addWidget(b)
        self.prepare_btn.setChecked(True)
        self.prepare_btn.clicked.connect(lambda: self.set_mode("prepare"))
        self.preview_btn.clicked.connect(lambda: self.set_mode("preview"))
        lay.addWidget(seg)
        lay.addStretch(1)
        self.assistant_btn = tool("bulb", tr("assistant"), tr("assistant_tip"), self._toggle_assistant)
        look = tool("sun", tr("theme"), tr("theme_tip"), None)
        tm = QMenu(self)
        for name in theme.THEME_NAMES:
            a = tm.addAction(tr(f"theme_{name}"), lambda n=name: self.themeChangeRequested.emit(n, theme.LOGO_FILE))
            a.setCheckable(True)
            a.setChecked(name == theme.CURRENT)
        tm.addSeparator()
        tm.addAction(theme.icon("open"), tr("theme_logo"), self._choose_logo)
        if theme.LOGO_FILE:
            tm.addAction(theme.icon("x"), tr("theme_logo_clear"), lambda: self.themeChangeRequested.emit(theme.CURRENT, ""))
        look.setMenu(tm)
        look.setPopupMode(QToolButton.InstantPopup)
        lang_btn = tool("globe", i18n.NATIVE[i18n.LANG], tr("language_tip"), None)
        lm = QMenu(self)
        for code in i18n.LANGS:
            act = lm.addAction(i18n.NATIVE[code], lambda c=code: self.languageSwitchRequested.emit(c))
            act.setCheckable(True)
            act.setChecked(code == i18n.LANG)
        lang_btn.setMenu(lm)
        lang_btn.setPopupMode(QToolButton.InstantPopup)
        return bar

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("theme_logo"), os.path.expanduser("~"),
                                              "Images (*.png *.svg *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.themeChangeRequested.emit(theme.CURRENT, path)

    def _build_left_panel(self):
        panel = QFrame()
        panel.setObjectName("SideBar")
        panel.setFixedWidth(320)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        objects = _card()
        ol = QVBoxLayout(objects)
        ol.setContentsMargins(12, 12, 12, 10)
        ol.addWidget(_title(tr("objects")))
        self.object_list = QListWidget()
        self.object_list.setMinimumHeight(110)
        self.object_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.object_list.setToolTip(tr("multi_select_tip"))
        self.object_list.currentItemChanged.connect(self._on_list_selection)
        self.object_list.itemSelectionChanged.connect(self._on_list_multi)
        ol.addWidget(self.object_list, 1)
        self.object_info = QLabel("")
        self.object_info.setObjectName("Faint")
        self.object_info.setWordWrap(True)
        ol.addWidget(self.object_info)
        lay.addWidget(objects, 1)

        tr_card = _card()
        g = QGridLayout(tr_card)
        g.setContentsMargins(12, 12, 12, 12)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(8)
        g.addWidget(_title(tr("transform")), 0, 0, 1, 4)
        axes = ["X", "Y", "Z"]
        for i, a in enumerate(axes):
            h = QLabel(a)
            h.setAlignment(Qt.AlignCenter)
            h.setStyleSheet(f"color: {['#ff6b7a', '#4fe0a0', '#6aa8ff'][i]}; font-weight: 700;")
            g.addWidget(h, 1, i + 1)
        g.addWidget(QLabel(tr("position")), 2, 0)
        self.pos_x = _spin(-5000, 5000, 1, 1)
        self.pos_y = _spin(-5000, 5000, 1, 1)
        g.addWidget(self.pos_x, 2, 1)
        g.addWidget(self.pos_y, 2, 2)
        self.pos_z = _spin(0, 2000, 0.5, 2)
        self.pos_z.setToolTip(tr("pos_z_tip"))
        g.addWidget(self.pos_z, 2, 3)
        g.addWidget(QLabel(tr("rotation")), 3, 0)
        self.rot = [_spin(-360, 360, 15, 1, "°") for _ in range(3)]
        for i, s in enumerate(self.rot):
            g.addWidget(s, 3, i + 1)
        g.addWidget(QLabel(tr("scale")), 4, 0)
        self.scale = [_spin(0.1, 10000, 5, 1, "%") for _ in range(3)]
        for i, s in enumerate(self.scale):
            g.addWidget(s, 4, i + 1)
        g.addWidget(QLabel(tr("size")), 5, 0)
        self.size_spins = [_spin(0.01, 5000, 1, 2) for _ in range(3)]
        for i, s in enumerate(self.size_spins):
            g.addWidget(s, 5, i + 1)
        self.uniform = QCheckBox(tr("uniform"))
        self.uniform.setChecked(True)
        g.addWidget(self.uniform, 6, 0, 1, 4)
        mirror_row = QHBoxLayout()
        mirror_row.setSpacing(4)
        ml = QLabel(tr("mirror"))
        ml.setObjectName("Dim")
        mirror_row.addWidget(ml)
        for i, a in enumerate("XYZ"):
            b = QPushButton(a)
            b.setStyleSheet("padding: 4px 0; font-weight: 700;")
            b.setToolTip(f"{tr('mirror')} {a}")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.mirror_selected(i))
            mirror_row.addWidget(b)
        g.addLayout(mirror_row, 7, 0, 1, 4)
        turn_row = QHBoxLayout()
        turn_row.setSpacing(4)
        tl90 = QLabel(tr("rotate_90"))
        tl90.setObjectName("Dim")
        turn_row.addWidget(tl90)
        for i, a in enumerate("XYZ"):
            b = QPushButton(a)
            b.setStyleSheet("padding: 4px 0; font-weight: 700;")
            b.setToolTip(tr("rotate_90_tip").format(a))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.rotate_selected(i, -90 if QApplication.keyboardModifiers() & Qt.ShiftModifier else 90))
            turn_row.addWidget(b)
        g.addLayout(turn_row, 8, 0, 1, 4)
        btns = QGridLayout()
        btns.setSpacing(6)
        for k, (icon_name, text, tip, slot) in enumerate((("compass", tr("auto_orient"), tr("auto_orient_tip"), self.auto_orient_selected),
                                           ("flat", tr("lay_flat"), tr("lay_flat_tip"), self.lay_flat),
                                           ("center", tr("center"), tr("center"), self.center_selected),
                                           ("reset", tr("reset_transform"), tr("reset_transform"), self.reset_transform))):
            b = QPushButton(theme.icon(icon_name, theme.TEXT, 16), text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            btns.addWidget(b, k // 2, k % 2)
        g.addLayout(btns, 9, 0, 1, 4)
        self.cutter_box = QCheckBox(tr("cutter_role"))
        self.cutter_box.setToolTip(tr("cutter_role_tip"))
        g.addWidget(self.cutter_box, 10, 0, 1, 4)
        self.transform_card = tr_card
        lay.addWidget(tr_card)

        sup = _card()
        sl = QVBoxLayout(sup)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)
        sl.addWidget(_title(tr("supports_card")))
        row = QGridLayout()
        row.setSpacing(6)
        self.sup_auto_btn = QPushButton(theme.icon("pillar", theme.TEXT, 16), tr("sup_auto"))
        self.sup_auto_btn.setToolTip(tr("sup_auto_tip"))
        self.sup_auto_btn.clicked.connect(self.generate_supports)
        self.sup_add_btn = QPushButton(theme.icon("plus", theme.TEXT, 16), tr("sup_add"))
        self.sup_add_btn.setToolTip(tr("sup_add_tip"))
        self.sup_remove_btn = QPushButton(theme.icon("x", theme.TEXT, 16), tr("sup_remove"))
        self.sup_remove_btn.setToolTip(tr("sup_remove_tip"))
        for b, mode in ((self.sup_add_btn, "add"), (self.sup_remove_btn, "remove")):
            b.setCheckable(True)
            b.toggled.connect(lambda on, mode=mode: self._set_pick_mode(mode if on else None))
        self.sup_clear_btn = QPushButton(theme.icon("trash", theme.TEXT, 16), tr("sup_clear"))
        self.sup_clear_btn.clicked.connect(self.clear_supports)
        self.sup_manual_btn = QPushButton(theme.icon("anchor", theme.ACCENT, 16), tr("sup_manual"))
        self.sup_manual_btn.setToolTip(tr("sup_manual_tip"))
        self.sup_manual_btn.clicked.connect(self.manual_supports)
        for k, b in enumerate((self.sup_auto_btn, self.sup_clear_btn, self.sup_add_btn, self.sup_remove_btn)):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b, k // 2, k % 2)
        self.sup_manual_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.sup_manual_btn, 2, 0)
        raft_btn = QPushButton(theme.icon("grid", theme.ACCENT, 16), tr("raft_menu"))
        raft_btn.setToolTip(tr("raft_menu_tip"))
        raft_btn.setCursor(Qt.PointingHandCursor)
        raft_btn.clicked.connect(self.raft_menu)
        row.addWidget(raft_btn, 2, 1)
        sl.addLayout(row)
        self.sup_info = QLabel(tr("sup_none"))
        self.sup_info.setObjectName("Faint")
        self.sup_info.setWordWrap(True)
        sl.addWidget(self.sup_info)
        self.supports_card = sup
        lay.addWidget(sup)

        self.pos_x.valueChanged.connect(lambda v: self._edit("x", v))
        self.pos_y.valueChanged.connect(lambda v: self._edit("y", v))
        self.pos_z.valueChanged.connect(lambda v: self._edit("z", v))
        self.cutter_box.toggled.connect(self._toggle_cutter)
        for i in range(3):
            self.rot[i].valueChanged.connect(lambda v, i=i: self._edit("rot", v, i))
            self.scale[i].valueChanged.connect(lambda v, i=i: self._edit("scale", v, i))
            self.size_spins[i].valueChanged.connect(lambda v, i=i: self._edit("size", v, i))
        # short screens: scroll instead of squashing the buttons
        scroll = QScrollArea()
        scroll.setObjectName("SideBar")
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(330)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel.setFixedWidth(320)
        return scroll

    def _build_viewport(self):
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        self.viewer = Viewer()
        self.viewer.objectClicked.connect(self._on_view_click)
        self.viewer.boxSelected.connect(self._on_box_select)
        self.viewer.contextRequested.connect(self._show_context_menu)
        self.viewer.objectDragged.connect(self._on_drag)
        self.viewer.objectDragFinished.connect(self._on_drag_finished)
        self.viewer.surfacePicked.connect(self._on_support_add)
        self.viewer.rotateStarted.connect(self._on_rotate_start)
        self.viewer.rotateDragged.connect(self._on_rotate_drag)
        self.viewer.rotateFinished.connect(self._on_rotate_end)
        self.viewer.facePicked.connect(self._on_face_picked)
        self.viewer.holePicked.connect(self._on_hole_picked)
        self.viewer.measured.connect(self._on_measured)
        self.viewer.previewObjectClicked.connect(self._on_preview_click)
        self.viewer.rayPicked.connect(self._on_support_remove)
        self.viewer.hoverRay.connect(self._on_support_hover)
        self.viewer.glFailed.connect(self._on_gl_failed)
        grid.addWidget(self.viewer, 0, 0)

        # floating view tools
        tools = _card()
        tools.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.92)}; }}")
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(6, 6, 6, 6)
        tl.setSpacing(2)
        for icon_name, tip, fn in (("view_iso", tr("view_iso"), lambda: self.viewer.view_preset("iso")),
                                   ("view_top", tr("view_top"), lambda: self.viewer.view_preset("top")),
                                   ("view_front", tr("view_front"), lambda: self.viewer.view_preset("front")),
                                   ("view_side", tr("view_side"), lambda: self.viewer.view_preset("side")),
                                   ("fit", tr("view_fit"), self.viewer.fit_objects)):
            b = QToolButton()
            b.setIcon(theme.icon(icon_name, theme.TEXT, 18))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            tl.addWidget(b)
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {theme.BORDER};")
        tl.addWidget(sep)
        self.overhang_btn = QToolButton()
        self.overhang_btn.setIcon(theme.icon("overhang", theme.DANGER, 18))
        self.overhang_btn.setIconSize(QSize(18, 18))
        self.overhang_btn.setCheckable(True)
        self.overhang_btn.setToolTip(tr("overhangs"))
        self.overhang_btn.toggled.connect(self._update_overhang)
        tl.addWidget(self.overhang_btn)
        self.minima_btn = QToolButton()
        self.minima_btn.setIcon(theme.icon("pillar", theme.DANGER, 18))
        self.minima_btn.setIconSize(QSize(18, 18))
        self.minima_btn.setCheckable(True)
        self.minima_btn.setToolTip(tr("show_unsupported_tip"))
        self.minima_btn.toggled.connect(lambda _on: self._unsupported_timer.start())
        tl.addWidget(self.minima_btn)
        self.section_btn = QToolButton()
        self.section_btn.setIcon(theme.icon("layers", theme.TEXT, 18))
        self.section_btn.setIconSize(QSize(18, 18))
        self.section_btn.setCheckable(True)
        self.section_btn.setToolTip(tr("section_view_tip"))
        tl.addWidget(self.section_btn)
        self.section_slider = QSlider(Qt.Horizontal)
        self.section_slider.setRange(0, 1000)
        self.section_slider.setValue(1000)
        self.section_slider.setFixedWidth(160)
        self.section_slider.setToolTip(tr("section_view_tip"))
        self.section_slider.hide()
        self.section_label = QLabel("")
        self.section_label.setObjectName("Faint")
        self.section_label.hide()
        tl.addWidget(self.section_slider)
        tl.addWidget(self.section_label)
        self.section_btn.toggled.connect(self._toggle_section)
        self.section_slider.valueChanged.connect(self._on_section)
        wrap = QWidget()
        wrap.setObjectName("Overlay")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(14, 14, 14, 14)
        wl.addWidget(tools)
        wl.addStretch(1)
        wrap.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        grid.addWidget(wrap, 0, 0, Qt.AlignTop | Qt.AlignLeft)

        # manual placement tools
        palette = _card()
        palette.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.92)}; }}")
        pl = QVBoxLayout(palette)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.setSpacing(2)
        self.tool_buttons = {}
        for name, icon_name, key in (("move", "move", "M"), ("rotate", "rotate3d", "R"), ("face", "face", "F"),
                                     ("hole", "drop", "H"), ("measure", "ruler", "D")):
            b = QToolButton()
            b.setIcon(theme.icon(icon_name, theme.TEXT, 20))
            b.setIconSize(QSize(20, 20))
            b.setCheckable(True)
            b.setToolTip(f"{tr('tool_' + name)} ({key})\n{tr('tool_' + name + '_tip')}")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            pl.addWidget(b)
            self.tool_buttons[name] = b
        self.tool_buttons["move"].setChecked(True)
        pwrap = QWidget()
        pwrap.setObjectName("Overlay")
        pwl = QVBoxLayout(pwrap)
        pwl.setContentsMargins(14, 0, 14, 0)
        pwl.addWidget(palette)
        grid.addWidget(pwrap, 0, 0, Qt.AlignVCenter | Qt.AlignLeft)
        self.palette_wrap = pwrap

        # empty state
        self._build_edit_bar(holder)
        self.empty_hint = QLabel(tr("no_objects"))
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 15px; background: transparent;")
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        grid.addWidget(self.empty_hint, 0, 0, Qt.AlignCenter)

        nav = QLabel(tr("hint_nav"))
        nav.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 11px; background: transparent; padding: 10px;")
        nav.setAttribute(Qt.WA_TransparentForMouseEvents)
        grid.addWidget(nav, 0, 0, Qt.AlignBottom | Qt.AlignHCenter)

        # layer slider
        self.layer_box = _card()
        self.layer_box.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.92)}; }}")
        lb = QVBoxLayout(self.layer_box)
        lb.setContentsMargins(10, 12, 10, 12)
        lb.setSpacing(8)
        self.layer_label = QLabel("")
        self.layer_label.setAlignment(Qt.AlignCenter)
        self.layer_label.setStyleSheet("font-weight: 700;")
        self.layer_z = QLabel("")
        self.layer_z.setAlignment(Qt.AlignCenter)
        self.layer_z.setObjectName("Faint")
        self.layer_slider = QSlider(Qt.Vertical)
        self.layer_slider.setMinimumHeight(320)
        self.layer_slider.valueChanged.connect(self._on_layer)
        lb.addWidget(self.layer_label)
        lb.addWidget(self.layer_z)
        lb.addWidget(self.layer_slider, 1, Qt.AlignHCenter)
        self.island_btn = QToolButton()
        self.island_btn.setIcon(theme.icon("info", theme.DANGER, 18))
        self.island_btn.setToolTip(tr("next_island"))
        self.island_btn.setCursor(Qt.PointingHandCursor)
        self.island_btn.clicked.connect(self._next_island)
        self.island_btn.hide()
        lb.addWidget(self.island_btn, 0, Qt.AlignHCenter)
        self.island_fix_btn = QToolButton()
        self.island_fix_btn.setIcon(theme.icon("pillar", theme.DANGER, 18))
        self.island_fix_btn.setToolTip(tr("islands_support_all"))
        self.island_fix_btn.setCursor(Qt.PointingHandCursor)
        self.island_fix_btn.clicked.connect(self.support_all_islands)
        self.island_fix_btn.hide()
        lb.addWidget(self.island_fix_btn, 0, Qt.AlignHCenter)
        self.pixel_btn = QToolButton()
        self.pixel_btn.setIcon(theme.icon("grid", theme.ACCENT, 18))
        self.pixel_btn.setToolTip(tr("pixel_edit"))
        self.pixel_btn.setCursor(Qt.PointingHandCursor)
        self.pixel_btn.clicked.connect(self.open_layer_editor)
        lb.addWidget(self.pixel_btn, 0, Qt.AlignHCenter)
        lwrap = QWidget()
        lwrap.setObjectName("Overlay")
        lwl = QVBoxLayout(lwrap)
        lwl.setContentsMargins(14, 70, 14, 40)
        lwl.addWidget(self.layer_box)
        grid.addWidget(lwrap, 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.layer_wrap = lwrap

        # legend
        self.legend = _card()
        self.legend.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.92)}; }}")
        lg = QVBoxLayout(self.legend)
        lg.setContentsMargins(12, 10, 12, 10)
        lg.setSpacing(4)
        lg.addWidget(_title(tr("legend")))
        for i, kind in enumerate(theme.FEATURE_ORDER):
            row = QHBoxLayout()
            sw = QLabel()
            sw.setFixedSize(12, 12)
            sw.setStyleSheet(f"background: {theme.FEATURE_COLORS[kind]}; border-radius: 3px;")
            cb = QCheckBox(tr(f"feat_{kind}"))
            cb.setChecked(kind != "travel")
            cb.setStyleSheet("QCheckBox::indicator { width: 0; height: 0; border: none; } QCheckBox:!checked { color: " + theme.TEXT_FAINT + "; }")
            cb.setCursor(Qt.PointingHandCursor)
            cb.toggled.connect(lambda v, i=i, sw=sw, kind=kind: (self.viewer.set_feature_visible(i, v), sw.setStyleSheet(
                f"background: {theme.FEATURE_COLORS[kind] if v else theme.SURFACE_3}; border-radius: 3px;")))
            row.addWidget(sw)
            row.addWidget(cb, 1)
            lg.addLayout(row)
        gwrap = QWidget()
        gwrap.setObjectName("Overlay")
        gwl = QVBoxLayout(gwrap)
        gwl.setContentsMargins(14, 14, 14, 36)
        gwl.addWidget(self.legend)
        grid.addWidget(gwrap, 0, 0, Qt.AlignBottom | Qt.AlignLeft)
        self.legend_wrap = gwrap

        # resin layer mask
        self.mask_card = _card()
        self.mask_card.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.95)}; }}")
        ml = QVBoxLayout(self.mask_card)
        ml.setContentsMargins(10, 10, 10, 10)
        ml.addWidget(_title(tr("sla_layer_image")))
        self.mask_label = QLabel()
        self.mask_label.setFixedSize(320, 200)
        self.mask_label.setAlignment(Qt.AlignCenter)
        self.mask_label.setStyleSheet("background: black; border-radius: 6px;")
        ml.addWidget(self.mask_label)
        mwrap = QWidget()
        mwrap.setObjectName("Overlay")
        mwl = QVBoxLayout(mwrap)
        mwl.setContentsMargins(14, 14, 14, 36)
        mwl.addWidget(self.mask_card)
        grid.addWidget(mwrap, 0, 0, Qt.AlignBottom | Qt.AlignLeft)
        self.mask_wrap = mwrap

        # learning assistant
        self.assistant_panel = AssistantPanel()
        # queued: run after the click is finished, so a rebuilt panel never deletes the button mid-click
        self.assistant_panel.applyRequested.connect(self._apply_suggestion, Qt.QueuedConnection)
        self.assistant_panel.dismissed.connect(self._dismiss_suggestion, Qt.QueuedConnection)
        self.assistant_panel.printReported.connect(self._on_print_report)
        self.assistant_panel.learningToggled.connect(self._on_learning_toggled)
        self.assistant_panel.resetRequested.connect(self._on_learning_reset)
        self.assistant_panel.closeRequested.connect(lambda: self.assistant_wrap.hide())
        awrap = QWidget()
        awrap.setObjectName("Overlay")
        awl = QVBoxLayout(awrap)
        awl.setContentsMargins(14, 72, 14, 14)
        awl.addWidget(self.assistant_panel)
        grid.addWidget(awrap, 0, 0, Qt.AlignTop | Qt.AlignLeft)
        self.assistant_wrap = awrap
        awrap.hide()

        # collapse / expand the side panels (state remembered)
        self.left_toggle = QToolButton()
        self.right_toggle = QToolButton()
        for b, side in ((self.left_toggle, "left"), (self.right_toggle, "right")):
            b.setObjectName("PanelToggle")
            b.setFixedSize(16, 64)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QToolButton#PanelToggle {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
                            f" border-radius: 6px; padding: 0; }} QToolButton#PanelToggle:hover {{ background: {theme.ACCENT_SOFT};"
                            f" border-color: {theme.ACCENT}; }}")
            b.clicked.connect(lambda _=False, side=side: self.toggle_panel(side))
        for b, align in ((self.left_toggle, Qt.AlignLeft), (self.right_toggle, Qt.AlignRight)):
            holder_t = QWidget()
            holder_t.setObjectName("Overlay")
            hl = QVBoxLayout(holder_t)
            hl.setContentsMargins(0, 0, 0, 120)          # low on the edge, clear of the tool palette
            hl.addWidget(b)
            grid.addWidget(holder_t, 0, 0, align | Qt.AlignBottom)

        # collision warning (top centre)
        self.collision_wrap = QWidget()
        self.collision_wrap.setObjectName("Overlay")
        cwl0 = QHBoxLayout(self.collision_wrap)
        cwl0.setContentsMargins(0, 72, 0, 0)
        banner = QFrame()
        banner.setStyleSheet(f"QFrame {{ background: {theme.DANGER}; border-radius: 10px; }} QLabel {{ color: white; font-weight: 600; }}")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.collision_label = QLabel("")
        bl.addWidget(self.collision_label)
        fix = QPushButton(tr("auto_layout"))
        fix.setStyleSheet("QPushButton { background: white; color: black; border: none; border-radius: 7px; padding: 5px 12px; font-weight: 600; }")
        fix.setCursor(Qt.PointingHandCursor)
        fix.clicked.connect(lambda: self.auto_layout(scope="all", dialog=False))
        bl.addWidget(fix)
        cwl0.addWidget(banner)
        grid.addWidget(self.collision_wrap, 0, 0, Qt.AlignTop | Qt.AlignHCenter)
        self.collision_wrap.hide()

        # view cube (top right): click a face, edge or corner to look from there
        from .viewcube import ViewCube
        self.view_cube = ViewCube(self.viewer)
        cwrap = QWidget()
        cwrap.setObjectName("Overlay")
        cwl = QVBoxLayout(cwrap)
        cwl.setContentsMargins(10, 10, 14, 10)
        cwl.addWidget(self.view_cube)
        grid.addWidget(cwrap, 0, 0, Qt.AlignTop | Qt.AlignRight)
        self.viewer.cameraChanged.connect(self.view_cube.update)

        self.layer_wrap.hide()
        self.legend_wrap.hide()
        self.mask_wrap.hide()
        return holder

    def _build_right_panel(self):
        panel = QFrame()
        panel.setObjectName("SideBar")
        panel.setFixedWidth(400)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)
        s = self.store
        overrides = {}
        try:
            overrides = json.loads(s.value("overrides", "{}"))
        except (TypeError, ValueError):
            pass
        self.settings = SettingsPanel(s.value("printer", "creality_ender3v2"), s.value("material", None),
                                      s.value("quality", None), overrides, s.value("advanced", "false") == "true")
        try:                                       # settings sections open / closed as last time
            expanded = json.loads(s.value("expanded_sections", "{}"))
            if isinstance(expanded, dict) and expanded:
                self.settings._expanded.update({str(k): bool(v) for k, v in expanded.items()})
                self.settings.rebuild()
        except (TypeError, ValueError):
            pass
        self._unsupported_timer = QTimer(self)
        self._unsupported_timer.setSingleShot(True)
        self._unsupported_timer.setInterval(300)
        self._unsupported_timer.timeout.connect(self._show_unsupported)
        # dental CAD hot folder (3Shape / exocad / Medit export here)
        from .. import dental_cad
        self._cad_tracker = dental_cad.FolderTracker()
        self._cad_timer = QTimer(self)
        self._cad_timer.setInterval(1500)
        self._cad_timer.timeout.connect(self._poll_cad_folder)
        QTimer.singleShot(0, self._start_cad_watch)
        from .remote_dialog import RemoteController
        self.remote = RemoteController(parent=self)
        self.remote.commandDone.connect(self._remote_command_done)
        QTimer.singleShot(1500, self.remote.start_if_enabled)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(120000)          # autosave every 2 minutes while there are models
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()
        self._collision_timer = QTimer(self)
        self._collision_timer.setSingleShot(True)
        self._collision_timer.setInterval(250)
        self._collision_timer.timeout.connect(self._check_collisions)
        # a burst of edits (typing, spin arrows held down) is handled once, after it settles
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(180)
        self._settings_timer.timeout.connect(self._on_settings_changed)
        self.settings.changed.connect(self._settings_timer.start)
        self.settings.valueEdited.connect(lambda k, v: self.assistant.record_override(k, v))
        self.settings.technologyChanged.connect(lambda _t: self._apply_printer())
        lay.addWidget(self.settings, 1)

        action = _card()
        al = QVBoxLayout(action)
        al.setContentsMargins(14, 14, 14, 14)
        al.setSpacing(10)
        stats = QGridLayout()
        stats.setSpacing(8)
        self.stat_time = StatTile("clock", tr("stat_time"))
        self.stat_material = StatTile("spool", tr("stat_filament"))
        self.stat_weight = StatTile("weight", tr("stat_weight"))
        self.stat_cost = StatTile("coin", tr("stat_cost"))
        stats.addWidget(self.stat_time, 0, 0)
        stats.addWidget(self.stat_material, 0, 1)
        stats.addWidget(self.stat_weight, 1, 0)
        stats.addWidget(self.stat_cost, 1, 1)
        al.addLayout(stats)
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("Warning")
        self.warn_label.setWordWrap(True)
        self.warn_label.hide()
        al.addWidget(self.warn_label)
        self.cup_fix_btn = QPushButton(tr("cup_fix"))
        self.cup_fix_btn.setObjectName("Primary")
        self.cup_fix_btn.setIcon(theme.icon("sparkle", theme.ON_ACCENT, 16))
        self.cup_fix_btn.setToolTip(tr("cup_fix_tip"))
        self.cup_fix_btn.setCursor(Qt.PointingHandCursor)
        self.cup_fix_btn.clicked.connect(self.fix_suction_cups)
        self.cup_fix_btn.hide()
        al.addWidget(self.cup_fix_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.hide()
        al.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Faint")
        self.progress_label.hide()
        al.addWidget(self.progress_label)
        btns = QHBoxLayout()
        self.slice_btn = QPushButton(theme.icon("layers", "#ffffff", 18), "  " + tr("slice"))
        self.slice_btn.setObjectName("Primary")
        self.slice_btn.setIconSize(QSize(18, 18))
        self.slice_btn.setCursor(Qt.PointingHandCursor)
        self.slice_btn.clicked.connect(lambda: self._act("slice", "button", self.slice_or_cancel))
        self.export_btn = QPushButton(theme.icon("download", "#08251a", 18), "  " + tr("export_gcode"))
        self.export_btn.setObjectName("Success")
        self.export_btn.setIconSize(QSize(18, 18))
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(lambda: self._act("export", "button", self.export))
        self.send_btn = QPushButton(theme.icon("wifi", theme.TEXT, 18), "")
        self.send_btn.setToolTip(tr("net_send_tip"))
        self.send_btn.setIconSize(QSize(18, 18))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(lambda: self._act("send", "button", self.send_to_printer))
        btns.addWidget(self.slice_btn, 1)
        btns.addWidget(self.export_btn, 1)
        btns.addWidget(self.send_btn)
        al.addLayout(btns)
        lay.addWidget(action)
        return panel

    def _install_shortcuts(self):
        def via_key(name, fn):
            return lambda: self._act(name, "shortcut", fn)

        for seq, fn in ((QKeySequence.Open, via_key("open", self.open_files)), (QKeySequence.Delete, self.delete_selected),
                        (QKeySequence("A"), via_key("arrange", self.arrange_objects)),
                        (QKeySequence("Ctrl+D"), via_key("duplicate", self.duplicate_selected)),
                        (QKeySequence.SelectAll, self.select_all),
                        (QKeySequence("Ctrl+["), lambda: self.toggle_panel("left")),
                        (QKeySequence("Ctrl+]"), lambda: self.toggle_panel("right")),
                        (QKeySequence("Shift+A"), lambda: self.auto_layout()),
                        (QKeySequence("S"), lambda: self._set_pick_mode(None if self.viewer.pick_mode == "add" else "add")
                         if self.supports_card.isVisible() else None),
                        (QKeySequence("Shift+S"), lambda: self._set_pick_mode(None if self.viewer.pick_mode == "remove" else "remove")
                         if self.supports_card.isVisible() else None),
                        (QKeySequence("Ctrl+R"), via_key("slice", self.slice_or_cancel)),
                        (QKeySequence("Ctrl+E"), via_key("export", self.export)),
                        (QKeySequence("Up"), lambda: self._step_layer(1)), (QKeySequence("Down"), lambda: self._step_layer(-1)),
                        (QKeySequence.Undo, via_key("undo", self.undo)), (QKeySequence("Ctrl+Y"), self.redo),
                        (QKeySequence.Redo, self.redo), (QKeySequence.Save, via_key("save_project", self.save_project)), (QKeySequence("Escape"), lambda: (self._set_pick_mode(None), self.set_tool("move"))),
                        (QKeySequence("M"), lambda: self.set_tool("move")), (QKeySequence("R"), lambda: self.set_tool("rotate")),
                        (QKeySequence("F"), lambda: self.set_tool("face")), (QKeySequence("H"), lambda: self.set_tool("hole")),
                        (QKeySequence("D"), lambda: self.set_tool("measure"))):
            act = QAction(self)
            act.setShortcut(seq)
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(fn)
            self.addAction(act)

    # ---------------------------------------------------------- section view / unsupported areas
    def _toggle_section(self, on: bool):
        self.section_slider.setVisible(on)
        self.section_label.setVisible(on)
        if not on:
            self.viewer.section_z = 1e9
            self.viewer.update()
        else:
            self._on_section(self.section_slider.value())

    def _on_section(self, value):
        """Clipping view (as Asiga Composer's clipping tool): cut every model at a height to look inside."""
        top = max((o.world_bounds()[1, 2] + (self._lift() if self._shows_supports(o) else 0) for o in self.objects), default=1.0)
        z = top * value / 1000.0
        self.viewer.section_z = z if value < 1000 else 1e9
        self.section_label.setText(f"Z {z:.2f} mm")
        self.viewer.update()

    def _show_unsupported(self):
        """Red overlay on overhangs that no support reaches (PreForm's "show minima")."""
        if not self.minima_btn.isChecked() or self.viewer.technology != "sla":
            self.viewer.set_warning_mesh(None)
            return
        cfg = self.settings.config()
        parts, count = [], 0
        for o in self.objects:
            if o.role == "cutter" or o.direct:
                continue
            lift = self._lift(cfg)
            world, geo = self._support_geometry(o, lift)
            if o.support_points is not None:
                pts = o.world_support_points(lift)
            elif cfg["support_enable"]:
                if "auto_points" not in geo:
                    geo["auto_points"] = sla_supports.auto_support_points(world, cfg)
                pts = geo["auto_points"]
            else:
                pts = np.zeros((0, 3))
            faces = sla_supports.unsupported_faces(world, cfg, pts)
            if len(faces):
                count += 1
                part = Mesh(world.vertices, world.faces[faces])
                if not self._shows_supports(o):
                    part = part.translated([0, 0, -lift])
                parts.append(part)
        self.viewer.set_warning_mesh(Mesh.merge(parts) if parts else None)
        self.status.setText(tr("unsupported_found").format(count) if parts else tr("unsupported_none"))

    def support_all_islands(self):
        """Add one support at every island found by the last slice (PreForm's "support all minima")."""
        if self.result is None or self.result[0] != "sla" or not self.result[1].islands:
            self.status.setText(tr("islands_none"))
            return
        job = self.result[1]
        pts = sla_supports.island_points(job.layers, job.layer_z, job.islands)
        cfg = self.settings.config()
        lift = self._lift(cfg)
        self._push_undo()
        added = 0
        for p in pts:
            for o in self.objects:
                if o.role == "cutter" or o.direct:
                    continue
                b = o.world_bounds()
                if not (b[0, 0] - 0.5 <= p[0] <= b[1, 0] + 0.5 and b[0, 1] - 0.5 <= p[1] <= b[1, 1] + 0.5):
                    continue
                world, geo = self._support_geometry(o, lift)
                if "caster" not in geo:
                    geo["caster"] = sla_supports._Caster(world)
                contact = sla_supports.underside_contact(geo["caster"], p + [0, 0, 0.3])
                if contact is None:
                    continue
                if o.support_points is None:
                    o.set_world_support_points(sla_supports.auto_support_points(world, cfg), lift)
                o.set_world_support_points(np.vstack([o.world_support_points(lift), contact[None, :]]), lift)
                added += 1
                break
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("islands_supported").format(added))

    def set_panel_visible(self, side: str, visible: bool, save: bool = True):
        panel = self.left_panel if side == "left" else self.right_panel
        panel.setVisible(visible)
        button = self.left_toggle if side == "left" else self.right_toggle
        # the leading panel is on the right in Hebrew (right-to-left) layout
        physical_left = (side == "left") != (self.layoutDirection() == Qt.RightToLeft)
        points_left = physical_left == visible            # arrow shows where the panel will go
        button.setIcon(theme.icon("chevron_left" if points_left else "chevron", theme.TEXT_DIM, 14))
        button.setToolTip(tr("panel_hide") if visible else tr("panel_show"))
        if save:
            self.store.setValue(f"{side}_panel", "true" if visible else "false")

    def toggle_panel(self, side: str):
        panel = self.left_panel if side == "left" else self.right_panel
        self.set_panel_visible(side, not panel.isVisible())

    def _on_gl_failed(self, message: str):
        text = tr("gl_error").format(message)
        self.status.setText(text)
        self.empty_hint.setText(text)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setMaximumWidth(520)
        self.empty_hint.setStyleSheet(f"color: {theme.WARNING}; font-size: 14px; background: transparent;")
        self.empty_hint.show()
        self._gl_failed = True

    # ================================================================ objects
    def selected(self) -> SceneObject | None:
        item = self.object_list.currentItem()
        if item is None:
            return None
        uid = item.data(Qt.UserRole)
        return next((o for o in self.objects if o.uid == uid), None)

    def selected_objects(self) -> list[SceneObject]:
        """Every selected object (Ctrl/Shift-click, box select, Ctrl+A); the current one first."""
        uids = [it.data(Qt.UserRole) for it in self.object_list.selectedItems()]
        cur = self.selected()
        if cur is not None and cur.uid not in uids:
            uids.insert(0, cur.uid)
        elif cur is not None:
            uids.remove(cur.uid)
            uids.insert(0, cur.uid)
        by_uid = {o.uid: o for o in self.objects}
        return [by_uid[u] for u in uids if u in by_uid]

    def select_all(self):
        if self.objects:
            self._syncing = True
            self.object_list.selectAll()
            self._syncing = False
            self._on_list_multi()

    def open_files(self):
        last = self.store.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("open"), last, tr("file_filter"))
        if paths:
            self.store.setValue("last_dir", os.path.dirname(paths[0]))
            self.load_files(paths)
            for p in paths:
                self._remember_file(p)

    def load_files(self, paths):
        added = None
        for path in paths:
            try:
                meshes = load_mesh(path)
            except Exception as exc:
                meshes = self._rescue_model(path, exc)     # the assistant tries to recover the file
                if not meshes:
                    continue
            for m in meshes:
                added = self.add_mesh(m, rearrange=False)
                self.status.setText(tr("loaded").format(m.name, len(m.faces)))
            self._import_project_filaments(meshes)
        if added is not None:
            self.arrange_objects()
            self.viewer.fit_objects()

    # ================================================================ filaments (multi-colour FDM)
    def _import_project_filaments(self, meshes):
        """A Bambu / Orca / Creality Print project names its filaments: take their colours and types."""
        info = next((getattr(m, "project_filaments", None) for m in meshes if getattr(m, "project_filaments", None)), None)
        if not info or not info.get("colours"):
            return
        from .. import filaments as fil
        cfg = self.settings.config()
        used = max([n for o in self.objects for n in o.filaments_used()] + [1])
        count = max(int(cfg["filament_count"]), min(16, max(used, len(info["colours"]))))
        colours = [fil.normalise_colour(c) for c in info["colours"]][:16]
        types = [t.upper() for t in info.get("types", [])][:16]
        old_c = cfg["filament_colors"].split(";")
        old_t = cfg["filament_types"].split(";")
        colours += old_c[len(colours):count]
        types += old_t[len(types):count]
        self.settings.set_value("filament_count", count)
        self.settings.set_value("filament_colors", ";".join(colours[:count]))
        self.settings.set_value("filament_types", ";".join(t or "PLA" for t in types[:count]))
        if cfg["technology"] == "fdm":
            self.status.setText(tr("fil_imported").format(count))
        self._objects_changed()

    def filament_colour_rgb(self, number: int, cfg=None):
        from .. import filaments as fil
        cfg = cfg or self.settings.config()
        flist = fil.filament_list(cfg)
        k = max(0, int(number) - 1)
        return fil.rgb(flist[k].colour) if k < len(flist) else fil.rgb(fil.DEFAULT_COLOURS[k % len(fil.DEFAULT_COLOURS)])

    def _multi_filament_view(self, cfg) -> bool:
        return cfg["technology"] == "fdm" and (int(cfg["filament_count"]) > 1 or
                                               any(o.filaments_used() != [1] for o in self.objects))

    def set_filament_selected(self, number: int):
        """Print the selected models with filament ``number`` (1 = T0); adds filament slots if needed."""
        group = self.selected_objects()
        if not group:
            return
        self._push_undo()
        for o in group:
            o.filament = int(number)
            o.filament_parts = []
        cfg = self.settings.config()
        if number > int(cfg["filament_count"]):
            self.settings.set_value("filament_count", int(number))
        self._objects_changed()
        self.status.setText(tr("fil_assigned").format(number, len(group)))

    def _fill_filament_menu(self, menu):
        from PySide6.QtGui import QColor, QIcon, QPixmap
        from .. import filaments as fil
        cfg = self.settings.config()
        flist = fil.filament_list(cfg)
        group = self.selected_objects()
        current = {n for o in group for n in o.filaments_used()}
        for k in range(max(len(flist), min(16, len(flist) + 1))):
            colour = flist[k].colour if k < len(flist) else fil.DEFAULT_COLOURS[k % len(fil.DEFAULT_COLOURS)]
            pm = QPixmap(16, 16)
            pm.fill(QColor(colour))
            label = (tr("fil_n").format(k + 1) + (f"  ·  {flist[k].type}" if k < len(flist) else "  ·  " + tr("fil_new")))
            act = menu.addAction(QIcon(pm), label, lambda n=k + 1: self.set_filament_selected(n))
            act.setCheckable(True)
            act.setChecked(current == {k + 1})
        if any(o.filament_parts for o in group):
            menu.addSeparator()
            note = menu.addAction(tr("fil_parts_note"))
            note.setEnabled(False)
        menu.addSeparator()
        menu.addAction(theme.icon("settings"), tr("fil_edit") + "…", self.filaments_dialog)

    def filaments_dialog(self):
        """Edit the print's filaments: count, colour, type and temperature of each."""
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QComboBox, QGridLayout, QLabel, QPushButton, QSpinBox
        from .. import filaments as fil
        cfg = self.settings.config()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("fil_title"))
        lay = QVBoxLayout(dlg)
        intro = QLabel(tr("fil_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        count = QSpinBox()
        count.setRange(1, 16)
        count.setValue(int(cfg["filament_count"]))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("fil_count")))
        row.addWidget(count)
        row.addStretch(1)
        lay.addLayout(row)
        grid = QGridLayout()
        lay.addLayout(grid)
        state = {"rows": []}
        types = ("PLA", "PETG", "ABS", "ASA", "TPU", "PA", "PC", "PVA", "HIPS")

        def build():
            while grid.count():
                w = grid.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            flist = fil.filament_list(self.settings.config().update({"filament_count": count.value(),
                                                                         "filament_colors": ";".join(r["colour"] for r in state["rows"])
                                                                         if state["rows"] else cfg["filament_colors"],
                                                                         "filament_types": ";".join(r["type"].currentText() for r in state["rows"])
                                                                         if state["rows"] else cfg["filament_types"]}))
            old = state["rows"]
            state["rows"] = []
            for h, text in enumerate((tr("fil_slot"), tr("fil_colour"), tr("fil_type"), tr("fil_temp"))):
                lab = QLabel(f"<b>{text}</b>")
                grid.addWidget(lab, 0, h)
            for k, f in enumerate(flist):
                r = {"colour": old[k]["colour"] if k < len(old) else f.colour}
                grid.addWidget(QLabel(f"T{k}  ({tr('fil_n').format(k + 1)})"), k + 1, 0)
                btn = QPushButton(r["colour"])
                btn.setStyleSheet(f"background:{r['colour']}; color:{'#000' if sum(fil.rgb(r['colour'])) > 1.5 else '#fff'}")

                def pick(_=False, r=r, btn=btn):
                    c = QColorDialog.getColor(QColor(r["colour"]), dlg, tr("fil_colour"))
                    if c.isValid():
                        r["colour"] = c.name().upper()
                        btn.setText(r["colour"])
                        btn.setStyleSheet(f"background:{r['colour']}; color:{'#000' if sum(fil.rgb(r['colour'])) > 1.5 else '#fff'}")
                btn.clicked.connect(pick)
                grid.addWidget(btn, k + 1, 1)
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItems(types)
                combo.setCurrentText(old[k]["type"].currentText() if k < len(old) else f.type)
                r["type"] = combo
                grid.addWidget(combo, k + 1, 2)
                temp = QSpinBox()
                temp.setRange(150, 350)
                temp.setSuffix(" °C")
                temp.setValue(old[k]["temp"].value() if k < len(old) else f.temp)
                r["temp"] = temp
                grid.addWidget(temp, k + 1, 3)
                state["rows"].append(r)

        build()
        count.valueChanged.connect(lambda _v: build())
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton(tr("apply"))
        ok.setDefault(True)
        cancel = QPushButton(tr("cancel"))
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        if dlg.exec() != QDialog.Accepted:
            return
        rows = state["rows"]
        base_temp = int(cfg["nozzle_temp"])
        temps = [r["temp"].value() for r in rows]
        self.settings.set_value("filament_count", len(rows))
        self.settings.set_value("filament_colors", ";".join(r["colour"] for r in rows))
        self.settings.set_value("filament_types", ";".join(r["type"].currentText().strip().upper() or "PLA" for r in rows))
        self.settings.set_value("filament_temps", "" if all(t == base_temp for t in temps) else ";".join(map(str, temps)))
        self._objects_changed()

    def _rescue_model(self, path: str, error: Exception) -> list:
        """A model that did not load: recover it without losing quality, and learn what worked."""
        from .. import model_rescue
        name = os.path.basename(path)
        data_sig = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                with open(path, "rb") as fh:
                    data_sig = model_rescue.signature(path, fh.read(), error)
            except OSError:
                pass
            hint = self.assistant.rescue_order(data_sig) if data_sig else []
            result = model_rescue.rescue(path, error, hint)
        except model_rescue.RescueFailed as fail:
            QApplication.restoreOverrideCursor()
            self.assistant.learn_rescue(getattr(fail, "signature", data_sig or ""), None)
            QMessageBox.warning(self, tr("load_error"), tr("rs_failed").format(name) + "\n\n" +
                                tr("rs_why_" + (fail.reason if fail.reason in model_rescue.REASONS else "unknown")) +
                                (f"\n\n({fail.detail})" if fail.detail else "") + f"\n\n{type(error).__name__}: {error}")
            return []
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        self.assistant.learn_rescue(result.signature, result.strategy)
        lines = [f"• {tr(k).format(*a)}" for k, a in result.notes] or [f"• {tr('rs_generic')}"]
        quality = tr("rs_quality").format(result.kept, result.dropped)
        text = tr("rs_done").format(name) + "\n\n" + "\n".join(lines) + "\n\n" + quality
        self.status.setText(tr("rs_status").format(name))
        QMessageBox.information(self, tr("rs_title"), text)
        return result.meshes

    def add_mesh(self, mesh, rearrange: bool = True):
        self._push_undo()
        obj = SceneObject.from_mesh(mesh)
        cfg = self.settings.config()
        obj.x, obj.y = self._plate_center(cfg)
        self.objects.append(obj)
        if rearrange:
            self._arrange(cfg)
        self._refresh_objects(select=obj.uid)
        return obj

    def duplicate_selected(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            dups = [o.duplicate() for o in group]
            self.objects += dups
            cfg = self.settings.config()
            self._arrange(cfg)
            self._refresh_objects(select=[d.uid for d in dups])

    def delete_selected(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for obj in group:
                self.objects.remove(obj)
                self.viewer.remove_object(obj.uid)
            self._refresh_objects(select=self.objects[-1].uid if self.objects else None)

    def _arrange(self, cfg) -> bool:
        """Auto layout of every part with the learned options (used after adding parts). True if all fit."""
        from .. import layout
        opts = self._layout_defaults()
        options = layout.LayoutOptions(**{k: opts[k] for k in ("strategy", "spacing", "margin", "rotation", "anchor", "sort")})
        return not layout.auto_layout(self.objects, plate_area(cfg), options)

    def arrange_objects(self):
        """Quick auto layout (A) with the options learned from earlier layouts."""
        self.auto_layout(dialog=False)

    def _fill_plate_quick(self):
        if not self.selected():
            self.status.setText(tr("select_first"))
            return
        from .. import layout
        opts = self._layout_defaults()
        self._push_undo()
        options = layout.LayoutOptions(**{k: opts[k] for k in ("strategy", "spacing", "margin", "rotation", "anchor", "sort")})
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            added = layout.fill_plate(self.objects, self.selected(), plate_area(self.settings.config()), options)
        finally:
            QApplication.restoreOverrideCursor()
        self.objects += added
        self._refresh_objects(select=self.selected().uid)
        self.status.setText(tr("fill_done").format(len(added)))

    def _layout_defaults(self) -> dict:
        """Options for Auto layout and Quick layout: what was used last time (remembered between sessions),
        on top of what the assistant learned."""
        cfg = self.settings.config()
        base = {"strategy": "contour", "spacing": cfg["arrange_spacing"], "margin": cfg["arrange_margin"],
                "rotation": "none", "anchor": "center", "sort": "area"}
        out = self.assistant.learned_layout(base)
        try:
            last = json.loads(self.store.value("layout_options", "{}"))
        except (TypeError, ValueError):
            last = {}
        for k in base:
            if k in last:
                out[k] = last[k]
        return out

    def auto_layout(self, scope: str | None = None, dialog: bool = True):
        """Auto layout with PreForm / CHITUBOX / Composer style options; the assistant learns the choices."""
        from .. import layout
        if not self.objects:
            return
        opts = self._layout_defaults()
        opts["scope"] = scope or ("selected" if len(self.selected_objects()) > 1 and dialog else "all")
        fill = False
        if dialog:
            got = self._layout_dialog(opts)
            if got is None:
                return
            opts, fill = got
        learned = opts.pop("learned", False)
        self._push_undo()
        cfg = self.settings.config()
        plate = plate_area(cfg)
        options = layout.LayoutOptions(**{k: opts[k] for k in ("strategy", "spacing", "margin", "rotation", "scope",
                                                               "anchor", "sort")})
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if fill and self.selected():
                added = layout.fill_plate(self.objects, self.selected(), plate, options)
                self.objects += added
                failed = []
                self.status.setText(tr("fill_done").format(len(added)))
            else:
                failed = layout.auto_layout(self.objects, plate, options, self.selected_objects())
        finally:
            QApplication.restoreOverrideCursor()
        if dialog:
            self.assistant.record_layout_choice(opts)
            self.store.setValue("layout_options", json.dumps({k: opts[k] for k in ("strategy", "spacing", "margin",
                                                                                   "rotation", "anchor", "sort")}))
        self._layout_pending = True            # the final arrangement at slicing time is a lesson
        self._refresh_objects(select=[o.uid for o in self.selected_objects()] or None)
        if failed:
            self.status.setText(tr("layout_failed").format(len(failed)))
        elif not fill:
            self.status.setText(tr("layout_done_learned") if learned else tr("layout_done"))

    def _layout_dialog(self, opts: dict):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("auto_layout"))
        form = QFormLayout(dlg)

        def combo(items, current):
            c = QComboBox()
            for value, key in items:
                c.addItem(tr(key), value)
            c.setCurrentIndex(max(0, c.findData(current)))
            return c
        strategy = combo((("contour", "lay_contour"), ("box", "lay_box"), ("grid", "lay_grid")), opts["strategy"])
        rotation = combo((("none", "lay_rot_none"), ("90", "lay_rot_90"), ("free", "lay_rot_free")), opts["rotation"])
        scope = combo((("all", "lay_all"), ("selected", "lay_selected"), ("outside", "lay_outside")), opts["scope"])
        anchor = combo((("center", "lay_center"), ("front_left", "lay_front_left"), ("front_right", "lay_front_right"),
                        ("back_left", "lay_back_left"), ("back_right", "lay_back_right")), opts["anchor"])
        sort = combo((("area", "lay_sort_area"), ("height", "lay_sort_height"), ("name", "lay_sort_name"),
                      ("none", "lay_sort_none")), opts["sort"])
        spacing = _spin(0, 50, 0.5, 1, " mm", 110)
        spacing.setValue(opts["spacing"])
        margin = _spin(0, 30, 0.5, 1, " mm", 110)
        margin.setValue(opts["margin"])
        fill = QCheckBox(tr("lay_fill"))
        fill.setToolTip(tr("lay_fill_tip"))
        fill.setEnabled(len(self.selected_objects()) == 1)
        for label, w in (("lay_strategy", strategy), ("lay_spacing", spacing), ("lay_margin", margin),
                         ("lay_rotation", rotation), ("lay_scope", scope), ("lay_anchor", anchor), ("lay_sort", sort)):
            form.addRow(tr(label), w)
        form.addRow(fill)
        if opts.get("learned"):
            note = QLabel(tr("lay_learned_note"))
            note.setObjectName("Faint")
            note.setWordWrap(True)
            form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return None
        out = {"strategy": strategy.currentData(), "spacing": spacing.value(), "margin": margin.value(),
               "rotation": rotation.currentData(), "scope": scope.currentData(), "anchor": anchor.currentData(),
               "sort": sort.currentData(), "learned": opts.get("learned", False)}
        return out, fill.isChecked()

    def lay_flat(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for obj in group:
                obj.lay_flat()
                self._drop_supports(obj)
            self._objects_changed()

    def center_selected(self):
        """Move the selection (as one group) to the middle of the plate."""
        group = self.selected_objects()
        if group:
            self._push_undo()
            cx, cy = self._plate_center(self.settings.config())
            gx, gy = np.mean([o.x for o in group]), np.mean([o.y for o in group])
            for obj in group:
                obj.x += cx - gx
                obj.y += cy - gy
            self._objects_changed()

    def reset_transform(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for obj in group:
                obj.rotation[:] = 0
                obj.scale[:] = 1
                obj.mirror[:] = 1
                obj.base_rotation = np.eye(3)
                self._drop_supports(obj)
            self._objects_changed()

    def auto_orient_selected(self):
        targets = self._support_targets()
        if not targets:
            return
        self._push_undo()
        cfg = self.settings.config()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            tech = cfg["technology"]
            for o in targets:
                o.auto_orient(tech, cfg["support_angle"], self.assistant.orient_weights(tech))
                self._drop_supports(o)
                # remember the choice; if the user turns the model afterwards, that is a lesson
                mesh = o.mesh.transform(o.matrix())
                o.__dict__["_auto_orient_choice"] = (tech, o.matrix().tobytes(),
                                             orient.current_features(mesh, tech, cfg["support_angle"]))
        finally:
            QApplication.restoreOverrideCursor()
        self._objects_changed()

    def auto_prepare(self):
        """PreForm-style one click: orient every model, arrange the plate and (for resin) add supports."""
        if not self.objects:
            self.status.setText(tr("no_models"))
            return
        self._select_uid(None)
        self.auto_orient_selected()
        cfg = self.settings.config()
        if not self._arrange(cfg):
            self.status.setText(tr("arrange_full"))
        if cfg["technology"] == "sla":
            self.generate_supports()
        self._objects_changed()
        self.viewer.fit_objects()
        self.status.setText(tr("auto_prepare_done"))

    # ---------------------------------------------------------- manual orientation
    def set_tool(self, name: str):
        self.edit_bar.hide()
        for n, b in self.tool_buttons.items():
            b.setChecked(n == name)
        self.viewer.tool = name
        self.viewer.setCursor(Qt.CrossCursor if name in ("face", "hole", "measure") else Qt.ArrowCursor)
        if name != "measure":
            self.viewer.measure_points = []
            self.viewer.measure_hover = None
            self.viewer.measure_label.hide()
        if name != "move":
            self._set_pick_mode(None)
            if self.viewer.mode != "prepare":
                self.set_mode("prepare")
        hint = {"rotate": "tool_rotate_hint", "face": "tool_face_hint", "hole": "tool_hole_hint",
                "measure": "tool_measure_hint"}.get(name)
        if hint:
            self.status.setText(tr(hint))
        self.viewer.update()

    @staticmethod
    def _axis_rotation(axis: int, degrees: float) -> np.ndarray:
        a = np.radians(degrees)
        c, s = np.cos(a), np.sin(a)
        i, j = (axis + 1) % 3, (axis + 2) % 3
        r = np.eye(3)
        r[i, i], r[i, j], r[j, i], r[j, j] = c, -s, s, c
        return r

    def _rotate_group(self, group, axis: int, degrees: float):
        """Turn each object about its own centre; around Z the group also turns as a whole."""
        rot = self._axis_rotation(axis, degrees)
        if axis == 2 and len(group) > 1:
            gx, gy = np.mean([o.x for o in group]), np.mean([o.y for o in group])
            for o in group:
                o.x, o.y = (rot[:2, :2] @ [o.x - gx, o.y - gy]) + [gx, gy]
        for o in group:
            o.apply_world_rotation(rot)

    def rotate_selected(self, axis: int, degrees: float):
        group = self.selected_objects()
        if not group:
            self.status.setText(tr("select_first"))
            return
        self._push_undo()
        self._rotate_group(group, axis, degrees)
        for obj in group:
            self._drop_supports(obj)
        self._objects_changed()

    def _on_rotate_start(self, uid):
        group = self.selected_objects()
        if any(o.uid == uid for o in group):
            self._push_undo()
            self._rot_start = [(o, o.state()) for o in group]

    def _on_rotate_drag(self, uid, axis, degrees):
        if not getattr(self, "_rot_start", None):
            return
        group = [o for o, _ in self._rot_start]
        for o, st in self._rot_start:
            o.restore(st)
        self._rotate_group(group, axis, degrees)
        for o in group:
            self.viewer.set_object_transform(o.uid, self._display_matrix(o))
        self.status.setText(f"{'XYZ'[axis]}: {degrees:+.0f}°   ·   {tr('rotate_snap_hint')}")

    def _on_rotate_end(self, uid):
        start, self._rot_start = getattr(self, "_rot_start", None), None
        if start:
            for o, _ in start:
                self._drop_supports(o)
            self._objects_changed()

    def _on_face_picked(self, uid, normal):
        obj = next((o for o in self.objects if o.uid == uid), None)
        if obj is None:
            return
        from ..mesh import rotation_between
        self._push_undo()
        obj.apply_world_rotation(rotation_between(np.asarray(normal, dtype=float), np.array([0.0, 0.0, -1.0])))
        self._drop_supports(obj)
        self._objects_changed()
        self.status.setText(tr("face_done"))

    def _on_hole_picked(self, uid, point, normal):
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is None:
            return
        self._push_undo()
        lift = self._lift() if self._shows_supports(o) else 0.0
        existing = o.world_drain_holes(lift)
        reach = max(2.0, self.settings.config()["drain_hole_diameter"])
        if len(existing):
            d = np.linalg.norm(existing[:, :3] - np.asarray(point), axis=1)
            k = int(np.argmin(d))
            if d[k] < reach:              # clicking an existing hole removes it
                o.drain_holes = np.delete(o.drain_holes, k, axis=0)
                self._objects_changed(refresh_list=False)
                self.status.setText(tr("hole_removed"))
                return
        o.add_world_drain_hole(point, normal, lift)
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("hole_added").format(len(o.drain_holes)))

    def _hole_preview(self, o: SceneObject, cfg):
        """Red cylinders marking manual drain holes, in the object's own coordinates."""
        if len(o.drain_holes) == 0:
            return None
        lift = self._lift(cfg) if self._shows_supports(o) else 0.0
        tubes = sla.hole_meshes([o.world_drain_holes(lift)], cfg)
        return Mesh.merge(tubes).transform(np.linalg.inv(self._display_matrix(o)))

    def _on_measured(self, a, b):
        d = np.asarray(b) - np.asarray(a)
        self.status.setText(tr("measure_result").format(float(np.linalg.norm(d)), *np.abs(d)))

    def _toggle_cutter(self, on):
        if self._syncing:
            return
        group = self.selected_objects()
        if group:
            self._push_undo()
            for obj in group:
                obj.role = "cutter" if on else "model"
                obj.support_points = None
            self._refresh_objects(select=[o.uid for o in group])

    def add_text(self):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit
        from .. import mesh_tools
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("add_text"))
        form = QFormLayout(dlg)
        text = QLineEdit("UniSlicer")
        height, depth = _spin(1, 200, 1, 1, " mm", 110), _spin(0.2, 50, 0.1, 2, " mm", 110)
        height.setValue(8.0)
        depth.setValue(1.5)
        mode = QComboBox()
        mode.addItem(tr("text_emboss"), "model")
        mode.addItem(tr("text_engrave"), "cutter")
        form.addRow(tr("text_text"), text)
        form.addRow(tr("text_height"), height)
        form.addRow(tr("text_depth"), depth)
        form.addRow(tr("text_mode"), mode)
        note = QLabel(tr("text_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted or not text.text().strip():
            return
        try:
            mesh = mesh_tools.text_mesh(text.text().strip(), height.value(), depth.value())
        except ValueError as exc:
            QMessageBox.warning(self, tr("add_text"), str(exc))
            return
        obj = self.add_mesh(mesh, rearrange=False)
        obj.role = mode.currentData()
        sel = self.selected()
        self._refresh_objects(select=obj.uid)
        if sel is not None:
            obj.x, obj.y = sel.x, sel.y
        self._objects_changed()

    def cut_selected(self):
        from .. import mesh_tools
        obj = self.selected()
        if not obj:
            self.status.setText(tr("select_first"))
            return
        b = obj.world_bounds()
        z, ok = QInputDialog.getDouble(self, tr("cut_height"), tr("cut_height_q"), round((b[0, 2] + b[1, 2]) / 2, 2),
                                       b[0, 2] + 0.01, b[1, 2] - 0.01, 2)
        if not ok:
            return
        try:
            lo, hi = mesh_tools.cut_mesh(obj.world_mesh(), z)
        except Exception as exc:
            QMessageBox.warning(self, tr("cut_height"), str(exc))
            return
        self._push_undo()
        self._replace_with_parts(obj, [lo, hi])

    def split_selected(self):
        from .. import mesh_tools
        obj = self.selected()
        if not obj:
            self.status.setText(tr("select_first"))
            return
        parts = mesh_tools.split_parts(obj.world_mesh())
        if len(parts) < 2:
            self.status.setText(tr("split_single"))
            return
        self._push_undo()
        self._replace_with_parts(obj, parts)

    def _replace_with_parts(self, obj, meshes):
        """Swap ``obj`` for new objects made from world-space ``meshes``, keeping them where they were."""
        idx = self.objects.index(obj)
        new = []
        for m in meshes:
            if len(m.faces) == 0:
                continue
            b = m.bounds
            o = SceneObject.from_mesh(m)
            o.x, o.y, o.z = (b[0, 0] + b[1, 0]) / 2, (b[0, 1] + b[1, 1]) / 2, max(0.0, b[0, 2])
            new.append(o)
        self.objects[idx:idx + 1] = new
        self.viewer.remove_object(obj.uid)
        self._refresh_objects(select=new[0].uid if new else None)
        self.status.setText(tr("parts_made").format(len(new)))

    def repair_selected(self):
        from .. import mesh_tools
        targets = self._support_targets()
        if not targets:
            return
        self._push_undo()
        totals = {"duplicates": 0, "flipped": 0, "holes_filled": 0}
        for o in targets:
            fixed, rep = mesh_tools.repair(o.mesh)
            o.mesh = fixed
            o.regions = []                        # faces changed: shaded regions no longer apply
            for k in totals:
                totals[k] += rep[k]
        self._objects_changed()
        self.status.setText(tr("repair_done").format(totals["flipped"], totals["holes_filled"], totals["duplicates"]))

    def open_layer_editor(self):
        """Paint / erase pixels on the sliced layers (resin), as in CHITUBOX Pro."""
        if self.result is None or self.result[0] != "sla":
            QMessageBox.information(self, tr("pixel_edit"), tr("pixel_need_slice"))
            return
        from .layer_editor import LayerEditor
        job, cfg = self.result[1], self.result[2]
        dlg = LayerEditor(job, cfg, self.layer_slider.value(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        changed = dlg.apply()
        if not changed:
            return
        self._islands = {}
        for i, poly in job.islands:
            self._islands.setdefault(i, []).append(poly)
        self.island_btn.setVisible(bool(self._islands))
        self.island_fix_btn.setVisible(bool(self._islands))
        self.stat_material.set(f"{job.volume_ml:.1f} ml", tr("stat_resin"))
        self._on_layer(self.layer_slider.value())
        self.status.setText(tr("pixel_done").format(changed))

    def repair_advanced_dialog(self):
        """Advanced repair: analysis first, chosen fixes, and a before / after report."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        from .. import mesh_tools
        targets = [o for o in self._support_targets() if o.role != "cutter"]
        if not targets:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("repair_advanced"))
        dlg.setMinimumWidth(520)
        form = QFormLayout(dlg)

        def describe(a):
            return tr("repair_report").format(a["faces"], a["open_edges"], a["nonmanifold_edges"], a["degenerate"],
                                              a["duplicates"], a["shells"], "✓" if a["watertight"] else "✗")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            info = "\n\n".join(f"{o.name}:\n{describe(mesh_tools.analyze(o.mesh))}" for o in targets[:4])
        finally:
            QApplication.restoreOverrideCursor()
        head = QLabel(info)
        head.setWordWrap(True)
        form.addRow(head)
        weld = _spin(0.0, 1.0, 0.001, 4, " mm", 110)
        weld.setValue(0.0001)
        weld.setToolTip(tr("repair_weld_tip"))
        shells = _spin(0.0, 50.0, 0.5, 1, " %", 110)
        shells.setValue(1.0)
        shells.setToolTip(tr("repair_shells_tip"))
        nonman = QCheckBox(tr("repair_nonmanifold"))
        nonman.setChecked(True)
        holes = QCheckBox(tr("repair_holes"))
        holes.setChecked(True)
        form.addRow(tr("repair_weld"), weld)
        form.addRow(tr("repair_shells"), shells)
        form.addRow(nonman)
        form.addRow(holes)
        note = QLabel(tr("repair_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("repair_run"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        self._push_undo()
        lines = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for o in targets:
                fixed, rep = mesh_tools.repair_advanced(o.mesh, weld.value(), shells.value() / 100.0,
                                                        nonman.isChecked(), holes.isChecked())
                o.mesh = fixed
                o.regions = []                    # faces changed: shaded regions no longer apply
                self._drop_supports(o)
                lines.append(f"{o.name}: " + tr("repair_result").format(
                    rep["degenerate_removed"], rep["duplicates_removed"], rep["inner_walls_removed"],
                    rep.get("nonmanifold_fixed", 0), rep["shells_removed"], rep["holes_filled"], rep["flipped"])
                    + "\n" + describe(rep["after"]))
        finally:
            QApplication.restoreOverrideCursor()
        self._objects_changed()
        QMessageBox.information(self, tr("repair_advanced"), "\n\n".join(lines))

    def _on_preview_click(self, uid):
        self.set_mode("prepare")
        self._select_uid(uid)

    def mirror_selected(self, axis: int):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for obj in group:
                obj.mirror[axis] *= -1
                self._drop_supports(obj)
            self._objects_changed()

    def multiply_selected(self):
        group = self.selected_objects()
        if not group:
            return
        count, ok = QInputDialog.getInt(self, tr("multiply"), tr("multiply_count"), 4, 1, 200)
        if not ok:
            return
        self._push_undo()
        self.objects += [obj.duplicate() for obj in group for _ in range(count)]
        cfg = self.settings.config()
        if not self._arrange(cfg):
            self.status.setText(tr("arrange_full"))
        self._refresh_objects(select=[o.uid for o in group])

    # ---------------------------------------------------------------- undo
    def _snapshot(self):
        return [(o, o.state()) for o in self.objects]

    def _push_undo(self):
        self._undo.append(self._snapshot())
        del self._undo[:-60]
        self._redo.clear()

    def _restore(self, snap):
        sel = [o.uid for o in self.selected_objects()]
        self.objects = [o for o, _ in snap]
        for o, st in snap:
            o.restore(st)
        uids = {o.uid for o in self.objects}
        keep = [u for u in sel if u in uids]
        self._refresh_objects(select=keep or (self.objects[0].uid if self.objects else None))

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())

    def _refresh_objects(self, select=None):
        """Rebuild the object list; ``select`` is a uid or a list of uids (the first becomes current)."""
        chosen = list(select) if isinstance(select, (list, tuple, set)) else ([select] if select is not None else [])
        self._syncing = True
        self.object_list.clear()
        items = {}
        for o in self.objects:
            label = ((o.name or "object") + ("  ✂" if o.role == "cutter" else "") + ("  ⊥" if o.direct else "")
                     + (f"  [{o.material}]" if o.material else ""))
            it = QListWidgetItem(theme.icon("cube", theme.DANGER if o.role == "cutter" else theme.TEXT_DIM, 16), label)
            it.setData(Qt.UserRole, o.uid)
            self.object_list.addItem(it)
            items[o.uid] = it
        if chosen and chosen[0] in items:
            self.object_list.setCurrentItem(items[chosen[0]])
        for uid in chosen:
            if uid in items:
                items[uid].setSelected(True)
        self._syncing = False
        self._objects_changed(refresh_list=False)
        self._sync_transform()

    def _objects_changed(self, refresh_list: bool = True):
        cfg = self.settings.config()
        outside = {o.uid: o.name for o in out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg), region=plate_region(cfg))}
        uids = {o.uid for o in self.objects}
        for uid in list(self.viewer.objects):
            if uid not in uids:
                self.viewer.remove_object(uid)
        colliding = getattr(self, "_colliding", set())
        multi = self._multi_filament_view(cfg)
        for o in self.objects:
            state = "cutter" if o.role == "cutter" else (self._outside_state(o, cfg) if o.uid in outside else
                                                         "collision" if o.uid in colliding else "normal")
            self.viewer.set_object(o.uid, o.mesh, self._display_matrix(o), state)
            if multi:
                self.viewer.set_object_color(o.uid, self.filament_colour_rgb(o.filament, cfg))
                self.viewer.set_object_regions(o.uid, [(p["faces"], self.filament_colour_rgb(p["filament"], cfg))
                                                       for p in o.filament_parts] or None)
            else:
                self.viewer.set_object_color(o.uid, dental_shades.display_rgb(o.material))
                self.viewer.set_object_regions(o.uid, [(r["faces"], dental_shades.display_rgb(r["shade"]))
                                                       for r in o.regions] or None)
            self.viewer.set_object_supports(o.uid, self._support_preview(o, cfg))
            self.viewer.set_object_holes(o.uid, self._hole_preview(o, cfg))
        sel = self.selected()
        self._update_support_info()
        self.viewer.set_selected(sel.uid if sel else None, [o.uid for o in self.selected_objects()])
        self.empty_hint.setVisible(not self.objects or getattr(self, "_gl_failed", False))
        self.transform_card.setEnabled(sel is not None)
        self._sync_transform()
        self._invalidate()
        self._schedule_suggestions()
        if not self._dragging:
            self._collision_timer.start()
            if self.minima_btn.isChecked():
                self._unsupported_timer.start()
        if outside:
            self.status.setText(tr("out_of_bounds").format(", ".join(sorted(outside.values()))))
        elif self.status.text().startswith(tr("out_of_bounds").split("{")[0]):
            self.status.setText(tr("ready"))            # everything is back on the plate

    def _outside_state(self, o: SceneObject, cfg) -> str:
        """'outside' when the whole model is off the plate (drawn in the theme's outside colour), 'partial'
        when only part of it is (that part is painted purple in the 3D view)."""
        from shapely.geometry import MultiPoint, box
        lo, hi = o.world_bounds()
        if lo[2] > cfg["max_z"]:
            return "outside"
        region = plate_region(cfg)
        plate = region if region is not None else box(*plate_rect(cfg))
        hull = MultiPoint(o.world_mesh().vertices[:, :2]).convex_hull
        return "partial" if hull.intersection(plate).area > 1e-6 else "outside"

    def _sync_transform(self):
        obj = self.selected()
        self._syncing = True
        if obj:
            size = obj.world_size()
            self.pos_x.setValue(obj.x)
            self.pos_y.setValue(obj.y)
            self.pos_z.setValue(obj.z)
            self.cutter_box.setChecked(obj.role == "cutter")
            for i in range(3):
                self.rot[i].setValue(float(obj.rotation[i]))
                self.scale[i].setValue(float(obj.scale[i] * 100))
                self.size_spins[i].setValue(float(size[i]))
            group = self.selected_objects()
            watertight = obj.mesh.is_watertight()   # cached after the first call
            info = f"{len(obj.mesh.faces):,} {tr('triangles')} · {tr('volume')} {obj.volume / 1000:.2f} cm³"
            if not watertight:
                info += f"\n⚠ {tr('not_watertight')}"
            if len(group) > 1:
                info = tr("group_selected").format(len(group), sum(o.volume for o in group) / 1000) + "\n" + info
            self.object_info.setText(info)
        else:
            self.object_info.setText("")
        self._syncing = False

    def _edit(self, what, value, axis=0):
        if self._syncing:
            return
        obj = self.selected()
        if not obj:
            return
        group = self.selected_objects()     # edits apply to the whole selection
        self._push_undo()
        if what in ("rot", "scale", "size"):
            for o in group:
                self._drop_supports(o)
        if what in ("x", "y", "z"):
            delta = value - getattr(obj, what)   # positions move the group together
            for o in group:
                setattr(o, what, max(0.0, o.z + delta) if what == "z" else getattr(o, what) + delta)
        elif what == "rot":
            for o in group:
                o.rotation[axis] = value
        elif what in ("scale", "size"):
            if what == "size":
                current = obj.world_size()[axis]
                if current <= 0:
                    return
                factor = value / current
            else:
                factor = (value / 100.0) / obj.scale[axis]
            for o in group:
                if self.uniform.isChecked():
                    o.scale *= factor
                else:
                    o.scale[axis] *= factor
        self._objects_changed(refresh_list=False)

    def _on_list_selection(self, current, _previous):
        if self._syncing:
            return
        self._on_list_multi()

    def _on_list_multi(self):
        if self._syncing:
            return
        sel = self.selected()
        self.viewer.set_selected(sel.uid if sel else None, [o.uid for o in self.selected_objects()])
        self.transform_card.setEnabled(sel is not None)
        self._sync_transform()

    def _item(self, uid):
        for i in range(self.object_list.count()):
            it = self.object_list.item(i)
            if it.data(Qt.UserRole) == uid:
                return it
        return None

    def _select_uid(self, uid, flags: int = 0):
        """Select from the viewport: plain click selects one, Ctrl toggles, Shift adds."""
        from .viewer import SEL_ADD, SEL_TOGGLE
        self._syncing = True
        try:
            if uid is None:
                if not flags:
                    self.object_list.setCurrentItem(None)
                    self.object_list.clearSelection()
                return
            it = self._item(uid)
            if it is None:
                return
            if flags & SEL_TOGGLE and it.isSelected():
                it.setSelected(False)
                rest = self.object_list.selectedItems()
                self.object_list.setCurrentItem(rest[0] if rest else None, QItemSelectionModel.NoUpdate)
            elif flags & (SEL_TOGGLE | SEL_ADD):
                self.object_list.setCurrentItem(it, QItemSelectionModel.Select)
            else:
                self.object_list.setCurrentItem(it, QItemSelectionModel.ClearAndSelect)
        finally:
            self._syncing = False
            self._on_list_multi()

    # ---------------------------------------------------------- floating edit bar
    def _build_edit_bar(self, holder):
        """Bar of the common editing actions that opens next to a model when it is clicked."""
        bar = QFrame(holder)
        bar.setObjectName("Card")
        bar.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.97)}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        self.edit_title = QLabel("")
        self.edit_title.setObjectName("Dim")
        self.edit_title.setMaximumWidth(150)
        lay.addWidget(self.edit_title)

        def button(icon_name, tip, slot=None, menu=None):
            b = QToolButton()
            b.setIcon(theme.icon(icon_name, theme.TEXT, 18))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            if menu is not None:
                b.setMenu(menu)
                b.setPopupMode(QToolButton.InstantPopup)
            else:
                b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        self._orient_menu = QMenu(self)
        self._fill_orientation_menu(self._orient_menu)
        button("rotate3d", tr("ctx_orientation"), menu=self._orient_menu)
        button("compass", tr("auto_orient"), self.auto_orient_selected)
        button("face", tr("ctx_face_tool"), lambda: self.set_tool("face"))
        button("move", tr("move_to"), self.move_to_dialog)
        button("fit", tr("scale_to"), self.scale_dialog)
        mirror = QMenu(self)
        for axis, name in enumerate("XYZ"):
            mirror.addAction(f"{tr('mirror')} {name}", lambda a=axis: self.mirror_selected(a))
        button("view_side", tr("mirror"), menu=mirror)
        button("flat", tr("drop_to_plate"), self.drop_to_plate)
        button("copy", tr("duplicate"), self.duplicate_selected)
        sup_menu = QMenu(self)
        sup_menu.addAction(theme.icon("pillar"), tr("sup_auto"), self.generate_supports)
        sup_menu.addAction(theme.icon("anchor"), tr("sup_manual"), self.manual_supports)
        sup_menu.addAction(theme.icon("plus"), tr("sup_add") + "\tS", lambda: self._set_pick_mode("add"))
        sup_menu.addAction(theme.icon("x"), tr("sup_remove") + "\tShift+S", lambda: self._set_pick_mode("remove"))
        sup_menu.addAction(theme.icon("trash"), tr("sup_clear"), self.clear_supports)
        sup_menu.addAction(theme.icon("grid"), tr("raft_menu") + "…", self.raft_menu)
        self.edit_sup_btn = button("pillar", tr("supports_card"), menu=sup_menu)
        button("drop", tr("shade_menu"), self.dental_shades_dialog)
        button("trash", tr("delete"), self.delete_selected)
        button("more", tr("more"), lambda: self._show_context_menu(
            self.selected().uid if self.selected() else None, self.edit_bar.mapToGlobal(self.edit_bar.rect().bottomRight())))
        close = button("x", tr("close"), lambda: self.edit_bar.hide())
        close.setIconSize(QSize(14, 14))
        bar.hide()
        self.edit_bar = bar

    def _fill_orientation_menu(self, menu):
        for axis, name in enumerate("XYZ"):
            for deg in (90, -90, 45, -45):
                menu.addAction(f"{name} {deg:+d}°", lambda a=axis, d=deg: self.rotate_selected(a, d))
            menu.addSeparator()
        menu.addAction(theme.icon("rotate3d"), tr("ctx_rotate_exact"), self.rotate_exact_dialog)
        menu.addAction(theme.icon("rotate3d"), tr("ctx_rotate_tool"), lambda: self.set_tool("rotate"))
        menu.addAction(theme.icon("face"), tr("ctx_face_tool"), lambda: self.set_tool("face"))
        menu.addAction(theme.icon("compass"), tr("auto_orient"), self.auto_orient_selected)
        menu.addAction(theme.icon("flat"), tr("lay_flat"), self.lay_flat)
        menu.addAction(theme.icon("reset"), tr("reset_transform"), self.reset_transform)

    def _show_edit_bar(self):
        group = self.selected_objects()
        if not group or self.viewer.mode != "prepare" or self.viewer.tool != "move" or self.viewer.pick_mode:
            self.edit_bar.hide()
            return
        self.edit_title.setText(group[0].name if len(group) == 1 else tr("n_models").format(len(group)))
        sla_supports_on = self.viewer.technology == "sla" and self.settings.config()["sla_process"] != "polyjet"
        self.edit_sup_btn.setVisible(sla_supports_on)
        self.edit_bar.adjustSize()
        holder = self.edit_bar.parentWidget()
        p = self.viewer.mapTo(holder, self.viewer.last_click.toPoint())
        x = min(max(8, p.x() - self.edit_bar.width() // 2), holder.width() - self.edit_bar.width() - 8)
        y = p.y() - self.edit_bar.height() - 24
        if y < 70:
            y = p.y() + 28
        self.edit_bar.move(x, min(y, holder.height() - self.edit_bar.height() - 8))
        self.edit_bar.show()
        self.edit_bar.raise_()

    def move_to_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        obj = self.selected()
        if not obj:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("move_to"))
        form = QFormLayout(dlg)
        spins = []
        for name, value in (("X", obj.x), ("Y", obj.y), ("Z", obj.z)):
            sp = _spin(-5000 if name != "Z" else 0, 5000, 1, 2, " mm", 120)
            sp.setValue(value)
            form.addRow(name, sp)
            spins.append(sp)
        note = QLabel(tr("move_to_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        self._push_undo()
        dx, dy, dz = spins[0].value() - obj.x, spins[1].value() - obj.y, spins[2].value() - obj.z
        for o in self.selected_objects():
            o.x, o.y, o.z = o.x + dx, o.y + dy, max(0.0, o.z + dz)
        self._objects_changed()

    def scale_dialog(self):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout
        group = self.selected_objects()
        if not group:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("scale_to"))
        form = QFormLayout(dlg)
        mode = QComboBox()
        mode.addItem(tr("scale_percent"), "percent")
        mode.addItem(tr("scale_fit_height"), "height")
        mode.addItem(tr("scale_fit_longest"), "longest")
        mode.addItem(tr("scale_inch"), "inch")
        value = _spin(0.01, 100000, 1, 2, "", 120)
        value.setValue(100.0)
        mode.currentIndexChanged.connect(lambda _i: value.setValue(
            {"percent": 100.0, "height": float(group[0].world_size()[2]), "longest": float(max(group[0].world_size())),
             "inch": 2540.0}[mode.currentData()]))
        form.addRow(tr("scale_mode"), mode)
        form.addRow(tr("scale_value"), value)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        self._push_undo()
        for o in group:
            size = o.world_size()
            m = mode.currentData()
            if m in ("percent", "inch"):
                factor = value.value() / 100.0
            elif m == "height":
                factor = value.value() / size[2] if size[2] > 0 else 1.0
            else:
                factor = value.value() / max(size) if max(size) > 0 else 1.0
            o.scale *= factor
            self._drop_supports(o)
        self._objects_changed()

    def drop_to_plate(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for o in group:
                o.z = 0.0
            self._objects_changed()

    def rename_selected(self):
        obj = self.selected()
        if obj:
            name, ok = QInputDialog.getText(self, tr("rename"), tr("rename"), text=obj.name)
            if ok and name.strip():
                self._push_undo()
                obj.name = name.strip()
                self._refresh_objects(select=[o.uid for o in self.selected_objects()])

    def _on_view_click(self, uid, flags):
        self._select_uid(uid, flags)
        QTimer.singleShot(0, self._show_edit_bar)

    def _show_context_menu(self, uid, pos):
        """Right click in the viewport: orientation and editing for the model under the cursor (or the selection)."""
        if uid is not None and uid not in {o.uid for o in self.selected_objects()}:
            self._select_uid(uid)
        group = self.selected_objects() if uid is not None else []
        menu = QMenu(self)
        if group:
            title = menu.addAction(group[0].name if len(group) == 1 else tr("group_selected").format(
                len(group), sum(o.volume for o in group) / 1000).split("·")[0].strip())
            title.setEnabled(False)
            self.edit_bar.hide()
            orient_menu = menu.addMenu(theme.icon("rotate3d"), tr("ctx_orientation"))
            self._fill_orientation_menu(orient_menu)
            menu.addAction(theme.icon("move"), tr("move_to"), self.move_to_dialog)
            menu.addAction(theme.icon("fit"), tr("scale_to"), self.scale_dialog)
            menu.addAction(theme.icon("flat"), tr("drop_to_plate"), self.drop_to_plate)
            mirror_menu = menu.addMenu(tr("mirror"))
            for axis, name in enumerate("XYZ"):
                mirror_menu.addAction(name, lambda a=axis: self.mirror_selected(a))
            menu.addSeparator()
            menu.addAction(theme.icon("copy"), tr("duplicate") + "\tCtrl+D", self.duplicate_selected)
            menu.addAction(theme.icon("copy"), tr("multiply"), self.multiply_selected)
            menu.addAction(theme.icon("center"), tr("center"), self.center_selected)
            menu.addAction(theme.icon("arrange"), tr("layout_selected"), lambda: self.auto_layout(scope="selected"))
            menu.addAction(theme.icon("code"), tr("rename"), self.rename_selected)
            menu.addAction(theme.icon("x"), tr("hide_models"), self.hide_selected)
            if len(group) > 1:
                menu.addAction(theme.icon("copy"), tr("merge_models"), self.merge_selected)
            if self.viewer.technology != "sla":
                self._fill_filament_menu(menu.addMenu(theme.icon("layers"), tr("fil_menu")))
            menu.addAction(theme.icon("drop"), tr("shade_menu") + "…", self.dental_shades_dialog)
            dental = menu.addMenu(theme.icon("sparkle"), tr("dental_indication"))
            for key in ("model", "splint", "crown", "guide"):
                dental.addAction(tr(f"dental_{key}"), lambda k=key: self.apply_dental_indication(k))
            menu.addAction(theme.icon("download"), tr("export_selection_stl"), lambda: self.export_stl(selection_only=True))
            if self.viewer.technology == "sla" and self.settings.config()["sla_process"] == "polyjet":
                menu.addSeparator()
                menu.addAction(theme.icon("drop"), tr("material_part"), self.set_part_material)
            elif self.viewer.technology == "sla":
                menu.addSeparator()
                menu.addAction(theme.icon("pillar"), tr("sup_auto"), self.generate_supports)
                menu.addAction(theme.icon("anchor"), tr("sup_manual"), self.manual_supports)
                menu.addAction(theme.icon("plus"), tr("sup_add") + "\tS", lambda: self._set_pick_mode("add"))
                menu.addAction(theme.icon("x"), tr("sup_remove") + "\tShift+S", lambda: self._set_pick_mode("remove"))
                menu.addAction(theme.icon("trash"), tr("sup_clear"), self.clear_supports)
                menu.addAction(theme.icon("grid"), tr("raft_menu") + "…", self.raft_menu)
                flat = menu.addAction(theme.icon("flat"), tr("direct_on_plate"))
                flat.setToolTip(tr("direct_on_plate_tip"))
                flat.setCheckable(True)
                flat.setChecked(all(o.direct for o in group))
                flat.toggled.connect(self._set_direct)
            menu.addSeparator()
            menu.addAction(theme.icon("square"), tr("cut_height"), self.cut_selected)
            menu.addAction(theme.icon("grid"), tr("split_parts"), self.split_selected)
            menu.addAction(theme.icon("settings"), tr("repair_mesh"), self.repair_selected)
            menu.addAction(theme.icon("settings"), tr("repair_advanced") + "…", self.repair_advanced_dialog)
            cutter = menu.addAction(tr("cutter_role"))
            cutter.setCheckable(True)
            cutter.setChecked(all(o.role == "cutter" for o in group))
            cutter.toggled.connect(self._toggle_cutter)
            menu.addSeparator()
            menu.addAction(theme.icon("trash", theme.DANGER), tr("delete") + "\tDel", self.delete_selected)
        menu.addAction(tr("select_all") + "\tCtrl+A", self.select_all)
        if any(self.viewer.objects.get(o.uid, {}).get("hidden") for o in self.objects):
            menu.addAction(tr("show_all_models"), self.show_all_models)
        menu.addAction(theme.icon("arrange"), tr("arrange") + "\tA", self.arrange_objects)
        menu.addAction(theme.icon("fit"), tr("view_fit"), self.viewer.fit_objects)
        menu.exec(pos)

    # ------------------------------------------------------------ dental shades
    def dental_shades_dialog(self):
        from .shade_dialog import ShadeDialog
        group = self.selected_objects() or list(self.objects)
        if not group:
            self.status.setText(tr("select_first"))
            return
        dlg = ShadeDialog(group, self)
        dlg.applyRequested.connect(lambda code, objs: self._apply_shade(code, objs))
        dlg.splitRequested.connect(lambda: self.split_denture(dlg))
        dlg.exec()

    def split_denture(self, dialog=None):
        """One-file denture: mark the base as gingiva and each tooth as its own shaded region (numbered along
        the arch). The denture stays one model, so it moves, is laid out and prints as one piece."""
        targets = [o for o in (dialog.objects if dialog is not None else self.selected_objects())]
        done = 0
        for obj in targets:
            regions = dental_shades.denture_regions(obj.mesh)
            if regions:
                if not done:
                    self._push_undo()
                obj.regions = regions
                done += 1
        if not done:
            QMessageBox.information(self, tr("shade_menu"), tr("denture_single"))
            return
        self._refresh_objects(select=[o.uid for o in targets])
        n = sum(len(o.regions) - 1 for o in targets)
        self.status.setText(tr("denture_split").format(n))
        if dialog is not None:
            dialog.set_objects(targets)

    def _apply_shade(self, code: str, targets: list):
        """``targets``: (object, region index or None) pairs."""
        self._push_undo()
        objs = []
        for o, r in targets:
            if r is None:
                o.material = code
            else:
                o.regions[r]["shade"] = code
            if o not in objs:
                objs.append(o)
        self._refresh_objects(select=[o.uid for o in objs])
        self.status.setText(tr("shade_applied").format(code, len(targets)))

    def set_part_material(self):
        group = self.selected_objects()
        if not group:
            return
        name, ok = QInputDialog.getText(self, tr("material_part"), tr("material_part_q"), text=group[0].material)
        if ok:
            self._push_undo()
            for o in group:
                o.material = name.strip()
            self._refresh_objects(select=[o.uid for o in group])

    def _set_direct(self, on: bool):
        group = self.selected_objects()
        if group:
            self._push_undo()
            for o in group:
                o.direct = on
                if on:
                    o.support_points = None
            self._refresh_objects(select=[o.uid for o in group])

    def hide_selected(self):
        for o in self.selected_objects():
            self.viewer.set_object_hidden(o.uid, True)
        self._select_uid(None)
        self.status.setText(tr("hidden_hint"))

    def show_all_models(self):
        for o in self.objects:
            self.viewer.set_object_hidden(o.uid, False)

    def merge_selected(self):
        """Join the selected models into one (as CHITUBOX's Boolean join; they slice as one part)."""
        group = [o for o in self.selected_objects() if o.role != "cutter"]
        if len(group) < 2:
            return
        self._push_undo()
        merged = Mesh.merge([o.world_mesh() for o in group], " + ".join(o.name for o in group)[:60])
        b = merged.bounds
        new = SceneObject.from_mesh(merged)
        new.x, new.y, new.z = (b[0, 0] + b[1, 0]) / 2, (b[0, 1] + b[1, 1]) / 2, max(0.0, b[0, 2])
        idx = min(self.objects.index(o) for o in group)
        for o in group:
            self.objects.remove(o)
            self.viewer.remove_object(o.uid)
        self.objects.insert(idx, new)
        self._refresh_objects(select=new.uid)
        self.status.setText(tr("merged_done").format(len(group)))

    DENTAL = {
        # indication: (tilt about X in degrees, print straight on the plate)
        "model": (0.0, True),
        "splint": (45.0, False),
        "crown": (45.0, False),
        "guide": (45.0, False),
        "die": (0.0, True),
        "denture": (45.0, False),
    }

    # ------------------------------------------------------------ dental CAD (3Shape / exocad / Medit)
    def _start_cad_watch(self):
        folder = str(self.store.value("cad_folder", ""))
        on = self.store.value("cad_watch", "false") == "true" and os.path.isdir(folder)
        self._cad_timer.stop()
        if on:
            self._cad_tracker.mark_existing(folder)
            self._cad_timer.start()

    def _poll_cad_folder(self):
        folder = str(self.store.value("cad_folder", ""))
        if not os.path.isdir(folder) or (self.task is not None and self.task.isRunning()):
            return
        new = self._cad_tracker.poll(folder)
        if new:
            self.import_from_cad(new)

    def import_from_cad(self, paths: list[str]):
        """Designs handed over by the dental CAD: import, orient by indication, support and lay out."""
        from .. import dental_cad
        auto = self.store.value("cad_auto", "true") == "true"
        before = {o.uid for o in self.objects}
        kinds = {}
        for p in paths:
            n0 = len(self.objects)
            self.load_files([p])
            kind = dental_cad.detect_indication(p, dental_cad.order_text_for(p))
            for o in self.objects[n0:]:
                kinds[o.uid] = kind
        new = [o for o in self.objects if o.uid not in before]
        if not new:
            return
        if auto:
            for kind in ("model", "die", "crown", "splint", "guide", "denture"):
                group = [o for o in new if kinds.get(o.uid) == kind]
                if group:
                    self._select_uid(group[0].uid)
                    from .viewer import SEL_ADD
                    for o in group[1:]:
                        self._select_uid(o.uid, SEL_ADD)
                    self.apply_dental_indication(kind)
            needs = [o for o in new if not o.direct]
            if needs and self.settings.config()["technology"] == "sla":
                self._select_uid(needs[0].uid)
                from .viewer import SEL_ADD
                for o in needs[1:]:
                    self._select_uid(o.uid, SEL_ADD)
                self.generate_supports()
            self.auto_layout(scope="all", dialog=False)
        names = ", ".join(f"{o.name} ({i18n.pick(*dental_cad.LABEL[kinds.get(o.uid, 'unknown')])})" for o in new[:6])
        self.status.setText(tr("cad_imported").format(len(new), names))
        self._select_uid(None)
        self.raise_()
        self.activateWindow()

    def dental_cad_dialog(self):
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("cad_title"))
        dlg.setMinimumWidth(560)
        form = QFormLayout(dlg)
        intro = QLabel(tr("cad_intro"))
        intro.setWordWrap(True)
        form.addRow(intro)
        watch = QCheckBox(tr("cad_watch"))
        watch.setChecked(self.store.value("cad_watch", "false") == "true")
        form.addRow(watch)
        folder = QLineEdit(str(self.store.value("cad_folder", "")))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(lambda: folder.setText(QFileDialog.getExistingDirectory(dlg, tr("cad_folder"), folder.text())
                                                      or folder.text()))
        row = QHBoxLayout()
        row.addWidget(folder, 1)
        row.addWidget(browse)
        form.addRow(tr("cad_folder"), row)
        auto = QCheckBox(tr("cad_auto"))
        auto.setChecked(self.store.value("cad_auto", "true") == "true")
        form.addRow(auto)
        how = QLabel(tr("cad_how"))
        how.setWordWrap(True)
        how.setObjectName("Faint")
        form.addRow(how)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        path = folder.text().strip()
        if watch.isChecked() and not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                QMessageBox.warning(self, tr("cad_title"), tr("cad_bad_folder"))
                return
        self.store.setValue("cad_watch", "true" if watch.isChecked() else "false")
        self.store.setValue("cad_folder", path)
        self.store.setValue("cad_auto", "true" if auto.isChecked() else "false")
        self._start_cad_watch()
        self.status.setText(tr("cad_watching").format(path) if watch.isChecked() else tr("cad_off"))

    def apply_dental_indication(self, kind: str):
        """Starting orientation per dental indication (as Phrozen DS Slicer does per indication)."""
        if kind not in self.DENTAL:
            return
        tilt, direct = self.DENTAL[kind]
        group = [o for o in self.selected_objects() if o.role != "cutter"]
        if not group:
            return
        self._push_undo()
        for o in group:
            o.lay_flat()
            if tilt:
                o.apply_world_rotation(self._axis_rotation(0, tilt))
            o.direct = direct
            o.support_points = None
        if not direct:
            self._ensure_supports_enabled()
        self._objects_changed()
        if f"dental_{kind}_hint" in i18n.STRINGS:
            self.status.setText(tr(f"dental_{kind}_hint"))

    def rotate_exact_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        if not self.selected_objects():
            self.status.setText(tr("select_first"))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("ctx_rotate_exact"))
        form = QFormLayout(dlg)
        spins = [_spin(-360, 360, 5, 1, "°", 110) for _ in range(3)]
        for name, sp in zip("XYZ", spins):
            form.addRow(name, sp)
        note = QLabel(tr("rotate_exact_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        group = self.selected_objects()
        self._push_undo()
        for axis, sp in enumerate(spins):
            if sp.value():
                self._rotate_group(group, axis, sp.value())
        for o in group:
            self._drop_supports(o)
        self._objects_changed()

    def _on_box_select(self, uids, additive):
        self._syncing = True
        if not additive:
            self.object_list.clearSelection()
        for k, uid in enumerate(uids):
            it = self._item(uid)
            if it is not None:
                if k == 0 and self.selected() is None:
                    self.object_list.setCurrentItem(it, QItemSelectionModel.Select)
                it.setSelected(True)
        self._syncing = False
        self._on_list_multi()
        if uids:
            self.status.setText(tr("group_selected").format(len(self.selected_objects()),
                                                           sum(o.volume for o in self.selected_objects()) / 1000))

    def _on_drag(self, uid, dx, dy):
        group = self.selected_objects()
        if not any(o.uid == uid for o in group):
            group = [o for o in self.objects if o.uid == uid]
        if group:
            self.viewer.begin_interaction()
            if not self._dragging:
                self._push_undo()
                self._dragging = True
                self.edit_bar.hide()
            for obj in group:
                obj.x += dx
                obj.y += dy
                self.viewer.set_object_transform(obj.uid, self._display_matrix(obj))
            lead = self.selected() or group[0]
            self._syncing = True
            self.pos_x.setValue(lead.x)
            self.pos_y.setValue(lead.y)
            self._syncing = False

    def _on_drag_finished(self, _uid):
        self._dragging = False
        QTimer.singleShot(0, self._show_edit_bar)
        self._objects_changed(refresh_list=False)

    # ================================================================ supports
    def _lift(self, cfg=None) -> float:
        cfg = cfg or self.settings.config()
        return cfg["sla_elevation"] + cfg["sla_raft_thickness"]

    def _shows_supports(self, o: SceneObject) -> bool:
        return (o.support_points is not None and self.viewer.technology == "sla" and not o.direct
                and self.settings.config()["sla_process"] != "polyjet")

    def _display_matrix(self, o: SceneObject) -> np.ndarray:
        """Where the model is drawn: raised onto its supports when it has edited supports."""
        m = o.world_matrix()
        if self._shows_supports(o):
            m[2, 3] += self._lift()
        return m

    def _support_preview(self, o: SceneObject, cfg) -> Mesh | None:
        """Support structure in the object's own coordinates (cached; moving the object keeps it valid)."""
        if not self._shows_supports(o) or len(o.support_points) == 0:
            return None
        keys = sorted(k for k in cfg.values if k.startswith("sla_") and not k.startswith("sla_layer"))
        key = (o.uid, o.support_points.tobytes(), o.matrix().tobytes(), tuple(cfg[k] for k in keys))
        hit = self._support_cache.get(o.uid)
        if hit is not None and hit[0] == key:
            return hit[1]
        lift = self._lift(cfg)
        disp = self._display_matrix(o)
        world, geo = self._support_geometry(o, lift)
        owners = {}
        mesh, _ = sla_supports.build_supports(world, cfg, o.world_support_points(lift), owners=owners, cache=geo,
                                              label=o.name if cfg["sla_raft_label"] else "")
        local = mesh.transform(np.linalg.inv(disp)) if len(mesh.faces) else None
        self._support_cache[o.uid] = (key, local, owners)
        return local

    def _support_geometry(self, o: SceneObject, lift: float):
        """The raised model in bed coordinates and its ray / underside indexes, kept until the model
        moves or turns, so adding or removing one support does not redo them."""
        key = (id(o.mesh), o.world_matrix().tobytes(), lift)
        cache = self.__dict__.setdefault("_geometry_cache", {})
        hit = cache.get(o.uid)
        if hit is None or hit[0] != key:
            world = o.mesh.transform(o.world_matrix()).translated([0, 0, lift])
            hit = (key, world, {})
            cache[o.uid] = hit
        return hit[1], hit[2]

    def _drop_supports(self, o: SceneObject):
        if o.support_points is not None:
            o.support_points = None
            self.status.setText(tr("supports_cleared"))

    def _support_targets(self) -> list[SceneObject]:
        return self.selected_objects() or list(self.objects)

    def generate_supports(self):
        if not self.objects:
            return
        self._push_undo()
        self._ensure_supports_enabled()
        cfg = self.settings.config()
        learned = self.assistant.learned_config(cfg)   # density and angle learned from earlier edits
        lift = self._lift(cfg)
        for o in self._support_targets():
            if o.direct or o.role == "cutter":
                continue
            world = o.mesh.transform(o.world_matrix())
            world = world.translated([0, 0, lift])
            o.set_world_support_points(sla_supports.auto_support_points(world, learned), lift)
            o.__dict__["support_session"] = {"auto": len(o.support_points), "added": [], "removed": 0,
                                             "threshold": learned["support_angle"]}
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("sup_generated_hint"))

    RAFT_KEYS = ("sla_raft_type", "sla_raft_thickness", "sla_raft_margin", "sla_raft_area_ratio", "sla_raft_slope",
                 "sla_raft_bar_spacing", "sla_raft_bar_width", "sla_raft_group_reach", "sla_elevation")

    def raft_menu(self):
        """Raft controls in one small window; every change shows on the supports right away."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from .settings_panel import SettingRow
        dlg = getattr(self, "_raft_dialog", None)
        if dlg is not None:
            dlg.close()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("raft_menu"))
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        note = QLabel(tr("raft_menu_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        lay.addWidget(note)
        rows = {}

        def fill():
            cfg = self.settings.config()
            kind = cfg["sla_raft_type"]
            for key, row in rows.items():
                row.setVisible(not ((key in ("sla_raft_bar_spacing", "sla_raft_bar_width") and kind != "grid")
                                    or (key == "sla_raft_group_reach" and kind != "mini")
                                    or (key not in ("sla_raft_type", "sla_elevation") and kind == "none")))

        def changed(key, value):
            self._ensure_supports_enabled()
            self.settings.set_value(key, value)
            fill()
            self._objects_changed(refresh_list=False)
        cfg = self.settings.config()
        for key in self.RAFT_KEYS:
            row = SettingRow(SCHEMA_BY_KEY[key], cfg[key], key in self.settings.overrides, cfg)
            row.changed.connect(changed)
            row.reset.connect(lambda k: (self.settings.set_value(k, self.settings.base_config()[k]), dlg.close(), self.raft_menu()))
            lay.addWidget(row)
            rows[key] = row
        fill()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("close"))
        buttons.rejected.connect(dlg.close)
        lay.addWidget(buttons)
        self._raft_dialog = dlg
        dlg.show()

    def manual_supports(self):
        """Start the selected models (or all) with no supports, then place each one by clicking."""
        targets = [o for o in self._support_targets() if not o.direct and o.role != "cutter"]
        if not targets:
            return
        self._push_undo()
        self._ensure_supports_enabled()
        for o in targets:
            o.support_points = np.zeros((0, 3))
            o.__dict__.pop("support_session", None)
        self._objects_changed(refresh_list=False)
        self._set_pick_mode("add")
        self.status.setText(tr("sup_manual_hint"))

    def clear_supports(self):
        targets = [o for o in self._support_targets() if o.support_points is not None]
        if targets:
            self._push_undo()
            for o in targets:
                o.support_points = None
            self._objects_changed(refresh_list=False)

    def _ensure_supports_enabled(self):
        if not self.settings.config()["support_enable"]:
            self.settings.set_value("support_enable", True)

    def _set_pick_mode(self, mode):
        for b, m in ((self.sup_add_btn, "add"), (self.sup_remove_btn, "remove")):
            b.blockSignals(True)
            b.setChecked(mode == m)
            b.blockSignals(False)
        self.viewer.pick_mode = mode
        self.viewer.set_marker(None)
        self.viewer.set_highlight(None)
        self._hover_key = None
        self.viewer.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)
        if mode:
            self.edit_bar.hide()
            self.status.setText(tr("sup_add_hint") if mode == "add" else tr("sup_remove_hint"))
            if mode == "remove" and not any(self._shows_supports(o) and len(o.support_points) for o in self.objects):
                self.status.setText(tr("sup_remove_none"))

    def _on_support_add(self, uid, point):
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is None:
            return
        self._push_undo()
        self._ensure_supports_enabled()
        cfg = self.settings.config()
        lift = self._lift(cfg)
        # the click is on the drawn model, which is raised only once it has edited supports
        p = self._add_point(o, point, lift)
        if p is None:
            self._undo.pop()
            self.status.setText(tr("sup_add_no_underside"))
            return
        if o.support_points is None:
            o.support_points = np.zeros((0, 3))   # only the clicked support, no automatic ones
        pts = o.world_support_points(lift)
        o.set_world_support_points(np.vstack([pts, p[None, :]]), lift)
        session = o.__dict__.get("support_session")
        if session is not None:
            session["added"].append(self._surface_angle(o, p - [0, 0, lift]))
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("sup_added_one"))

    def _add_point(self, o: SceneObject, point, lift: float):
        """Contact point (raised coordinates) for a click on the model: on the underside below the click."""
        p = np.asarray(point, dtype=float)
        if not self._shows_supports(o):
            p = p + [0, 0, lift]
        world, geo = self._support_geometry(o, lift)
        if "caster" not in geo:
            geo["caster"] = sla_supports._Caster(world)
        return sla_supports.underside_contact(geo["caster"], p)

    @staticmethod
    def _surface_angle(o: SceneObject, point) -> float:
        """Overhang angle (from vertical) of the model surface nearest to ``point`` (bed coordinates)."""
        mesh = o.mesh.transform(o.world_matrix())
        centers = mesh.triangles.mean(axis=1)
        k = int(np.argmin(((centers - point) ** 2).sum(axis=1)))
        nz = float(mesh.face_normals()[k, 2])
        return float(np.degrees(np.arcsin(np.clip(-nz, -1, 1))))

    def _support_under_ray(self, origin, direction):
        """(object, support index, highlight mesh in bed coordinates) of the support the ray touches first."""
        origin = np.asarray(origin, dtype=float)
        direction = np.asarray(direction, dtype=float)
        best = None
        cfg = self.settings.config()
        for o in self.objects:
            if not self._shows_supports(o) or len(o.support_points) == 0:
                continue
            local = self._support_preview(o, cfg)
            entry = self._support_cache.get(o.uid)
            if local is None or entry is None or len(entry) < 3:
                continue
            disp = self._display_matrix(o)
            inv = np.linalg.inv(disp)
            r = self.viewer._ray_mesh(inv[:3, :3] @ origin + inv[:3, 3], inv[:3, :3] @ direction, local)
            if r is None or (best is not None and r[0] >= best[0]):
                continue
            owners = entry[2]
            g = int(owners["faces"][r[1]])
            if g < 0:
                continue                      # raft or brace: shared, not one support
            group = owners["groups"][g]
            hit = origin + direction * r[0]
            pts = o.world_support_points(self._lift(cfg))
            k = min(group, key=lambda i: np.linalg.norm(pts[i, :2] - hit[:2])) if len(group) > 1 else group[0]
            mine = [i for i, grp in enumerate(owners["groups"]) if grp == (k,) or (len(grp) > 1 and grp == group)]
            mask = np.isin(owners["faces"], mine)
            part = Mesh(local.vertices, local.faces[mask]).transform(disp)
            best = (r[0], o, k, part)
        return None if best is None else best[1:]

    def _nearest_support(self, origin, direction):
        """(distance, object, index) of the support whose head or pillar passes closest to the view ray."""
        lift = self._lift()
        origin = np.asarray(origin, dtype=float)
        direction = np.asarray(direction, dtype=float)
        direction = direction / (np.linalg.norm(direction) or 1.0)
        best = None
        t = np.linspace(0.0, 1.0, 12)[None, :, None]
        for o in self.objects:
            if not self._shows_supports(o) or len(o.support_points) == 0:
                continue
            pts = o.world_support_points(lift)
            foot = pts.copy()
            foot[:, 2] = 0.0
            # sample each support from the contact point down to the plate; the nearest sample to the ray wins
            samples = pts[:, None, :] + (foot - pts)[:, None, :] * t
            d = np.linalg.norm(np.cross(samples - origin, direction), axis=2).min(axis=1)
            d += np.linalg.norm(np.cross(pts - origin, direction), axis=1) * 0.15   # prefer the head
            k = int(np.argmin(d))
            if best is None or d[k] < best[0]:
                best = (float(d[k]), o, k)
        return best

    def _support_reach(self) -> float:
        cfg = self.settings.config()
        return max(1.5, cfg["sla_pillar_diameter"] * 1.5)

    def _on_support_hover(self, origin, direction, uid, point):
        """Remove mode: the support under the cursor turns red. Add mode: a dot where the support would touch.
        Mouse moves are coalesced: only the latest is handled, at most every ~30 ms."""
        self._hover_args = (origin, direction, uid, point)
        if not hasattr(self, "_hover_timer"):
            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(30)
            self._hover_timer.timeout.connect(lambda: self._support_hover_now(*self._hover_args))
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _support_hover_now(self, origin, direction, uid, point):
        mode = self.viewer.pick_mode
        if mode == "remove":
            found = self._support_under_ray(origin, direction)
            key = (found[0].uid, found[1]) if found else None
            if key != getattr(self, "_hover_key", None):       # only re-upload when another support is hit
                self._hover_key = key
                self.viewer.set_highlight(found[2] if found else None)
        elif mode == "add":
            o = next((x for x in self.objects if x.uid == uid), None)
            if o is None:
                self.viewer.set_marker(None)
                return
            lift = self._lift()
            p = self._add_point(o, point, lift)
            if p is not None and self._shows_supports(o):
                self.viewer.set_marker(p)
            elif p is not None:
                self.viewer.set_marker(p - [0, 0, lift])
            else:
                self.viewer.set_marker(None)

    def _on_support_remove(self, origin, direction):
        """Remove the one support the click touches, wherever on it (tip, pillar, branch or base)."""
        found = self._support_under_ray(origin, direction)
        if found is None:
            best = self._nearest_support(origin, direction)       # a near miss on a thin support
            if best is None or best[0] > self._support_reach():
                return
            _, o, k = best
        else:
            o, k, _ = found
        self._push_undo()
        self.viewer.set_highlight(None)
        self._hover_key = None
        self.status.setText(tr("sup_removed_one"))
        o.support_points = np.delete(o.support_points, k, axis=0)
        session = o.__dict__.get("support_session")
        if session is not None:
            session["removed"] += 1
        self._objects_changed(refresh_list=False)

    def _update_support_info(self):
        sla_mode = self.viewer.technology == "sla" and self.settings.config()["sla_process"] != "polyjet"
        self.supports_card.setVisible(sla_mode)
        if not sla_mode:
            self._set_pick_mode(None)
            return
        counts = [len(o.support_points) for o in self.objects if o.support_points is not None]
        if counts:
            self.sup_info.setText(tr("sup_count").format(sum(counts)))
        else:
            self.sup_info.setText(tr("sup_none"))

    # ================================================================ settings
    @staticmethod
    def _plate_center(cfg) -> tuple[float, float]:
        x0, y0, x1, y1 = plate_rect(cfg)
        return (x0 + x1) / 2, (y0 + y1) / 2

    def _update_raft_view(self, cfg):
        raft = cfg["technology"] == "sla" and cfg["support_enable"] and cfg["sla_raft_type"] != "none"
        self.viewer.set_raft_top(cfg["sla_raft_thickness"] if raft else 0.0)

    def _apply_printer(self):
        cfg = self.settings.config()
        self._update_raft_view(cfg)
        tech = cfg["technology"]
        rect = plate_rect(cfg)
        old = getattr(self, "_plate", None)
        self.viewer.set_bed(cfg["bed_x"], cfg["bed_y"], cfg["max_z"], tech, rect,
                            cfg["bed_shape"], cfg["bed_inner_diameter"],
                            (cfg["bed_dead_sector_start"], cfg["bed_dead_sector"]))
        if old is not None and old != rect:
            # new printer or plate: bring the models along to the new plate and frame it
            dx = (rect[0] + rect[2] - old[0] - old[2]) / 2
            dy = (rect[1] + rect[3] - old[1] - old[3]) / 2
            for o in self.objects:
                o.x += dx
                o.y += dy
            if out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=rect, region=plate_region(cfg)):
                self._arrange(cfg)
            self.viewer.view_preset("iso")
        self._plate = rect
        self.stat_material.set("—", tr("stat_resin") if tech == "sla" else tr("stat_filament"))
        self.export_btn.setText("  " + (tr("export_sla") if tech == "sla" else tr("export_gcode")))
        self._update_overhang()
        self._objects_changed(refresh_list=False)

    def _update_overhang(self, *_):
        cfg = self.settings.config()
        self.viewer.set_overhang(self.overhang_btn.isChecked(), cfg["support_angle"])

    def _on_settings_changed(self):
        cfg = self.settings.config()
        self._update_raft_view(cfg)
        b = self.viewer.bed
        if ((cfg["bed_x"], cfg["bed_y"], cfg["max_z"]) != b or cfg["technology"] != self.viewer.technology
                or (cfg["bed_shape"], cfg["bed_inner_diameter"]) != getattr(self.viewer, "bed_shape", ("rect", 0.0))
                or (cfg["bed_dead_sector_start"], cfg["bed_dead_sector"]) != getattr(self.viewer, "bed_sector", (0.0, 0.0))
                or plate_rect(cfg) != getattr(self, "_plate", None)):
            self._apply_printer()
        else:
            self._update_overhang()
            self._invalidate()
        fkey = (cfg["technology"], cfg["filament_count"], cfg["filament_colors"])
        if fkey != getattr(self, "_filament_view_key", None):      # model colours follow the filament list
            self._filament_view_key = fkey
            self._objects_changed()
        self._save_state()
        self._schedule_suggestions()

    def _save_state(self):
        s = self.store
        s.setValue("printer", self.settings.printer_id)
        s.setValue("material", self.settings.material_id or "")
        s.setValue("quality", self.settings.quality_id or "")
        s.setValue("overrides", json.dumps(self.settings.overrides))
        s.setValue("advanced", "true" if self.settings.show_advanced else "false")
        sections = {cat: sec.header.isChecked() for cat, sec in getattr(self.settings, "_sections", {}).items()}
        if sections and not self.settings.filter_text:
            s.setValue("expanded_sections", json.dumps({**self.settings._expanded, **sections}))
        s.sync()

    # ================================================================ slicing
    def _invalidate(self):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
        had = self.result is not None
        self.result = None
        self.export_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.viewer.set_toolpaths(None, 0.4)
        self.viewer.set_extra_meshes([])
        for tile in (self.stat_time, self.stat_material, self.stat_weight, self.stat_cost):
            tile.set("—")
        self.warn_label.hide()
        self.cup_fix_btn.hide()
        if had:
            self.status.setText(tr("dirty"))
        if self.viewer.mode == "preview":
            self.set_mode("prepare", slice_if_needed=False)

    def slice_or_cancel(self):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
            return
        self.slice()

    def slice(self):
        if not self.objects:
            self.status.setText(tr("no_models"))
            return
        cfg = self.settings.config()
        outside = out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg), region=plate_region(cfg))
        if outside:
            QMessageBox.warning(self, tr("slice"), tr("out_of_bounds").format(", ".join(o.name for o in outside)))
            return
        models = [o for o in self.objects if o.role != "cutter"]
        if not models:
            self.status.setText(tr("no_models"))
            return
        if not self._confirm_supports(cfg, models):
            return
        self._check_collisions()
        if getattr(self, "_collision_pairs", None):
            ans = QMessageBox.warning(self, tr("collision_title"), self.collision_label.text() + "\n\n" + tr("collision_slice_q"),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        cfg = self.settings.config()
        self._learn_from_scene(cfg)
        meshes = [o.world_mesh() for o in models]
        cutters = [o.world_mesh() for o in self.objects if o.role == "cutter"]
        manual = [o.world_support_points(0.0) for o in models]
        direct = [o.direct for o in models]
        materials = [dental_shades.face_shades(len(o.mesh.faces), o.material, o.regions) if o.regions else o.material
                     for o in models]
        holes = [o.world_drain_holes(0.0) for o in models]
        name = self.objects[0].name or "print"

        def work(progress, cancelled):
            if cfg["technology"] == "sla":
                job = sla.prepare(meshes, cfg, progress, cancelled, support_points=manual, direct=direct, holes=holes, cutters=cutters,
                                  materials=materials)
                return ("sla", job, cfg)
            parts = [mt for o in models for mt in o.print_meshes()]
            fdm_meshes, tools = [m for m, _t in parts], [t for _m, t in parts]
            plan = fdm.plan(fdm_meshes, cfg, progress, cancelled, cutters=cutters, tools=tools)
            progress("gcode", 0.0)
            text, stats = gcode.generate(plan, cfg, name, meshes=fdm_meshes, tools=tools)
            progress("preview", 0.0)
            verts = toolpath_vertices(plan)
            return ("fdm", plan, text, stats, verts, cfg)

        self._start_task(work, self._on_sliced, tr("slicing"))

    def _confirm_supports(self, cfg, models) -> bool:
        """Resin: warn before slicing parts that would print with no supports at all."""
        if cfg["technology"] != "sla" or cfg["sla_process"] == "polyjet":
            return True
        bare = [o for o in models if not o.direct and (not cfg["support_enable"]
                or (o.support_points is not None and len(o.support_points) == 0))]
        if not bare:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("no_supports_title"))
        box.setText(tr("no_supports_text").format(", ".join(o.name or "model" for o in bare[:6])
                                                  + ("…" if len(bare) > 6 else "")))
        gen = box.addButton(tr("no_supports_generate"), QMessageBox.AcceptRole)
        anyway = box.addButton(tr("no_supports_anyway"), QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(gen)
        box.exec()
        if box.clickedButton() is gen:
            keep = self.selected_objects()
            self._syncing = True
            self.object_list.clearSelection()
            for o in bare:
                it = self._item(o.uid)
                if it is not None:
                    it.setSelected(True)
            self._syncing = False
            self.generate_supports()            # also turns supports on
            self._refresh_objects(select=[o.uid for o in keep] or None)
            return True
        return box.clickedButton() is anyway

    def _start_task(self, fn, on_done, busy_text):
        self.task = Task(fn, self)
        self.task.progress.connect(self._on_progress)
        self.task.succeeded.connect(on_done)
        self.task.failed.connect(self._on_failed)
        self.task.finished.connect(self._on_task_finished)
        self.progress.setValue(0)
        self.progress.show()
        self.progress_label.setText(busy_text)
        self.progress_label.show()
        self.slice_btn.setText("  " + tr("cancel"))
        self.slice_btn.setIcon(theme.icon("x", "#ffffff", 18))
        self.export_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.task.start()

    def _on_progress(self, stage, fraction):
        weights = {"slice": (0.0, 0.35), "support": (0.35, 0.45), "hollow": (0.35, 0.45), "paths": (0.45, 0.85), "gcode": (0.85, 0.95),
                   "preview": (0.95, 1.0), "export": (0.0, 0.5), "upload": (0.5, 1.0)}
        lo, hi = weights.get(stage, (0.0, 1.0))
        self.progress.setValue(int((lo + (hi - lo) * fraction) * 1000))
        self.progress_label.setText(f"{tr('stage_' + stage)} · {int(fraction * 100)}%")

    def _on_task_finished(self):
        self.progress.hide()
        self.progress_label.hide()
        self.slice_btn.setText("  " + tr("slice"))
        self.slice_btn.setIcon(theme.icon("layers", "#ffffff", 18))
        self.export_btn.setEnabled(self.result is not None)
        self.send_btn.setEnabled(self.result is not None)

    def _on_failed(self, message):
        if message:
            QMessageBox.critical(self, tr("slice_error"), message)
            self.status.setText(tr("slice_error"))
        else:
            self.status.setText(tr("ready"))

    def _on_sliced(self, result):
        self.result = result
        warnings = []
        if result[0] == "fdm":
            _, plan, text, stats, verts, cfg = result
            self.viewer.set_toolpaths(verts, plan.line_width)
            self.viewer.set_extra_meshes([])
            self._islands = {}
            self.island_btn.hide()
            n = len(plan.layers)
            self._layer_z = [l.z for l in plan.layers]
            self.stat_time.set(gcode.format_duration(stats.time_s))
            self.stat_material.set(f"{stats.filament_mm / 1000:.2f} m", tr("stat_filament"))
            self.stat_weight.set(f"{stats.filament_g:.1f} g")
            self.stat_cost.set(f"₪{stats.cost:.2f}")
            warnings = plan.warnings
        else:
            _, job, cfg = result
            extras = [(m, theme.MODEL_COLOR) for m in job.models]
            if job.supports is not None:
                extras.append((job.supports, theme.SUPPORT_COLOR))
            self.viewer.set_toolpaths(None, 0.4)
            self.viewer.set_extra_meshes(extras)
            n = job.layer_count
            self._layer_z = list(job.layer_z) or [(i + 1) * job.layer_height for i in range(n)]
            self._mask_cfg = cfg.copy()
            scale = 320.0 / cfg["resolution_x"]
            self._mask_cfg.values["resolution_x"] = 320
            self._mask_cfg.values["resolution_y"] = max(1, int(round(cfg["resolution_y"] * scale)))
            self._mask_cfg.values["antialiasing"] = "1"
            self.stat_time.set(gcode.format_duration(job.time_s))
            if job.support_layers:
                self.stat_material.set(f"{job.volume_ml:.1f} + {job.support_ml:.1f} ml", tr("stat_model_support"))
            else:
                self.stat_material.set(f"{job.volume_ml:.1f} ml", tr("stat_resin"))
            self.stat_weight.set(f"{job.weight_g:.1f} g")
            self.stat_cost.set(f"₪{job.cost:.2f}")
            warnings = [tr(ENGINE_TEXT[w]) if w in ENGINE_TEXT else w for w in job.warnings]
            warnings = self._print_checks(job) + warnings
            if job.saved_ml > 0:
                warnings.insert(0, tr("saved_resin").format(job.saved_ml))
            self._islands = {}
            for i, poly in job.islands:
                self._islands.setdefault(i, []).append(poly)
            self.island_btn.setVisible(bool(self._islands))
            self.island_fix_btn.setVisible(bool(self._islands))
        self.cup_fix_btn.setVisible(result[0] == "sla" and bool(result[1].cups))
        self.result_cfg = cfg
        if warnings:
            import html

            def line(w):
                color = theme.SUCCESS if w.startswith("✓") else theme.TEXT_DIM if w.startswith("▸") else theme.WARNING
                text = html.escape(w if w.startswith(("✓", "▸")) else "⚠ " + w)
                return f'<div style="color:{color}">{text}</div>'
            self.warn_label.setTextFormat(Qt.RichText)
            self.warn_label.setText("".join(line(w) for w in warnings))
            self.warn_label.show()
        self.layer_slider.blockSignals(True)
        self.layer_slider.setRange(0, max(0, n - 1))
        self.layer_slider.setValue(n - 1)
        self.layer_slider.blockSignals(False)
        self.export_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status.setText(f"{tr('stat_layers')}: {n} · {tr('estimate_note')}")
        self.set_mode("preview", slice_if_needed=False)
        self._on_layer(n - 1)
        self._schedule_suggestions()

    def set_mode(self, mode: str, slice_if_needed: bool = True):
        self.edit_bar.hide()
        if mode == "preview" and self.result is None:
            if slice_if_needed and self.objects:
                self.slice()
            self.prepare_btn.setChecked(True)
            self.preview_btn.setChecked(False)
            return
        self.viewer.set_mode(mode)
        self.prepare_btn.setChecked(mode == "prepare")
        self.preview_btn.setChecked(mode == "preview")
        preview = mode == "preview" and self.result is not None
        is_sla = preview and self.result[0] == "sla"
        self.layer_wrap.setVisible(preview)
        self.legend_wrap.setVisible(preview and not is_sla)
        self.mask_wrap.setVisible(is_sla)

    def _step_layer(self, delta):
        if self.viewer.mode == "preview":
            self.layer_slider.setValue(self.layer_slider.value() + delta)

    def _on_layer(self, value):
        if self.result is None:
            return
        n = self.layer_slider.maximum() + 1
        z = self._layer_z[value] if value < len(self._layer_z) else 0.0
        self.layer_label.setText(f"{value + 1} / {n}")
        self.layer_z.setText(f"{z:.2f} mm")
        if self.result[0] == "fdm":
            self.viewer.set_layer_range(0, value)
        else:
            job = self.result[1]
            self.viewer.set_layer_range(0, value, clip_z=z)
            img = sla.render_layer(job.layers[value], self._mask_cfg).convert("RGB")
            if job.support_layers and value < len(job.support_layers) and not job.support_layers[value].is_empty:
                from PIL import Image as _Image      # PolyJet gel support, drawn grey around the model
                gel = sla.render_layer(job.support_layers[value], self._mask_cfg)
                img = _Image.composite(_Image.new("RGB", img.size, (120, 130, 150)), img, gel)
            islands = self._islands.get(value)
            if islands:
                from PIL import Image as _Image
                mask = sla.render_layer(MultiPolygon(islands), self._mask_cfg)
                red = _Image.new("RGB", img.size, (255, 70, 90))
                img = _Image.composite(red, img, mask)
                self.layer_z.setText(f"{z:.2f} mm · {tr('island')}")
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
            pm = QPixmap.fromImage(qimg).scaled(self.mask_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.mask_label.setPixmap(pm)

    @staticmethod
    def _print_checks(job) -> list[str]:
        """PreForm-style validation lines; ✓ lines are passes, the others are warnings."""
        def layers_text(idx):
            idx = sorted(set(idx))
            return ", ".join(str(i + 1) for i in idx[:6]) + ("…" if len(idx) > 6 else "")
        out = [tr("check_title")]
        if job.islands:
            out.append(tr("check_islands").format(len(job.islands), layers_text(job.island_layers)))
        else:
            out.append("✓ " + tr("check_no_islands"))
        if job.cups:
            first, last = job.cups[0][0], job.cups[-1][0]
            out.append(tr("check_cups").format(first + 1, last + 1, max(a for _, a in job.cups)))
        else:
            out.append("✓ " + tr("check_no_cups"))
        if job.thin_layers:
            out.append(tr("check_thin").format(len(job.thin_layers), job.thin_layers[0] + 1))
        else:
            out.append("✓ " + tr("check_no_thin"))
        return out

    # ------------------------------------------------------------ suction cups
    def _cup_owners(self, job, cfg) -> list[tuple]:
        """(object, vent, lift) for every vent the slicer found, matched to the model it belongs to."""
        out = []
        models = [o for o in self.objects if o.role != "cutter"]
        for v in job.cup_vents:
            for o in models:
                lift = self._lift(cfg) if cfg["support_enable"] and not o.direct else 0.0
                lo, hi = o.world_mesh().bounds
                if lo[0] - 0.5 <= v["x"] <= hi[0] + 0.5 and lo[1] - 0.5 <= v["y"] <= hi[1] + 0.5 \
                        and lo[2] - 0.5 <= v["z_surface"] - lift <= hi[2] + 0.5:
                    out.append((o, v, lift))
                    break
        return out

    def fix_suction_cups(self):
        """Fix menu for the suction-cup warning: vents, drain holes, orientation, tilt, or show the layer."""
        if self.result is None or self.result[0] != "sla":
            return
        job, cfg = self.result[1], self.result[2]
        owners = self._cup_owners(job, cfg)
        menu = QMenu(self)
        vent = menu.addAction(theme.icon("sparkle", theme.ACCENT, 16), tr("cup_fix_vents").format(len(owners)))
        vent.setEnabled(bool(owners))
        drain = None
        if cfg["hollow_enable"] and int(cfg["drain_holes"]) == 0:
            drain = menu.addAction(tr("cup_fix_drain"))
        orient_a = menu.addAction(theme.icon("compass", theme.TEXT, 16), tr("cup_fix_orient"))
        tilt = menu.addAction(tr("cup_fix_tilt"))
        menu.addSeparator()
        show = menu.addAction(tr("cup_fix_show"))
        btn = self.cup_fix_btn
        chosen = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen is None:
            return
        targets = list({o.uid: o for o, _, _ in owners}.values()) or [o for o in self.objects if o.role != "cutter"]
        if chosen == show:
            self.layer_slider.setValue(job.cups[0][0])
            return
        self._push_undo()
        if chosen == vent:
            for o, v, lift in owners:
                for row in sla.vent_hole_rows(v, cfg, lift):
                    o.add_world_drain_hole(row[:3], row[3:], 0.0)
            self._objects_changed(refresh_list=False)
            self.status.setText(tr("cup_vents_added").format(len(owners)))
            QTimer.singleShot(400, self.slice)    # check again (after the change debounce, which would cancel it)
        elif chosen == drain:
            self.settings.set_value("drain_holes", 2)
            QTimer.singleShot(400, self.slice)
        elif chosen == orient_a:
            from .viewer import SEL_ADD
            self._select_uid(targets[0].uid)
            for o in targets[1:]:
                self._select_uid(o.uid, SEL_ADD)
            self.auto_orient_selected()
            self.status.setText(tr("cup_reoriented"))
        elif chosen == tilt:
            a = np.radians(30.0)            # a tilted cavity drains as it rises out of the vat
            rot = np.array([[1.0, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
            for o in targets:
                o.apply_world_rotation(rot)
                self._drop_supports(o)
            self._objects_changed()
            self.status.setText(tr("cup_tilted"))

    def _next_island(self):
        if not self._islands:
            return
        cur = self.layer_slider.value()
        later = [i for i in sorted(self._islands) if i > cur]
        self.layer_slider.setValue(later[0] if later else min(self._islands))

    # ================================================================ assistant
    def _act(self, name: str, via: str, fn):
        self.assistant.record_action(name, via)
        fn()

    def _toggle_assistant(self):
        show = not self.assistant_wrap.isVisible()
        self.assistant_wrap.setVisible(show)
        if show:
            self._refresh_suggestions()

    def _schedule_suggestions(self):
        self._suggest_timer.start()

    def _learn_from_scene(self, cfg):
        """Commit point (slicing): turn what the user changed after the automatic tools into lessons."""
        learned = False
        for o in self.objects:
            state = o.__dict__.pop("_auto_orient_choice", None)
            if state is not None:
                tech, key, auto_features = state
                if o.matrix().tobytes() != key and tech == cfg["technology"]:
                    mesh = o.mesh.transform(o.matrix())
                    user = orient.current_features(mesh, tech, cfg["support_angle"])
                    learned |= self.assistant.learn_orientation(tech, auto_features, user)
            session = o.__dict__.pop("support_session", None)
            if session is not None:
                learned |= self.assistant.learn_support_edits(session["auto"], session["added"], session["removed"],
                                                              session["threshold"])
        if getattr(self, "_layout_pending", False):
            from .. import layout
            self._layout_pending = False
            learned |= self.assistant.learn_layout(layout.layout_stats(self.objects, plate_rect(cfg)))
        if learned:
            self.status.setText(tr("assistant_learned"))

    def _scene_context(self) -> dict:
        cfg = self.settings.config()
        objects = []
        for o in self.objects:
            det = abs(float(np.linalg.det(o.matrix()[:3, :3])))
            area = float(o.mesh.face_areas().sum()) * det ** (2 / 3)
            gain = self._orient_gain.get(o.uid)
            objects.append({
                "uid": o.uid, "name": o.name, "watertight": o.mesh.is_watertight(),
                "hollow_saving_ml": hollow_saving_ml(o.volume, area, cfg["hollow_wall"]),
                "orient_gain": gain[1] if gain and gain[0] == self._orient_key(o) else 0.0,
            })
        job = {}
        if self.result is not None and self.result[0] == "sla":
            j = self.result[1]
            job = {"islands": len(j.islands), "cups": len(j.cups), "thin": len(j.thin_layers)}
        free = 0
        if len(self.objects) == 1:
            x0, y0, x1, y1 = plate_rect(cfg)
            sx, sy = self.objects[0].world_size()[:2] + 6
            free = int(((x1 - x0) // sx) * ((y1 - y0) // sy)) - 1
        return {"technology": cfg["technology"], "cfg": cfg, "objects": objects, "job": job,
                "overrides": dict(self.settings.overrides), "plate_free_copies": free,
                "overlapping": self._overlapping_count(), "crashes": self._crash_context(),
                "dismissed_session": self._dismissed_session}

    def _crash_context(self) -> list[dict]:
        from .crash_dialog import entry_diagnosis, recent_crashes
        out = []
        for e in recent_crashes()[:3]:
            try:
                t = i18n.diagnosis_text(entry_diagnosis(e))
            except (TypeError, ValueError, IndexError):
                continue
            out.append({"signature": e["signature"], "area": t["area"] or "-", "title": t["title"],
                        "fix": t["fixes"][0] if t["fixes"] else ""})
        return out

    def on_crash_reported(self, _d):
        """The crash agent diagnosed an error: save the plate and show the problem in the assistant."""
        try:
            self._autosave()
        except Exception:
            pass
        QTimer.singleShot(0, self._schedule_suggestions)

    def _show_crash(self, sig: str):
        from .crash_dialog import CrashDialog, entry_diagnosis, recent_crashes
        for e in recent_crashes():
            if e["signature"] == sig:
                CrashDialog(entry_diagnosis(e), self).exec()
                return

    def _column_map(self, o: SceneObject):
        """Collision map of a part where it is drawn (cached per shape; moving only shifts it)."""
        from .. import collision
        disp = self._display_matrix(o)
        key = (id(o.mesh), disp[:3, :3].tobytes())
        cache = self.__dict__.setdefault("_column_maps", {})
        hit = cache.get(o.uid)
        if hit is None or hit[0] != key:
            hit = (key, collision.ColumnMap(o.mesh.transform(disp)), disp[:3, 3].copy())
            cache[o.uid] = hit
        d = disp[:3, 3] - hit[2]
        return hit[1].shifted(d[0], d[1], d[2])

    def _check_collisions(self):
        """Parts touching or closer than 0.5 mm turn red, with a warning above the view."""
        from .. import collision
        parts = [o for o in self.objects if o.role != "cutter"]
        pairs = []
        if 1 < len(parts) <= 80:
            maps = {o.uid: self._column_map(o) for o in parts}
            pairs = collision.colliding_pairs(maps, clearance=0.5)
        colliding = {u for p in pairs for u in p}
        changed = colliding != getattr(self, "_colliding", set())
        self._colliding = colliding
        self._collision_pairs = pairs
        if changed:
            cfg = self.settings.config()
            outside = {o.uid for o in out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg), region=plate_region(cfg))}
            for o in self.objects:
                if o.role != "cutter" and o.uid not in outside:
                    self.viewer.set_object_state(o.uid, "collision" if o.uid in colliding else "normal")
        if pairs:
            names = {o.uid: o.name or "model" for o in self.objects}
            text = ", ".join(f"{names[a]} ↔ {names[b]}" for a, b in pairs[:3]) + ("…" if len(pairs) > 3 else "")
            self.collision_label.setText("⚠ " + tr("collision_warning").format(text))
            self.collision_wrap.show()
            self.collision_wrap.raise_()
        else:
            self.collision_wrap.hide()

    def _overlapping_count(self) -> int:
        """Parts whose footprints overlap another part (they would print fused)."""
        from ..layout import footprint
        parts = [o for o in self.objects if o.role != "cutter"]
        if len(parts) < 2 or len(parts) > 60:
            return 0
        cache = self.__dict__.setdefault("_footprints", {})
        prints = []
        from shapely import affinity
        for o in parts:
            # the outline only changes with rotation / scale; moving just shifts it
            key = (id(o.mesh), o.matrix().tobytes())
            hit = cache.get(o.uid)
            if hit is None or hit[0] != key:
                hit = (key, footprint(o), o.x, o.y)
                cache[o.uid] = hit
            prints.append(affinity.translate(hit[1], o.x - hit[2], o.y - hit[3]))
        bad = set()
        for i in range(len(prints)):
            for j in range(i + 1, len(prints)):
                if prints[i].intersection(prints[j]).area > 1.0:
                    bad |= {i, j}
        return len(bad)

    def _refresh_suggestions(self):
        if not self.assistant.enabled:
            items = []
        else:
            items = self.assistant.suggestions(self._scene_context())
        self.assistant_panel.set_suggestions(items)
        self.assistant_panel.set_learning(self.assistant.enabled, self.assistant.summary())
        problems = [s for s in items if s.kind == "problem"]
        self.assistant_btn.setText(f"{tr('assistant')} ({len(items)})" if items else tr("assistant"))
        self.assistant_btn.setIcon(theme.icon("bulb", theme.WARNING if problems else (theme.ACCENT if items else theme.TEXT), 18))
        new = {s.id for s in problems} - self._seen_problems
        if new:
            self._seen_problems |= new
            self.assistant_wrap.show()
        self._analyse_orientations()

    def _orient_key(self, o: SceneObject) -> bytes:
        return o.matrix().tobytes() + self.settings.config()["technology"].encode()

    def _analyse_orientations(self):
        """Background: how much better would auto-orient be for each model? (feeds a suggestion)"""
        if self._orient_task is not None and self._orient_task.isRunning():
            return
        cfg = self.settings.config()
        tech, angle = cfg["technology"], cfg["support_angle"]
        todo = [o for o in self.objects if self._orient_gain.get(o.uid, (None,))[0] != self._orient_key(o)]
        if not todo or not self.assistant.enabled:
            return
        o = todo[0]
        key = self._orient_key(o)
        weights = self.assistant.orient_weights(tech)
        mesh = orient.sampled(o.mesh, 12000).transform(o.matrix())    # a light copy keeps the UI responsive
        uid = o.uid

        def work(progress, cancelled):
            now = float(orient.current_features(mesh, tech, angle) @ weights)
            dirs = orient.candidate_directions(mesh, tech, 160)
            best = float((orient.features(mesh, tech, angle, dirs) @ weights).min())
            return 0.0 if now <= 1e-9 else max(0.0, 1.0 - best / now)

        def done(gain):
            self._orient_gain[uid] = (key, gain)
            self._schedule_suggestions()

        self._orient_task = Task(work, self)
        self._orient_task.succeeded.connect(done)
        self._orient_task.start()

    def _apply_suggestion(self, s):
        a, args = s.action, s.action_args
        if a == "set":
            for k, v in args.items():
                self.settings.set_value(k, v)
        elif a == "auto_orient_all":
            self._select_uid(None)
            self.auto_orient_selected()
        elif a == "auto_layout":
            self.auto_layout(scope="all", dialog=False)
        elif a == "auto_orient":
            self._select_uid(args["uid"])
            self.auto_orient_selected()
        elif a == "denser_supports":
            cfg = self.settings.config()
            self.settings.set_value("sla_support_spacing", round(cfg["sla_support_spacing"] * 0.75, 2))
            self._select_uid(None)
            if cfg["technology"] == "sla":
                self.generate_supports()
        elif a == "multiply":
            if self.objects:
                self._select_uid(self.objects[0].uid)
                self.multiply_selected()
        elif a == "save_default":
            self._save_as_default(args["key"], args["value"])
        elif a == "show_crash":
            self._show_crash(args["signature"])
            return                              # keep it listed until dismissed
        self._dismissed_session.add(s.id)
        self._schedule_suggestions()

    def _save_as_default(self, key: str, value):
        """Write a setting the user keeps choosing into their copy of the matching profile."""
        st = self.settings
        cat = SCHEMA_BY_KEY[key].category
        if cat in ("material", "exposure"):
            kind, pid = "materials", st.material_id
        elif cat == "printer" or cat == "gcode":
            kind, pid = "printers", st.printer_id
        else:
            kind, pid = "qualities", st.quality_id
        prof = profiles.load(kind).get(pid)
        if not prof:
            return
        values = dict(prof.get("values", {}))
        values[key] = value
        profiles.save_user_profile(kind, profiles.display_name(prof), values, st.technology, pid=pid,
                                   vendor=prof.get("vendor", "User"))
        st.overrides.pop(key, None)
        st.rebuild()
        self.status.setText(tr("saved_default").format(profiles.display_name(prof)))

    def _dismiss_suggestion(self, s):
        if s.persistent_dismiss:
            self.assistant.dismiss(s.id)
        else:
            self._dismissed_session.add(s.id)
        self._schedule_suggestions()

    def _ask_print_feedback(self):
        self.assistant_panel.ask_print_feedback()
        self.assistant_wrap.show()
        self._refresh_suggestions()

    def _on_print_report(self, outcome):
        cfg = self.settings.config()
        extra = self.assistant.record_print(outcome, cfg["technology"], cfg)
        self.status.setText(tr("print_thanks"))
        items = extra + self.assistant.suggestions(self._scene_context())
        self.assistant_panel.set_suggestions(items)
        self.assistant_panel.set_learning(self.assistant.enabled, self.assistant.summary())

    def _on_learning_toggled(self, on):
        self.assistant.set_enabled(on)
        self._refresh_suggestions()

    def _on_learning_reset(self):
        if QMessageBox.question(self, tr("assistant"), tr("learning_reset_q")) == QMessageBox.Yes:
            self.assistant.reset()
            self._refresh_suggestions()

    # ================================================================ files
    def save_project(self):
        last = self.store.value("project_dir", os.path.expanduser("~"))
        name = (self.objects[0].name if self.objects else "project") or "project"
        path, _ = QFileDialog.getSaveFileName(self, tr("save_project"), os.path.join(last, name + project.EXTENSION),
                                              tr("project_filter"))
        if not path:
            return
        if not path.endswith(project.EXTENSION):
            path += project.EXTENSION
        try:
            project.save(path, self.objects, self.settings.state())
        except OSError as exc:
            QMessageBox.critical(self, tr("save_project"), str(exc))
            return
        self.store.setValue("project_dir", os.path.dirname(path))
        self.status.setText(tr("export_done").format(path))

    def _remember_file(self, path: str):
        try:
            recent = json.loads(self.store.value("recent_files", "[]"))
        except (TypeError, ValueError):
            recent = []
        path = os.path.abspath(path)
        recent = [path] + [p for p in recent if p != path]
        self.store.setValue("recent_files", json.dumps(recent[:10]))

    def _fill_recent(self):
        self.recent_menu.clear()
        try:
            recent = [p for p in json.loads(self.store.value("recent_files", "[]")) if os.path.exists(p)]
        except (TypeError, ValueError):
            recent = []
        if not recent:
            a = self.recent_menu.addAction(tr("recent_empty"))
            a.setEnabled(False)
            return
        for p in recent:
            fn = (lambda _=False, p=p: self.open_project(p)) if p.endswith(project.EXTENSION) else (lambda _=False, p=p: self.load_files([p]))
            self.recent_menu.addAction(os.path.basename(p), fn).setToolTip(p)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction(tr("recent_clear"), lambda: self.store.setValue("recent_files", "[]"))

    def _autosave_path(self) -> str:
        return os.path.join(profiles.USER_DIR, "autosave" + project.EXTENSION)

    def _autosave(self):
        """Save the plate every two minutes, so a crash or power cut loses little work."""
        if not self.objects or (self.task is not None and self.task.isRunning()):
            return
        running = getattr(self, "_autosave_task", None)
        if running is not None and running.isRunning():
            return
        # a snapshot is written on a background thread, so the window never pauses for it
        snapshot = [o.duplicate() for o in self.objects]
        for copy, o in zip(snapshot, self.objects):
            copy.name = o.name
        state = self.settings.state()
        path = self._autosave_path()

        def work(progress, cancelled):
            os.makedirs(profiles.USER_DIR, exist_ok=True)
            tmp = path + ".tmp"
            project.save(tmp, snapshot, state)
            os.replace(tmp, path)
            return True

        task = Task(work, self)
        task.succeeded.connect(lambda _r: self.store.setValue("autosave_clean", "false"))
        self._autosave_task = task
        task.start()

    def offer_recovery(self):
        """After a crash: offer the plate saved by autosave."""
        path = self._autosave_path()
        if self.store.value("autosave_clean", "true") != "false" or not os.path.exists(path) or self.objects:
            return
        ans = QMessageBox.question(self, tr("recover_title"), tr("recover_text"), QMessageBox.Yes | QMessageBox.No)
        if ans == QMessageBox.Yes:
            self.open_project(path)
        self.store.setValue("autosave_clean", "true")

    def open_project(self, path: str | None = None):
        if not path:
            last = self.store.value("project_dir", os.path.expanduser("~"))
            path, _ = QFileDialog.getOpenFileName(self, tr("open_project"), last, tr("project_filter"))
            if not path:
                return
        try:
            objects, state = project.load(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("project_error"), f"{os.path.basename(path)}\n\n{exc}")
            return
        self._push_undo()
        self.store.setValue("project_dir", os.path.dirname(path))
        if os.path.abspath(path) != os.path.abspath(self._autosave_path()):
            self._remember_file(path)
        if state:
            self.settings.load_state(state)
        self.objects = objects
        self._apply_printer()
        self._refresh_objects(select=objects[0].uid if objects else None)
        self.viewer.fit_objects()

    def export_stl(self, selection_only: bool = False):
        if not self.objects:
            return
        chosen = self.selected_objects() if selection_only else self.objects
        cfg = self.settings.config()
        last = self.store.value("export_dir", os.path.expanduser("~"))
        name = self.objects[0].name or "model"
        path, _ = QFileDialog.getSaveFileName(self, tr("export_stl"), os.path.join(last, name + ".stl"), "STL (*.stl)")
        if not path:
            return
        models = [o for o in chosen if o.role != "cutter"]
        meshes = [o.world_mesh() for o in models]
        if cfg["technology"] == "sla" and cfg["support_enable"] and cfg["sla_process"] != "polyjet":
            lift = self._lift(cfg)
            parts = []
            for o, m in zip(models, meshes):
                lifted = m.translated([0, 0, lift])
                sup, _ = sla_supports.build_supports(lifted, cfg, o.world_support_points(lift))
                parts += [lifted, sup]
            meshes = parts
        try:
            save_stl(Mesh.merge(meshes, name), path)
        except OSError as exc:
            QMessageBox.critical(self, tr("export_error"), str(exc))
            return
        self.store.setValue("export_dir", os.path.dirname(path))
        self.status.setText(tr("export_done").format(path))

    # ================================================================ export
    def export(self):
        if self.result is None or (self.task is not None and self.task.isRunning()):
            return
        name = (self.objects[0].name if self.objects else "print") or "print"
        last = self.store.value("export_dir", os.path.expanduser("~"))
        if self.result[0] == "fdm":
            path, _ = QFileDialog.getSaveFileName(self, tr("export_gcode"), os.path.join(last, name + ".gcode"),
                                                  "G-code (*.gcode *.gco *.g)")
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.result[2])
            except OSError as exc:
                QMessageBox.critical(self, tr("export_error"), str(exc))
                return
            self.store.setValue("export_dir", os.path.dirname(path))
            self.status.setText(tr("export_done").format(path))
            self._ask_print_feedback()
        else:
            job, cfg = self.result[1], self.result[2]
            ext = {"sl1": ".sl1", "ctb": ".ctb", "goo": ".goo", "prz": ".prz", "slc": ".slc", "cli": ".cli",
                   "3mf": ".3mf"}.get(cfg["sla_format"], ".zip")
            path, _ = QFileDialog.getSaveFileName(self, tr("export_sla"), os.path.join(last, name + ext), f"*{ext}")
            if not path:
                return
            self.store.setValue("export_dir", os.path.dirname(path))
            result = self.result

            def work(progress, cancelled):
                return sla.export(job, cfg, path, name, lambda f: progress("export", f), cancelled)

            def done(p):
                self.status.setText(tr("export_done").format(p))
                self._ask_print_feedback()

            self._start_task(work, done, tr("exporting"))
            self.result = result

    # ================================================================ remote control
    def remote_control(self):
        from .remote_dialog import RemoteDialog
        RemoteDialog(self.remote, self).exec()

    def _remote_command_done(self, cmd, ok, msg):
        action = (cmd or {}).get("action", "")
        if action == "refresh":
            return
        text = tr("remote_cmd_" + action) if ("remote_cmd_" + action) in i18n.STRINGS else action
        self.status.setText(tr("remote_cmd_done").format(text) if ok else tr("remote_cmd_failed").format(text, msg))

    # ================================================================ network printers
    def network_printers(self):
        from .network_dialog import NetworkDialog
        NetworkDialog(self.settings.printer_id, self).exec()

    def send_to_printer(self):
        """Export the sliced plate to a temporary file and send it to a printer on the network (Wi-Fi/LAN)."""
        import tempfile

        from .. import network
        from .network_dialog import NetworkDialog, SendDialog, lang
        if self.result is None or (self.task is not None and self.task.isRunning()):
            return
        while True:
            items = network.load_printers()
            if not items:
                if NetworkDialog(self.settings.printer_id, self).exec() != QDialog.Accepted:
                    return
                items = network.load_printers()
                if not items:
                    return
            base = (self.objects[0].name if self.objects else "print") or "print"
            if self.result[0] == "fdm":
                ext = ".gcode"
            else:
                ext = {"sl1": ".sl1", "ctb": ".ctb", "goo": ".goo", "prz": ".prz", "slc": ".slc", "cli": ".cli",
                       "3mf": ".3mf"}.get(self.result[2]["sla_format"], ".zip")
            dlg = SendDialog(items, self.settings.printer_id, base + ext, True, self)
            r = dlg.exec()
            if r == 2:                                   # "Manage printers…"
                NetworkDialog(self.settings.printer_id, self).exec()
                continue
            if r != QDialog.Accepted:
                return
            break
        target, start = dlg.selected(), dlg.start.isChecked()
        name = dlg.name.text().strip() or base + ext
        if not name.lower().endswith(ext):
            name += ext
        result = self.result
        folder = tempfile.mkdtemp(prefix="unislicer_send_")
        path = os.path.join(folder, name)

        def work(progress, cancelled):
            try:
                if result[0] == "fdm":
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(result[2])
                else:
                    job, cfg = result[1], result[2]
                    sla.export(job, cfg, path, os.path.splitext(name)[0], lambda f: progress("export", f), cancelled)
                progress("upload", 0.0)
                network.send(target, path, name, start, lambda f: progress("upload", f), cancelled)
                return ("ok", name)
            except network.NetworkError as e:
                return ("err", network.explain(e, lang()))
            finally:
                import shutil
                shutil.rmtree(folder, ignore_errors=True)

        def done(r):
            kind, text = r
            if kind == "ok":
                msg = tr("net_sent_started" if start else "net_sent").format(text, target.name)
                self.status.setText(msg)
                QMessageBox.information(self, tr("net_send"), msg)
                self._ask_print_feedback()
            else:
                self.status.setText(tr("net_send_failed"))
                QMessageBox.warning(self, tr("net_send_failed"), text)

        self._start_task(work, done, tr("net_sending").format(target.name))
        self.result = result

    # ================================================================ events
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        projects = [p for p in paths if p.lower().endswith(project.EXTENSION)]
        if projects:
            self.open_project(projects[0])
            return
        paths = [p for p in paths if os.path.splitext(p)[1].lower() in SUPPORTED_EXTENSIONS]
        if paths:
            self.load_files(paths)

    def closeEvent(self, e):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
            self.task.wait(3000)
        # background helpers must finish before the window goes away (a running thread would crash)
        for t in (getattr(self, "_autosave_task", None), getattr(self, "_orient_task", None)):
            if t is not None and t.isRunning():
                t.wait(5000)
        if getattr(self, "remote", None) is not None:
            self.remote.shutdown()
        self.store.setValue("geometry", self.saveGeometry())
        self._save_state()
        self.store.setValue("autosave_clean", "true")     # a normal exit: nothing to recover next time
        super().closeEvent(e)
