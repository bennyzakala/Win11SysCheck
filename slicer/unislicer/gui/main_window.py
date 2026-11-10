"""Main application window."""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PySide6.QtCore import QEventLoop, QItemSelectionModel, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QCheckBox, QDoubleSpinBox, QFileDialog, QInputDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QScrollArea, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, dental_shades, fdm, gcode, orient, primitives, profiles, project, sla, sla_supports
from ..assistant import Assistant, hollow_saving_ml
from ..mesh import SUPPORTED_EXTENSIONS, Mesh, load_mesh, save_stl
from ..scene import SceneObject, out_of_bounds, plate_area, plate_rect, plate_region
from ..settings import SCHEMA_BY_KEY
from ..slicing import MultiPolygon
from . import i18n, theme
from .assistant_panel import AssistantPanel
from .i18n import label as setting_label, tr
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


def _two_lines(text: str) -> str:
    """'Export print file' as 'Export' over 'print file' (the button keeps its full label in a narrow panel)."""
    first, _, rest = text.strip().partition(" ")
    return f"{first}\n{rest}" if rest else first


class BarGrip(QLabel):
    """Handle that drags its floating bar around inside the bar's parent; double-click puts it back by the model."""

    def __init__(self, bar, moved, reset):
        super().__init__()
        self._bar, self._moved, self._reset = bar, moved, reset
        self._grab = None
        self.setPixmap(theme.icon("grip", theme.TEXT_DIM, 18).pixmap(18, 18))
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(tr("bar_drag_tip"))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint() - self._bar.pos()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._grab is None:
            return
        holder = self._bar.parentWidget()
        p = e.globalPosition().toPoint() - self._grab
        x = min(max(0, p.x()), max(0, holder.width() - self._bar.width()))
        y = min(max(0, p.y()), max(0, holder.height() - self._bar.height()))
        self._bar.move(x, y)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._grab is not None:
            self._grab = None
            self._moved(self._bar.pos())
            e.accept()

    def mouseDoubleClickEvent(self, e):
        self._grab = None
        self._reset()
        e.accept()


class StatTile(QFrame):
    def __init__(self, icon_name: str, caption: str, parent=None):
        super().__init__(parent)
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

    on_set = None                                 # called after every change (the summary next to Slice)

    def set(self, value: str, caption: str | None = None):
        self.value.setText(value)
        if caption:
            self.caption.setText(caption)
        if self.on_set is not None:
            self.on_set()


