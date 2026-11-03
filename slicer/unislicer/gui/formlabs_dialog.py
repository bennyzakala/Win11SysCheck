"""Send to Formlabs: the plate's models go through PreFormServer (Formlabs' Local API) to a Form printer."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout)

from .. import formlabs
from .i18n import tr
from .worker import Task

STEP_KEYS = {"scene": "fl_step_scene", "import": "fl_step_import", "orient": "fl_step_orient",
             "supports": "fl_step_supports", "layout": "fl_step_layout", "check": "fl_step_check",
             "save": "fl_step_save", "print": "fl_step_print", "done": "fl_step_done"}


class FormlabsDialog(QDialog):
    """``export_meshes(mode)`` returns [(name, Mesh)] of the models to send ("auto"/"mini_rafts_on_bp": models
    alone; "none": with UniSlicer's supports)."""

    def __init__(self, store, export_meshes, job_name: str, client: formlabs.Client | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("fl_title"))
        self.setMinimumWidth(560)
        self.store = store
        self.export_meshes = export_meshes
        self.client = client or formlabs.Client(port=int(store.value("formlabs_port", formlabs.DEFAULT_PORT)))
        self.types: list[dict] = []
        self.devices: list[dict] = []
        self.task: Task | None = None
        lay = QVBoxLayout(self)
        intro = QLabel(tr("fl_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("Faint")
        lay.addWidget(intro)
        form = QFormLayout()
        lay.addLayout(form)
        # PreFormServer
        self.exe = QLineEdit(formlabs.find_server(str(store.value("formlabs_exe", "") or "")))
        self.exe.setPlaceholderText("PreFormServer.exe")
        browse = QPushButton(tr("fl_browse"))
        browse.clicked.connect(self._browse)
        get = QPushButton(tr("fl_download"))
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(formlabs.DOWNLOAD_PAGE)))
        row = QHBoxLayout()
        row.addWidget(self.exe, 1)
        row.addWidget(browse)
        row.addWidget(get)
        form.addRow(tr("fl_server"), row)
        self.server_state = QLabel(tr("fl_not_connected"))
        self.connect_btn = QPushButton(tr("fl_connect"))
        self.connect_btn.clicked.connect(self._connect)
        row = QHBoxLayout()
        row.addWidget(self.server_state, 1)
        row.addWidget(self.connect_btn)
        form.addRow("", row)
        # printer
        self.printer = QComboBox()
        self.printer.setEditable(True)                 # a serial name or an IP address can be typed in
        self.printer.lineEdit().setPlaceholderText(tr("fl_printer_hint"))
        last = str(store.value("formlabs_printer", "") or "")
        if last:
            self.printer.addItem(last, {"id": last})
        self.find_btn = QPushButton(tr("fl_find"))
        self.find_btn.clicked.connect(self._find)
        self.printer.currentIndexChanged.connect(self._printer_chosen)
        row = QHBoxLayout()
        row.addWidget(self.printer, 1)
        row.addWidget(self.find_btn)
        form.addRow(tr("fl_printer"), row)
        # material
        self.ptype, self.material, self.setting = QComboBox(), QComboBox(), QComboBox()
        self.ptype.currentIndexChanged.connect(self._fill_materials)
        self.material.currentIndexChanged.connect(self._fill_settings)
        form.addRow(tr("fl_type"), self.ptype)
        form.addRow(tr("fl_material"), self.material)
        form.addRow(tr("fl_layer"), self.setting)
        # how
        self.supports = QComboBox()
        self.supports.addItem(tr("fl_sup_auto"), "auto")
        self.supports.addItem(tr("fl_sup_mine"), "none")
        self.supports.addItem(tr("fl_sup_plate"), "mini_rafts_on_bp")
        idx = self.supports.findData(str(store.value("formlabs_supports", "auto")))
        self.supports.setCurrentIndex(max(0, idx))
        form.addRow(tr("fl_supports"), self.supports)
        self.orient = QCheckBox(tr("fl_orient"))
        self.orient.setChecked(str(store.value("formlabs_orient", "false")) == "true")
        form.addRow("", self.orient)
        self.job = QLineEdit(job_name)
        form.addRow(tr("fl_job"), self.job)
        self.now = QRadioButton(tr("fl_now"))
        self.queue = QRadioButton(tr("fl_queue"))
        (self.queue if str(store.value("formlabs_queue", "false")) == "true" else self.now).setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.now)
        row.addWidget(self.queue)
        row.addStretch(1)
        form.addRow("", row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        buttons = QHBoxLayout()
        self.form_btn = QPushButton(tr("fl_save_form"))
        self.form_btn.clicked.connect(self._save_form)
        self.send_btn = QPushButton(tr("fl_send"))
        self.send_btn.setObjectName("Primary")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._send)
        close = QPushButton(tr("close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(self.form_btn)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.send_btn)
        lay.addLayout(buttons)
        self._busy(False)

    # ------------------------------------------------------------ helpers
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("fl_server"), os.path.dirname(self.exe.text()),
                                              "PreFormServer (PreFormServer*)")
        if path:
            self.exe.setText(path)
            self.store.setValue("formlabs_exe", path)

    def _busy(self, on: bool, text: str = ""):
        for w in (self.connect_btn, self.find_btn, self.send_btn, self.form_btn):
            w.setEnabled(not on and (w is self.connect_btn or bool(self.types)))
        self.bar.setVisible(on)
        if text or not on:
            self.status.setText(text)

    def _run(self, fn, done, text: str):
        if self.task is not None and self.task.isRunning():
            return
        self._busy(True, text)
        self.task = Task(fn, self)
        self.task.progress.connect(lambda stage, f: self.status.setText(tr(STEP_KEYS.get(stage, "fl_working"))))

        def ok(result):
            self._busy(False)
            done(result)

        def bad(message):
            self._busy(False, tr("fl_failed").format(message) if message else "")

        self.task.succeeded.connect(ok)
        self.task.failed.connect(bad)
        self.task.start()

    def ensure_connected(self, progress=None, cancelled=None):
        exe = self.exe.text().strip()
        self.client.start(exe)
        return self.client.version(), self.client.materials()

    # ------------------------------------------------------------ actions
    def _connect(self):
        self.store.setValue("formlabs_exe", self.exe.text().strip())

        def done(result):
            version, types = result
            self.types = types
            self.server_state.setText(tr("fl_connected").format(version))
            self.ptype.blockSignals(True)
            self.ptype.clear()
            for t in types:
                self.ptype.addItem(t["label"])
            self.ptype.blockSignals(False)
            want = str(self.store.value("formlabs_type", "") or "")
            self.ptype.setCurrentIndex(max(0, self.ptype.findText(want)))
            self._fill_materials()
            self._busy(False)

        self._run(lambda p, c: self.ensure_connected(p, c), done, tr("fl_starting"))

    def _find(self):
        ip = self.printer.currentText().strip() if self.printer.currentText().strip().count(".") == 3 else ""

        def done(devices):
            self.devices = devices
            current = self.printer.currentText()
            self.printer.blockSignals(True)
            self.printer.clear()
            for d in devices:
                label = f"{d.get('id', '')} · {d.get('product_name', '')} · {d.get('ip_address', '')} · {d.get('status', '')}"
                self.printer.addItem(label, d)
            if not devices and current:
                self.printer.addItem(current, {"id": current})
            self.printer.blockSignals(False)
            self._printer_chosen()
            self.status.setText(tr("fl_found").format(len(devices)))

        self._run(lambda p, c: self.client.discover(10, ip, c), done, tr("fl_searching"))

    def _printer_chosen(self, *_):
        """Pick the printer type (and the resin of the tank in it) for the chosen printer."""
        d = self.printer.currentData() or {}
        i = formlabs.match_printer_type(self.types, d.get("product_name", ""))
        if i >= 0:
            self.ptype.setCurrentIndex(i)
        tank = d.get("tank_material_code") or ""
        if tank and 0 <= self.ptype.currentIndex() < len(self.types):
            for k, m in enumerate(self.types[self.ptype.currentIndex()]["materials"]):
                if any(s["scene_settings"].get("material_code") == tank for s in m["settings"]):
                    self.material.setCurrentIndex(k)
                    break

    def _fill_materials(self, *_):
        self.material.blockSignals(True)
        self.material.clear()
        i = self.ptype.currentIndex()
        mats = self.types[i]["materials"] if 0 <= i < len(self.types) else []
        for m in mats:
            self.material.addItem(m["label"])
            self.material.setItemData(self.material.count() - 1, m.get("description", ""), Qt.ToolTipRole)
        self.material.blockSignals(False)
        want = str(self.store.value("formlabs_material", "") or "")
        self.material.setCurrentIndex(max(0, self.material.findText(want)))
        self._fill_settings()

    def _fill_settings(self, *_):
        self.setting.clear()
        for s in self._settings():
            self.setting.addItem(s["label"], s["scene_settings"])
        want = str(self.store.value("formlabs_layer", "") or "")
        self.setting.setCurrentIndex(max(0, self.setting.findText(want)))

    def _settings(self) -> list[dict]:
        i, k = self.ptype.currentIndex(), self.material.currentIndex()
        if 0 <= i < len(self.types) and 0 <= k < len(self.types[i]["materials"]):
            return self.types[i]["materials"][k]["settings"]
        return []

    def _printer_id(self) -> str:
        """The printer to send to: its IP address (or serial name) from the list, or what was typed."""
        d = self.printer.currentData()
        text = self.printer.currentText().strip()
        if isinstance(d, dict) and d.get("id") and text == self.printer.itemText(self.printer.currentIndex()):
            return str(d.get("ip_address") or d["id"])
        return text

    def _job(self, printer: str, save_form: str):
        scene = self.setting.currentData()
        if not scene:
            self.status.setText(tr("fl_need_material"))
            return
        mode = self.supports.currentData()
        st = self.store.setValue
        st("formlabs_type", self.ptype.currentText())
        st("formlabs_material", self.material.currentText())
        st("formlabs_layer", self.setting.currentText())
        st("formlabs_supports", mode)
        st("formlabs_orient", "true" if self.orient.isChecked() else "false")
        st("formlabs_queue", "true" if self.queue.isChecked() else "false")
        if printer:
            st("formlabs_printer", printer)
        folder = tempfile.mkdtemp(prefix="unislicer-formlabs-")
        from ..mesh import save_stl
        paths = []
        for k, (name, mesh) in enumerate(self.export_meshes(mode)):
            safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in (name or "model"))[:60] or "model"
            path = os.path.join(folder, f"{k + 1:02d}_{safe}.stl")
            save_stl(mesh, path)
            paths.append(path)
        if not paths:
            self.status.setText(tr("fl_nothing"))
            return
        job_name = self.job.text().strip() or "UniSlicer"
        print_now = None if not printer else not self.queue.isChecked()

        def work(progress, cancelled):
            try:
                return self.client.send(paths, dict(scene), printer, job_name, supports=mode,
                                        orient=self.orient.isChecked(), print_now=print_now, save_form=save_form,
                                        progress=progress, cancelled=cancelled)
            finally:
                for p in paths:
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        def done(result):
            bad = formlabs.validation_problems(result.get("validation"))
            lines = [tr("fl_sent").format(printer) if printer else tr("fl_saved").format(save_form)]
            if result.get("print_time_s"):
                from .. import gcode
                lines.append(tr("fl_time").format(gcode.format_duration(result["print_time_s"])))
            if any(bad.values()):
                lines.append(tr("fl_warnings").format(bad["cups"], bad["unsupported_minima"], bad["undersupported"]))
            self.status.setText("\n".join(lines))

        self._run(work, done, tr("fl_working"))

    def _send(self):
        printer = self._printer_id()
        if not printer:
            self.status.setText(tr("fl_need_printer"))
            return
        self._job(printer, "")

    def _save_form(self):
        last = str(self.store.value("export_dir", os.path.expanduser("~")))
        path, _ = QFileDialog.getSaveFileName(self, tr("fl_save_form"),
                                              os.path.join(last, (self.job.text().strip() or "UniSlicer") + ".form"),
                                              "PreForm (*.form)")
        if path:
            self._job("", path)

    def closeEvent(self, e):
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
            self.task.wait(3000)
        super().closeEvent(e)
