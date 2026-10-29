"""Main application window."""
from __future__ import annotations

import json
import os

import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QInputDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, fdm, gcode, primitives, project, sla, sla_supports
from ..mesh import SUPPORTED_EXTENSIONS, Mesh, load_mesh, save_stl
from ..scene import SceneObject, arrange, out_of_bounds
from ..slicing import MultiPolygon
from . import theme
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
    languageSwitchRequested = Signal()

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

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left_panel())
        body.addWidget(self._build_viewport(), 1)
        body.addWidget(self._build_right_panel())
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
        logo.setPixmap(theme.logo_pixmap(32))
        lay.addWidget(logo)
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

        tool("open", tr("open"), tr("open_tip"), self.open_files)
        shapes = tool("shapes", tr("shapes"), tr("shapes"), None)
        menu = QMenu(self)
        menu.addAction(theme.icon("cube"), tr("shape_cube"), lambda: self.add_mesh(primitives.box(20, 20, 20, "Cube")))
        menu.addAction(tr("shape_cylinder"), lambda: self.add_mesh(primitives.cylinder(10, 25, 64, "Cylinder")))
        menu.addAction(tr("shape_sphere"), lambda: self.add_mesh(primitives.sphere(12, 32, 64, "Sphere")))
        menu.addAction(tr("shape_bridge"), lambda: self.add_mesh(primitives.demo_bridge()))
        shapes.setMenu(menu)
        shapes.setPopupMode(QToolButton.InstantPopup)
        tool("sparkle", tr("auto_prepare"), tr("auto_prepare_tip"), self.auto_prepare)
        tool("arrange", tr("arrange"), tr("arrange_tip"), self.arrange_objects)
        tool("copy", tr("duplicate"), tr("duplicate"), self.duplicate_selected)
        tool("trash", tr("delete"), tr("delete"), self.delete_selected)
        self.undo_btn = tool("undo", "", tr("undo") + " (Ctrl+Z)", self.undo)
        self.redo_btn = tool("redo", "", tr("redo") + " (Ctrl+Y)", self.redo)
        more = tool("more", tr("more"), tr("more"), None)
        mm = QMenu(self)
        mm.addAction(theme.icon("open"), tr("open_project"), self.open_project)
        mm.addAction(theme.icon("save"), tr("save_project"), self.save_project)
        mm.addSeparator()
        mm.addAction(theme.icon("download"), tr("export_stl"), self.export_stl)
        mm.addAction(theme.icon("copy"), tr("multiply"), self.multiply_selected)
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
        tool("globe", tr("language"), tr("language_tip"), self.languageSwitchRequested.emit)
        return bar

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
        self.object_list.setMinimumHeight(120)
        self.object_list.currentItemChanged.connect(self._on_list_selection)
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
        g.addLayout(btns, 8, 0, 1, 4)
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
        for k, b in enumerate((self.sup_auto_btn, self.sup_add_btn, self.sup_remove_btn, self.sup_clear_btn)):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b, k // 2, k % 2)
        sl.addLayout(row)
        self.sup_info = QLabel(tr("sup_none"))
        self.sup_info.setObjectName("Faint")
        self.sup_info.setWordWrap(True)
        sl.addWidget(self.sup_info)
        self.supports_card = sup
        lay.addWidget(sup)

        self.pos_x.valueChanged.connect(lambda v: self._edit("x", v))
        self.pos_y.valueChanged.connect(lambda v: self._edit("y", v))
        for i in range(3):
            self.rot[i].valueChanged.connect(lambda v, i=i: self._edit("rot", v, i))
            self.scale[i].valueChanged.connect(lambda v, i=i: self._edit("scale", v, i))
            self.size_spins[i].valueChanged.connect(lambda v, i=i: self._edit("size", v, i))
        return panel

    def _build_viewport(self):
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        self.viewer = Viewer()
        self.viewer.objectClicked.connect(self._select_uid)
        self.viewer.objectDragged.connect(self._on_drag)
        self.viewer.objectDragFinished.connect(self._on_drag_finished)
        self.viewer.surfacePicked.connect(self._on_support_add)
        self.viewer.rayPicked.connect(self._on_support_remove)
        self.viewer.glFailed.connect(self._on_gl_failed)
        grid.addWidget(self.viewer, 0, 0)

        # floating view tools
        tools = _card()
        tools.setStyleSheet("QFrame#Card { background: rgba(27,31,39,0.92); }")
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
        wrap = QWidget()
        wrap.setObjectName("Overlay")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(14, 14, 14, 14)
        wl.addWidget(tools)
        wl.addStretch(1)
        wrap.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        grid.addWidget(wrap, 0, 0, Qt.AlignTop | Qt.AlignLeft)

        # empty state
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
        self.layer_box.setStyleSheet("QFrame#Card { background: rgba(27,31,39,0.92); }")
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
        lwrap = QWidget()
        lwrap.setObjectName("Overlay")
        lwl = QVBoxLayout(lwrap)
        lwl.setContentsMargins(14, 70, 14, 40)
        lwl.addWidget(self.layer_box)
        grid.addWidget(lwrap, 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.layer_wrap = lwrap

        # legend
        self.legend = _card()
        self.legend.setStyleSheet("QFrame#Card { background: rgba(27,31,39,0.92); }")
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
            cb.setStyleSheet("QCheckBox::indicator { width: 0; height: 0; border: none; } QCheckBox:!checked { color: #5b6273; }")
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
        self.mask_card.setStyleSheet("QFrame#Card { background: rgba(27,31,39,0.95); }")
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
        self.settings.changed.connect(self._on_settings_changed)
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
        self.slice_btn.clicked.connect(self.slice_or_cancel)
        self.export_btn = QPushButton(theme.icon("download", "#08251a", 18), "  " + tr("export_gcode"))
        self.export_btn.setObjectName("Success")
        self.export_btn.setIconSize(QSize(18, 18))
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export)
        btns.addWidget(self.slice_btn, 1)
        btns.addWidget(self.export_btn, 1)
        al.addLayout(btns)
        lay.addWidget(action)
        return panel

    def _install_shortcuts(self):
        for seq, fn in ((QKeySequence.Open, self.open_files), (QKeySequence.Delete, self.delete_selected),
                        (QKeySequence("A"), self.arrange_objects), (QKeySequence("Ctrl+D"), self.duplicate_selected),
                        (QKeySequence("Ctrl+R"), self.slice_or_cancel), (QKeySequence("Ctrl+E"), self.export),
                        (QKeySequence("Up"), lambda: self._step_layer(1)), (QKeySequence("Down"), lambda: self._step_layer(-1)),
                        (QKeySequence.Undo, self.undo), (QKeySequence("Ctrl+Y"), self.redo), (QKeySequence.Redo, self.redo),
                        (QKeySequence.Save, self.save_project), (QKeySequence("Escape"), lambda: self._set_pick_mode(None))):
            act = QAction(self)
            act.setShortcut(seq)
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(fn)
            self.addAction(act)

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

    def open_files(self):
        last = self.store.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("open"), last, tr("file_filter"))
        if paths:
            self.store.setValue("last_dir", os.path.dirname(paths[0]))
            self.load_files(paths)

    def load_files(self, paths):
        added = None
        for path in paths:
            try:
                meshes = load_mesh(path)
            except Exception as exc:
                QMessageBox.warning(self, tr("load_error"), f"{os.path.basename(path)}\n\n{exc}")
                continue
            for m in meshes:
                added = self.add_mesh(m, rearrange=False)
                self.status.setText(tr("loaded").format(m.name, len(m.faces)))
        if added is not None:
            self.arrange_objects()
            self.viewer.fit_objects()

    def add_mesh(self, mesh, rearrange: bool = True):
        self._push_undo()
        obj = SceneObject.from_mesh(mesh)
        cfg = self.settings.config()
        obj.x, obj.y = cfg["bed_x"] / 2, cfg["bed_y"] / 2
        self.objects.append(obj)
        if rearrange:
            arrange(self.objects, cfg["bed_x"], cfg["bed_y"])
        self._refresh_objects(select=obj.uid)
        return obj

    def duplicate_selected(self):
        obj = self.selected()
        if obj:
            self._push_undo()
            dup = obj.duplicate()
            self.objects.append(dup)
            cfg = self.settings.config()
            arrange(self.objects, cfg["bed_x"], cfg["bed_y"])
            self._refresh_objects(select=dup.uid)

    def delete_selected(self):
        obj = self.selected()
        if obj:
            self._push_undo()
            self.objects.remove(obj)
            self.viewer.remove_object(obj.uid)
            self._refresh_objects(select=self.objects[-1].uid if self.objects else None)

    def arrange_objects(self):
        self._push_undo()
        cfg = self.settings.config()
        arrange(self.objects, cfg["bed_x"], cfg["bed_y"])
        self._objects_changed()

    def lay_flat(self):
        obj = self.selected()
        if obj:
            self._push_undo()
            obj.lay_flat()
            self._drop_supports(obj)
            self._objects_changed()

    def center_selected(self):
        obj = self.selected()
        if obj:
            self._push_undo()
            cfg = self.settings.config()
            obj.x, obj.y = cfg["bed_x"] / 2, cfg["bed_y"] / 2
            self._objects_changed()

    def reset_transform(self):
        obj = self.selected()
        if obj:
            self._push_undo()
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
            for o in targets:
                o.auto_orient(cfg["technology"], cfg["support_angle"])
                self._drop_supports(o)
        finally:
            QApplication.restoreOverrideCursor()
        self._objects_changed()

    def auto_prepare(self):
        """PreForm-style one click: orient every model, arrange the plate and (for resin) add supports."""
        if not self.objects:
            self.status.setText(tr("no_models"))
            return
        self.object_list.setCurrentItem(None)
        self.auto_orient_selected()
        cfg = self.settings.config()
        if not arrange(self.objects, cfg["bed_x"], cfg["bed_y"]):
            self.status.setText(tr("arrange_full"))
        if cfg["technology"] == "sla":
            self.generate_supports()
        self._objects_changed()
        self.viewer.fit_objects()
        self.status.setText(tr("auto_prepare_done"))

    def mirror_selected(self, axis: int):
        obj = self.selected()
        if obj:
            self._push_undo()
            obj.mirror[axis] *= -1
            self._drop_supports(obj)
            self._objects_changed()

    def multiply_selected(self):
        obj = self.selected()
        if not obj:
            return
        count, ok = QInputDialog.getInt(self, tr("multiply"), tr("multiply_count"), 4, 1, 200)
        if not ok:
            return
        self._push_undo()
        self.objects += [obj.duplicate() for _ in range(count)]
        cfg = self.settings.config()
        if not arrange(self.objects, cfg["bed_x"], cfg["bed_y"]):
            self.status.setText(tr("arrange_full"))
        self._refresh_objects(select=obj.uid)

    # ---------------------------------------------------------------- undo
    def _snapshot(self):
        return [(o, o.state()) for o in self.objects]

    def _push_undo(self):
        self._undo.append(self._snapshot())
        del self._undo[:-60]
        self._redo.clear()

    def _restore(self, snap):
        sel = self.selected()
        self.objects = [o for o, _ in snap]
        for o, st in snap:
            o.restore(st)
        self._refresh_objects(select=sel.uid if sel in self.objects else (self.objects[0].uid if self.objects else None))

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())

    def _refresh_objects(self, select=None):
        self._syncing = True
        self.object_list.clear()
        for o in self.objects:
            it = QListWidgetItem(theme.icon("cube", theme.TEXT_DIM, 16), o.name or "object")
            it.setData(Qt.UserRole, o.uid)
            self.object_list.addItem(it)
            if o.uid == select:
                self.object_list.setCurrentItem(it)
        self._syncing = False
        self._objects_changed(refresh_list=False)
        self._sync_transform()

    def _objects_changed(self, refresh_list: bool = True):
        cfg = self.settings.config()
        outside = {o.uid: o.name for o in out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"])}
        uids = {o.uid for o in self.objects}
        for uid in list(self.viewer.objects):
            if uid not in uids:
                self.viewer.remove_object(uid)
        for o in self.objects:
            self.viewer.set_object(o.uid, o.mesh, self._display_matrix(o), "outside" if o.uid in outside else "normal")
            self.viewer.set_object_supports(o.uid, self._support_preview(o, cfg))
        sel = self.selected()
        self._update_support_info()
        self.viewer.set_selected(sel.uid if sel else None)
        self.empty_hint.setVisible(not self.objects or getattr(self, "_gl_failed", False))
        self.transform_card.setEnabled(sel is not None)
        self._sync_transform()
        self._invalidate()
        if outside:
            self.status.setText(tr("out_of_bounds").format(", ".join(sorted(outside.values()))))

    def _sync_transform(self):
        obj = self.selected()
        self._syncing = True
        if obj:
            size = obj.world_size()
            self.pos_x.setValue(obj.x)
            self.pos_y.setValue(obj.y)
            for i in range(3):
                self.rot[i].setValue(float(obj.rotation[i]))
                self.scale[i].setValue(float(obj.scale[i] * 100))
                self.size_spins[i].setValue(float(size[i]))
            watertight = obj.mesh.is_watertight()   # cached after the first call
            info = f"{len(obj.mesh.faces):,} {tr('triangles')} · {tr('volume')} {obj.volume / 1000:.2f} cm³"
            if not watertight:
                info += f"\n⚠ {tr('not_watertight')}"
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
        self._push_undo()
        if what in ("rot", "scale", "size"):
            self._drop_supports(obj)
        if what == "x":
            obj.x = value
        elif what == "y":
            obj.y = value
        elif what == "rot":
            obj.rotation[axis] = value
        elif what in ("scale", "size"):
            if what == "size":
                current = obj.world_size()[axis]
                if current <= 0:
                    return
                factor = value / current
            else:
                factor = (value / 100.0) / obj.scale[axis]
            if self.uniform.isChecked():
                obj.scale *= factor
            else:
                obj.scale[axis] *= factor
        self._objects_changed(refresh_list=False)

    def _on_list_selection(self, current, _previous):
        if self._syncing:
            return
        self.viewer.set_selected(current.data(Qt.UserRole) if current else None)
        self.transform_card.setEnabled(current is not None)
        self._sync_transform()

    def _select_uid(self, uid):
        if uid is None:
            self.object_list.setCurrentItem(None)
            self.object_list.clearSelection()
            return
        for i in range(self.object_list.count()):
            it = self.object_list.item(i)
            if it.data(Qt.UserRole) == uid:
                self.object_list.setCurrentItem(it)
                break

    def _on_drag(self, uid, dx, dy):
        obj = next((o for o in self.objects if o.uid == uid), None)
        if obj:
            if not self._dragging:
                self._push_undo()
                self._dragging = True
            obj.x += dx
            obj.y += dy
            self.viewer.set_object_transform(uid, self._display_matrix(obj))
            self._syncing = True
            self.pos_x.setValue(obj.x)
            self.pos_y.setValue(obj.y)
            self._syncing = False

    def _on_drag_finished(self, _uid):
        self._dragging = False
        self._objects_changed(refresh_list=False)

    # ================================================================ supports
    def _lift(self, cfg=None) -> float:
        cfg = cfg or self.settings.config()
        return cfg["sla_elevation"] + cfg["sla_raft_thickness"]

    def _shows_supports(self, o: SceneObject) -> bool:
        return o.support_points is not None and self.viewer.technology == "sla"

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
        keys = ("sla_tip_diameter", "sla_pillar_diameter", "sla_contact_depth", "sla_supports_on_model",
                "sla_elevation", "sla_raft_thickness", "sla_raft_margin")
        key = (o.uid, o.support_points.tobytes(), o.matrix().tobytes(), tuple(cfg[k] for k in keys))
        hit = self._support_cache.get(o.uid)
        if hit is not None and hit[0] == key:
            return hit[1]
        lift = self._lift(cfg)
        disp = self._display_matrix(o)
        world = o.mesh.transform(disp)
        mesh, _ = sla_supports.build_supports(world, cfg, o.world_support_points(lift))
        local = mesh.transform(np.linalg.inv(disp)) if len(mesh.faces) else None
        self._support_cache[o.uid] = (key, local)
        return local

    def _drop_supports(self, o: SceneObject):
        if o.support_points is not None:
            o.support_points = None
            self.status.setText(tr("supports_cleared"))

    def _support_targets(self) -> list[SceneObject]:
        sel = self.selected()
        return [sel] if sel else list(self.objects)

    def generate_supports(self):
        if not self.objects:
            return
        self._push_undo()
        self._ensure_supports_enabled()
        cfg = self.settings.config()
        lift = self._lift(cfg)
        for o in self._support_targets():
            world = o.mesh.transform(o.world_matrix())
            world = world.translated([0, 0, lift])
            o.set_world_support_points(sla_supports.auto_support_points(world, cfg), lift)
        self._objects_changed(refresh_list=False)

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
        self.viewer.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)
        if mode:
            self.status.setText(tr("sup_add_hint") if mode == "add" else tr("sup_remove_hint"))

    def _on_support_add(self, uid, point):
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is None:
            return
        self._push_undo()
        self._ensure_supports_enabled()
        cfg = self.settings.config()
        lift = self._lift(cfg)
        # the click is on the drawn model, which is raised only once it has edited supports
        p = np.asarray(point, dtype=float)
        if not self._shows_supports(o):
            p = p + [0, 0, lift]
        if o.support_points is None:
            # start from the automatic layout, then add to it
            world = o.mesh.transform(o.world_matrix()).translated([0, 0, lift])
            o.set_world_support_points(sla_supports.auto_support_points(world, cfg), lift)
        pts = o.world_support_points(lift)
        o.set_world_support_points(np.vstack([pts, p[None, :]]), lift)
        self._objects_changed(refresh_list=False)

    def _on_support_remove(self, origin, direction):
        lift = self._lift()
        best = None
        for o in self.objects:
            if not self._shows_supports(o) or len(o.support_points) == 0:
                continue
            pts = o.world_support_points(lift)
            d = np.linalg.norm(np.cross(pts - origin, direction), axis=1)
            k = int(np.argmin(d))
            if d[k] < 3.0 and (best is None or d[k] < best[0]):
                best = (d[k], o, k)
        if best is None:
            return
        self._push_undo()
        _, o, k = best
        o.support_points = np.delete(o.support_points, k, axis=0)
        self._objects_changed(refresh_list=False)

    def _update_support_info(self):
        sla_mode = self.viewer.technology == "sla"
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
    def _apply_printer(self):
        cfg = self.settings.config()
        tech = cfg["technology"]
        self.viewer.set_bed(cfg["bed_x"], cfg["bed_y"], cfg["max_z"], tech)
        self.stat_material.set("—", tr("stat_resin") if tech == "sla" else tr("stat_filament"))
        self.export_btn.setText("  " + (tr("export_sla") if tech == "sla" else tr("export_gcode")))
        self._update_overhang()
        self._objects_changed(refresh_list=False)

    def _update_overhang(self, *_):
        cfg = self.settings.config()
        self.viewer.set_overhang(self.overhang_btn.isChecked(), cfg["support_angle"])

    def _on_settings_changed(self):
        cfg = self.settings.config()
        b = self.viewer.bed
        if (cfg["bed_x"], cfg["bed_y"], cfg["max_z"]) != b or cfg["technology"] != self.viewer.technology:
            self._apply_printer()
        else:
            self._update_overhang()
            self._invalidate()
        self._save_state()

    def _save_state(self):
        s = self.store
        s.setValue("printer", self.settings.printer_id)
        s.setValue("material", self.settings.material_id or "")
        s.setValue("quality", self.settings.quality_id or "")
        s.setValue("overrides", json.dumps(self.settings.overrides))
        s.setValue("advanced", "true" if self.settings.show_advanced else "false")

    # ================================================================ slicing
    def _invalidate(self):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
        had = self.result is not None
        self.result = None
        self.export_btn.setEnabled(False)
        self.viewer.set_toolpaths(None, 0.4)
        self.viewer.set_extra_meshes([])
        for tile in (self.stat_time, self.stat_material, self.stat_weight, self.stat_cost):
            tile.set("—")
        self.warn_label.hide()
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
        outside = out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"])
        if outside:
            QMessageBox.warning(self, tr("slice"), tr("out_of_bounds").format(", ".join(o.name for o in outside)))
            return
        meshes = [o.world_mesh() for o in self.objects]
        manual = [o.world_support_points(0.0) for o in self.objects]
        name = self.objects[0].name or "print"

        def work(progress, cancelled):
            if cfg["technology"] == "sla":
                job = sla.prepare(meshes, cfg, progress, cancelled, support_points=manual)
                return ("sla", job, cfg)
            plan = fdm.plan(meshes, cfg, progress, cancelled)
            progress("gcode", 0.0)
            text, stats = gcode.generate(plan, cfg, name)
            progress("preview", 0.0)
            verts = toolpath_vertices(plan)
            return ("fdm", plan, text, stats, verts, cfg)

        self._start_task(work, self._on_sliced, tr("slicing"))

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
        self.task.start()

    def _on_progress(self, stage, fraction):
        weights = {"slice": (0.0, 0.35), "support": (0.35, 0.45), "hollow": (0.35, 0.45), "paths": (0.45, 0.85), "gcode": (0.85, 0.95),
                   "preview": (0.95, 1.0), "export": (0.0, 1.0)}
        lo, hi = weights.get(stage, (0.0, 1.0))
        self.progress.setValue(int((lo + (hi - lo) * fraction) * 1000))
        self.progress_label.setText(f"{tr('stage_' + stage)} · {int(fraction * 100)}%")

    def _on_task_finished(self):
        self.progress.hide()
        self.progress_label.hide()
        self.slice_btn.setText("  " + tr("slice"))
        self.slice_btn.setIcon(theme.icon("layers", "#ffffff", 18))
        self.export_btn.setEnabled(self.result is not None)

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
            self._layer_z = [(i + 1) * job.layer_height for i in range(n)]
            self._mask_cfg = cfg.copy()
            scale = 320.0 / cfg["resolution_x"]
            self._mask_cfg.values["resolution_x"] = 320
            self._mask_cfg.values["resolution_y"] = max(1, int(round(cfg["resolution_y"] * scale)))
            self._mask_cfg.values["antialiasing"] = "1"
            self.stat_time.set(gcode.format_duration(job.time_s))
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
        self.result_cfg = cfg
        if warnings:
            self.warn_label.setText("\n".join(("" if w.startswith(("✓", "▸")) else "⚠ ") + w for w in warnings))
            self.warn_label.show()
        self.layer_slider.blockSignals(True)
        self.layer_slider.setRange(0, max(0, n - 1))
        self.layer_slider.setValue(n - 1)
        self.layer_slider.blockSignals(False)
        self.export_btn.setEnabled(True)
        self.status.setText(f"{tr('stat_layers')}: {n} · {tr('estimate_note')}")
        self.set_mode("preview", slice_if_needed=False)
        self._on_layer(n - 1)

    def set_mode(self, mode: str, slice_if_needed: bool = True):
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

    def _next_island(self):
        if not self._islands:
            return
        cur = self.layer_slider.value()
        later = [i for i in sorted(self._islands) if i > cur]
        self.layer_slider.setValue(later[0] if later else min(self._islands))

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
        if state:
            self.settings.load_state(state)
        self.objects = objects
        self._apply_printer()
        self._refresh_objects(select=objects[0].uid if objects else None)
        self.viewer.fit_objects()

    def export_stl(self):
        if not self.objects:
            return
        cfg = self.settings.config()
        last = self.store.value("export_dir", os.path.expanduser("~"))
        name = self.objects[0].name or "model"
        path, _ = QFileDialog.getSaveFileName(self, tr("export_stl"), os.path.join(last, name + ".stl"), "STL (*.stl)")
        if not path:
            return
        meshes = [o.world_mesh() for o in self.objects]
        if cfg["technology"] == "sla" and cfg["support_enable"]:
            lift = self._lift(cfg)
            parts = []
            for o, m in zip(self.objects, meshes):
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
        else:
            job, cfg = self.result[1], self.result[2]
            ext = {"sl1": ".sl1", "ctb": ".ctb", "goo": ".goo", "prz": ".prz"}.get(cfg["sla_format"], ".zip")
            path, _ = QFileDialog.getSaveFileName(self, tr("export_sla"), os.path.join(last, name + ext), f"*{ext}")
            if not path:
                return
            self.store.setValue("export_dir", os.path.dirname(path))
            result = self.result

            def work(progress, cancelled):
                return sla.export(job, cfg, path, name, lambda f: progress("export", f), cancelled)

            def done(p):
                self.status.setText(tr("export_done").format(p))

            self._start_task(work, done, tr("exporting"))
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
        self.store.setValue("geometry", self.saveGeometry())
        self._save_state()
        super().closeEvent(e)