class MainWindow(QMainWindow):
    languageSwitchRequested = Signal(str)
    themeChangeRequested = Signal(str, str)     # theme name, logo file

    def __init__(self, objects: list[SceneObject] | None = None, carry: dict | None = None):
        """``carry``: what the window this one replaces (a language or theme switch) hands over - see
        ``handover`` - so the plate comes back as it was, supports and all, without building anything again."""
        super().__init__()
        if carry and carry.get("direction") is not None:
            self.setLayoutDirection(carry["direction"])     # set before the children are made
        self.setWindowTitle(f"UniSlicer {__version__}")
        self.setAcceptDrops(True)
        self.store = QSettings(SETTINGS_ORG, SETTINGS_ORG)
        self.objects: list[SceneObject] = list(objects or [])
        self.result = None          # ("fdm", plan, text, stats) | ("sla", job, cfg)
        self.result_cfg = None
        self.task: Task | None = None
        self._pre_task: Task | None = None           # background pre-slice (fills the slicer cache)
        # start the worker processes early, in the background: the first slice should not wait for them
        QTimer.singleShot(1500, self, lambda: __import__("threading").Thread(target=self._prewarm_pool, daemon=True).start())
        self._syncing = False
        self._undo: list = []
        self._redo: list = []
        self._dragging = False
        self._support_cache: dict = {}
        self._sup_pending: dict = {}                 # uid -> a support build waiting for the background
        if carry:                                    # the supports already built, the history, the slice
            self._support_cache.update(carry.get("support_cache") or {})
            self.__dict__["_geometry_cache"] = dict(carry.get("geometry_cache") or {})
            self._undo, self._redo = list(carry.get("undo") or []), list(carry.get("redo") or [])
            self.result, self.result_cfg = carry.get("result"), carry.get("result_cfg")
        self._fit_down: set = set()                  # parts whose fitting surface faced the plate (last generate)
        self.async_supports = os.environ.get("UNISLICER_SYNC_SUPPORTS") != "1"
        self._islands: dict = {}
        self.assistant = Assistant()
        self._orient_gain: dict = {}          # uid -> (orientation key, gain)
        self._model_source: dict = {}         # uid -> "extension|exporting program" (for learned repairs)
        self._repair_offers: dict = {}        # uid -> repair the user ran on models like this one
        self._orient_task: Task | None = None
        self._dismissed_session: set = set()
        self._seen_problems: set = set()
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(600)
        self._suggest_timer.timeout.connect(lambda: self._after_job(self._suggest_timer, self._refresh_suggestions))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_menu_bar()
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
        # a long message must never make the window wider than the screen (the right panel would go off it)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.setMinimumWidth(0)
        self.statusBar().addWidget(self.status, 1)
        if os.environ.get("UNISLICER_SOFTWARE_GL"):
            sw = QLabel("⚠ " + tr("software_gl"))
            sw.setObjectName("Warning")
            sw.setToolTip(tr("software_gl_tip"))
            self.statusBar().addPermanentWidget(sw)
        self.update_btn = QPushButton("⬆ " + tr("upd_available"))
        self.update_btn.setObjectName("Primary")
        self.update_btn.hide()
        self.update_btn.clicked.connect(self.install_update)
        self.statusBar().addPermanentWidget(self.update_btn)
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
        self._fit_to_screen()
        self._apply_printer()
        self._refresh_objects(select=self.objects[0].uid if self.objects else None)
        self.viewer.view_preset("iso")
        self.set_mouse_scheme(str(self.store.value("mouse_scheme", "chitubox")))
        self.settings.set_simple(self.store.value("settings_simple", "false") == "true")
        from .. import updater
        if (self.store.value("upd_auto", "true") == "true" and updater.build_commit()
                and not os.environ.get("UNISLICER_SMOKE_TEST")):
            QTimer.singleShot(5000, self, lambda: self.check_updates(manual=False))
        self.settings.simpleChanged.connect(lambda on: self.store.setValue("settings_simple", "true" if on else "false"))
        if carry:
            self._take_over(carry)

    def handover(self) -> dict:
        """What a new window needs to carry on where this one is (language / theme switch): the supports
        already built, the geometry indexes, undo / redo, the slice, the camera, the selection and the mode.
        Without it the new window built every model's supports again (14 crowns: ~9 s with the parts shown
        bare meanwhile), and lost the view and the undo history."""
        cam = self.viewer.camera
        return {"support_cache": dict(self._support_cache),
                "geometry_cache": dict(self.__dict__.get("_geometry_cache", {})),
                "undo": list(self._undo), "redo": list(self._redo),
                "result": self.result, "result_cfg": self.result_cfg,
                "camera": (cam.target.copy(), cam.distance, cam.yaw, cam.pitch),
                "selection": [o.uid for o in self.selected_objects()],
                "mode": "preview" if self.preview_btn.isChecked() else "prepare",
                "geometry": self.saveGeometry(), "maximized": self.isMaximized()}

    def release(self):
        """A window replaced by a language / theme switch drops what it held - the plate, its supports, the
        history, the slice, the model data in the 3D view - so it can be freed (it was kept, and every switch
        added another full plate to the memory)."""
        self._sup_pending.clear()
        for t in self.findChildren(QTimer):            # its timers must not fire on a window that is going
            t.stop()
        from PySide6.QtCore import QThread
        for t in self.findChildren(QThread):           # nor its background work outlive it (a thread deleted
            if t.isRunning():                          # while running takes the program down)
                t.wait(15000)
        self._support_cache = {}
        self.__dict__.pop("_geometry_cache", None)
        self._undo, self._redo = [], []
        self.result = None
        self.__dict__.pop("_last_result", None)
        for uid in list(self.viewer.objects):
            self.viewer.remove_object(uid)
        self.objects = []

    def _take_over(self, carry: dict):
        if carry.get("geometry") is not None:
            self.restoreGeometry(carry["geometry"])
        if carry.get("result") is not None:            # building the window cleared it: the slice comes back
            self.result, self.result_cfg = carry["result"], carry.get("result_cfg")
            self.export_btn.setEnabled(self._exportable() is not None)
            self.send_btn.setEnabled(self._exportable() is not None)
        cam = carry.get("camera")
        if cam is not None:
            c = self.viewer.camera
            c.target, c.distance, c.yaw, c.pitch = np.array(cam[0], dtype=float), cam[1], cam[2], cam[3]
            self.viewer.update()
        uids = {o.uid for o in self.objects}
        sel = [u for u in carry.get("selection") or [] if u in uids]
        if sel:
            self._refresh_objects(select=sel)
        else:
            self._select_uid(None)
        if carry.get("mode") == "preview" and self.result is not None:
            QTimer.singleShot(0, self, lambda: self.set_mode("preview", slice_if_needed=False))

    # ================================================================ layout
    def _fit_to_screen(self):
        """Keep the whole window on its screen (a saved size from a bigger screen, or a long message, must not
        push the right panel off the edge)."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None or self.isMaximized() or self.isFullScreen():
            return
        avail = screen.availableGeometry()
        g = self.frameGeometry()
        w, h = min(g.width(), avail.width()), min(g.height(), avail.height())
        if (w, h) != (g.width(), g.height()):
            self.resize(max(200, w - (g.width() - self.width())), max(200, h - (g.height() - self.height())))
        g = self.frameGeometry()
        x = min(max(g.x(), avail.x()), avail.right() - g.width() + 1)
        y = min(max(g.y(), avail.y()), avail.bottom() - g.height() + 1)
        if (x, y) != (g.x(), g.y()):
            self.move(x, y)

    def showEvent(self, e):
        super().showEvent(e)
        self._compact_top_bar()
        QTimer.singleShot(0, self, self._fit_to_screen)

    def _compact_top_bar(self):
        """On a narrow window (small screen, large Windows scaling) the top bar shows icons only, so it never
        forces the window wider than the screen; the names stay in the tooltips."""
        tools = getattr(self, "_top_tools", [])
        if not tools:
            return
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry().width() if screen is not None else 1920
        compact = min(self.width(), avail) < 1500
        style = Qt.ToolButtonIconOnly if compact else Qt.ToolButtonTextBesideIcon
        if tools[0].toolButtonStyle() != style:
            for b in tools:
                if b.text():
                    b.setToolButtonStyle(style)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._compact_top_bar()

    def _build_menu_bar(self):
        """Classic File / Edit / Info menus above the toolbar (every action is also on the toolbar or a key).
        Keys are shown as hints only: the window's own shortcuts already handle them, and giving the menu the
        same keys would make Qt treat them as ambiguous and fire neither."""
        bar = self.menuBar()
        bar.setObjectName("MainMenu")

        def add(menu, text, fn, keys="", icon=None):
            text = text.replace("……", "…").replace("…...", "…").replace("...…", "…").replace("......", "...")
            act = menu.addAction(theme.icon(icon) if icon else QIcon(), text + (f"\t{keys}" if keys else ""))
            act.triggered.connect(lambda _c=False: fn())
            return act

        f = bar.addMenu(tr("menu_file"))
        add(f, tr("new_project"), self.new_project, "Ctrl+N", "square")
        add(f, tr("open"), lambda: self._act("open", "menu", self.open_files), "Ctrl+O", "open")
        add(f, tr("open_project"), self.open_project, "", "open")
        recent = f.addMenu(theme.icon("clock"), tr("recent_files"))
        recent.aboutToShow.connect(lambda m=recent: self._fill_recent_into(m))
        add(f, tr("save_project"), lambda: self._act("save_project", "menu", self.save_project), "Ctrl+S", "save")
        f.addSeparator()
        add(f, tr("slice"), lambda: self._act("slice", "menu", self.slice_or_cancel), "Ctrl+R", "layers")
        add(f, tr("export_print"), lambda: self._act("export", "menu", self.export), "Ctrl+E", "download")
        add(f, tr("export_as") + "…", lambda: self.export(ask=True), "", "download")
        add(f, tr("export_stl"), self.export_stl, "", "download")
        add(f, tr("report_save") + "…", lambda: self.save_job_report(), "", "download")
        f.addSeparator()
        add(f, tr("export_library_open"), self.open_export_library, "", "open")
        add(f, tr("export_library_choose") + "…", self.choose_export_library, "", "settings")
        f.addSeparator()
        add(f, tr("net_title") + "…", self.network_printers, "", "wifi")
        add(f, tr("queue_title") + "…", self.queue_dialog, "", "layers")
        f.addSeparator()
        add(f, tr("menu_exit"), self.close, "Alt+F4", "x")

        e = bar.addMenu(tr("menu_edit"))
        add(e, tr("undo"), lambda: self._act("undo", "menu", self.undo), "Ctrl+Z", "undo")
        add(e, tr("redo"), self.redo, "Ctrl+Y", "redo")
        e.addSeparator()
        add(e, tr("select_all"), self.select_all, "Ctrl+A")
        add(e, tr("duplicate"), lambda: self._act("duplicate", "menu", self.duplicate_selected), "Ctrl+D", "copy")
        add(e, tr("multiply") + "…", self.multiply_selected, "", "copy")
        add(e, tr("delete"), self.delete_selected, "Del", "trash")
        e.addSeparator()
        add(e, tr("layout_quick"), lambda: self._act("arrange", "menu", self.arrange_objects), "A", "arrange")
        add(e, tr("auto_layout") + "…", lambda: self.auto_layout(), "Shift+A", "settings")
        self._fill_align_menu(e.addMenu(theme.icon("align"), tr("align_menu")))
        add(e, tr("center"), self.center_selected, "", "center")
        add(e, tr("move_to") + "…", self.move_to_dialog, "", "move")
        add(e, tr("scale_to") + "…", self.scale_dialog, "", "fit")
        add(e, tr("rename"), self.rename_selected, "", "code")
        e.addSeparator()
        add(e, tr("model_settings") + "…", self.model_settings_dialog, "", "settings")
        add(e, tr("texture_title") + "…", self.texture_dialog, "", "grid")
        add(e, tr("void_title") + "…", self.void_body_dialog, "", "drop")
        add(e, tr("cut_height"), self.cut_selected, "", "square")
        add(e, tr("split_parts"), self.split_selected, "", "grid")
        add(e, tr("merge_models"), self.merge_selected, "", "copy")
        add(e, tr("repair_mesh"), self.repair_selected, "", "settings")
        e.addSeparator()
        add(e, tr("hide_models"), self.hide_selected, "", "x")
        add(e, tr("show_all_models"), self.show_all_models)

        i = bar.addMenu(tr("menu_info"))
        add(i, tr("assistant"), self._toggle_assistant, "", "sparkle")
        add(i, tr("estimate_title") + "…", self.estimate_dialog, "", "coin")
        add(i, tr("manual_menu"), self.open_manual, "F1", "bulb")
        add(i, tr("menu_shortcuts"), self.shortcuts_dialog, "", "code")
        i.addSeparator()
        add(i, tr("menu_updates"), self.open_updates_page, "", "download")
        add(i, tr("menu_about") + "…", self.about_dialog, "", "info")
        self.main_menu = bar

    def _fill_recent_into(self, menu):
        """File > Recent: the same list as More > Recent files."""
        menu.clear()
        keep, self.recent_menu = self.recent_menu, menu
        try:
            self._fill_recent()
        finally:
            self.recent_menu = keep

    def shortcuts_dialog(self):
        """Info > Keyboard shortcuts: the guide's shortcut page (made from the same table as the shortcuts)."""
        self.open_manual("shortcuts")

    UPDATES_URL = "https://github.com/bennyzakala/Win11SysCheck/releases/download/unislicer-latest/UniSlicer-Setup.exe"

    def open_updates_page(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self.UPDATES_URL))

    def about_dialog(self):
        from .. import __version__
        QMessageBox.about(self, tr("menu_about"), tr("about_text").format(__version__))

    def _build_top_bar(self):
        self._top_tools = []
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
            self._top_tools.append(b)
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
        menu.addAction(theme.icon("drop"), tr("void_title") + "…", self.void_body_dialog)
        shapes.setMenu(menu)
        shapes.setPopupMode(QToolButton.InstantPopup)
        tool("sparkle", tr("ocp_button"), tr("ocp_tip"), self.one_click_plate)
        tool("sparkle", tr("auto_prepare"), tr("auto_prepare_tip"), self.auto_prepare)
        arr = tool("arrange", tr("arrange"), tr("arrange_tip"), None)
        am = QMenu(self)
        am.addAction(theme.icon("arrange"), tr("layout_quick") + "\tA", lambda: self._act("arrange", "button", self.arrange_objects))
        am.addAction(theme.icon("settings"), tr("auto_layout") + "…\tShift+A", lambda: self.auto_layout())
        am.addAction(theme.icon("copy"), tr("lay_fill"), self._fill_plate_quick)
        am.addSeparator()
        self._fill_align_menu(am.addMenu(theme.icon("align"), tr("align_menu")))
        arr.setMenu(am)
        arr.setPopupMode(QToolButton.MenuButtonPopup)
        arr.clicked.connect(lambda: self._act("arrange", "button", self.arrange_objects))
        tool("copy", tr("duplicate"), tr("duplicate"), lambda: self._act("duplicate", "button", self.duplicate_selected))
        tool("trash", tr("delete"), tr("delete"), self.delete_selected)
        self.undo_btn = tool("undo", "", tr("undo") + " (Ctrl+Z)", lambda: self._act("undo", "button", self.undo))
        self.redo_btn = tool("redo", "", tr("redo") + " (Ctrl+Y)", self.redo)
        more = tool("more", tr("more"), tr("more"), None)
        mm = QMenu(self)
        mm.addAction(theme.icon("square"), tr("new_project") + "\tCtrl+N", self.new_project)
        mm.addAction(theme.icon("open"), tr("open_project"), self.open_project)
        self.recent_menu = mm.addMenu(theme.icon("clock"), tr("recent_files"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        mm.addAction(theme.icon("save"), tr("save_project"), lambda: self._act("save_project", "button", self.save_project))
        mm.addSeparator()
        mm.addAction(theme.icon("download"), tr("export_stl"), self.export_stl)
        mm.addAction(theme.icon("coin"), tr("estimate_title") + "…", self.estimate_dialog)
        mm.addAction(theme.icon("download"), tr("report_save") + "…", lambda: self.save_job_report())
        self.report_auto_act = mm.addAction(tr("report_auto"))
        self.report_auto_act.setCheckable(True)
        self.report_auto_act.setChecked(self.store.value("export_report", "true") == "true")
        self.report_auto_act.toggled.connect(lambda on: self.store.setValue("export_report", "true" if on else "false"))
        self.project_auto_act = mm.addAction(tr("project_auto"))
        self.project_auto_act.setCheckable(True)
        self.project_auto_act.setChecked(self.store.value("export_project", "true") == "true")
        self.project_auto_act.toggled.connect(lambda on: self.store.setValue("export_project", "true" if on else "false"))
        self.close_holes_act = mm.addAction(tr("auto_close_holes"))
        self.close_holes_act.setCheckable(True)
        self.close_holes_act.setChecked(self.store.value("auto_close_holes", "true") == "true")
        self.close_holes_act.toggled.connect(lambda on: self.store.setValue("auto_close_holes", "true" if on else "false"))
        self.open_up_act = mm.addAction(tr("open_up_menu"))
        self.open_up_act.setCheckable(True)
        self.open_up_act.setChecked(self.store.value("crowns_open_up", "true") == "true")
        self.open_up_act.toggled.connect(lambda on: self.store.setValue("crowns_open_up", "true" if on else "false"))
        self.tissue_up_act = mm.addAction(tr("tissue_up_menu"))
        self.tissue_up_act.setCheckable(True)
        self.tissue_up_act.setChecked(self.store.value("tissue_flat_up", "true") == "true")
        self.tissue_up_act.toggled.connect(lambda on: self.store.setValue("tissue_flat_up", "true" if on else "false"))
        mm.addAction(theme.icon("bulb"), tr("manual_menu") + "\tF1", self.open_manual)
        mm.addAction(tr("upd_check_now"), lambda: self.check_updates(manual=True))
        self.upd_auto_act = mm.addAction(tr("upd_check_start"))
        self.upd_auto_act.setCheckable(True)
        self.upd_auto_act.setChecked(self.store.value("upd_auto", "true") == "true")
        self.upd_auto_act.toggled.connect(lambda on: self.store.setValue("upd_auto", "true" if on else "false"))
        mouse = mm.addMenu(tr("mouse_menu"))
        self._mouse_acts = {}
        for key in ("chitubox", "classic"):
            act = mouse.addAction(tr(f"mouse_{key}"))
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, k=key: self.set_mouse_scheme(k))
            self._mouse_acts[key] = act
        self.parts_act = mm.addAction(tr("ap_show_menu"))
        self.parts_act.setCheckable(True)
        self.parts_act.setChecked(self.store.value("ap_show", "true") == "true")
        self.parts_act.toggled.connect(lambda on: self.store.setValue("ap_show", "true" if on else "false"))
        self.defects_act = mm.addAction(tr("show_defects"))
        self.defects_act.setCheckable(True)
        self.defects_act.setChecked(self.store.value("show_defects", "true") == "true")
        self.defects_act.toggled.connect(self._toggle_defects)
        mm.addSeparator()
        mm.addAction(theme.icon("download"), tr("export_as") + "…", lambda: self.export(ask=True))
        mm.addAction(theme.icon("open"), tr("export_library_open"), self.open_export_library)
        mm.addAction(theme.icon("settings"), tr("export_library_choose") + "…", self.choose_export_library)
        mm.addAction(theme.icon("wifi"), tr("net_title") + "…", self.network_printers)
        mm.addAction(theme.icon("wifi"), tr("fl_menu"), self.send_to_formlabs)
        mm.addAction(theme.icon("layers"), tr("queue_title") + "…", self.queue_dialog)
        mm.addAction(theme.icon("wifi"), tr("remote_title") + "…", self.remote_control)
        mm.addAction(theme.icon("layers"), tr("fil_title") + "…", self.filaments_dialog)
        mm.addAction(theme.icon("sparkle"), tr("cad_title") + "…", self.dental_cad_dialog)
        mm.addAction(theme.icon("copy"), tr("multiply"), self.multiply_selected)
        mm.addSeparator()
        mm.addAction(theme.icon("square"), tr("cut_height"), self.cut_selected)
        mm.addAction(theme.icon("grid"), tr("split_parts"), self.split_selected)
        mm.addAction(theme.icon("settings"), tr("repair_mesh"), self.repair_selected)
        mm.addAction(theme.icon("settings"), tr("repair_advanced") + "…", self.repair_advanced_dialog)
        mm.addAction(theme.icon("grid"), tr("texture_title") + "…", self.texture_dialog)
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
        self.viewer.show_defects = self.store.value("show_defects", "true") == "true"
        self.viewer.objectClicked.connect(self._on_view_click)
        self.viewer.boxSelected.connect(self._on_box_select)
        self.viewer.contextRequested.connect(self._show_context_menu)
        self.viewer.objectDragged.connect(self._on_drag)
        self.viewer.objectHovered.connect(self._on_hover_object)
        self.viewer.objectDragFinished.connect(self._on_drag_finished)
        self.viewer.surfacePicked.connect(self._on_support_add)
        self.viewer.rotateStarted.connect(self._on_rotate_start)
        self.viewer.rotateDragged.connect(self._on_rotate_drag)
        self.viewer.rotateFinished.connect(self._on_rotate_end)
        self.viewer.facePicked.connect(self._on_face_picked)
        self.viewer.holePicked.connect(self._on_hole_picked)
        self.viewer.keepoutPicked.connect(self._on_keepout_picked)
        self.viewer.labelPicked.connect(self._on_label_picked)
        self.viewer.labelDragged.connect(self._on_label_dragged)
        self.viewer.labelDragFinished.connect(self._on_label_drag_finished)
        self.viewer.labelWheel.connect(self._on_label_wheel)
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
        from PySide6.QtWidgets import QComboBox
        self.section_axis = QComboBox()
        self.section_axis.addItems(["Z", "X", "Y"])
        self.section_axis.setToolTip(tr("section_axis_tip"))
        self.section_axis.hide()
        self.section_flip = QToolButton()
        self.section_flip.setText("⇄")
        self.section_flip.setCheckable(True)
        self.section_flip.setToolTip(tr("section_flip_tip"))
        self.section_flip.hide()
        tl.addWidget(self.section_axis)
        tl.addWidget(self.section_flip)
        tl.addWidget(self.section_slider)
        tl.addWidget(self.section_label)
        self.section_axis.currentIndexChanged.connect(lambda _i: self._on_section(self.section_slider.value()))
        self.section_flip.toggled.connect(lambda _on: self._on_section(self.section_slider.value()))
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
                                     ("hole", "drop", "H"), ("keepout", "shield", "K"), ("label", "code", "L"),
                                     ("measure", "ruler", "D")):
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

        nav = QLabel(tr("hint_nav_chitubox"))
        self._nav_label = nav
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
        # play the layers bottom to top (as CHITUBOX's preview playback)
        self.play_btn = QToolButton()
        self.play_btn.setIcon(theme.icon("play", theme.ACCENT, 18))
        self.play_btn.setToolTip(tr("layers_play"))
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._play_layers)
        lb.addWidget(self.play_btn, 0, Qt.AlignHCenter)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._play_step)
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
        self.assistant_panel.learnLayoutRequested.connect(self.learn_layout_now)
        self.assistant_panel.exportRequested.connect(self.export_learning)
        self.assistant_panel.shareToggled.connect(self._on_share_toggled)
        self.assistant_panel.syncRequested.connect(lambda: self.sync_learning(manual=True))
        self.assistant_panel.importRequested.connect(self.import_learning)
        self.assistant_panel.autoRepairToggled.connect(self._on_auto_repair_toggled)
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
        cwl0.setContentsMargins(0, 6, 0, 0)               # at the very top: never over the plate
        banner = QFrame()
        banner.setStyleSheet(f"QFrame {{ background: {theme.DANGER}; border-radius: 8px; }} QLabel {{ color: white; font-weight: 600; font-size: 12px; }}")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(10, 3, 3, 3)
        bl.setSpacing(8)
        self.collision_label = QLabel("")
        # short text (how many models touch); the names are in the tooltip, so the box stays small
        self.collision_label.setWordWrap(False)
        bl.addWidget(self.collision_label)
        fix = QPushButton(tr("auto_layout"))
        fix.setStyleSheet("QPushButton { background: white; color: black; border: none; border-radius: 6px; padding: 3px 10px; font-weight: 600; font-size: 12px; }")
        fix.setCursor(Qt.PointingHandCursor)
        fix.clicked.connect(lambda: self.auto_layout(scope="all", dialog=False))
        bl.addWidget(fix)
        cwl0.addWidget(banner)
        self.collision_banner = banner
        banner.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.collision_wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
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
        self._unsupported_timer.timeout.connect(lambda: self._after_job(self._unsupported_timer, self._show_unsupported))
        # dental CAD hot folder (3Shape / exocad / Medit export here)
        from .. import dental_cad
        self._cad_tracker = dental_cad.FolderTracker()
        self._cad_timer = QTimer(self)
        self._cad_timer.setInterval(1500)
        self._cad_timer.timeout.connect(self._poll_cad_folder)
        QTimer.singleShot(0, self, self._start_cad_watch)
        from .remote_dialog import RemoteController
        self.remote = RemoteController(parent=self)
        self.remote.commandDone.connect(self._remote_command_done)
        QTimer.singleShot(1500, self, self.remote.start_if_enabled)
        # shared learning between the user's computers: every 15 minutes (and once soon after start)
        self._learn_sync_timer = QTimer(self)
        self._learn_sync_timer.setInterval(15 * 60 * 1000)
        self._learn_sync_timer.timeout.connect(lambda: self.sync_learning(manual=False))
        self._learn_sync_task = None
        QTimer.singleShot(0, self, self._init_learning_share)      # the assistant panel is built later
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(120000)          # autosave every 2 minutes while there are models
        self._autosave_timer.timeout.connect(lambda: self._after_job(None, self._autosave))
        self._autosave_timer.start()
        self._collision_timer = QTimer(self)
        self._collision_timer.setSingleShot(True)
        self._collision_timer.setInterval(250)
        self._collision_timer.timeout.connect(lambda: self._after_job(self._collision_timer, self._check_collisions))
        # a burst of edits (typing, spin arrows held down) is handled once, after it settles
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(180)
        self._settings_timer.timeout.connect(self._on_settings_changed)
        self.settings.changed.connect(self._settings_timer.start)
        self.settings.valueEdited.connect(lambda k, v: self.assistant.record_override(k, v))
        self.settings.technologyChanged.connect(lambda _t: self._apply_printer())
        self.settings.filamentEditRequested.connect(self.filaments_dialog)
        self.settings.cfsSyncRequested.connect(self.sync_cfs)
        self.settings.settingApplied.connect(self.apply_setting)
        lay.addWidget(self.settings, 1)

        action = _card()
        al = QVBoxLayout(action)
        al.setContentsMargins(14, 14, 14, 14)
        al.setSpacing(10)
        # time / resin / weight / cost are kept up to date but not shown in the panel (more room for the settings);
        # they are in the job report PDF of every export and in the estimate window
        self.stat_time = StatTile("clock", tr("stat_time"), action)
        self.stat_material = StatTile("spool", tr("stat_filament"), action)
        self.stat_weight = StatTile("weight", tr("stat_weight"), action)
        self.stat_cost = StatTile("coin", tr("stat_cost"), action)
        for tile in (self.stat_time, self.stat_material, self.stat_weight, self.stat_cost):
            tile.hide()
            tile.on_set = self._update_slice_info
        self.warn_label = QLabel("")             # kept as the plain text of the checks (not shown)
        self.warn_label.setObjectName("Warning")
        self.warn_label.setWordWrap(True)
        self.warn_label.hide()
        al.addWidget(self.warn_label)
        # the print check: one row per result, a Fix button next to every problem that has one
        self.warn_box = QWidget()
        self.warn_rows = QVBoxLayout(self.warn_box)
        self.warn_rows.setContentsMargins(0, 0, 0, 0)
        self.warn_rows.setSpacing(3)
        self.warn_box.hide()
        al.addWidget(self.warn_box)
        self.cup_fix_btn = QPushButton(tr("check_fix_all"))          # "Fix all" problems of the print check
        self.cup_fix_btn.setObjectName("Primary")
        self.cup_fix_btn.setIcon(theme.icon("sparkle", theme.ON_ACCENT, 16))
        self.cup_fix_btn.setToolTip(tr("check_fix_all_tip"))
        self.cup_fix_btn.setCursor(Qt.PointingHandCursor)
        self.cup_fix_btn.clicked.connect(self.fix_all_checks)
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
        # print time and material of the sliced plate, just above Slice (a full row: the buttons need the width)
        self.slice_info = QLabel("")
        self.slice_info.setObjectName("SliceInfo")
        self.slice_info.setToolTip(tr("slice_info_tip"))
        self.slice_info.hide()
        al.addWidget(self.slice_info, 0, Qt.AlignLeading)
        self.slice_btn = QPushButton(theme.icon("layers", "#ffffff", 18), " " + tr("slice"))
        self.slice_btn.setObjectName("Primary")
        self.slice_btn.setIconSize(QSize(18, 18))
        self.slice_btn.setCursor(Qt.PointingHandCursor)
        self.slice_btn.clicked.connect(lambda: self._act("slice", "button", self.slice_or_cancel))
        self.export_btn = QPushButton(theme.icon("download", "#08251a", 18), _two_lines(tr("export_gcode")))
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
        btns.setSpacing(6)
        btns.addWidget(self.slice_btn, 1)
        btns.addWidget(self.export_btn, 1)
        self.export_btn.setMinimumWidth(self.export_btn.sizeHint().width())
        btns.addWidget(self.send_btn)
        al.addLayout(btns)
        lay.addWidget(action)
        return panel

    def _install_shortcuts(self):
        """The shortcuts of ``shortcuts.SHORTCUTS`` (the same table prints the guide's shortcut page)."""
        from .shortcuts import SHORTCUTS, STANDARD_KEYS

        def via_key(name, fn):
            return lambda: self._act(name, "shortcut", fn)

        def pick(mode):
            if self.supports_card.isVisible():
                self._set_pick_mode(None if self.viewer.pick_mode == mode else mode)

        actions = {
            "new_project": self.new_project,
            "open": via_key("open", self.open_files), "save_project": via_key("save_project", self.save_project),
            "export": via_key("export", self.export), "undo": via_key("undo", self.undo), "redo": self.redo,
            "delete": self.delete_selected, "duplicate": via_key("duplicate", self.duplicate_selected),
            "select_all": self.select_all, "escape": lambda: (self._set_pick_mode(None), self.set_tool("move")),
            "tool_move": lambda: self.set_tool("move"), "tool_rotate": lambda: self.set_tool("rotate"),
            "tool_face": lambda: self.set_tool("face"), "tool_hole": lambda: self.set_tool("hole"),
            "tool_keepout": lambda: self.set_tool("keepout"), "tool_label": lambda: self.set_tool("label"),
            "tool_measure": lambda: self.set_tool("measure"),
            "sup_add": lambda: pick("add"), "sup_remove": lambda: pick("remove"),
            "arrange": via_key("arrange", self.arrange_objects), "auto_layout": lambda: self.auto_layout(),
            "slice": via_key("slice", self.slice_or_cancel),
            "layer_up": lambda: self._step_layer(1), "layer_down": lambda: self._step_layer(-1),
            "panel_left": lambda: self.toggle_panel("left"), "panel_right": lambda: self.toggle_panel("right"),
            "manual": self.open_manual,
        }
        for name, seqs, _group, _he, _en in SHORTCUTS:
            for seq in seqs:
                key = getattr(QKeySequence, seq) if seq in STANDARD_KEYS else QKeySequence(seq)
                act = QAction(self)
                act.setShortcut(key)
                act.setShortcutContext(Qt.WindowShortcut)
                act.triggered.connect(actions[name])
                self.addAction(act)

    def open_manual(self, page: str = ""):
        """Build the user guide from the program as it is now (shortcuts, settings, printers) and open it."""
        from PySide6.QtCore import QStandardPaths, QUrl
        from PySide6.QtGui import QDesktopServices
        from .. import manual
        folder = os.path.join(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or
                              os.path.expanduser("~"), "manual")
        lang = "he" if i18n.LANG == "he" else "en"     # Hebrew, or English for the other languages
        try:
            path = manual.write(folder, lang)
        except OSError as e:
            QMessageBox.warning(self, tr("manual_menu"), str(e))
            return
        if page == "shortcuts":                     # the one-page sheet written next to the guide
            path = os.path.join(os.path.dirname(path), manual.file_names(lang)[1])
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------------------------------------------------------- section view / unsupported areas
    def _toggle_section(self, on: bool):
        self.section_slider.setVisible(on)
        self.section_label.setVisible(on)
        self.section_axis.setVisible(on)
        self.section_flip.setVisible(on)
        if not on:
            self.viewer.section_z = 1e9
            self.viewer.section_axis, self.viewer.section_sign = 2, 1.0
            self.viewer.update()
        else:
            self._on_section(self.section_slider.value())

    def _on_section(self, value):
        """Clipping view (as Asiga Composer's clipping tool): cut every model at a height to look inside."""
        axis = {"Z": 2, "X": 0, "Y": 1}[self.section_axis.currentText()]
        flip = self.section_flip.isChecked()
        if axis == 2:
            lo = 0.0
            hi = max((o.world_bounds()[1, 2] + (self._lift() if self._shows_supports(o) else 0) for o in self.objects),
                     default=1.0)
        else:
            boxes = [o.world_bounds() for o in self.objects] or [np.array([[0, 0, 0], [1, 1, 1]], dtype=float)]
            lo, hi = min(b[0, axis] for b in boxes), max(b[1, axis] for b in boxes)
        t = value / 1000.0
        pos = lo + (hi - lo) * (1.0 - t if flip else t)
        full = value >= 1000
        self.viewer.section_axis = axis
        self.viewer.section_sign = -1.0 if flip else 1.0
        # keep what is below / before the cut; flipped: what is above / past it (the shader tests sign * pos > clip)
        self.viewer.section_z = 1e9 if full else (-pos if flip else pos)
        self.section_label.setText(f"{'ZXY'[[2, 0, 1].index(axis)]} {pos:.2f} mm")
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
                    geo["auto_points"] = sla_supports.drop_keepout(sla_supports.auto_support_points(world, cfg),
                                                                   o.world_keepout(lift))
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
        res = self.result or getattr(self, "_fix_result", None)
        if res is None or res[0] != "sla" or not res[1].islands:
            self.status.setText(tr("islands_none"))
            return
        job = res[1]
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
                    geo["caster"] = sla_supports.caster_for(world)
                contact = sla_supports.underside_contact(geo["caster"], p + [0, 0, 0.3])
                if contact is None:
                    continue
                if o.support_points is None:
                    o.set_world_support_points(sla_supports.drop_keepout(sla_supports.auto_support_points(world, cfg),
                                                                         o.world_keepout(lift)), lift)
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
        if not paths:
            return
        self.store.setValue("last_dir", os.path.dirname(paths[0]))
        models = [p for p in paths if not p.lower().endswith((".unislicer", ".usp"))]
        if not models or self.store.value("ap_show", "true") != "true":
            self.load_files(paths)
        else:
            from .add_parts_dialog import AddPartsDialog
            def first_mesh(path):
                try:
                    m = load_mesh(path)
                except Exception:
                    return None
                return m[0] if isinstance(m, list) and m else (m if not isinstance(m, list) else None)

            dlg = AddPartsDialog(models, self.store, first_mesh, self)
            if dlg.exec() != QDialog.Accepted:
                return
            self.load_parts(dlg.parts())
            others = [p for p in paths if p not in models]
            if others:
                self.load_files(others)
        for p in paths:
            self._remember_file(p)

    def load_parts(self, parts: list[dict]):
        self._long(True)
        held = self._one_undo_step()
        try:
            self._load_parts(parts)
        finally:
            self._undo_held = held
            self._long(False)

    def _one_undo_step(self) -> bool:
        """Save the plate once and hold further saves (opening many files is one Ctrl+Z, not one per part -
        which filled the 60-step history so the plate before the load could not be had back). Returns the
        previous hold, to put back afterwards."""
        held = bool(getattr(self, "_undo_held", False))
        if not held:
            self._push_undo()
        self._undo_held = True
        self._held_before_load = held
        return held

    def _load_parts(self, parts: list[dict]):
        """Parts from the Add parts window: each with its type (on the plate or on supports, hollow), units
        and number of copies. Crowns and bridges are turned opening up (see _open_up); every other part keeps
        the file's orientation; placement as a plain Open does; no supports are added here."""
        added = []
        kinds = {}
        paths = list(dict.fromkeys(p["path"] for p in parts))
        read = dict(zip(paths, self._read_files(paths)))       # all files at once, in the background
        for part in parts:
            path = part["path"]
            meshes, source = read[path]
            if isinstance(meshes, Exception):
                meshes = self._rescue_model(path, meshes)
                if not meshes:
                    continue
            meshes = meshes if isinstance(meshes, list) else [meshes]
            group = []
            for m in meshes:
                if part.get("unit") == "inch":
                    m = Mesh(np.asarray(m.vertices) * 25.4, m.faces, m.name)
                for _ in range(max(1, int(part.get("qty", 1)))):
                    o = self.add_mesh(m, rearrange=False)
                    self._model_source[o.uid] = source
                    group.append(o)
            self._import_project_filaments(meshes)
            self._breathe()
            kind = part.get("kind", "asis")
            preset = self.DENTAL.get("model" if kind == "model_hollow" else kind)
            for o in group:                  # only a crown / bridge is turned (opening up), below
                kinds[o.uid] = kind if kind in ("crown", "gingiva") else "other"
                if preset is not None:
                    o.direct = preset[1]         # a model prints straight on the plate, a crown on supports
                if kind == "model_hollow":
                    o.settings["hollow_enable"] = True
            added += group
        if not added:
            return
        self._open_up(added, kinds)
        self.arrange_objects()
        self._objects_changed()
        self.viewer.fit_objects()
        self._undo_held = self.__dict__.get("_held_before_load", False)
        for o in added:                       # after arranging, so Ctrl+Z first undoes an automatic repair
            self._check_learned_repair(o)
        self._auto_close_holes(added)
        self.status.setText(tr("ap_loaded").format(len(added)))

    def load_files(self, paths):
        self._long(True)
        held = self._one_undo_step()
        try:
            self._load_files(paths)
        finally:
            self._undo_held = held
            self._long(False)

    def _read_files(self, paths) -> list:
        """[(meshes or the error, source)] for each file, read on several threads in the background with
        what loading asks of each mesh worked out there too (closed or not, the part type, a crown's
        opening, a mask's flat side, the repair fingerprint): one file after another, on the window's
        thread, 14 crowns took ~7 s with the window frozen up to 1.3 s at a time."""
        from concurrent.futures import ThreadPoolExecutor
        from .. import dental_cad
        crowns_on = self.store.value("crowns_open_up", "true") == "true"
        tissue_on = self.store.value("tissue_flat_up", "true") == "true"
        learned = bool(self.assistant.enabled and self.assistant.data.get("repair", {}).get("by_sig"))

        def one(path):
            try:
                meshes = load_mesh(path)
            except Exception as exc:
                return exc, ""
            source = self._file_source(path)
            for m in meshes:
                try:
                    closed = m.is_watertight()
                    kind = dental_cad.kind_of(m.name, m)
                    if kind == "crown" and crowns_on:
                        dental_cad.opening_direction(m)        # kept on the mesh for the turn after loading
                    elif kind == "gingiva" and tissue_on:
                        dental_cad.flat_side(m)
                    if learned and not (closed and len(m.faces) > 150_000):
                        from .. import mesh_tools
                        from ..assistant import repair_fingerprint
                        m.__dict__["_repair_fp"] = (m.vertices, repair_fingerprint(mesh_tools.analyze(m), source))
                except Exception:
                    pass                                         # worked out again later, as before
            return meshes, source

        paths = list(paths)
        if len(paths) <= 1:
            return [one(p) for p in paths]
        workers = max(1, min(len(paths), (os.cpu_count() or 2), 8))

        def all_files():
            with ThreadPoolExecutor(max_workers=workers) as ex:
                return list(ex.map(one, paths))
        return self._in_background(all_files)

    def _load_files(self, paths):
        added = None
        loaded = []
        for path, (meshes, source) in zip(paths, self._read_files(paths)):
            if isinstance(meshes, Exception):
                meshes = self._rescue_model(path, meshes)     # the assistant tries to recover the file
                if not meshes:
                    continue
            new_objs = []
            for m in meshes:
                added = self.add_mesh(m, rearrange=False)
                self._model_source[added.uid] = source
                new_objs.append(added)
                self.status.setText(tr("loaded").format(m.name, len(m.faces)))
            self._import_project_filaments(meshes)
            loaded += new_objs
            self._breathe()
        self._open_up(loaded)                     # crowns and bridges: opening up, before arranging
        if added is not None:
            self.arrange_objects()
            self.viewer.fit_objects()
        self._undo_held = self.__dict__.get("_held_before_load", False)
        for o in loaded:                      # after arranging, so Ctrl+Z first undoes an automatic repair
            self._check_learned_repair(o)
        self._auto_close_holes(loaded)

    def _open_up(self, objs, kinds: dict | None = None) -> int:
        """The two automatic turns, and only these: crowns and bridges with their opening (fitting surface)
        up, and gingiva masks with their flat side up, away from the plate. Only parts of those types are
        turned (``kinds``: the type chosen for each part in Add parts; otherwise the part's own model type or
        what its name and shape say); a part already that way up stays as it is; every other part keeps its
        orientation. Each is switched off under More. Returns how many parts were turned."""
        crowns_on = self.store.value("crowns_open_up", "true") == "true"
        tissue_on = self.store.value("tissue_flat_up", "true") == "true"
        if not (crowns_on or tissue_on):
            return 0
        from .. import dental_cad
        from ..mesh import rotation_between
        crowns, tissues = [], []
        for o in objs:
            if o.role == "cutter":
                continue
            kind = (kinds or {}).get(o.uid)
            if kind is None:
                own = o.settings.get("sla_model_type")
                kind = "crown" if own == "cb" else (None if own not in (None, "auto") else
                                                    dental_cad.kind_of(o.name, o.mesh))
            if kind == "crown" and crowns_on:
                crowns.append(o)
            elif kind == "gingiva" and tissue_on:
                tissues.append(o)
        if not crowns and not tissues:
            return 0
        found = self._in_background(lambda: dental_cad.opening_directions([o.mesh for o in crowns])
                                    + [dental_cad.flat_side(o.mesh) for o in tissues])
        turned = 0
        for o, d in zip(crowns + tissues, found):
            if d is None:
                continue
            dw = o.matrix()[:3, :3] @ d
            dw /= np.linalg.norm(dw) or 1.0
            if dw[2] > 0.98:
                continue                              # already that side up
            if dw[2] < -0.98:                         # upside down: turn over about its long side
                size = o.world_size()
                rot = np.diag([1.0, -1.0, -1.0]) if size[0] >= size[1] else np.diag([-1.0, 1.0, -1.0])
            else:
                rot = rotation_between(dw, np.array([0.0, 0.0, 1.0]))
            o.apply_world_rotation(rot)
            self._drop_supports(o)
            turned += 1
        if turned:
            self.status.setText(tr("auto_turn_done").format(turned))
        return turned

    # ================================================================ learned repairs
    def _file_source(self, path: str) -> str:
        """"extension|exporting program" of a model file (the program that wrote it, when it says so)."""
        from .. import model_rescue
        ext = os.path.splitext(path)[1].lower() or "?"
        try:
            with open(path, "rb") as fh:
                data = fh.read(4 << 20)
            prog = model_rescue.exporter_of(data) or "-"
        except Exception:
            prog = "-"
        return f"{ext}|{prog}"

    def _repair_fingerprint(self, o) -> str:
        hit = o.mesh.__dict__.get("_repair_fp")             # worked out while the file was read
        if hit is not None and hit[0] is o.mesh.vertices:
            return hit[1]
        from .. import mesh_tools
        from ..assistant import repair_fingerprint
        return repair_fingerprint(mesh_tools.analyze(o.mesh), self._model_source.get(o.uid, ""))

    def _recipe_text(self, recipe: dict) -> str:
        if recipe.get("mode") != "advanced":
            return tr("rr_basic")
        yes, no = tr("rr_yes"), tr("rr_no")
        return tr("rr_advanced").format(f"{recipe['weld']:g}", f"{recipe['shells'] * 100:g}",
                                        yes if recipe["holes"] else no, yes if recipe["nonmanifold"] else no)

    def _apply_repair_recipe(self, o, recipe: dict) -> dict:
        """Repair a model the way the user repaired similar ones (quick repair, or the advanced options)."""
        from .. import mesh_tools
        if recipe.get("mode") == "advanced":
            fixed, rep = mesh_tools.repair_advanced(o.mesh, recipe["weld"], recipe["shells"], recipe["nonmanifold"],
                                                    recipe["holes"])
        else:
            fixed, rep = mesh_tools.repair(o.mesh)
        o.mesh = fixed
        o.regions = []                    # faces changed: shaded regions / filament parts no longer apply
        o.filament_parts = []
        self._drop_supports(o)
        return rep

    # ---------------------------------------------------------------- automatic hole closing on load
    def _auto_close_holes(self, objs):
        """Models that load open (not watertight) are closed in the background, in a worker process so the
        program stays responsive (fast repair, then the advanced one). Ctrl+Z gives back the file's mesh."""
        if self.store.value("auto_close_holes", "true") != "true":
            return
        for o in objs:
            try:
                if o.role == "cutter" or o.mesh.is_watertight():
                    continue
            except Exception:
                continue
            self._start_close(o)

    def _start_close(self, o):
        from ..mesh import Mesh  # noqa: F401  (imported here: the worker returns raw arrays)
        mesh = o.mesh

        def work(progress, cancelled):
            from .. import mesh_tools, parallel
            ex = parallel.pool()
            if ex is not None:
                try:
                    return ex.submit(mesh_tools.watertight_job, mesh.vertices, mesh.faces, mesh.name).result()
                except Exception:
                    pass
            return mesh_tools.watertight_job(mesh.vertices, mesh.faces, mesh.name)

        task = Task(work, self)
        task.succeeded.connect(lambda res, o=o, mesh=mesh: self._on_closed(o, mesh, res))
        if not hasattr(self, "_close_tasks"):
            self._close_tasks = []
        self._close_tasks.append(task)
        task.finished.connect(lambda t=task: t in self._close_tasks and self._close_tasks.remove(t))
        self.status.setText(tr("closing_holes").format(o.name or "model"))
        task.start(QThread.LowPriority)

    def _on_closed(self, o, original, res):
        from ..mesh import Mesh
        v, f, ok = res
        if o not in self.objects or o.mesh is not original:
            return                            # removed or changed meanwhile: leave it alone
        if not ok and len(f) == len(original.faces):
            self.status.setText(tr("holes_not_closed").format(o.name or "model"))
            return
        self._push_undo()
        o.mesh = Mesh(v, f, original.name)
        o.__dict__.pop("_bounds_cache", None)
        o.regions = []
        o.filament_parts = []
        self._objects_changed()
        self.status.setText(tr("holes_closed" if ok else "holes_partly_closed").format(o.name or "model"))

    def _closing_holes(self) -> bool:
        return any(t.isRunning() for t in getattr(self, "_close_tasks", []))

    def _check_learned_repair(self, o):
        """A model that loaded with problems like ones the user repaired before: repair it the same way
        (automatically when that is switched on), or offer it in the assistant."""
        if not self.assistant.enabled or o.role == "cutter":
            return
        if o.mesh.is_watertight() and len(o.mesh.faces) > 150_000:
            return                        # a big closed model: nothing worth the time to analyse
        if not self.assistant.data.get("repair", {}).get("by_sig"):
            return                        # no repair learned yet: nothing to match (skips a full analysis)
        try:
            fp = self._repair_fingerprint(o)
        except Exception:
            return
        recipe = self.assistant.repair_recipe(fp)
        if recipe is None:
            return
        if self.assistant.auto_repair:
            self._push_undo()                 # Ctrl+Z gives back the model as it was in the file
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._apply_repair_recipe(o, recipe)
            except Exception:
                return
            finally:
                QApplication.restoreOverrideCursor()
            self.assistant.note_auto_repair()
            self._objects_changed()
            self.status.setText(tr("rr_auto_done").format(o.name, self._recipe_text(recipe)))
        else:
            self._repair_offers[o.uid] = recipe
            self._schedule_suggestions()

    def _on_auto_repair_toggled(self, on: bool):
        self.assistant.set_auto_repair(on)
        if on and self._repair_offers:          # models already offered: repair them now, as asked
            self._push_undo()
            for o in [x for x in self.objects if x.uid in self._repair_offers]:
                self._apply_repair_recipe(o, self._repair_offers.pop(o.uid))
                self.assistant.note_auto_repair()
            self._objects_changed()
        self._schedule_suggestions()

    def _learn_repair(self, fingerprints: dict, recipe: dict):
        learned = False
        for fp in fingerprints.values():
            learned |= self.assistant.learn_repair(fp, recipe)
        if learned:
            self.status.setText(self.status.text() + "  ·  " + tr("rr_learned"))

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

    def sync_cfs(self):
        """Read the filaments loaded in the printer's CFS (Creality, LAN mode) and offer them as the print's filaments."""
        from .. import network
        printers = [p for p in network.load_printers() if p.kind == "creality"]
        mine = [p for p in printers if p.printer_id == self.settings.printer_id] or printers
        if not mine:
            QMessageBox.information(self, tr("fil_sync_title"), tr("fil_sync_none"))
            return
        target = mine[0]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            slots = network.creality_cfs_slots(target.host)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, tr("fil_sync_title"),
                                tr("fil_sync_failed").format(target.name) + "\n\n" +
                                (network.explain(exc, "he" if i18n.LANG == "he" else "en") if isinstance(exc, network.NetworkError)
                                 else f"{type(exc).__name__}: {exc}"))
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        panel = self.settings.filament_panel
        panel.set_cfs_slots(slots)
        loaded = [s for s in slots if s.get("loaded") and s.get("slot") != "EXT"]
        if not loaded:
            QMessageBox.information(self, tr("fil_sync_title"), tr("fil_sync_empty").format(target.name))
            return
        listing = "\n".join(f"• {s['slot']}: {s.get('type', '')} {s.get('name', '')}  {s.get('colour', '')}".rstrip()
                            for s in loaded)
        ans = QMessageBox.question(self, tr("fil_sync_title"), tr("fil_sync_use").format(target.name, listing))
        if ans == QMessageBox.Yes:
            panel.use_printer_slots()
            self._objects_changed()
        self.status.setText(tr("fil_synced").format(len(loaded), target.name))

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
        if getattr(self, "_long_job", 0):
            self._added_in_job = getattr(self, "_added_in_job", []) + [obj.uid]
            return obj                    # opening many files: the plate is refreshed once, at the end
        self._refresh_objects(select=obj.uid)
        return obj

    def void_body_dialog(self):
        """Shapes › Insert / void body: a ready hole or pocket (die pin, magnet, implant analog, insert, vent)
        cut into the selected model; it is a cutter part, so it can be dragged to the exact spot."""
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout
        from .. import void_body
        target = self.selected()
        if target is not None and target.role == "cutter":
            target = None
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("void_title"))
        form = QFormLayout(dlg)
        preset = QComboBox()
        for key in void_body.PRESETS:
            preset.addItem(tr("void_" + key), key)
        preset.addItem(tr("void_custom"), "custom")
        shape = QComboBox()
        shape.addItem(tr("void_cylinder"), "cylinder")
        shape.addItem(tr("void_box"), "box")
        side = QComboBox()
        for k in void_body.SIDES:
            side.addItem(tr("void_side_" + k), k)

        def spin(lo, hi, step, suffix=" mm"):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(2)
            w.setSingleStep(step)
            w.setSuffix(suffix)
            return w
        dia, length, depth, clear = spin(0.3, 60, 0.1), spin(0.3, 120, 0.5), spin(0.2, 200, 0.5), spin(0.0, 2.0, 0.05)
        form.addRow(tr("void_preset"), preset)
        form.addRow(tr("void_shape"), shape)
        form.addRow(tr("void_diameter"), dia)
        form.addRow(tr("void_length"), length)
        form.addRow(tr("void_depth"), depth)
        form.addRow(tr("void_clearance"), clear)
        form.addRow(tr("void_side"), side)

        def fill(_i=None):
            key = preset.currentData()
            if key == "custom":
                return
            sp = void_body.PRESETS[key]
            shape.setCurrentIndex(shape.findData(sp.shape))
            dia.setValue(sp.diameter)
            length.setValue(sp.length)
            depth.setValue(sp.depth or 1.0)
            clear.setValue(sp.clearance)
            side.setCurrentIndex(side.findData(sp.side))
            sync()

        def sync(_i=None):
            length.setEnabled(shape.currentData() == "box")
            depth.setEnabled(side.currentData() != "through")
        preset.currentIndexChanged.connect(fill)
        shape.currentIndexChanged.connect(sync)
        side.currentIndexChanged.connect(sync)
        fill()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("void_add"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        spec = void_body.VoidSpec(shape.currentData(), dia.value(), length.value(), depth.value(), clear.value(),
                                  side.currentData())
        self.add_void_body(spec, target, preset.currentText())

    def add_void_body(self, spec, target=None, label: str = ""):
        from .. import void_body
        self._push_undo()
        cfg = self.settings.config()
        if target is not None:
            bounds = target.world_bounds()
        else:
            cx, cy = self._plate_center(cfg)
            bounds = ((cx, cy, 0.0), (cx, cy, spec.depth))
        height = float(bounds[1][2] - bounds[0][2])
        cutter = SceneObject.from_mesh(void_body.body(spec, height))
        cutter.name = (tr("void_name") + " · " + label).strip(" ·") if label else tr("void_name")
        void_body.place(cutter, bounds, spec)
        self.objects.append(cutter)
        self._refresh_objects(select=cutter.uid)
        self.status.setText(tr("void_added"))
        return cutter

    def duplicate_selected(self):
        group = self.selected_objects()
        if group:
            self._push_undo()
            dups = [o.duplicate() for o in group]
            self.objects += dups
            cfg = self.settings.config()
            self._arrange(cfg)
            self._refresh_objects(select=[d.uid for d in dups])

    def new_project(self):
        """File > New project (Ctrl+N): an empty plate - every model goes; the printer, the material and the
        settings stay. One Ctrl+Z brings the whole plate back."""
        if not self.objects:
            self.status.setText(tr("new_project_empty"))
            return
        self._push_undo()
        if self.preview_btn.isChecked():
            self.set_mode("prepare")
        for o in list(self.objects):
            self.viewer.remove_object(o.uid)
        self.objects = []
        self._sup_pending.clear()                      # no support builds for parts that are gone
        self._invalidate()
        self.__dict__.pop("_last_result", None)        # nothing of the old plate can be exported any more
        self._refresh_objects(select=None)
        self.status.setText(tr("new_project_done"))

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
        self._mark_raft_tabs()
        return not layout.auto_layout(self.objects, plate_area(cfg), options)

    def _mark_raft_tabs(self):
        """Tell the layout which parts get a "raft & label" crescent tab, so it keeps neighbours clear of it."""
        cfg = self.settings.config()
        on = cfg["technology"] == "sla" and cfg["support_enable"]
        for o in self.objects:
            o.__dict__["raft_tab"] = bool(on and not o.direct and o.role != "cutter"
                                          and o.config(cfg)["sla_raft_type"] == "raft_label")

    def learn_layout_now(self):
        """Assistant › Learn layout: take the arrangement now on the plate as the user's own layout."""
        from .. import layout
        parts = [o for o in self.objects if o.role != "cutter"]
        if len(parts) < 2:
            self.status.setText(tr("learn_layout_need"))
            return
        cfg = self.settings.config()
        if out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg), region=plate_region(cfg)):
            self.status.setText(tr("learn_layout_outside"))
            return
        tpl = layout.capture_template(parts, plate_area(cfg))
        if tpl is None:
            self.status.setText(tr("learn_layout_touching"))
            return
        if not self.assistant.teach_layout(tpl):
            self.status.setText(tr("learn_layout_off"))
            return
        self.status.setText(tr("learn_layout_done").format(tpl["count"], tpl["spacing"]))
        self.assistant_panel.set_learning(self.assistant.enabled, self.assistant.summary())

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
        self._mark_raft_tabs()
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
                "rotation": "free", "anchor": "center", "sort": "area"}
        self._migrate_layout_defaults()
        out = self.assistant.learned_layout(base)
        try:
            last = json.loads(self.store.value("layout_options", "{}"))
        except (TypeError, ValueError):
            last = {}
        for k in base:
            if k in last:
                out[k] = last[k]
        return out

    def _migrate_layout_defaults(self):
        """Once: the old defaults (6 mm gap, no turning) stored from earlier layouts are dropped, so the new
        defaults apply (2 mm, turning about Z) unless the user picks other values again."""
        if self.store.value("layout_defaults_v2", "") == "1":
            return
        try:
            last = json.loads(self.store.value("layout_options", "{}"))
        except (TypeError, ValueError):
            last = {}
        if isinstance(last, dict):
            if last.get("spacing") == 6.0:
                last.pop("spacing")
            if last.get("rotation") == "none":
                last.pop("rotation")
            self.store.setValue("layout_options", json.dumps(last))
        lay = self.assistant.data.get("layout", {}).get("choices", {})
        if "spacing_values" in lay:
            lay["spacing_values"] = [v for v in lay["spacing_values"] if v != 6.0]
        if "rotation" in lay:
            lay["rotation"].pop("none", None)
        self.assistant.save()
        self.store.setValue("layout_defaults_v2", "1")

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
        self._mark_raft_tabs()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if fill and self.selected():
                added = layout.fill_plate(self.objects, self.selected(), plate, options)
                self.objects += added
                failed = []
                self.status.setText(tr("fill_done").format(len(added)))
            elif options.scope == "all" and layout.apply_template(self.objects, plate, self.assistant.taught_layout()):
                failed = []                     # the user's own taught layout
                learned = "taught"
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
            self.status.setText(tr("layout_done_taught") if learned == "taught" else
                                tr("layout_done_learned") if learned else tr("layout_done"))

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
        self.viewer.setCursor(Qt.CrossCursor if name in ("face", "hole", "keepout", "label", "measure") else Qt.ArrowCursor)
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
        if name == "keepout":
            self.status.setText(tr("tool_keepout_hint").format(f"{self.KEEPOUT_DIAMETER:g}"))
        if name == "label":
            sel = self.selected()
            self.status.setText(tr("tool_label_hint").format(self._label_text(sel) if sel else "{name}"))
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
        sel = self.selected()
        if sel is not None:                       # the Rotation fields follow the drag
            self._syncing = True
            try:
                for i in range(3):
                    self.rot[i].setValue(float(sel.rotation[i]))
            finally:
                self._syncing = False
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

    KEEPOUT_DIAMETER = 6.0

    def _on_keepout_picked(self, uid, point):
        """Support-free zone tool: a click adds a zone there; a click on a zone removes it."""
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is None:
            return
        self._push_undo()
        lift = self._lift() if self._shows_supports(o) else 0.0
        zones = o.world_keepout(lift)[:len(o.keepout)]          # the clicked zones, not the labels' own
        if len(zones):
            d = np.linalg.norm(zones[:, :3] - np.asarray(point), axis=1)
            k = int(np.argmin(d))
            if d[k] < zones[k, 3]:
                o.keepout = np.delete(o.keepout, k, axis=0)
                self._objects_changed(refresh_list=False)
                self.status.setText(tr("keepout_removed"))
                return
        o.add_world_keepout(point, self.KEEPOUT_DIAMETER / 2, lift)
        if o.support_points is not None and len(o.support_points):   # drop edited supports already inside
            o.set_world_support_points(sla_supports.drop_keepout(o.world_support_points(lift), o.world_keepout(lift)),
                                       lift)
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("keepout_added").format(len(o.keepout)))

    # ---------------------------------------------------------- labels
    def _label_prefs(self) -> dict:
        v = self.store.value
        if v("label_template_v2", "") != "1":           # the short SOD code is the default now
            if str(v("label_template", "{name}")) == "{name}":
                self.store.setValue("label_template", "{code}")
            self.store.setValue("label_template_v2", "1")
        return {"template": str(v("label_template", "{code}")), "height": float(v("label_height", 4.0)),
                "depth": float(v("label_depth", 0.6)), "engrave": str(v("label_engrave", "true")) == "true",
                "max": int(v("label_max", 14))}

    def _label_text(self, o, number: int = 1) -> str:
        import datetime
        import os as _os
        from .. import dental_cad
        prefs = self._label_prefs()
        name = _os.path.splitext(o.name or "model")[0]
        code = dental_cad.case_code(name) or name           # no SOD number: the file name
        try:
            text = prefs["template"].format(name=name, code=code, n=number,
                                            date=datetime.date.today().strftime("%d.%m.%y"))
        except (KeyError, IndexError, ValueError):
            text = prefs["template"]
        return text.strip()[:max(1, prefs["max"])]

    def _on_label_picked(self, uid, point, normal):
        """Label tool: a click on a label selects it for editing (drag to move it); a click elsewhere on the
        model puts a new label there, facing out of the surface, and selects it."""
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is None:
            return
        prefs = self._label_prefs()
        lift = self._lift() if self._shows_supports(o) else 0.0
        hit, best = None, None
        for k, (p, _n, lab) in enumerate(o.world_labels(lift)):
            reach = max(2.0, 0.5 * len(lab["text"]) * 0.62 * lab["height"], lab["height"])
            d = float(np.linalg.norm(p - np.asarray(point)))
            if d < reach and (best is None or d < best):
                hit, best = k, d
        if hit is None:
            from .. import mesh_tools
            self._push_undo()                      # a new label (picking one to edit changes nothing)
            text = self._label_text(o, len(o.labels) + 1)
            size = prefs["height"]
            # as big as the surface there allows: a small round die gets small letters that still hug it
            tris = o.world_mesh().triangles + [0, 0, lift]
            near = tris[np.linalg.norm(tris.mean(axis=1) - np.asarray(point), axis=1) < 40.0]
            room = mesh_tools.label_room(near, point, normal, size)
            if room > 0:
                size = max(1.0, min(size, 0.9 * room / max(1, len(text)) / 0.62))
            o.add_world_label(text, point, normal, size, prefs["depth"], prefs["engrave"], 0.0, lift)
            hit = len(o.labels) - 1
            self._drop_label_supports(o)
            self._objects_changed(refresh_list=False)
        self._label_sel = (o.uid, hit)
        self._label_dragged = False
        self.edit_label(o, hit)

    def _on_label_wheel(self, steps: float, rotate: bool):
        """Label tool, a label selected: Ctrl + wheel resizes it, Alt + wheel turns it."""
        spins = getattr(self, "_label_spins", None)
        ed = getattr(self, "_label_editor", None)
        if spins is None or ed is None or not ed.isVisible():
            self.status.setText(tr("label_wheel_hint"))
            return
        height, angle = spins
        if rotate:
            angle.setValue(angle.value() + 5.0 * steps)
        else:
            height.setValue(round(height.value() * (1.1 ** steps), 1))

    def _drop_label_supports(self, o: SceneObject):
        """Supports already placed under a label (it was added, moved or grown) go away by themselves."""
        if o.support_points is None or len(o.support_points) == 0 or not o.labels:
            return
        lift = self._lift() if self._shows_supports(o) else 0.0
        kept = sla_supports.drop_keepout(o.world_support_points(lift), o.label_zones(lift))
        if len(kept) != len(o.support_points):
            o.set_world_support_points(kept, lift)

    def _on_label_dragged(self, uid, point, normal):
        """Dragging the selected label over its model's surface."""
        sel = getattr(self, "_label_sel", None)
        o = next((x for x in self.objects if x.uid == uid), None)
        if sel is None or o is None or sel[0] != uid or sel[1] >= len(o.labels):
            return
        lift = self._lift() if self._shows_supports(o) else 0.0
        if not getattr(self, "_label_dragged", False):
            self._push_undo()                      # the first move of a drag: Ctrl+Z puts the label back
        o.move_label(sel[1], point, normal, lift)
        self._label_dragged = True
        self.viewer.set_object_labels(o.uid, self._label_preview(o, self.settings.config(), fit=False))

    def _on_label_drag_finished(self):
        if getattr(self, "_label_dragged", False):
            self._label_dragged = False
            sel = getattr(self, "_label_sel", None)
            o = next((x for x in self.objects if sel and x.uid == sel[0]), None)
            if o is not None:
                self._drop_label_supports(o)
            self._objects_changed(refresh_list=False)

    def edit_label(self, o: SceneObject, k: int):
        """A small window to edit one label: text, size, depth, angle (turn buttons), mirrored, engraved /
        embossed, delete. Every change shows on the model at once; drag the label (label tool) to move it."""
        from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit

        def _wrap(a: float) -> float:
            return (a + 180.0) % 360.0 - 180.0
        old = getattr(self, "_label_editor", None)
        if old is not None:
            old.close()
        lab = o.labels[k]
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("label_edit_title"))
        dlg.setWindowFlag(Qt.Tool, True)
        form = QFormLayout(dlg)
        text = QLineEdit(lab["text"])
        height, depth = _spin(0.5, 30, 0.1, 1, " mm", 110), _spin(0.1, 5, 0.1, 2, " mm", 110)
        height.setValue(float(lab["height"]))
        depth.setValue(float(lab["depth"]))
        angle = _spin(-180, 180, 5, 0, "°", 110)
        angle.setValue(float(lab.get("angle", 0.0)))
        mode = QComboBox()
        mode.addItem(tr("label_engrave"), True)
        mode.addItem(tr("label_emboss"), False)
        mode.setCurrentIndex(0 if lab["engrave"] else 1)
        form.addRow(tr("label_text"), text)
        size_row = QHBoxLayout()
        smaller, bigger = QPushButton("-"), QPushButton("+")
        for btn, f in ((smaller, 1 / 1.15), (bigger, 1.15)):
            btn.setFixedWidth(40)
            f_ = btn.font()
            f_.setPointSizeF(f_.pointSizeF() * 1.4)
            f_.setBold(True)
            btn.setFont(f_)
            btn.clicked.connect(lambda _=False, f=f: height.setValue(round(height.value() * f, 1)))
        size_row.addWidget(height)
        size_row.addWidget(smaller)
        size_row.addWidget(bigger)
        size_row.addStretch(1)
        form.addRow(tr("label_size"), size_row)
        form.addRow(tr("text_depth"), depth)
        turn_row = QHBoxLayout()
        turn_row.addWidget(angle)
        for txt, tip, fn in (("↺ 90°", tr("label_turn_left"), lambda: angle.setValue(_wrap(angle.value() + 90))),
                             ("↻ 90°", tr("label_turn_right"), lambda: angle.setValue(_wrap(angle.value() - 90))),
                             ("180°", tr("label_turn_180"), lambda: angle.setValue(_wrap(angle.value() + 180))),
                             (tr("label_upright"), tr("label_upright"), lambda: angle.setValue(0.0))):
            btn = QPushButton(txt)                  # plain text: arrow glyphs like ⟲ are missing in Windows fonts
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            turn_row.addWidget(btn)
        turn_row.addStretch(1)
        form.addRow(tr("label_angle"), turn_row)
        mirror = QCheckBox(tr("label_mirror"))
        mirror.setChecked(bool(lab.get("mirror", False)))
        form.addRow("", mirror)
        form.addRow(tr("text_mode"), mode)
        hint = QLabel(tr("label_drag_hint"))
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        form.addRow(hint)
        row = QHBoxLayout()
        delete = QPushButton(theme.icon("trash", theme.DANGER), tr("delete"))
        done = QPushButton(tr("close"))
        done.setDefault(True)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(done)
        form.addRow(row)
        timer = QTimer(dlg)
        timer.setSingleShot(True)
        timer.setInterval(400)
        timer.timeout.connect(lambda: (self._drop_label_supports(o), self._objects_changed(refresh_list=False)))

        def apply(*_):
            if k >= len(o.labels):
                return
            o.labels[k] = dict(o.labels[k], text=text.text().strip() or o.labels[k]["text"], height=height.value(),
                               depth=depth.value(), angle=angle.value(), engrave=bool(mode.currentData()), width=0.0,
                               mirror=mirror.isChecked())
            self.viewer.set_object_labels(o.uid, self._label_preview(o, self.settings.config()))
            timer.start()                        # the slice inputs follow a moment later

        def remove():
            if k < len(o.labels):
                self._push_undo()
                del o.labels[k]
                self._label_sel = None
                self._objects_changed(refresh_list=False)
                self.status.setText(tr("label_removed"))
            dlg.close()

        for w in (height, depth, angle):
            w.valueChanged.connect(apply)
        text.textChanged.connect(apply)
        mode.currentIndexChanged.connect(apply)
        mirror.toggled.connect(apply)
        delete.clicked.connect(remove)
        done.clicked.connect(dlg.close)
        dlg.finished.connect(lambda _r: self._objects_changed(refresh_list=False))
        self._label_editor = dlg
        self._label_spins = (height, angle)        # Ctrl / Alt + mouse wheel over the view change these
        dlg.show()

    def auto_label(self, targets=None) -> int:
        """Put a label on each model by itself: on the largest flat side of its lower part. Crowns and bridges
        are left out: a label on the tooth itself would spoil it (their code goes on the raft)."""
        from .. import dental_cad, mesh_tools
        targets = targets if targets is not None else (self.selected_objects() or
                                                       [o for o in self.objects if o.role != "cutter"])
        prefs = self._label_prefs()
        self._push_undo()
        done, missed, teeth = 0, 0, 0
        for i, o in enumerate(targets):
            if o.role == "cutter":
                continue
            if dental_cad.kind_of(o.name, o.mesh) == "crown":
                teeth += 1                # never on a crown / bridge itself: its code goes on its raft
                continue
            world = o.world_mesh()
            spot = mesh_tools.label_spot(world)
            if spot is None or spot[3] < prefs["height"] * 0.6 or spot[2] < prefs["height"]:
                missed += 1
                continue
            point, normal, width, height = spot
            h = min(prefs["height"], height * 0.8)
            o.add_world_label(self._label_text(o, i + 1), point, normal, h, prefs["depth"], prefs["engrave"],
                              width * 0.85)
            self._drop_label_supports(o)
            done += 1
        self._objects_changed(refresh_list=False)
        msg = tr("label_added").format(done)
        if missed:
            msg += " · " + tr("label_no_spot").format(missed)
        if teeth:
            msg += " · " + tr("label_teeth_skipped").format(teeth)
        self.status.setText(msg)
        return done

    def clear_labels(self):
        targets = [o for o in (self.selected_objects() or self.objects) if o.labels]
        if targets:
            self._push_undo()
            for o in targets:
                o.labels = []
            self._objects_changed(refresh_list=False)

    def label_dialog(self):
        """Labels: text (file name, case number, date...), size, engraved or embossed; label the selected models."""
        from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox
        prefs = self._label_prefs()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("label_title"))
        form = QFormLayout(dlg)
        text = QLineEdit(prefs["template"] or "{code}")
        text.setToolTip(tr("label_text_tip"))
        tip = QLabel(tr("label_text_tip"))
        tip.setObjectName("Faint")
        height, depth = _spin(1, 30, 0.5, 1, " mm", 110), _spin(0.2, 5, 0.1, 2, " mm", 110)
        height.setValue(prefs["height"])
        depth.setValue(prefs["depth"])
        most = QSpinBox()
        most.setRange(1, 60)
        most.setValue(prefs["max"])
        mode = QComboBox()
        mode.addItem(tr("label_engrave"), True)
        mode.addItem(tr("label_emboss"), False)
        mode.setCurrentIndex(0 if prefs["engrave"] else 1)
        form.addRow(tr("label_text"), text)
        form.addRow(tip)
        form.addRow(tr("text_height"), height)
        form.addRow(tr("text_depth"), depth)
        form.addRow(tr("label_max"), most)
        form.addRow(tr("text_mode"), mode)
        note = QLabel(tr("label_auto_note"))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        auto = buttons.addButton(tr("label_auto"), QDialogButtonBox.AcceptRole)
        auto.setDefault(True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        st = self.store.setValue
        st("label_template", text.text().strip() or "{code}")
        st("label_height", height.value())
        st("label_depth", depth.value())
        st("label_engrave", "true" if mode.currentData() else "false")
        st("label_max", most.value())
        self.auto_label()

    def _label_preview(self, o: SceneObject, cfg, fit: bool = True):
        """(embossed, engraved) label text in the object's own coordinates; engraved text as a dark inlay at
        the surface, so it reads as a groove (``fit`` off: flat, instant - while dragging)."""
        if not o.labels:
            return None
        lift = self._lift(cfg) if self._shows_supports(o) else 0.0
        # in the object's own coordinates the result does not change when the part only moves: kept until its
        # mesh, labels, turn / scale or the support lift change (every edit rebuilt all labels: ~1 s a plate)
        key = (id(o.mesh), repr(o.labels), o.matrix()[:3, :3].tobytes(), round(lift, 6), fit)
        cache = self.__dict__.setdefault("_label_cache", {})
        hit = cache.get(o.uid)
        if hit is not None and hit[0] == key and hit[2] is o.mesh:
            return hit[1]
        up, cut = o.world_label_meshes(lift, fit=fit, look=True)
        inv = np.linalg.inv(self._display_matrix(o))
        out = (up.transform(inv) if up is not None else None, cut.transform(inv) if cut is not None else None)
        cache[o.uid] = (key, out, o.mesh)
        return out

    def clear_keepout(self):
        targets = [o for o in self._support_targets() if len(o.keepout)]
        if not targets:
            return
        self._push_undo()
        for o in targets:
            o.keepout = np.zeros((0, 4))
        self._objects_changed(refresh_list=False)

    def _hole_preview(self, o: SceneObject, cfg):
        """Red cylinders marking manual drain holes, in the object's own coordinates."""
        if len(o.drain_holes) == 0:
            return None
        lift = self._lift(cfg) if self._shows_supports(o) else 0.0
        tubes = sla.hole_meshes([o.world_drain_holes(lift)], cfg)
        return Mesh.merge(tubes).transform(np.linalg.inv(self._display_matrix(o)))

    def _keepout_preview(self, o: SceneObject, cfg):
        """Blue see-through balls marking the support-free zones, in the object's own coordinates."""
        if len(o.keepout) == 0:
            return None
        from ..primitives import sphere
        lift = self._lift(cfg) if self._shows_supports(o) else 0.0
        balls = [sphere(float(z[3]), 10, 20).translated(z[:3] - [0, 0, z[3]]) for z in o.world_keepout(lift)]
        return Mesh.merge(balls).transform(np.linalg.inv(self._display_matrix(o)))

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

    def _toggle_defects(self, on: bool):
        self.store.setValue("show_defects", "true" if on else "false")
        self.viewer.set_show_defects(on)

    def repair_selected(self):
        from .. import mesh_tools
        targets = self._support_targets()
        if not targets:
            return
        self._push_undo()
        totals = {"duplicates": 0, "flipped": 0, "holes_filled": 0}
        prints = {}
        if self.assistant.enabled:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                prints = {o.uid: self._repair_fingerprint(o) for o in targets}
            finally:
                QApplication.restoreOverrideCursor()
        for o in targets:
            self._repair_offers.pop(o.uid, None)
            fixed, rep = mesh_tools.repair(o.mesh)
            o.mesh = fixed
            o.regions = []                        # faces changed: shaded regions no longer apply
            for k in totals:
                totals[k] += rep[k]
        self._objects_changed()
        self.status.setText(tr("repair_done").format(totals["flipped"], totals["holes_filled"], totals["duplicates"]))
        self._learn_repair(prints, {"mode": "basic"})

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
        recipe = {"mode": "advanced", "weld": weld.value(), "shells": shells.value() / 100.0,
                  "nonmanifold": nonman.isChecked(), "holes": holes.isChecked()}
        prints = {}
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.assistant.enabled:
                prints = {o.uid: self._repair_fingerprint(o) for o in targets}
            for o in targets:
                self._repair_offers.pop(o.uid, None)
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
        self._learn_repair(prints, recipe)
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
        if getattr(self, "_undo_held", False):
            return                          # part of one bigger step that already saved the plate
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
                     + ("  ⚙" if o.settings else "")
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

    # ================================================================ one-click plate
    def one_click_plate(self):
        """Recognise each part's type (from its name), arrange the plate and generate the supports in one go,
        then show what came out with a fix next to every warning, before slicing. Crowns and bridges whose
        opening faces down are turned opening up; every other part keeps its orientation. The plate's support
        settings and each part's own type decide the supports."""
        if not [o for o in self.objects if o.role != "cutter"]:
            self.status.setText(tr("no_models"))
            return
        self._long(True)
        self._push_undo()                        # one Ctrl+Z gives back the plate as it was before
        self._undo_held = True
        try:
            self._select_uid(None)
            self._open_up([o for o in self.objects if o.role != "cutter"])
            self.arrange_objects()
            if self.settings.config()["technology"] == "sla":
                self.generate_supports()
                self.wait_supports()
        finally:
            self._undo_held = False
            self._long(False)
        self._plate_summary()

    def plate_facts(self) -> dict:
        """What the one-click summary shows: types, placement, supports, warnings, estimate."""
        from .. import collision, dental_cad
        from ..layout import footprint
        from ..scene import plate_rect as _rect
        cfg = self.settings.config()
        models = [o for o in self.objects if o.role != "cutter"]
        counts, unknown, recognised = {}, [], 0
        for o in models:
            own = o.settings.get("sla_model_type")
            t = own if own not in (None, "auto", "plate") else (
                dental_cad.model_type_of(o.name, o.mesh) if cfg["sla_model_type"] == "auto" else
                ("" if cfg["sla_model_type"] == "plate" else cfg["sla_model_type"]))
            if t:
                counts[t] = counts.get(t, 0) + 1
                recognised += 1
            else:
                unknown.append((o.uid, o.name or "model"))
        outside = [o.name or "model" for o in out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"],
                                                             rect=plate_rect(cfg), region=plate_region(cfg))]
        pairs = []
        if 1 < len(models) <= 80:
            pairs = collision.colliding_pairs({o.uid: self._column_map(o) for o in models}, clearance=0.5)
        x0, y0, x1, y1 = _rect(cfg)
        try:
            used = sum(footprint(o).area for o in models) / max(1e-6, (x1 - x0) * (y1 - y0)) * 100.0
        except Exception:
            used = 0.0
        sla_mode = cfg["technology"] == "sla" and cfg["support_enable"]
        supported = [o for o in models if not o.direct]
        points = sum(len(o.support_points) for o in supported if o.support_points is not None) if sla_mode else 0
        unsupported = [(o.uid, o.name or "model") for o in supported
                       if sla_mode and (o.support_points is None or len(o.support_points) == 0)]
        codes = 0
        if sla_mode and cfg["sla_raft_type"] != "none":
            codes = sum(1 for o in supported if self._raft_label_text(o, o.config(cfg)))
        est = None
        try:
            e = self.plate_estimate()
            ml = sum(r.model_ml + r.support_ml for r in e.rows)
            from .. import gcode as _g
            est = {"time": _g.format_duration(e.time_s), "ml": f"{ml:.0f}",
                   "material": self.settings.material_id and profiles.load("materials").get(
                       self.settings.material_id, {}).get("name", "") or ""}
        except Exception:
            est = None
        printer = profiles.load("printers").get(self.settings.printer_id, {}).get("name", "")
        return {"types": {"counts": counts, "unknown": unknown, "recognised": recognised},
                "placement": {"outside": outside, "collisions": len(pairs), "count": len(models),
                              "used_pct": f"{used:.0f}"},
                "supports": {"points": points, "models": len(supported) if sla_mode else 0,
                             "direct": len(models) - len(supported) if sla_mode else 0, "codes": codes,
                             "unsupported": unsupported,
                             "fit_down": [(o.uid, o.name or "model") for o in supported if o.uid in self._fit_down]},
                "estimate": est, "printer": printer}

    def _plate_summary(self):
        from .plate_summary import PlateSummary
        facts = self.plate_facts()
        dlg = PlateSummary(facts, {}, self._can_send(), self)
        dlg.exec()
        choice = dlg.choice
        if choice == "slice":
            self.slice()
        elif choice == "send":
            self._send_after_slice = True
            self.slice()
            if self.task is None or not self.task.isRunning():
                self._send_after_slice = False            # the slice did not start (a warning stopped it)
        elif isinstance(choice, tuple) and choice[0] == "fix":
            self._plate_fix(choice[1], choice[2], choice[3])
            self._plate_summary()

    def _plate_fix(self, action, uid, value=None):
        o = next((x for x in self.objects if x.uid == uid), None)
        self._long(True)
        try:
            if action == "arrange":
                self.arrange_objects()
            elif o is not None and action == "type":
                self._push_undo()
                o.settings = {**o.settings, "sla_model_type": value}
                self._regenerate_one(o)
            elif o is not None and action == "turn":
                self._push_undo()                         # Ctrl+Z gives the orientation back
                o.apply_world_rotation(self._axis_rotation(0, 180.0))
                self._objects_changed(refresh_list=False)
                self._regenerate_one(o)
            elif o is not None and action == "supports":
                self._push_undo()                         # Ctrl+Z takes the new supports away again
                o.direct = False
                self._regenerate_one(o)
        finally:
            self._long(False)

    def _regenerate_one(self, o):
        self._select_uid(o.uid)
        self._undo_held = True               # the fix saved the plate once: one Ctrl+Z undoes all of it
        try:
            self.generate_supports()
        finally:
            self._undo_held = False
        self._select_uid(None)
        self.wait_supports()

    def _can_send(self) -> bool:
        if profiles.load("printers").get(self.settings.printer_id, {}).get("vendor") == "Formlabs":
            return True
        try:
            from .. import network
            return bool(network.load_printers())
        except Exception:
            return False

    # ================================================================ updates
    def check_updates(self, manual: bool = False):
        """Ask GitHub (in the background) whether a newer build is published; if so the status bar shows
        'Update available'. Nothing about the user or their files is sent."""
        from .. import updater
        current = updater.build_commit()
        if not current:
            if manual:
                self.status.setText(tr("upd_source"))
            return
        if getattr(self, "_upd_task", None) is not None:
            return

        def work(progress, cancelled):
            return updater.latest()

        def done(info):
            self._upd_task = None
            if updater.newer(info, current):
                self._update_info = info
                self.update_btn.setToolTip(tr("upd_tip").format(info.get("date", "")))
                self.update_btn.show()
                self.status.setText(tr("upd_found").format(info.get("date", "")))
            elif manual:
                self.update_btn.hide()
                self.status.setText(tr("upd_latest"))

        def failed(msg):
            self._upd_task = None
            if manual:
                self.status.setText(tr("upd_failed").format(msg))

        task = Task(work, self)
        self._upd_task = task
        task.succeeded.connect(done)
        task.failed.connect(failed)
        task.start()

    def install_update(self):
        """Download the newer installer (in the background), then - after asking - install it silently: the
        program closes and opens again when the installation is done."""
        info = getattr(self, "_update_info", None)
        if not info:
            return
        ans = QMessageBox.question(self, tr("upd_title"), tr("upd_ask").format(info.get("date", "")),
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        import tempfile
        from .. import updater
        dest = os.path.join(tempfile.gettempdir(), "UniSlicer-update")
        self.update_btn.setEnabled(False)

        def work(progress, cancelled):
            return updater.download(info, dest, lambda f: progress("download", f), cancelled)

        task = Task(work, self)
        task.progress.connect(lambda _s, f: self.status.setText(tr("upd_downloading").format(int(f * 100))))
        task.succeeded.connect(self._run_installer)

        def failed(msg):
            self.update_btn.setEnabled(True)
            self.status.setText(tr("upd_failed").format(msg))
        task.failed.connect(failed)
        self._upd_dl = task
        task.start()

    def _run_installer(self, path: str):
        self.update_btn.setEnabled(True)
        if sys.platform != "win32":
            self.status.setText(tr("upd_saved").format(path))
            return
        import subprocess
        try:
            self._save_state()
        except Exception:
            pass
        # Inno Setup: quiet install, closes this program, then opens it again (the installer's [Run] entry)
        try:
            subprocess.Popen([path, "/SILENT", "/SP-", "/NOCANCEL", "/CLOSEAPPLICATIONS", "/NORESTART"],
                             close_fds=True)
        except OSError as exc:                    # blocked or missing: say so, keep working
            self.status.setText(tr("upd_failed").format(exc))
            return
        self.close()

    def set_mouse_scheme(self, key: str):
        """Mouse controls: CHITUBOX (right drag turns the view, wheel drag pans, wheel zooms at the cursor,
        left click / drag selects and moves, left drag on empty space frames a selection) or the classic ones."""
        key = key if key in ("chitubox", "classic") else "chitubox"
        self.store.setValue("mouse_scheme", key)
        self.viewer.mouse_scheme = key
        for k, act in getattr(self, "_mouse_acts", {}).items():
            act.setChecked(k == key)
        if getattr(self, "_nav_label", None) is not None:
            self._nav_label.setText(tr("hint_nav_" + key))

    def _breathe(self):
        """During a long job on many models (opening files, generating supports): let the window repaint and
        stay 'responding' between models (about once a second), without taking clicks or keys until the
        job is done."""
        import time as _time
        if getattr(self, "_long_job", 0) and not getattr(self, "_breathing", False):
            now = _time.monotonic()
            if now - getattr(self, "_last_breath", 0.0) < 1.0:
                return
            self._last_breath = now
            self._breathing = True
            try:
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            finally:
                self._breathing = False

    def _after_job(self, timer, fn):
        """A delayed check that fires while a long job is still running waits for the job to end (its result
        would be stale at once and it only slows the job down)."""
        if getattr(self, "_long_job", 0):
            if timer is not None:
                timer.start()
            return
        fn()

    def _long(self, on: bool):
        self._long_job = getattr(self, "_long_job", 0) + (1 if on else -1)
        if on:
            import time as _time
            self._last_breath = _time.monotonic()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            return
        QApplication.restoreOverrideCursor()
        added = getattr(self, "_added_in_job", [])
        if not self._long_job and added:          # the models opened during the job: one refresh for all
            self._added_in_job = []
            self._refresh_objects(select=[u for u in added if any(o.uid == u for o in self.objects)][-1:] or None)

    PER_PART_CACHES = ("_support_cache", "_geometry_cache", "_column_maps", "_label_cache", "_slice_label_cache")

    def _forget_removed(self, uids: set):
        """Parts that left the plate (deleted, replaced, undone): their cached previews go, and the meshes
        kept only for undo drop their worked-out data. Before, a day's plates stayed in memory: ~350 MB more
        after every plate, 9 GB after 25."""
        for name in self.PER_PART_CACHES:
            cache = self.__dict__.get(name)
            if cache:
                for uid in [u for u in cache if u not in uids]:
                    del cache[uid]
        on_plate = {id(o.mesh) for o in self.objects}
        for snap in list(getattr(self, "_undo", [])) + list(getattr(self, "_redo", [])):
            for o, st in snap:
                for m in (o.mesh, st[15] if len(st) > 15 else None):
                    if m is not None and id(m) not in on_plate and len(m.__dict__) > 5:
                        m.drop_caches()

    @staticmethod
    def _engine_text(w: str, cfg) -> str:
        """A slicer warning that carries numbers, in the program's language."""
        if w.startswith("The printer's screen resolution"):
            px, py = cfg["bed_x"] / max(1, int(cfg["resolution_x"])), cfg["bed_y"] / max(1, int(cfg["resolution_y"]))
            return tr("res_mismatch").format(int(cfg["resolution_x"]), int(cfg["resolution_y"]), f"{cfg['bed_x']:g}",
                                             f"{cfg['bed_y']:g}", f"{px * 1000:.0f}", f"{py * 1000:.0f}")
        return w

    def _objects_changed(self, refresh_list: bool = True):
        cfg = self.settings.config()
        outside = {o.uid: o.name for o in out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg), region=plate_region(cfg))}
        uids = {o.uid for o in self.objects}
        for uid in list(self.viewer.objects):
            if uid not in uids:
                self.viewer.remove_object(uid)
        self._forget_removed(uids)
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
            self.viewer.set_object_supports(o.uid, self._support_preview_async(o, cfg))
            self.viewer.set_object_holes(o.uid, self._hole_preview(o, cfg))
            self.viewer.set_object_keepout(o.uid, self._keepout_preview(o, cfg))
            self.viewer.set_object_labels(o.uid, self._label_preview(o, cfg))
            self._breathe()
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
        from ..layout import footprint                 # the model's outline from above, cached per shape
        try:
            hull = footprint(o)
        except Exception:
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
                n_open, n_bad = self.viewer.defect_counts(obj.uid)
                if n_open or n_bad:
                    info += "\n" + tr("defects_info").format(n_open, n_bad)
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
        # drag the bar anywhere by its handle; it then opens where you left it (double-click: back by the model)
        self.edit_grip = BarGrip(bar, self._edit_bar_moved, self._edit_bar_reset)
        lay.addWidget(self.edit_grip)
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
        sup_menu.addAction(theme.icon("shield"), tr("tool_keepout") + "\tK", lambda: self.set_tool("keepout"))
        sup_menu.addAction(tr("keepout_clear"), self.clear_keepout)
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

    def _update_slice_info(self):
        label = getattr(self, "slice_info", None)
        if label is None:
            return
        t = self.stat_time.value.text()
        if not t or t == "—":
            label.hide()
            return
        if "h" in t:                               # hours: the seconds are noise
            t = " ".join(p for p in t.split() if not p.endswith("s"))
        m = self.stat_material.value.text()
        label.setText(f"⏱  {t}" + (f"   ·   {m}" if m and m != "—" else ""))
        label.show()

    def _edit_bar_place(self):
        """Where the user dragged the edit bar to (None: it opens next to the clicked model)."""
        try:
            x, y = (int(v) for v in str(self.store.value("edit_bar_pos", "") or "").split(","))
            return x, y
        except (TypeError, ValueError):
            return None

    def _edit_bar_moved(self, pos):
        self.store.setValue("edit_bar_pos", f"{pos.x()},{pos.y()}")

    def _edit_bar_reset(self):
        self.store.remove("edit_bar_pos")
        if self.edit_bar.isVisible():
            self._show_edit_bar()

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
        placed = self._edit_bar_place()
        if placed is not None:                # the user moved it: keep it there (inside the view)
            x = min(max(0, placed[0]), max(0, holder.width() - self.edit_bar.width()))
            y = min(max(0, placed[1]), max(0, holder.height() - self.edit_bar.height()))
            self.edit_bar.move(x, y)
        else:
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
        QTimer.singleShot(0, self, self._show_edit_bar)

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
                self._fill_align_menu(menu.addMenu(theme.icon("align"), tr("align_menu")))
            menu.addAction(theme.icon("settings"), tr("model_settings") + "…", self.model_settings_dialog)
            menu.addAction(theme.icon("grid"), tr("texture_title") + "…", self.texture_dialog)
            lock = menu.addAction(theme.icon("lock"), tr("lock_position"))
            lock.setCheckable(True)
            lock.setChecked(all(o.locked for o in group))
            lock.toggled.connect(self._set_locked)
            if self.viewer.technology != "sla":
                self._fill_filament_menu(menu.addMenu(theme.icon("layers"), tr("fil_menu")))
            menu.addAction(theme.icon("drop"), tr("shade_menu") + "…", self.dental_shades_dialog)
            dental = menu.addMenu(theme.icon("sparkle"), tr("dental_indication"))
            for key in ("model", "splint", "crown", "gingiva", "guide"):
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
                menu.addAction(theme.icon("shield"), tr("tool_keepout") + "\tK", lambda: self.set_tool("keepout"))
                if any(len(o.keepout) for o in group):
                    menu.addAction(tr("keepout_clear"), self.clear_keepout)
                menu.addAction(theme.icon("code"), tr("label_title") + "…", self.label_dialog)
                if len(group) == 1 and group[0].labels:
                    for k, lab in enumerate(group[0].labels[:8]):
                        menu.addAction(theme.icon("code"), f"{tr('label_edit_title')}: {lab['text']}",
                                       lambda o=group[0], k=k: self.edit_label(o, k))
                if any(o.labels for o in group):
                    menu.addAction(tr("label_clear"), self.clear_labels)
                menu.addAction(theme.icon("grid"), tr("raft_menu") + "…", self.raft_menu)
                if any(o.direct for o in group):     # on: say plainly how to turn it off
                    flat = menu.addAction(theme.icon("flat"), tr("direct_off"))
                    flat.setToolTip(tr("direct_off_tip"))
                    flat.triggered.connect(lambda: self._set_direct(False))
                else:
                    flat = menu.addAction(theme.icon("flat"), tr("direct_on_plate"))
                    flat.setToolTip(tr("direct_on_plate_tip"))
                    flat.triggered.connect(lambda: self._set_direct(True))
                fill = menu.addAction(theme.icon("drop"), tr("fill_cavity"))
                fill.setToolTip(tr("fill_cavity_tip"))
                fill.setCheckable(True)
                fill.setChecked(all(o.fill_cavity for o in group))
                fill.toggled.connect(self._set_fill_cavity)
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

    # ------------------------------------------------------------ 3D surface texture (GrabCAD Print Pro)
    def texture_dialog(self):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel
        from .. import texture
        group = [o for o in self.selected_objects() if o.role != "cutter"]
        if not group:
            self.status.setText(tr("select_first"))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("texture_title"))
        form = QFormLayout(dlg)
        kind = QComboBox()
        for k in texture.PATTERNS:
            kind.addItem(tr("texture_" + k), k)
        area = QComboBox()
        for k in texture.AREAS:
            area.addItem(tr("texture_area_" + k), k)
        spacing = QDoubleSpinBox()
        spacing.setRange(0.3, 20.0)
        spacing.setSingleStep(0.1)
        spacing.setSuffix(" mm")
        spacing.setValue(float(self.store.value("texture_spacing", 1.5)))
        depth = QDoubleSpinBox()
        depth.setRange(-3.0, 3.0)
        depth.setSingleStep(0.05)
        depth.setSuffix(" mm")
        depth.setValue(float(self.store.value("texture_depth", 0.3)))
        last = str(self.store.value("texture_kind", "knurl"))
        kind.setCurrentIndex(max(0, kind.findData(last)))
        area.setCurrentIndex(max(0, area.findData(str(self.store.value("texture_area", "sides")))))
        form.addRow(tr("texture_pattern"), kind)
        form.addRow(tr("texture_spacing"), spacing)
        form.addRow(tr("texture_depth"), depth)
        form.addRow(tr("texture_area"), area)
        note = QLabel(tr("texture_note"))
        note.setWordWrap(True)
        note.setObjectName("Faint")
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("texture_apply"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        for k, val in (("texture_kind", kind.currentData()), ("texture_area", area.currentData()),
                       ("texture_spacing", spacing.value()), ("texture_depth", depth.value())):
            self.store.setValue(k, val)
        self.apply_texture(group, kind.currentData(), spacing.value(), depth.value(), area.currentData())

    def apply_texture(self, group, kind: str, spacing: float, depth: float, area: str):
        """Press the texture into each model, in plate orientation (so 'sides' / 'top' and the clean base
        follow how the model stands now). Undo puts the smooth mesh back."""
        from .. import texture
        self._push_undo()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for o in group:
                lin = o.matrix()
                inv = np.linalg.inv(lin)
                placed = o.mesh.transform(lin)
                done = texture.apply(placed, kind, spacing, depth, area)
                o.mesh = done.transform(inv)
                o.mesh.name = o.name
                if o.support_points is not None:
                    self._drop_supports(o)
                for cache in ("_support_cache", "_geometry_cache", "_column_maps"):
                    self.__dict__.get(cache, {}).pop(o.uid, None)
                self.viewer.remove_object(o.uid)
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_objects(select=[o.uid for o in group])
        self.status.setText(tr("texture_done").format(len(group), sum(len(o.mesh.faces) for o in group)))

    # ------------------------------------------------------------ per-model settings (GrabCAD model-level)
    MODEL_KEYS_SLA = ("sla_model_type", "sla_avoid_fit", "sla_support_preset", "sla_support_spacing", "support_angle", "sla_support_type",
                      "sla_tip_diameter", "sla_pillar_diameter", "sla_density", "hollow_enable", "hollow_wall",
                      "hollow_mode", "hollow_infill", "hollow_infill_spacing", "hollow_infill_thickness", "drain_holes")

    def model_settings_dialog(self):
        """Right-click > Model settings: supports and hollowing / infill for the selected models only, over the
        plate's settings (as GrabCAD Print's model-level settings). Ticked rows are the model's own."""
        from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QScrollArea,
                                       QVBoxLayout, QWidget)
        from ..settings import SCHEMA_BY_KEY
        from .settings_panel import SettingRow
        group = [o for o in self.selected_objects() if o.role != "cutter"]
        if not group:
            self.status.setText(tr("select_first"))
            return
        if self.settings.technology != "sla":
            QMessageBox.information(self, tr("model_settings"), tr("model_settings_resin_only"))
            return
        cfg = self.settings.config()
        first = group[0]
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("model_settings") + " · " + (first.name if len(group) == 1 else tr("group_n").format(len(group))))
        dlg.resize(620, 640)
        v = QVBoxLayout(dlg)
        hint = QLabel(tr("model_settings_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Faint")
        v.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box = QWidget()
        grid = QGridLayout(box)
        rows = {}
        own_cfg = first.config(cfg)
        for r, key in enumerate(self.MODEL_KEYS_SLA):
            setting = SCHEMA_BY_KEY[key]
            check = QCheckBox()
            check.setToolTip(tr("model_settings_own"))
            check.setChecked(key in first.settings)
            row = SettingRow(setting, own_cfg[key], key in first.settings, own_cfg)
            row.changed.connect(lambda _k, _v, c=check: c.setChecked(True))
            row.reset.connect(lambda k, c=check, rw=None: c.setChecked(False))
            grid.addWidget(check, r, 0)
            grid.addWidget(row, r, 1)
            rows[key] = (check, row)
        grid.setRowStretch(len(rows), 1)
        scroll.setWidget(box)
        v.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        buttons.button(QDialogButtonBox.Reset).setText(tr("model_settings_clear"))
        buttons.button(QDialogButtonBox.Reset).clicked.connect(lambda: [c.setChecked(False) for c, _ in rows.values()])
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        values = {key: row.editor_value() for key, (check, row) in rows.items() if check.isChecked()}
        self.set_model_settings(group, values)

    def set_model_settings(self, group, values: dict):
        self._push_undo()
        support_keys = {k for k in values if not k.startswith(("hollow", "drain"))}
        for o in group:
            changed_support = any(o.settings.get(k) != values.get(k) for k in support_keys | {k for k in o.settings
                                                                                              if not k.startswith(("hollow", "drain"))})
            o.settings = dict(values)
            if changed_support and o.support_points is not None:
                self._drop_supports(o)            # supports follow the model's new support settings
        self._invalidate()
        self._refresh_objects(select=[o.uid for o in group])
        self.status.setText(tr("model_settings_done").format(len(group), len(values)) if values else
                            tr("model_settings_cleared").format(len(group)))

    # ------------------------------------------------------------ align / lock / fill cavity
    ALIGN_MODES = ("left", "center_x", "right", "front", "center_y", "back", "dist_x", "dist_y")

    def _fill_align_menu(self, menu):
        for k, mode in enumerate(self.ALIGN_MODES):
            if k in (3, 6):
                menu.addSeparator()
            menu.addAction(tr("align_" + mode), lambda m=mode: self.align_selected(m))

    def align_selected(self, mode: str):
        """Align the selected models (or all) by their footprints; locked models are the reference."""
        from .. import layout
        group = [o for o in (self.selected_objects() or list(self.objects)) if o.role != "cutter"]
        if len(group) < 2:
            self.status.setText(tr("align_need_two"))
            return
        self._push_undo()
        layout.align(group, mode)
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("align_done").format(len(group)))

    def _set_locked(self, on: bool):
        group = self.selected_objects()
        if not group:
            return
        self._push_undo()
        for o in group:
            o.locked = bool(on)
        self._refresh_objects(select=[o.uid for o in group])
        self.status.setText(tr("locked_on" if on else "locked_off").format(len(group)))

    def _set_fill_cavity(self, on: bool):
        group = self.selected_objects()
        if not group:
            return
        self._push_undo()
        for o in group:
            o.fill_cavity = bool(on)
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("fill_cavity_on" if on else "fill_cavity_off").format(len(group)))

    DENTAL = {
        # indication: (tilt about X in degrees, print straight on the plate)
        "model": (0.0, True),
        "splint": (45.0, False),
        "crown": (45.0, False),
        "gingiva": (45.0, False),
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
                kinds[o.uid] = kind if kind != "unknown" else dental_cad.indication_from_shape(o.mesh)
        new = [o for o in self.objects if o.uid not in before]
        if not new:
            return
        if auto:
            for kind in ("model", "die", "crown", "gingiva", "splint", "guide", "denture"):
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
        group = [o for o in self.selected_objects() if o.role != "cutter"]
        if kind not in self.DENTAL or not group:
            return
        self._push_undo()
        self._apply_indication(group, kind)

    def _apply_indication(self, group, kind: str, enable_supports: bool = True):
        if kind not in self.DENTAL:
            return
        tilt, direct = self.DENTAL[kind]
        for o in group:
            o.lay_flat()
            if tilt:
                o.apply_world_rotation(self._axis_rotation(0, tilt))
            o.direct = direct
            o.support_points = None
        if not direct and enable_supports:
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

    def _on_hover_object(self, uid):
        """Mouse resting on a model: its name and size (W × D × H mm) in a see-through label."""
        o = next((x for x in self.objects if x.uid == uid), None) if uid is not None else None
        if o is None:
            self.viewer.show_hover(None)
            return
        w, d, h = o.world_size()
        self.viewer.show_hover(f"{o.name or 'model'}\n{w:.2f} × {d:.2f} × {h:.2f} mm")

    def _on_drag(self, uid, dx, dy):
        group = self.selected_objects()
        if not any(o.uid == uid for o in group):
            group = [o for o in self.objects if o.uid == uid]
        if group and all(o.locked for o in group):
            self.status.setText(tr("locked_hint"))
            return
        group = [o for o in group if not o.locked]       # locked parts stay in place
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
        QTimer.singleShot(0, self, self._show_edit_bar)
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

    @staticmethod
    def _raft_label_text(o: SceneObject, cfg) -> str:
        """What goes on a model's raft: on teeth (crowns / bridges) the case code from the SOD number,
        e.g. 46671-044-P; otherwise the model's name when "Model name on the raft" is on."""
        from .. import dental_cad
        if cfg["sla_raft_type"] == "raft_label":         # this raft is there to carry the code: always one
            return dental_cad.case_code(o.name) or os.path.splitext(o.name or "")[0]
        if cfg["sla_raft_label_teeth"] and dental_cad.kind_of(o.name, o.mesh) == "crown":
            code = dental_cad.case_code(o.name)
            if code:
                return code
        return o.name if cfg["sla_raft_label"] else ""

    def _in_background(self, fn):
        """Run ``fn()`` on a background thread and return its result; meanwhile the window keeps repainting
        and stays 'responding' (clicks and keys wait until it is done)."""
        if not self.async_supports:
            return fn()
        from .worker import Task
        box = {}

        def work(progress, cancelled):
            try:
                box["result"] = fn()
            except Exception as exc:          # handed back to the caller, on the window's thread
                box["error"] = exc
                raise

        task = Task(work, self)
        loop = QEventLoop()
        task.finished.connect(loop.quit)
        task.start()
        if not task.isFinished():
            loop.exec(QEventLoop.ExcludeUserInputEvents)
        task.wait()
        task.deleteLater()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _support_key(self, o: SceneObject, cfg):
        keys = sorted(k for k in cfg.values if k.startswith("sla_") and not k.startswith("sla_layer"))
        # the turn about Z is left out: the structure is kept in the part's own coordinates and turns with it
        # (a flat plate makes a turned structure as good as a new one), so turning a part - by hand or by
        # the auto layout - rebuilds nothing
        # the height above the plate and the mesh itself are in it: a part lifted with Move to... got its
        # old structure floating under it, and a repaired / closed mesh kept the one built on the open one
        # (rounded, and -0.0 made 0.0: a turn by exactly 90 degrees left -1e-17 here on Windows, whose bytes
        # differ from 0.0, and the supports were rebuilt)
        return (o.uid, o.support_points.tobytes(), (np.round(o.turn_free_matrix(), 5) + 0.0).tobytes(),
                tuple(cfg[k] for k in keys), tuple(sorted(o.settings.items())), o.name,
                round(float(o.z), 4), id(o.mesh), id(o.mesh.vertices))

    def _support_preview_async(self, o: SceneObject, cfg) -> Mesh | None:
        """The support structure to draw now: the finished one, or - while a changed one is built in the
        background - the one drawn so far; the view gets the new one when it is ready, so the window never
        waits for a support build (a big jaw model took ~1 s)."""
        if not self._shows_supports(o) or len(o.support_points) == 0:
            return None
        key = self._support_key(o, cfg)
        hit = self._support_cache.get(o.uid)
        if hit is not None and hit[0] == key:
            return hit[1]
        twin = self._twin_supports(o, key)
        if twin is not None:
            return twin[1]
        if not self.async_supports:
            return self._support_preview(o, cfg)
        self._queue_support_build(o, cfg, key)
        return hit[1] if hit is not None else None

    def _twin_supports(self, o: SceneObject, key):
        """A copy's supports from the part it was copied from: a duplicate (or a copy made by Multiply,
        Fill the plate or undo) has the same mesh, points, turn and settings, so its support structure -
        kept in the part's own coordinates - is the same; it is taken over instead of built again (48
        duplicated crowns once rebuilt 24 support structures in the background, and the window lagged)."""
        tail = key[1:]
        meshes = {x.uid: x.mesh for x in self.objects}
        for uid, entry in self._support_cache.items():
            if uid != o.uid and entry[0][1:] == tail and meshes.get(uid) is o.mesh:
                entry = (key, entry[1], dict(entry[2]) if entry[2] else entry[2])
                self._support_cache[o.uid] = entry
                self._sup_pending.pop(o.uid, None)
                return entry
        return None

    def _queue_support_build(self, o: SceneObject, cfg, key):
        running = getattr(self, "_sup_running", None)
        if running == (o.uid, key) or self._sup_pending.get(o.uid, (None,))[0] == key:
            return                                   # already on its way
        ocfg = o.config(cfg)
        lift = self._lift(ocfg)
        world, geo = self._support_geometry(o, lift)
        label = self._raft_label_text(o, ocfg)
        if label:
            from ..mesh_tools import text_polygons
            try:
                text_polygons(label[:24], 2.0)           # the code's letters, made here on the main thread
            except Exception:
                pass
        self._sup_pending[o.uid] = (key, world, ocfg, o.world_support_points(lift), dict(geo), label,
                                    self._display_matrix(o), geo, o.plate_frame())
        self._start_support_build()

    def _start_support_build(self):
        if getattr(self, "_sup_task", None) is not None or not self._sup_pending:
            return
        while self._sup_pending:
            uid = next(iter(self._sup_pending))
            entry = self._sup_pending.pop(uid)
            o = next((x for x in self.objects if x.uid == uid), None)
            twin = self._twin_supports(o, entry[0]) if o is not None else None
            if twin is None:
                break
            if self._shows_supports(o) and len(o.support_points):     # a copy built already: show it
                self.viewer.set_object_supports(uid, twin[1])
                self._update_support_info()
            entry = None
        if entry is None:
            return
        key, world, ocfg, pts, geo, label, disp, shared, frame = entry

        def work(progress, cancelled):
            owners = {}
            mesh, _ = sla_supports.build_supports(world, ocfg, pts, owners=owners, cache=geo, label=label,
                                                  label_frame=frame)
            local = mesh.transform(np.linalg.inv(disp)) if len(mesh.faces) else None
            return uid, key, local, owners, geo

        from .worker import Task
        task = Task(work, self)
        self._sup_task, self._sup_running = task, (uid, key)
        task.succeeded.connect(lambda res, shared=shared: self._support_built(res, shared))
        task.failed.connect(lambda _msg: self._support_build_done())
        task.start()

    def _support_build_done(self):
        task, self._sup_task, self._sup_running = getattr(self, "_sup_task", None), None, None
        if task is not None:
            task.wait()
            task.deleteLater()
        QTimer.singleShot(0, self, self._start_support_build)

    def _support_built(self, res, shared: dict):
        uid, key, local, owners, geo = res
        for k, v in geo.items():                         # keep the indexes it built for the next build
            shared.setdefault(k, v)
        self._support_cache[uid] = (key, local, owners)
        o = next((x for x in self.objects if x.uid == uid), None)
        if o is not None and self._shows_supports(o) and len(o.support_points):
            cfg = self.settings.config()
            if self._support_key(o, cfg) == key:
                self.viewer.set_object_supports(uid, local)
                self._update_support_info()
        self._support_build_done()

    def _built_supports(self, o: SceneObject, cfg):
        """The model's support structure as far as it is built: the finished one, else - while the
        background builds it - none (the estimate and hover use what exists; they never wait)."""
        if not self.async_supports:
            return self._support_preview(o, cfg)
        if not self._shows_supports(o) or len(o.support_points) == 0:
            return None
        hit = self._support_cache.get(o.uid)
        return hit[1] if hit is not None and hit[0] == self._support_key(o, cfg) else None

    def wait_supports(self, timeout: float = 120.0):
        """Wait until the work started in the background - support builds, collision maps - has finished,
        and run the collision check it was waiting for (tests, before saving)."""
        import time as _time
        end = _time.monotonic() + timeout
        while (getattr(self, "_sup_task", None) is not None or self._sup_pending
               or getattr(self, "_colmap_task", None) is not None) and _time.monotonic() < end:
            QApplication.processEvents(QEventLoop.AllEvents, 50)
            _time.sleep(0.005)
        if self._collision_timer.isActive():
            self._collision_timer.stop()
            self._check_collisions()

    def _support_preview(self, o: SceneObject, cfg) -> Mesh | None:
        """Support structure in the object's own coordinates (cached; moving the object keeps it valid)."""
        if not self._shows_supports(o) or len(o.support_points) == 0:
            return None
        key = self._support_key(o, cfg)
        hit = self._support_cache.get(o.uid)
        if hit is not None and hit[0] == key:
            return hit[1]
        twin = self._twin_supports(o, key)
        if twin is not None:
            return twin[1]
        cfg = o.config(cfg)                    # the model's own support settings, if any
        lift = self._lift(cfg)
        disp = self._display_matrix(o)
        world, geo = self._support_geometry(o, lift)
        owners = {}
        mesh, _ = sla_supports.build_supports(world, cfg, o.world_support_points(lift), owners=owners, cache=geo,
                                              label=self._raft_label_text(o, cfg), label_frame=o.plate_frame())
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
        self._long(True)
        try:
            self._generate_supports()
        finally:
            self._long(False)

    def _generate_supports(self):
        self._push_undo()
        self._ensure_supports_enabled()
        cfg = self.settings.config()
        learned_plate = self.assistant.learned_config(cfg)   # density and angle learned from earlier edits
        lift = self._lift(cfg)
        chosen = self.selected_objects()
        skipped = 0
        upside = []                               # parts whose fitting surface faces the plate
        jobs = []
        for o in self._support_targets():
            if o.role == "cutter":
                continue
            if o.direct:
                if not chosen:               # the whole plate: parts printed on the plate stay as they are
                    skipped += 1
                    continue
                o.direct = False             # supports asked for this very part: it leaves the plate
            jobs.append((o, o.mesh, o.world_matrix().copy()))

        def work():
            # each part's settings (its type: the horseshoe test reads its outline) and support points, off
            # the window's thread (a jaw model takes ~0.5 s); the window waits without input meanwhile
            out = []
            for o, mesh, matrix in jobs:
                ocfg = o.config(learned_plate)
                world = mesh.transform(matrix).translated([0, 0, lift])
                report = {}
                out.append((sla_supports.auto_support_points(world, ocfg, report), report, ocfg))
            return out

        results = self._in_background(work) if jobs else []
        for (o, _mesh, _matrix), (pts, report, ocfg) in zip(jobs, results):
            o.set_world_support_points(sla_supports.drop_keepout(pts, o.world_keepout(lift)), lift)
            if report.get("points", 0) >= 4 and report["fit_dropped"] > 0.4 * report["points"]:
                upside.append(o.name or "model")
                self._fit_down.add(o.uid)
            else:
                self._fit_down.discard(o.uid)
            o.__dict__["support_session"] = {"auto": len(o.support_points), "added": [], "removed": 0,
                                             "threshold": ocfg["support_angle"]}
        self._objects_changed(refresh_list=False)
        msg = tr("sup_generated_hint") + (" " + tr("sup_skipped_direct").format(skipped) if skipped else "")
        if upside:
            msg = "⚠ " + tr("fit_faces_plate").format(", ".join(upside[:3]) + ("…" if len(upside) > 3 else "")) + " " + msg
        self.status.setText(msg)

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
                row.setVisible(not ((key in ("sla_raft_bar_spacing", "sla_raft_bar_width") and kind not in ("grid", "hexagon"))
                                    or (key == "sla_raft_group_reach" and kind != "mini")
                                    or (key not in ("sla_raft_type", "sla_elevation") and kind == "none")))

        def changed(key, value):
            self._ensure_supports_enabled()
            self.settings.set_value(key, value)
            fill()
            self._objects_changed(refresh_list=False)
        cfg = self.settings.config()
        for key in self.RAFT_KEYS:
            row = SettingRow(SCHEMA_BY_KEY[key], cfg[key], key in self.settings.overrides, cfg, staged=True)
            row.changed.connect(changed)
            row.applied.connect(self.apply_setting)
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
            geo["caster"] = sla_supports.caster_for(world)
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
            local = self._built_supports(o, cfg)
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
        self.export_btn.setText(_two_lines(tr("export_sla") if tech == "sla" else tr("export_gcode")))
        self.export_btn.setMinimumWidth(self.export_btn.sizeHint().width())   # never clip "Export print file"
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

    # FDM settings that only show in the toolpaths: their Apply slices
    FDM_SLICE_CATEGORIES = ("quality", "walls", "infill", "support", "adhesion", "filaments")

    def setting_action(self, key: str) -> str:
        """What a setting's Apply carries out on the models: 'supports' (generate the supports with it),
        'raft' (rebuild the raft under the supports), 'arrange' (lay the plate out again), 'slice' (its
        result only shows in the sliced layers) or 'save' (kept for the next slice / export)."""
        setting = SCHEMA_BY_KEY.get(key)
        if setting is None:
            return "save"
        cat = setting.category
        if key in ("arrange_spacing", "arrange_margin"):
            return "arrange"
        if self.settings.technology == "sla":
            if cat == "hollow" or key in ("sla_internal_supports", "sla_internal_spacing"):
                return "slice"
            if cat == "support":
                return "supports"
            if cat == "raft":
                return "raft"
            return "save"
        return "slice" if cat in self.FDM_SLICE_CATEGORIES else "save"

    def apply_setting(self, key: str):
        """A setting's own "Apply": that one setting was committed (and saved); now carry it out on the
        models, e.g. supports settings generate the supports, raft settings rebuild the raft, layout
        spacing re-arranges the plate, hollowing / infill slices so the honeycomb shows."""
        if self._settings_timer.isActive():
            self._settings_timer.stop()
        self._on_settings_changed()                  # stores the value (kept after a restart too)
        setting = SCHEMA_BY_KEY.get(key)
        name = setting_label(setting) if setting is not None else key
        action = self.setting_action(key)
        if action != "save" and not self.objects:
            self.status.setText(tr("applied_no_models").format(name))
            return
        if action == "supports":
            self.generate_supports()
            self.status.setText(tr("applied_supports").format(name))
        elif action == "raft":
            if not any(o.support_points is not None and len(o.support_points) for o in self.objects):
                self.generate_supports()             # a raft stands under supports
            else:
                self._ensure_supports_enabled()
                self._objects_changed(refresh_list=False)
            self._update_raft_view(self.settings.config())
            self.status.setText(tr("applied_raft").format(name))
        elif action == "arrange":
            self.arrange_objects()
            self.status.setText(tr("applied_arrange").format(name))
        elif action == "slice":
            if self.task is not None and self.task.isRunning():
                self.task.cancel()
                self.task.wait(5000)
            self.slice()
        else:
            self.status.setText(tr("applied_saved").format(name))

    def _save_state(self):
        s = self.store
        s.setValue("printer", self.settings.printer_id)
        s.setValue("material", self.settings.material_id or "")
        s.setValue("quality", self.settings.quality_id or "")
        s.setValue("overrides", json.dumps(self.settings.overrides))
        s.setValue("advanced", "true" if self.settings.show_advanced else "false")
        sections = {cat: sec.toggle.isChecked() for cat, sec in getattr(self.settings, "_sections", {}).items()}
        if sections and not self.settings.filter_text:
            s.setValue("expanded_sections", json.dumps({**self.settings._expanded, **sections}))
        s.sync()

    # ================================================================ slicing
    def _invalidate(self):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
            self.__dict__.pop("_send_after_slice", None)      # that slice will not finish: nothing to send
        self._schedule_preslice()
        had = self.result is not None
        if self.result is not None:
            self._last_result = self.result      # still exportable: "export" works in any state
        self.result = None
        self.export_btn.setEnabled(self._exportable() is not None)
        self.send_btn.setEnabled(self._exportable() is not None)
        self.viewer.set_toolpaths(None, 0.4)
        self.viewer.set_extra_meshes([])
        for tile in (self.stat_time, self.stat_material, self.stat_weight, self.stat_cost):
            tile.set("—")
        self.warn_label.hide()
        if not getattr(self, "_keep_checks", False):    # a Fix keeps the list: only the fixed line goes
            self.warn_box.hide()
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
            ans = QMessageBox.warning(self, tr("collision_title"), getattr(self, "_collision_text", self.collision_label.text()) + "\n\n" + tr("collision_slice_q"),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        cfg = self.settings.config()
        self._learn_from_scene(cfg)
        meshes, kw = self._sla_inputs(models)
        cutters = kw["cutters"]
        name = self.objects[0].name or "print"
        pre = self._pre_task if self._pre_task is not None and self._pre_task.isRunning() else None

        def work(progress, cancelled):
            if cfg["technology"] == "sla":
                if pre is not None:          # the background pre-slice of this same plate: let it finish (cached)
                    progress("slice", 0.0)
                    pre.wait()
                job = sla.prepare(meshes, cfg, progress, cancelled, **kw)
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

    @staticmethod
    def _prewarm_pool():
        try:
            from .. import parallel
            parallel.prewarm()
        except Exception:
            pass

    def _exportable(self):
        """What Export / Send / Queue use: the current slice, else the last one (the plate changed since)."""
        if self.result is not None:
            return self.result
        return getattr(self, "_last_result", None)

    def _bg_export(self, fn, on_done, busy_text):
        """Run an export on its own thread: it is never cancelled by a change on the plate or by slicing, and
        the program stays usable meanwhile (progress in the status line)."""
        stale = self.result is None
        task = Task(fn, self)
        task.progress.connect(lambda _stage, f: self.status.setText(f"{busy_text} {int(f * 100)}%"))

        def done(p):
            on_done(p)
            if stale:
                self.status.setText(self.status.text() + "  ·  " + tr("export_last_slice"))
        task.succeeded.connect(done)
        task.failed.connect(lambda msg: msg and QMessageBox.critical(self, tr("export_error"), msg))
        if not hasattr(self, "_export_tasks"):
            self._export_tasks = []
        self._export_tasks.append(task)
        task.finished.connect(lambda t=task: t in self._export_tasks and self._export_tasks.remove(t))
        self.status.setText(busy_text)
        task.start()
        return task

    def _sla_inputs(self, models):
        """The model meshes and keyword arguments ``sla.prepare`` gets for these models."""
        cfg = self.settings.config()
        meshes = [o.world_mesh() for o in models]
        kw = dict(support_points=[o.world_support_points(0.0) for o in models], direct=[o.direct for o in models],
                  keepout=[o.world_keepout(0.0) for o in models],
                  labels=[self._slice_labels(o) if o.labels else None for o in models],
                  raft_labels=[self._raft_label_text(o, o.config(cfg)) for o in models],
                  holes=[o.world_drain_holes(0.0) for o in models],
                  cutters=[o.world_mesh() for o in self.objects if o.role == "cutter"],
                  materials=[dental_shades.face_shades(len(o.mesh.faces), o.material, o.regions) if o.regions
                             else o.material for o in models],
                  filled=[o.fill_cavity for o in models], model_settings=[o.effective_settings(cfg) or None for o in models],
                  prebuilt_supports=[self._ready_world_supports(o, cfg) for o in models],
                  raft_label_frames=[o.plate_frame() for o in models])
        return meshes, kw

    def _ready_world_supports(self, o: SceneObject, cfg):
        """The support structure already built for the view, in bed coordinates, when the slicer would build
        exactly the same one (same points, settings and label; no shrink compensation, a closed mesh and no
        support-free zones) - so slicing and export do not build every model's supports a second time
        (48 crowns were 48 more builds in the background after every change). None = let the slicer build."""
        if o.direct or o.support_points is None or not len(o.support_points) or not self._shows_supports(o):
            return None
        ocfg = o.config(cfg)
        if ocfg["shrink_xy"] or ocfg["shrink_z"] or len(o.keepout) or not o.mesh.is_watertight():
            return None
        hit = self._support_cache.get(o.uid)
        if hit is None or hit[1] is None or hit[0] != self._support_key(o, cfg):
            return None
        return hit[1].transform(self._display_matrix(o))

    def _slice_labels(self, o: SceneObject):
        """The part's label meshes on the plate, for slicing. Built in the part's own coordinates and kept
        until its mesh, labels, turn or scale change, then placed where the part is now (the pre-slice after
        every edit rebuilt all of them on the window's thread: ~0.15 s a part)."""
        key = (id(o.mesh), repr(o.labels), o.matrix()[:3, :3].tobytes())
        cache = self.__dict__.setdefault("_slice_label_cache", {})
        m = o.world_matrix()
        hit = cache.get(o.uid)
        if hit is None or hit[0] != key or hit[2] is not o.mesh:
            up, cut = o.world_label_meshes(0.0)
            inv = np.linalg.inv(m)
            hit = (key, (None if up is None else up.transform(inv), None if cut is None else cut.transform(inv)), o.mesh)
            cache[o.uid] = hit
        up, cut = hit[1]
        return (None if up is None else up.transform(m), None if cut is None else cut.transform(m))

    # ---------------------------------------------------------------- background pre-slice
    PRESLICE_DELAY_MS = 2000

    def _schedule_preslice(self):
        """Resin: once the plate has been left alone for a moment, slice and hollow it in the background. The
        results only fill the slicer's cache, so pressing Slice afterwards takes a fraction of the time."""
        if getattr(self, "_pre_task", None) is not None and self._pre_task.isRunning():
            self._pre_task.cancel()                  # the plate changed: that work is for an old plate
        timer = getattr(self, "_preslice_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._after_job(timer, self._preslice))
            self._preslice_timer = timer
        if self.store.value("preslice", "true") == "true":
            timer.start(self.PRESLICE_DELAY_MS)

    def _preslice(self):
        if self.result is not None or (self.task is not None and self.task.isRunning()) \
                or (self._pre_task is not None and self._pre_task.isRunning()):
            return
        if self._closing_holes():                    # the model is about to change: try again afterwards
            self._schedule_preslice()
            return
        try:
            cfg = self.settings.config()
            if cfg["technology"] != "sla" or cfg["sla_process"] == "polyjet":
                return
            models = [o for o in self.objects if o.role != "cutter"]
            if not models or out_of_bounds(self.objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg),
                                           region=plate_region(cfg)):
                return
            meshes, kw = self._sla_inputs(models)
        except Exception:
            return

        def work(progress, cancelled):
            sla.prepare(meshes, cfg, None, cancelled, **kw)
            return None

        self._pre_task = Task(work, self)
        if not getattr(self, "_preslice_atexit", False):
            # the program may end while it runs (a Qt thread still running at exit aborts the process)
            import atexit
            import weakref
            ref = weakref.ref(self)
            atexit.register(lambda: ref() is not None and ref()._stop_preslice())
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._stop_preslice)
            self._preslice_atexit = True
        self._pre_task.start(QThread.LowPriority)

    def _stop_preslice(self):
        self._sup_pending.clear()
        for t in (getattr(self, "_sup_task", None), getattr(self, "_colmap_task", None)):
            if t is not None and t.isRunning():
                t.wait(10000)
        timer = getattr(self, "_preslice_timer", None)
        try:
            if timer is not None:
                timer.stop()
            t = self._pre_task
            if t is not None and t.isRunning():
                t.cancel()
                t.wait(15000)
            for t in list(getattr(self, "_export_tasks", [])):
                if t.isRunning():
                    t.wait(60000)                    # an export is never cut short
            for t in list(getattr(self, "_close_tasks", [])):
                if t.isRunning():
                    t.cancel()
                    t.wait(15000)
        except RuntimeError:                         # Qt objects already deleted at exit
            pass

    def _confirm_supports(self, cfg, models) -> bool:
        """Resin: warn before slicing parts that would print with no supports at all."""
        if cfg["technology"] != "sla" or cfg["sla_process"] == "polyjet":
            return True
        if cfg["sla_cavity_pillars"]:          # on the plate with pillars in the cavity: chosen on purpose
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
        self.export_btn.setEnabled(self._exportable() is not None)
        self.send_btn.setEnabled(self._exportable() is not None)
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
        self.slice_btn.setText(" " + tr("slice"))
        self.slice_btn.setIcon(theme.icon("layers", "#ffffff", 18))
        self.export_btn.setEnabled(self._exportable() is not None)
        self.send_btn.setEnabled(self._exportable() is not None)

    def _on_failed(self, message):
        # a slice that failed or was cancelled sends nothing, and leaves no fix waiting for the next one
        self.__dict__.pop("_send_after_slice", None)
        self.__dict__.pop("_cup_fix_pending", None)
        if message:
            QMessageBox.critical(self, tr("slice_error"), message)
            self.status.setText(tr("slice_error"))
        else:
            self.status.setText(tr("ready"))

    def _on_sliced(self, result):
        self.result = result
        if self.__dict__.pop("_send_after_slice", False):
            QTimer.singleShot(300, self, self.send_to_printer)       # One-click plate > Slice & send
        pending = self.__dict__.pop("_cup_fix_pending", 0)
        if pending and result[0] == "sla":
            job, cfg = result[1], result[2]
            left = [a for _i, a in job.cups if a >= self.CUP_BLOCK_AREA]
            rounds = self.__dict__.get("_cup_fix_round", 1)
            owners = self._cup_owners(job, cfg) if left else []
            if left and owners and rounds < 3:
                # a vent can uncover the next pocket up: vent that one too and slice again (at most 3 rounds)
                for o, v, lift in owners:
                    for row in sla.vent_hole_rows(v, cfg, lift):
                        o.add_world_drain_hole(row[:3], row[3:], 0.0)
                self._cup_fix_pending = len(owners)
                self._cup_fix_round = rounds + 1
                self._objects_changed(refresh_list=False)
                QTimer.singleShot(400, self, self.slice)
            else:
                self.__dict__.pop("_cup_fix_round", None)
                QTimer.singleShot(0, self, lambda: self.status.setText(
                    tr("cup_fixed") if not left else tr("cup_still").format(len(left))))
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
            if getattr(job, "pillars", None) is not None:      # pillars inside the cavity / under overhangs
                extras.append((job.pillars, theme.SUPPORT_COLOR))
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
                split = tr("resin_split").format(f"{job.model_ml:.1f}", f"{max(0.0, job.volume_ml - job.model_ml):.1f}",
                                                 f"{job.raft_ml:.1f}", len(job.support_points))
                self.sup_info.setText(split)
                if getattr(self, "slice_info", None) is not None:
                    self.slice_info.setToolTip(split)
            self.stat_weight.set(f"{job.weight_g:.1f} g")
            self.stat_cost.set(f"₪{job.cost:.2f}")
            warnings = [tr(ENGINE_TEXT[w]) if w in ENGINE_TEXT else self._engine_text(w, cfg) for w in job.warnings]
            warnings = self._print_checks(job) + warnings
            if job.saved_ml > 0:
                warnings.insert(0, tr("saved_resin").format(job.saved_ml))
            if job.island_pillars:
                warnings.insert(0, tr("pillars_added").format(job.island_pillars))
            self._islands = {}
            for i, poly in job.islands:
                self._islands.setdefault(i, []).append(poly)
            self.island_btn.setVisible(bool(self._islands))
            self.island_fix_btn.setVisible(bool(self._islands))
        self._keep_checks = False
        self.result_cfg = cfg
        if warnings:
            import html

            def line(w):
                color = theme.SUCCESS if w.startswith("✓") else theme.TEXT_DIM if w.startswith("▸") else theme.WARNING
                text = html.escape(w if w.startswith(("✓", "▸")) else "⚠ " + w)
                return f'<div style="color:{color}">{text}</div>'
            self.warn_label.setTextFormat(Qt.RichText)
            self.warn_label.setText("".join(line(w) for w in warnings))
            self._show_check_rows(warnings, result)
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

    def _play_layers(self, on: bool):
        self.play_btn.setIcon(theme.icon("pause" if on else "play", theme.ACCENT, 18))
        if not on:
            self._play_timer.stop()
            return
        top = self.layer_slider.maximum()
        if top <= 0 or self.result is None:
            self.play_btn.setChecked(False)
            return
        if self.layer_slider.value() >= top:
            self.layer_slider.setValue(0)
        # about 8 seconds for the whole print, whatever its layer count
        self._play_stride = max(1, int(round(top / 200)))
        self._play_timer.start()

    def _play_step(self):
        top = self.layer_slider.maximum()
        v = min(top, self.layer_slider.value() + getattr(self, "_play_stride", 1))
        self.layer_slider.setValue(v)
        if v >= top:
            self.play_btn.setChecked(False)

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
            self._schedule_layer_cap(value, z)
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

    def _schedule_layer_cap(self, index: int, z: float):
        """Draw the layer itself at the cut in 3D, a moment after the slider stops (dragging stays smooth)."""
        self._cap_want = (index, z)
        timer = getattr(self, "_cap_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._show_layer_cap)
            self._cap_timer = timer
        timer.start()

    def _show_layer_cap(self):
        res = self.result
        want = getattr(self, "_cap_want", None)
        if res is None or res[0] != "sla" or want is None:
            self.viewer.set_layer_cap(None)
            return
        index, z = want
        job = res[1]
        if index >= len(job.layers) or index >= self.layer_slider.maximum():
            self.viewer.set_layer_cap(None)          # the top: the whole model shows, nothing to cut
            return
        self.viewer.set_layer_cap(sla.layer_triangles(job.layers[index], z + 0.02))

    def _check_fix(self, text: str, result):
        """The action that fixes one print-check line, or None."""
        cfg = result[2] if result and result[0] == "sla" else None
        if text.startswith(("✓", "▸")):
            return None
        if cfg is not None:
            job = result[1]
            if job.islands and text == tr("check_islands").format(len(job.islands), self._layers_text(job.island_layers)):
                return self.fix_islands
            if job.cups and text.startswith(tr("check_cups").split("{")[0]):
                return self.fix_cups_now
            if job.thin_layers and text.startswith(tr("check_thin").split("{")[0]):
                return lambda: self._fix_setting("sla_thicken_thin", True)
            if text == tr("note_no_drain"):
                return lambda: self._fix_setting("drain_holes", 2)
            if text == tr("note_too_thin"):
                return lambda: (self.settings.set_value("hollow_infill", "none"), self._fix_setting("hollow_enable", False))
        if "watertight" in text:
            return self._repair_all
        return None

    @staticmethod
    def _layers_text(idx) -> str:
        idx = sorted(set(idx))
        return ", ".join(str(i + 1) for i in idx[:6]) + ("…" if len(idx) > 6 else "")

    def _show_check_rows(self, warnings, result):
        self._check_texts = list(warnings)           # what Claude sees as the print check
        while self.warn_rows.count():
            item = self.warn_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.check_fix_buttons = []
        self._check_fixes = []
        for w in warnings:
            color = theme.SUCCESS if w.startswith("✓") else theme.TEXT_DIM if w.startswith("▸") else theme.WARNING
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            lab = QLabel(w if w.startswith(("✓", "▸")) else "⚠ " + w)
            lab.setWordWrap(True)
            lab.setStyleSheet(f"color: {color};")
            h.addWidget(lab, 1)
            fix = self._check_fix(w, result)
            if fix is not None:
                btn = QPushButton(tr("check_fix"))
                btn.setObjectName("RowApply")
                btn.setProperty("pending", "true")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip(tr("check_fix_tip"))
                btn.clicked.connect(lambda _c=False, f=fix, r=row: self._run_fix(f, r))
                h.addWidget(btn, 0, Qt.AlignTop)
                self.check_fix_buttons.append((w, btn))
                self._check_fixes.append((fix, row))
            self.warn_rows.addWidget(row)
        self.warn_box.show()
        self.cup_fix_btn.setVisible(len(self._check_fixes) > 1)

    def _run_fix(self, fix, row):
        """One Fix button: the check list stays, that line goes, the others stay until the new slice."""
        self._keep_checks = True
        self._fix_result = self.result
        row.hide()
        self._check_fixes = [(f, r) for f, r in self._check_fixes if r is not row]
        self.cup_fix_btn.setVisible(len(self._check_fixes) > 1)
        try:
            fix()
        finally:
            self._after_fix()

    def fix_all_checks(self):
        """'Fix all': every problem of the print check, then one slice to check them."""
        fixes = list(getattr(self, "_check_fixes", []))
        if not fixes:
            return
        self._keep_checks = True
        self._fix_result = self.result
        self._batch_fix = True
        try:
            for fix, row in fixes:
                row.hide()
                try:
                    fix()
                except Exception as exc:            # one fix failing must not stop the others
                    self.status.setText(str(exc))
        finally:
            self._batch_fix = False
        self._check_fixes = []
        self.cup_fix_btn.hide()
        self._after_fix()

    def _after_fix(self):
        """Slice again once (not once per fix) so the check shows what is left."""
        if getattr(self, "_batch_fix", False):
            return
        self.status.setText(tr("check_fixing"))
        if not getattr(self, "_fix_slice_pending", False):
            self._fix_slice_pending = True

            def go():
                self._fix_slice_pending = False
                self.slice()
            QTimer.singleShot(400, self, go)

    def fix_islands(self):
        """Print check > Fix next to the islands. With supports: a support at every island. Without supports
        (a model straight on the plate), or where no support can reach: a thin pillar under each island."""
        cfg = self.settings.config()
        added = 0
        if cfg["support_enable"]:
            before = sum(len(o.support_points) for o in self.objects if o.support_points is not None)
            self.support_all_islands()
            added = sum(len(o.support_points) for o in self.objects if o.support_points is not None) - before
        # pillars are the safety net: they only go under islands the supports did not reach, so one slice
        # after the fix clears them all (no second round of Fix)
        if not cfg["sla_island_pillars"]:
            self.settings.set_value("sla_island_pillars", True)
        if added <= 0:
            self.status.setText(tr("pillars_on"))

    def _fix_setting(self, key, value):
        """A fix that is a setting: change it (the caller slices again to check)."""
        self.settings.set_value(key, value)

    def fix_cups_now(self):
        """Print check > Fix next to the suction cups: vent holes, sliced again (more rounds if needed)."""
        res = self.result or getattr(self, "_fix_result", None)
        if res is None or res[0] != "sla":
            return
        job, cfg = res[1], res[2]
        owners = self._cup_owners(job, cfg)
        if not owners:
            self._cup_no_vent_dialog(job)
            return
        self._push_undo()
        for o, v, lift in owners:
            for row in sla.vent_hole_rows(v, cfg, lift):
                o.add_world_drain_hole(row[:3], row[3:], 0.0)
        self._objects_changed(refresh_list=False)
        self.status.setText(tr("cup_vents_added").format(len(owners)))
        self._cup_fix_pending = len(owners)

    def _repair_all(self):
        self.select_all()
        self.repair_selected()

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
        elif getattr(job, "cups_checked", False):
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
            best = None
            for o in models:
                lift = self._lift(cfg) if cfg["support_enable"] and not o.direct else 0.0
                lo, hi = o.world_mesh().bounds
                if lo[0] - 0.5 <= v["x"] <= hi[0] + 0.5 and lo[1] - 0.5 <= v["y"] <= hi[1] + 0.5 \
                        and lo[2] - 0.5 <= v["z_surface"] - lift <= hi[2] + 0.5:
                    best = (o, lift)
                    break
                # not inside any box (lift or compensation moved it a little): the nearest model in XY
                d = max(lo[0] - v["x"], v["x"] - hi[0], lo[1] - v["y"], v["y"] - hi[1], 0.0)
                if d < 3.0 and (best is None or d < best[2]):
                    best = (o, lift, d)
            if best is not None:
                out.append((best[0], v, best[1]))
        return out

    CUP_BLOCK_AREA = 20.0          # mm²: a sealed cavity this big tears layers apart (delamination)

    def _confirm_cups(self) -> bool:
        """Before a resin plate leaves UniSlicer: a big suction cup (a cavity sealed on the plate side, e.g. a
        hollow / honeycomb model with a closed floor) pulls a vacuum on every peel and tears the print apart.
        Offer the fix (vent holes) instead of letting it go out with only a line in the print check."""
        if self.result is None or self.result[0] != "sla":
            return True
        job, cfg = self.result[1], self.result[2]
        big = [a for _i, a in job.cups if a >= self.CUP_BLOCK_AREA]
        if not big:
            return True
        layers = [i for i, a in job.cups if a >= self.CUP_BLOCK_AREA]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("cup_block_title"))
        box.setText(tr("cup_block_text").format(min(layers) + 1, max(layers) + 1, max(big)))
        fix = box.addButton(tr("cup_block_fix"), QMessageBox.AcceptRole)
        anyway = box.addButton(tr("cup_block_anyway"), QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(fix)
        box.exec()
        if box.clickedButton() is anyway:
            return True
        if box.clickedButton() is fix:
            owners = self._cup_owners(job, cfg)
            if owners:
                self._push_undo()
                for o, v, lift in owners:
                    for row in sla.vent_hole_rows(v, cfg, lift):
                        o.add_world_drain_hole(row[:3], row[3:], 0.0)
                self._objects_changed(refresh_list=False)
                self.status.setText(tr("cup_vents_added").format(len(owners)))
                self._cup_fix_pending = len(owners)
                QTimer.singleShot(400, self, self.slice)
            else:
                self._cup_no_vent_dialog(job)
        return False

    def _cup_no_vent_dialog(self, job):
        """No spot for an automatic vent (the cavity is boxed in on every side): say so and offer what works."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(tr("cup_block_title"))
        box.setText(tr("cup_novent_text"))
        tilt = box.addButton(tr("cup_fix_tilt"), QMessageBox.AcceptRole)
        orient_b = box.addButton(tr("cup_fix_orient"), QMessageBox.ActionRole)
        show = box.addButton(tr("cup_fix_show"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        targets = [o for o in self.objects if o.role != "cutter"]
        if box.clickedButton() is show:
            self.set_mode("preview", slice_if_needed=False)
            self.layer_slider.setValue(job.cups[0][0])
        elif box.clickedButton() is tilt:
            self._push_undo()
            a = np.radians(30.0)
            rot = np.array([[1.0, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
            for o in targets:
                o.apply_world_rotation(rot)
                self._drop_supports(o)
            self._objects_changed()
            self.status.setText(tr("cup_tilted"))
        elif box.clickedButton() is orient_b:
            from .viewer import SEL_ADD
            if targets:
                self._select_uid(targets[0].uid)
                for o in targets[1:]:
                    self._select_uid(o.uid, SEL_ADD)
                self.auto_orient_selected()
                self.status.setText(tr("cup_reoriented"))

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
            QTimer.singleShot(400, self, self.slice)    # check again (after the change debounce, which would cancel it)
        elif chosen == drain:
            self.settings.set_value("drain_holes", 2)
            QTimer.singleShot(400, self, self.slice)
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
                "repair_offers": [{"uid": o.uid, "name": o.name, "recipe_text": self._recipe_text(self._repair_offers[o.uid])}
                                  for o in self.objects if o.uid in self._repair_offers],
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
        QTimer.singleShot(0, self, self._schedule_suggestions)

    def _show_crash(self, sig: str):
        from .crash_dialog import CrashDialog, entry_diagnosis, recent_crashes
        for e in recent_crashes():
            if e["signature"] == sig:
                CrashDialog(entry_diagnosis(e), self).exec()
                return

    def _column_map_key(self, o: SceneObject):
        disp = self._display_matrix(o)
        return (id(o.mesh), disp[:3, :3].tobytes())

    def _column_map_ready(self, o: SceneObject) -> bool:
        hit = self.__dict__.get("_column_maps", {}).get(o.uid)
        return hit is not None and hit[0] == self._column_map_key(o)

    def _build_column_maps(self, objs):
        """Build the collision maps of newly placed or turned parts in the background (~0.5 s each for a
        jaw model), then check collisions again."""
        if getattr(self, "_colmap_task", None) is not None:
            return                                         # one at a time; the next check picks up the rest
        from .. import collision
        jobs = [(o.uid, self._column_map_key(o), o.mesh.transform(self._display_matrix(o)),
                 self._display_matrix(o)[:3, 3].copy()) for o in objs]

        def work(progress, cancelled):
            return [(uid, key, collision.ColumnMap(mesh), at) for uid, key, mesh, at in jobs]

        def done(res):
            cache = self.__dict__.setdefault("_column_maps", {})
            for uid, key, cmap, at in res:
                cache[uid] = (key, cmap, at)
            finish()
            self._collision_timer.start()

        def finish():
            t, self._colmap_task = self._colmap_task, None
            if t is not None:
                t.wait()
                t.deleteLater()

        from .worker import Task
        task = Task(work, self)
        self._colmap_task = task
        task.succeeded.connect(done)
        task.failed.connect(lambda _m: finish())
        task.start()

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
            missing = [o for o in parts if not self._column_map_ready(o)]
            if missing and self.async_supports:
                self._build_column_maps(missing)          # the check runs again when they are ready
                return
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

            def short(n):
                return n if len(n) <= 28 else n[:12] + "…" + n[-14:]
            text = ", ".join(f"{short(names[a])} ↔ {short(names[b])}" for a, b in pairs[:3]) + ("…" if len(pairs) > 3 else "")
            self._collision_text = "⚠ " + tr("collision_warning").format(text)
            self.collision_label.setText("⚠ " + tr("collision_short").format(len(colliding)))
            tip = "\n".join(f"{names[a]} ↔ {names[b]}" for a, b in pairs)
            self.collision_label.setToolTip(tip)
            self.collision_banner.setToolTip(tip)
            self.collision_wrap.adjustSize()
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
        elif a == "repair_like_before":
            o = next((x for x in self.objects if x.uid == args["uid"]), None)
            recipe = self._repair_offers.pop(args["uid"], None)
            if o is not None and recipe is not None:
                self._push_undo()
                fp = self._repair_fingerprint(o)
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    self._apply_repair_recipe(o, recipe)
                finally:
                    QApplication.restoreOverrideCursor()
                self.assistant.learn_repair(fp, recipe)          # choosing it again confirms it
                self._objects_changed()
                self.status.setText(tr("rr_done").format(o.name, self._recipe_text(recipe)))
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

    # ------------------------------------------------------------ shared learning (all the user's computers)
    def _init_learning_share(self):
        share = self.store.value("learning_share", "true") == "true"
        self.assistant_panel.share_box.blockSignals(True)
        self.assistant_panel.share_box.setChecked(share)
        self.assistant_panel.share_box.blockSignals(False)
        if share:
            self._learn_sync_timer.start()
            QTimer.singleShot(90 * 1000, self, lambda: self.sync_learning(manual=False))
        self._show_sync_status()

    def _on_share_toggled(self, on: bool):
        self.store.setValue("learning_share", "true" if on else "false")
        if on:
            self._learn_sync_timer.start()
            self.sync_learning(manual=True)
        else:
            self._learn_sync_timer.stop()
        self._show_sync_status()

    def _show_sync_status(self, text: str | None = None):
        if text is None:
            last = self.store.value("learning_sync_last", "")
            n = int(self.store.value("learning_sync_peers", 0) or 0)
            if self.store.value("learning_share", "true") != "true":
                text = tr("learn_sync_off")
            elif last:
                text = tr("learn_sync_last").format(last, n)
            else:
                text = tr("learn_sync_never")
        self.assistant_panel.sync_status.setText(text)

    def sync_learning(self, manual: bool = False):
        """Upload what this computer's assistant learned and merge in what the user's other computers learned
        (their Firebase project, under the Google account this computer is linked to)."""
        from .. import learning_sync, remote
        if self._learn_sync_task is not None and self._learn_sync_task.isRunning():
            return
        if not self.assistant.enabled:
            if manual:
                self._show_sync_status(tr("learn_layout_off"))
            return
        agent = getattr(self.remote, "agent", None)
        if agent is None or not agent.running() or agent.state != "linked":
            self._show_sync_status(tr("learn_sync_link"))
            if manual and QMessageBox.question(self, tr("learn_share"), tr("learn_sync_link_q")) == QMessageBox.Yes:
                self.remote_control()
            return
        text = learning_sync.snapshot(self.assistant.data)

        def work(progress, cancelled):
            return agent.sync_learning(text)

        def done(others):
            import time as _t
            try:
                seen = json.loads(self.store.value("learning_sync_seen", "{}") or "{}")
            except ValueError:
                seen = {}
            new, seen = learning_sync.fresh(others, seen)
            self.store.setValue("learning_sync_seen", json.dumps(seen))
            merged = learning_sync.merge(self.assistant.data, [{k: v for k, v in o.items() if k != "_from"}
                                                               for o in new])
            for k in learning_sync.LOCAL_ONLY:
                if k in self.assistant.data:
                    merged[k] = self.assistant.data[k]
            self.assistant.data = merged
            self.assistant.save()
            stamp = _t.strftime("%H:%M")
            self.store.setValue("learning_sync_last", stamp)
            self.store.setValue("learning_sync_peers", len(others))
            self._show_sync_status()
            self.assistant_panel.set_learning(self.assistant.enabled, self.assistant.summary())
            if manual:
                self.status.setText(tr("learn_sync_done").format(len(others)))

        def failed(msg):
            self._show_sync_status(tr("learn_sync_failed").format(remote.explain(msg) if msg else "?"))

        self._learn_sync_task = Task(work, self)
        self._learn_sync_task.succeeded.connect(done)
        self._learn_sync_task.failed.connect(failed)
        self._learn_sync_task.start()

    def export_learning(self):
        """Assistant › Export learning: everything it learned, as a file to take to another computer."""
        import time as _time
        last = self.store.value("learning_dir", os.path.expanduser("~"))
        name = f"UniSlicer-learning-{_time.strftime('%Y-%m-%d')}.json"
        path, _ = QFileDialog.getSaveFileName(self, tr("learning_export"), os.path.join(last, name), "JSON (*.json)")
        if not path:
            return
        try:
            self.assistant.export_to(path)
        except OSError as exc:
            QMessageBox.critical(self, tr("learning_export"), str(exc))
            return
        self.store.setValue("learning_dir", os.path.dirname(path))
        self.status.setText(tr("learning_exported").format(path))

    def import_learning(self):
        """Assistant › Import learning: replace what it learned here with an exported file."""
        last = self.store.value("learning_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, tr("learning_import"), last, "JSON (*.json)")
        if not path:
            return
        if QMessageBox.question(self, tr("learning_import"), tr("learning_import_q")) != QMessageBox.Yes:
            return
        if not self.assistant.import_from(path):
            QMessageBox.warning(self, tr("learning_import"), tr("learning_import_bad"))
            return
        self.store.setValue("learning_dir", os.path.dirname(path))
        self._refresh_suggestions()
        self.status.setText(tr("learning_imported"))

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
            return project.replace_retry(tmp, path)   # locked for good: skip this round, the next one saves

        task = Task(work, self)
        task.succeeded.connect(lambda ok: ok and self.store.setValue("autosave_clean", "false"))
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

    def send_to_formlabs(self):
        """Send the plate (or the selected models) to a Formlabs printer through PreFormServer."""
        from .. import formlabs
        from .formlabs_dialog import FormlabsDialog
        if not [o for o in self.objects if o.role != "cutter"]:
            self.status.setText(tr("fl_nothing"))
            return
        if getattr(self, "_formlabs", None) is None:
            self._formlabs = formlabs.Client(port=int(self.store.value("formlabs_port", formlabs.DEFAULT_PORT)))
            QApplication.instance().aboutToQuit.connect(self._formlabs.stop)

        def meshes(mode):
            chosen = [o for o in (self.selected_objects() or self.objects) if o.role != "cutter"]
            cfg = self.settings.config()
            out = []
            for o in chosen:
                m = o.world_mesh()
                if mode == "none" and cfg["technology"] == "sla" and cfg["support_enable"] and not o.direct:
                    lift = self._lift(cfg)
                    m = m.translated([0, 0, lift])
                    sup, _ = sla_supports.build_supports(m, cfg, o.world_support_points(lift), label=self._raft_label_text(o, cfg), label_frame=o.plate_frame())
                    m = Mesh.merge([m, sup], o.name)
                out.append((o.name or "model", m))
            return out

        name = os.path.splitext(self.objects[0].name or "UniSlicer")[0]
        resin = ""
        mat = profiles.load("materials").get(self.settings.material_id or "", {})
        if mat.get("vendor") == "Formlabs":
            resin = mat.get("values", {}).get("material_name") or mat.get("name", "")
        FormlabsDialog(self.store, meshes, name, self._formlabs, self, material=resin).exec()

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
                sup, _ = sla_supports.build_supports(lifted, cfg, o.world_support_points(lift), label=self._raft_label_text(o, cfg), label_frame=o.plate_frame())
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
    # ------------------------------------------------------------ estimate & job report (GrabCAD Print)
    def plate_estimate(self):
        from .. import estimate
        cfg = self.settings.config()
        supports = {}
        if cfg["technology"] == "sla" and cfg["support_enable"]:
            for o in self.objects:
                try:
                    local = self._built_supports(o, cfg)
                except Exception:
                    local = None
                if local is not None and len(local.faces):
                    supports[o.uid] = local.transform(self._display_matrix(o))
        return estimate.plate(self.objects, cfg, supports, self._lift(cfg) if cfg["technology"] == "sla" else 0.0,
                              self.result)

    def estimate_dialog(self):
        """Per-model time / material / weight / cost for the plate (before slicing, or from the slice)."""
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
                                       QTableWidgetItem, QVBoxLayout)
        from .. import estimate
        if not [o for o in self.objects if o.role != "cutter"]:
            self.status.setText(tr("no_models"))
            return
        est = self.plate_estimate()
        unit = "ml" if est.technology == "sla" else "cm³"
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("estimate_title"))
        dlg.resize(760, 420)
        v = QVBoxLayout(dlg)
        head = QLabel(tr("estimate_head").format(estimate.fmt_time(est.time_s), est.layers, est.total_ml, unit,
                                                 est.weight_g, est.cost))
        head.setStyleSheet("font-weight: 700; font-size: 14px;")
        v.addWidget(head)
        note = QLabel(tr("estimate_sliced") if est.calibrated else tr("estimate_rough"))
        note.setObjectName("Faint")
        v.addWidget(note)
        cols = [tr("est_model"), tr("est_model_ml").format(unit), tr("est_support_ml").format(unit),
                tr("est_total_ml").format(unit), tr("est_weight"), tr("est_cost"), tr("est_height")]
        table = QTableWidget(len(est.rows) + 1, len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, r in enumerate(est.rows + [None]):
            vals = ([r.name, f"{r.model_ml:.2f}", f"{r.support_ml:.2f}", f"{r.total_ml:.2f}", f"{r.weight_g:.1f}",
                     f"₪{r.cost:.2f}", f"{r.height:.1f}"] if r is not None else
                    [tr("est_total"), "", "", f"{est.total_ml:.2f}", f"{est.weight_g:.1f}", f"₪{est.cost:.2f}", ""])
            for j, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if r is None:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("close"))
        copy = buttons.addButton(tr("estimate_copy"), QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(est.csv()), self.status.setText(tr("estimate_copied"))))
        rep = buttons.addButton(tr("report_save") + "…", QDialogButtonBox.ActionRole)
        rep.clicked.connect(lambda: self.save_job_report())
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        self._estimate_dialog = dlg
        dlg.exec()

    def write_job_report(self, path: str, file_path: str = "") -> str:
        """The job report of the plate as a PDF at ``path`` (returns the path)."""
        from PySide6.QtCore import QMarginsF, QUrl
        from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument
        from .. import report
        est = self.plate_estimate()
        cfg = self.settings.config()
        png = report.plate_picture_png(self.objects)
        printer = self.settings.printer_combo.currentText() if hasattr(self.settings, "printer_combo") else cfg.get("printer_model", "")
        material = self.settings.material_combo.currentText() if hasattr(self.settings, "material_combo") else ""
        name = (self.objects[0].name if self.objects else "print") or "print"
        doc = QTextDocument()
        if png:
            doc.addResource(QTextDocument.ImageResource, QUrl("plate.png"), QImage.fromData(png, "PNG"))
        doc.setHtml(report.build_html(est, cfg, printer.replace("★", "").strip(), material.replace("★", "").strip(), name,
                                      picture_src="plate.png" if png else "", file_path=file_path))
        writer = QPdfWriter(path)
        writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(12, 12, 12, 12),
                                         QPageLayout.Millimeter))
        writer.setResolution(110)
        doc.setPageSize(writer.pageLayout().paintRectPixels(writer.resolution()).size().toSizeF())
        doc.print_(writer)
        return path

    def save_job_report(self, path: str | None = None):
        if not [o for o in self.objects if o.role != "cutter"]:
            self.status.setText(tr("no_models"))
            return None
        if path is None:
            from .. import export_library
            name = export_library.safe_name((self.objects[0].name if self.objects else "print") or "print")
            start = os.path.join(self.export_library(), name, name + " - report.pdf")
            os.makedirs(os.path.dirname(start), exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, tr("report_save"), start, "PDF (*.pdf)")
            if not path:
                return None
        try:
            self.write_job_report(path)
        except Exception as exc:              # a report must never break exporting
            self.status.setText(tr("report_failed").format(exc))
            return None
        self.status.setText(tr("report_saved").format(path))
        return path

    def _report_with_export(self, print_path: str):
        """After an export: the job report and the project file beside the print file
        (More › Save job report / project file with each export)."""
        base = os.path.splitext(print_path)[0]
        if self.store.value("export_report", "true") == "true":
            try:
                self.write_job_report(base + " - report.pdf", file_path=print_path)
            except Exception:
                pass
        if self.store.value("export_project", "true") == "true":
            try:                              # the plate as a project: open it later to change and export again
                project.save(base + project.EXTENSION, self.objects, self.settings.state())
            except Exception:
                pass

    def export_library(self) -> str:
        """The central folder every print file is exported to (chosen in More › Export folder)."""
        from .. import export_library
        return self.store.value("export_library", "") or export_library.default_library()

    def choose_export_library(self):
        path = QFileDialog.getExistingDirectory(self, tr("export_library_choose"), self.export_library())
        if path:
            self.store.setValue("export_library", path)
            self.status.setText(tr("export_library_set").format(path))

    def _reveal(self, path: str):
        """Open the folder a file was saved in, with the file selected (Windows Explorer)."""
        import subprocess
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            if os.name == "nt" and os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def open_export_library(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        path = self.export_library()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _export_ext(self) -> str:
        res = self._exportable()
        if res[0] == "fdm":
            return ".gcode"
        return {"sl1": ".sl1", "ctb": ".ctb", "goo": ".goo", "prz": ".prz", "slc": ".slc", "cli": ".cli",
                "3mf": ".3mf"}.get(res[2]["sla_format"], ".zip")

    def export(self, ask: bool = False):
        """Export Print File: saved straight into the central library, in a folder named after the file
        (<library>/<name>/<name>.<ext>), no dialog. ``ask`` (More › Export as…) picks the place by hand."""
        from .. import export_library
        res = self._exportable()
        if res is None:
            return
        if not self._confirm_cups():
            return
        name = export_library.safe_name((self.objects[0].name if self.objects else "print") or "print")
        ext = self._export_ext()
        if ask:
            last = self.store.value("export_dir", os.path.expanduser("~"))
            path, _ = QFileDialog.getSaveFileName(self, tr("export_gcode") if ext == ".gcode" else tr("export_sla"),
                                                  os.path.join(last, name + ext), f"*{ext}")
            if not path:
                return
            self.store.setValue("export_dir", os.path.dirname(path))
        else:
            try:
                path = export_library.target_path(self.export_library(), name, ext)
            except OSError as exc:
                QMessageBox.critical(self, tr("export_error"), str(exc))
                return
        self.last_export_path = path
        if res[0] == "fdm":
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(res[2])
            except OSError as exc:
                QMessageBox.critical(self, tr("export_error"), str(exc))
                return
            self._report_with_export(path)
            self.status.setText(tr("export_done").format(path))
            self._reveal(path)
            self._ask_print_feedback()
        else:
            job, cfg = res[1], res[2]
            result = res
            base = os.path.splitext(os.path.basename(path))[0]

            def work(progress, cancelled):
                return sla.export(job, cfg, path, base, lambda f: progress("export", f), cancelled)

            def done(p):
                self._report_with_export(p if isinstance(p, str) and p else path)
                self.status.setText(tr("export_done").format(p))
                self._reveal(p if isinstance(p, str) and p else path)
                self._ask_print_feedback()

            self._bg_export(work, done, tr("exporting"))
            res = result

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

    # ================================================================ print queue (Schedule view)
    def queue_dialog(self):
        from .queue_dialog import QueueDialog
        dlg = getattr(self, "_queue_dialog", None)
        if dlg is None or not dlg.isVisible():
            dlg = QueueDialog(self)
            self._queue_dialog = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    def add_plate_to_queue(self, printer_id: str, dialog=None):
        """Save the sliced plate into the prints folder and put it at the end of that printer's queue."""
        from .. import export_library, print_queue
        res = self._exportable()
        if res is None:
            QMessageBox.information(self, tr("queue_title"), tr("queue_slice_first"))
            return
        if not self._confirm_cups():
            return
        name = export_library.safe_name((self.objects[0].name if self.objects else "print") or "print")
        try:
            path = export_library.target_path(self.export_library(), name, self._export_ext())
        except OSError as exc:
            QMessageBox.critical(self, tr("export_error"), str(exc))
            return
        est = self.plate_estimate()
        unit = "ml" if est.technology == "sla" else "cm³"
        result = res

        def finish(p):
            queue = dialog.queue if dialog is not None else print_queue.Queue()
            queue.add(printer_id, p, os.path.basename(p), est.time_s, f"{est.total_ml:.1f} {unit}")
            self._report_with_export(p)
            self.status.setText(tr("queue_added").format(os.path.basename(p)))
            if dialog is not None:
                dialog.refresh()

        if result[0] == "fdm":
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(result[2])
            except OSError as exc:
                QMessageBox.critical(self, tr("export_error"), str(exc))
                return
            finish(path)
            return
        job, cfg = result[1], result[2]
        base = os.path.splitext(os.path.basename(path))[0]
        self._bg_export(lambda progress, cancelled: sla.export(job, cfg, path, base, lambda f: progress("export", f), cancelled),
                         lambda p: finish(p if isinstance(p, str) and p else path), tr("exporting"))
        res = result

    # ================================================================ network printers
    def network_printers(self):
        from .network_dialog import NetworkDialog
        NetworkDialog(self.settings.printer_id, self).exec()

    def send_to_printer(self):
        """Export the sliced plate to a temporary file and send it to a printer on the network (Wi-Fi/LAN)."""
        import tempfile

        from .. import network
        from .network_dialog import NetworkDialog, SendDialog, lang
        if profiles.load("printers").get(self.settings.printer_id, {}).get("vendor") == "Formlabs":
            self.send_to_formlabs()                 # Formlabs printers take jobs only through PreForm
            return
        res = self._exportable()
        if res is None:
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
            if res[0] == "fdm":
                ext = ".gcode"
            else:
                ext = {"sl1": ".sl1", "ctb": ".ctb", "goo": ".goo", "prz": ".prz", "slc": ".slc", "cli": ".cli",
                       "3mf": ".3mf"}.get(res[2]["sla_format"], ".zip")
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
        result = res
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

        self._bg_export(work, done, tr("net_sending").format(target.name))
        res = result

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
        self._stop_preslice()
        self._sup_pending.clear()                    # no new support builds; let a running one finish
        for t in (getattr(self, "_autosave_task", None), getattr(self, "_orient_task", None),
                  getattr(self, "_sup_task", None), getattr(self, "_colmap_task", None)):
            if t is not None and t.isRunning():
                t.wait(5000)
        if getattr(self, "remote", None) is not None:
            self.remote.shutdown()
        self.store.setValue("geometry", self.saveGeometry())
        self._save_state()
        self.store.setValue("autosave_clean", "true")     # a normal exit: nothing to recover next time
        # a window closed for a theme or language switch is not deleted: its timers must not keep running
        # (its autosave wrote the old plate over the new window's every two minutes)
        for t in self.findChildren(QTimer):
            t.stop()
        super().closeEvent(e)
