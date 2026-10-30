"""Network printers: add / find / test printers on the network, and the "Send to printer" dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import network, profiles
from . import i18n, theme
from .i18n import tr
from .worker import Task

KIND_LABEL = {
    "sdcp": ("ELEGOO / ChiTu Wi-Fi (SDCP) - Saturn 3/4 Ultra, Mars 5 Ultra", "ELEGOO / ChiTu Wi-Fi (SDCP) - Saturn 3/4 Ultra, Mars 5 Ultra"),
    "prusalink": ("Prusa - PrusaLink (SL1, SL1S, MK4, XL, Core One)", "Prusa - PrusaLink (SL1, SL1S, MK4, XL, Core One)"),
    "octoprint": ("OctoPrint", "OctoPrint"),
    "moonraker": ("Klipper / Moonraker (Mainsail, Fluidd, Creality K1, Neptune 4)", "Klipper / Moonraker (Mainsail, Fluidd, Creality K1, Neptune 4)"),
    "folder": ("תיקייה ברשת / כונן משותף / USB", "Network folder / shared drive / USB"),
}


def kind_label(kind: str) -> str:
    he, en = KIND_LABEL.get(kind, (kind, kind))
    return he if i18n.LANG == "he" else en


def lang() -> str:
    return "he" if i18n.LANG == "he" else "en"


class NetworkDialog(QDialog):
    """List of the user's network printers with a form to edit the selected one."""

    def __init__(self, current_printer: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("net_title"))
        self.resize(820, 520)
        self.items = network.load_printers()
        self.current_printer = current_printer
        self._task = None
        lay = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_row)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton(theme.icon("plus", theme.TEXT, 16), tr("net_add"))
        add.clicked.connect(self._add)
        self.del_btn = QPushButton(theme.icon("trash", theme.TEXT, 16), tr("delete"))
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(self.del_btn)
        left.addLayout(row)
        self.find_btn = QPushButton(theme.icon("wifi", theme.ACCENT, 16), tr("net_find"))
        self.find_btn.setToolTip(tr("net_find_tip"))
        self.find_btn.clicked.connect(self._discover)
        left.addWidget(self.find_btn)
        lay.addLayout(left, 2)

        right = QVBoxLayout()
        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        self.name = QLineEdit()
        self.kind = QComboBox()
        for k in network.KINDS:
            self.kind.addItem(kind_label(k), k)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.host = QLineEdit()
        self.browse = QPushButton("…")
        self.browse.setFixedWidth(32)
        self.browse.clicked.connect(self._browse)
        hrow = QHBoxLayout()
        hrow.addWidget(self.host, 1)
        hrow.addWidget(self.browse)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText(tr("net_port_auto"))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.storage = QComboBox()
        self.storage.addItem(tr("net_storage_local"), "local")
        self.storage.addItem("USB", "usb")
        self.printer = QComboBox()
        self.printer.addItem(tr("net_any_printer"), "")
        for pid, prof in sorted(profiles.load("printers").items(), key=lambda kv: profiles.display_name(kv[1])):
            self.printer.addItem(profiles.display_name(prof), pid)
        self.host_label = QLabel(tr("net_host"))
        form.addRow(tr("net_name"), self.name)
        form.addRow(tr("net_kind"), self.kind)
        form.addRow(self.host_label, hrow)
        self.rows = {"port": (QLabel(tr("net_port")), self.port), "api_key": (QLabel(tr("net_api_key")), self.api_key),
                     "user": (QLabel(tr("net_user")), self.user), "password": (QLabel(tr("net_password")), self.password),
                     "storage": (QLabel(tr("net_storage")), self.storage)}
        for label, w in self.rows.values():
            form.addRow(label, w)
        form.addRow(tr("net_profile"), self.printer)
        right.addWidget(self.form_box)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Faint")
        right.addWidget(self.hint)
        trow = QHBoxLayout()
        self.test_btn = QPushButton(theme.icon("wifi", theme.TEXT, 16), tr("net_test"))
        self.test_btn.clicked.connect(self._test)
        self.status_btn = QPushButton(theme.icon("info", theme.TEXT, 16), tr("net_status"))
        self.status_btn.clicked.connect(self._status)
        trow.addWidget(self.test_btn)
        trow.addWidget(self.status_btn)
        trow.addStretch(1)
        right.addLayout(trow)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.result, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText(tr("close"))
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        lay.addLayout(right, 3)

        self._loading = False
        self._refresh_list()
        self.list.setCurrentRow(0 if self.items else -1)
        self._sync_enabled()

    # ---------------------------------------------------------------- list
    def _refresh_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.items:
            it = QListWidgetItem(theme.icon("wifi" if p.kind != "folder" else "open", theme.TEXT, 16),
                                 f"{p.name}\n{p.host}")
            self.list.addItem(it)
        self.list.blockSignals(False)

    def _sync_enabled(self):
        has = self.list.currentRow() >= 0
        for w in (self.form_box, self.test_btn, self.status_btn, self.del_btn):
            w.setEnabled(has)
        if not has:
            self.hint.setText(tr("net_empty"))

    def _cur(self) -> network.NetPrinter | None:
        r = self.list.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def _load_row(self, _row):
        p = self._cur()
        self._sync_enabled()
        if p is None:
            return
        self._loading = True
        self.name.setText(p.name)
        self.kind.setCurrentIndex(max(0, self.kind.findData(p.kind)))
        self.host.setText(p.host)
        self.port.setValue(p.port)
        self.api_key.setText(p.api_key)
        self.user.setText(p.user)
        self.password.setText(p.password)
        self.storage.setCurrentIndex(max(0, self.storage.findData(p.storage)))
        self.printer.setCurrentIndex(max(0, self.printer.findData(p.printer_id)))
        self._loading = False
        self._kind_changed()
        self.result.setText("")

    def _store_form(self):
        p = self._cur()
        if p is None or self._loading:
            return
        p.name = self.name.text().strip() or p.name
        p.kind = self.kind.currentData()
        p.host = self.host.text().strip()
        p.port = self.port.value()
        p.api_key = self.api_key.text().strip()
        p.user = self.user.text().strip()
        p.password = self.password.text()
        p.storage = self.storage.currentData()
        p.printer_id = self.printer.currentData()
        it = self.list.currentItem()
        if it:
            it.setText(f"{p.name}\n{p.host}")

    def _kind_changed(self):
        k = self.kind.currentData()
        show = {"port": k != "folder", "api_key": k in ("prusalink", "octoprint", "moonraker"),
                "user": k == "prusalink", "password": k == "prusalink", "storage": k == "prusalink"}
        for key, (label, w) in self.rows.items():
            label.setVisible(show[key])
            w.setVisible(show[key])
        self.browse.setVisible(k == "folder")
        self.host_label.setText(tr("net_folder") if k == "folder" else tr("net_host"))
        self.host.setPlaceholderText(r"\\server\share" if k == "folder" else "192.168.1.50")
        self.hint.setText(tr(f"net_hint_{k}"))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, tr("net_folder"), self.host.text())
        if d:
            self.host.setText(d)

    def _add(self, _=False, info: dict | None = None):
        self._store_form()
        prof = profiles.load("printers").get(self.current_printer) or {}
        kind = (info or {}).get("kind") or ("sdcp" if "elegoo" in self.current_printer else "prusalink"
                                            if "prusa" in self.current_printer else "sdcp")
        p = network.NetPrinter(name=(info or {}).get("name") or profiles.display_name(prof) or tr("net_new"),
                               kind=kind, host=(info or {}).get("host", ""),
                               mainboard_id=(info or {}).get("mainboard_id", ""),
                               printer_id=self.current_printer if not info else "")
        self.items.append(p)
        self._refresh_list()
        self.list.setCurrentRow(len(self.items) - 1)

    def _delete(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.items):
            del self.items[r]
            self._refresh_list()
            self.list.setCurrentRow(min(r, len(self.items) - 1))
            self._sync_enabled()

    # ---------------------------------------------------------------- network work (background)
    def _run(self, fn, done, busy: str):
        if self._task is not None and self._task.isRunning():
            return
        self.result.setText(busy)
        self._task = Task(lambda progress, cancelled: fn(), self)
        self._task.succeeded.connect(done)
        self._task.failed.connect(lambda m: self.result.setText("⚠ " + (m.split(": ", 1)[-1] if m else "")))
        self._task.start()

    def _discover(self):
        def found(hits):
            known = {p.host for p in self.items}
            new = [h for h in hits if h["host"] not in known]
            for h in new:
                self._add(info=h)
            if hits:
                self.result.setText(tr("net_found").format(len(hits), len(new)) + "\n" +
                                    "\n".join(f"• {h['model'] or h['name']} - {h['host']}" for h in hits))
            else:
                self.result.setText(tr("net_found_none"))
        self._run(lambda: network.discover(2.5), found, tr("net_searching"))

    def _test(self):
        self._store_form()
        p = self._cur()

        def work():
            try:
                return ("ok", network.test_connection(p))
            except network.NetworkError as e:
                return ("err", network.explain(e, lang()))
        self._run(work, self._show_test, tr("net_testing"))

    def _show_test(self, r):
        kind, text = r
        color = theme.SUCCESS if kind == "ok" else theme.WARNING
        prefix = "✓ " + tr("net_ok") + "\n" if kind == "ok" else "⚠ "
        self.result.setStyleSheet(f"color:{color}")
        self.result.setText(prefix + text)

    def _status(self):
        self._store_form()
        p = self._cur()

        def work():
            try:
                s = network.printer_status(p)
            except network.NetworkError as e:
                return ("err", network.explain(e, lang()))
            parts = [f"{tr('net_state')}: {s.get('state', '')}"]
            if s.get("file"):
                parts.append(f"{tr('net_file')}: {s['file']}")
            if s.get("layers"):
                parts.append(f"{tr('stat_layers')}: {s.get('layer')}/{s['layers']}")
            if s.get("percent") is not None:
                parts.append(f"{s['percent']:.0f}%")
            return ("ok", "\n".join(parts))
        self._run(work, self._show_test, tr("net_testing"))

    def _save_and_close(self):
        self._store_form()
        try:
            network.save_printers(self.items)
        except OSError as e:
            self.result.setText("⚠ " + str(e))
            return
        self.accept()

    def done(self, r):
        if self._task is not None and self._task.isRunning():
            self._task.wait(4000)
        super().done(r)


class SendDialog(QDialog):
    """Pick the network printer, the file name and whether to start printing."""

    def __init__(self, items: list, current_printer: str, file_name: str, can_start_default: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("net_send"))
        self.items = items
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.target = QComboBox()
        best = 0
        for i, p in enumerate(items):
            self.target.addItem(theme.icon("wifi" if p.kind != "folder" else "open", theme.TEXT, 16),
                                f"{p.name}  ({p.host})", i)
            if p.printer_id and p.printer_id == current_printer:
                best = i
        self.target.setCurrentIndex(best)
        self.target.currentIndexChanged.connect(self._target_changed)
        self.name = QLineEdit(file_name)
        self.start = QCheckBox(tr("net_start_print"))
        self.start.setChecked(can_start_default)
        form.addRow(tr("net_printer"), self.target)
        form.addRow(tr("net_file_name"), self.name)
        form.addRow("", self.start)
        lay.addLayout(form)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color:{theme.WARNING}")
        lay.addWidget(self.warn)
        manage = QPushButton(theme.icon("settings", theme.TEXT, 16), tr("net_manage"))
        manage.clicked.connect(lambda: self.done(2))
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.button(QDialogButtonBox.Ok).setText(tr("net_send_btn"))
        b.button(QDialogButtonBox.Cancel).setText(tr("cancel"))
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(manage)
        row.addStretch(1)
        row.addWidget(b)
        lay.addLayout(row)
        self.current_printer = current_printer
        self._target_changed()

    def selected(self) -> network.NetPrinter:
        return self.items[self.target.currentData()]

    def _target_changed(self):
        p = self.selected()
        self.start.setEnabled(network.can_start(p.kind))
        if not network.can_start(p.kind):
            self.start.setChecked(False)
        mismatch = p.printer_id and self.current_printer and p.printer_id != self.current_printer
        self.warn.setText(tr("net_profile_mismatch") if mismatch else "")
