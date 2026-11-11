"""Print queue window (Schedule view): a column per network printer with its live status and its ordered
list of jobs. Drag to reorder, right-click to send now / move to another printer / remove. A job is
started only after the user confirms the plate is empty."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import network, print_queue
from ..estimate import fmt_time
from . import theme
from .i18n import tr
from .worker import Task

POLL_MS = 20000


class PrinterColumn(QFrame):
    def __init__(self, dialog: "QueueDialog", printer: network.NetPrinter):
        super().__init__()
        self.dialog, self.printer = dialog, printer
        self.setObjectName("Card")
        self.setMinimumWidth(270)
        v = QVBoxLayout(self)
        title = QLabel(printer.name or printer.host)
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        v.addWidget(title)
        self.status = QLabel(tr("queue_status_unknown"))
        self.status.setObjectName("Faint")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.model().rowsMoved.connect(lambda *a: self._reordered())
        v.addWidget(self.list, 1)
        self.total = QLabel("")
        self.total.setObjectName("Faint")
        v.addWidget(self.total)
        self.send_btn = QPushButton(theme.icon("play", "#ffffff", 16), "  " + tr("queue_send_next"))
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(lambda: self.dialog.send_next(self.printer))
        v.addWidget(self.send_btn)
        self.ready = False

    def fill(self):
        q = self.dialog.queue
        self.list.blockSignals(True)
        self.list.clear()
        for j in q.for_printer(self.printer.id):
            mark = {"waiting": "⏳", "sending": "⇪", "sent": "✓", "failed": "⚠"}.get(j.state, "")
            text = f"{mark} {j.name}"
            if j.time_s:
                text += f"  ·  {fmt_time(j.time_s)}"
            if j.material:
                text += f"  ·  {j.material}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, j.id)
            it.setToolTip(j.path + (f"\n{j.note}" if j.note else ""))
            if j.state != "waiting":
                it.setFlags(it.flags() & ~Qt.ItemIsDragEnabled)
            self.list.addItem(it)
        self.list.blockSignals(False)
        waiting = q.for_printer(self.printer.id, waiting_only=True)
        self.total.setText(tr("queue_total").format(len(waiting), fmt_time(q.waiting_time(self.printer.id))))
        self.send_btn.setEnabled(bool(waiting))         # a folder printer just receives the file (no start)

    def set_status(self, st: dict | None, err: str = ""):
        if err:
            self.status.setText(tr("queue_status_off").format(err[:80]))
            self.ready = False
            return
        state = str((st or {}).get("state", "?"))
        pct = (st or {}).get("percent")
        file = (st or {}).get("file") or ""
        text = tr("queue_status").format(state) + (f" · {pct:.0f}%" if isinstance(pct, (int, float)) else "")
        if file:
            text += f"\n{file}"
        self.ready = print_queue.printer_ready(st)
        if self.ready:
            text += "\n" + tr("queue_ready")
        self.status.setText(text)

    def _reordered(self):
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self.dialog.queue.reorder(self.printer.id, ids)

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        job = self.dialog.queue.get(it.data(Qt.UserRole))
        if job is None:
            return
        menu = QMenu(self)
        send = menu.addAction(theme.icon("play"), tr("queue_send_now"))
        send.setEnabled(job.state in ("waiting", "failed"))
        move = menu.addMenu(tr("queue_move_to"))
        for p in self.dialog.printers:
            if p.id != self.printer.id:
                move.addAction(p.name or p.host, lambda pid=p.id: self.dialog.move(job.id, pid))
        again = menu.addAction(tr("queue_requeue"))
        again.setEnabled(job.state in ("sent", "failed"))
        menu.addSeparator()
        remove = menu.addAction(theme.icon("trash", theme.DANGER), tr("queue_remove"))
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen == send:
            self.dialog.send_job(self.printer, job)
        elif chosen == again:
            self.dialog.queue.mark(job.id, "waiting")
            self.dialog.refresh()
        elif chosen == remove:
            self.dialog.queue.remove(job.id)
            self.dialog.refresh()


class QueueDialog(QDialog):
    def __init__(self, main, queue: print_queue.Queue | None = None):
        super().__init__(main)
        self.main = main
        self.queue = queue or print_queue.Queue.shared()
        self.printers = network.load_printers()
        self.setWindowTitle(tr("queue_title"))
        self.resize(min(1300, 320 * max(1, len(self.printers)) + 60), 620)
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.add_btn = QPushButton(theme.icon("plus"), "  " + tr("queue_add_plate"))
        self.add_btn.clicked.connect(self._add_plate)
        bar.addWidget(self.add_btn)
        refresh = QPushButton(theme.icon("reset"), "  " + tr("queue_refresh"))
        refresh.clicked.connect(self.poll)
        bar.addWidget(refresh)
        clear = QPushButton(tr("queue_clear_sent"))
        clear.clicked.connect(lambda: (self.queue.clear_sent(), self.refresh()))
        bar.addWidget(clear)
        bar.addStretch(1)
        manage = QPushButton(tr("net_title") + "…")
        manage.clicked.connect(self._manage)
        bar.addWidget(manage)
        v.addLayout(bar)
        self.columns: dict[str, PrinterColumn] = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box = QWidget()
        self.row = QHBoxLayout(box)
        scroll.setWidget(box)
        v.addWidget(scroll, 1)
        self.empty = QLabel(tr("queue_no_printers"))
        self.empty.setWordWrap(True)
        v.addWidget(self.empty)
        self._build()
        self._poll_task = None
        self._send_task = None
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(200, self.poll)

    def _build(self):
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.columns = {}
        for p in self.printers:
            col = PrinterColumn(self, p)
            self.columns[p.id] = col
            self.row.addWidget(col)
        self.row.addStretch(1)
        self.empty.setVisible(not self.printers)
        self.add_btn.setEnabled(bool(self.printers))
        self.refresh()

    def refresh(self):
        for col in self.columns.values():
            col.fill()

    def _manage(self):
        self.main.network_printers()
        self.printers = network.load_printers()
        self._build()

    # ------------------------------------------------------------- status
    def poll(self):
        if self._poll_task is not None and self._poll_task.isRunning() or not self.printers:
            return
        printers = list(self.printers)

        def work(progress, cancelled):
            out = {}
            for p in printers:
                if p.kind == "folder":
                    out[p.id] = ({"state": "ready"}, "")
                    continue
                try:
                    out[p.id] = (network.printer_status(p), "")
                except Exception as exc:          # one printer off never stops the others
                    out[p.id] = (None, str(exc) or type(exc).__name__)
            return out
        self._poll_task = Task(work, self)
        self._poll_task.succeeded.connect(self._got_status)
        self._poll_task.start()

    def _got_status(self, result):
        for pid, (st, err) in result.items():
            col = self.columns.get(pid)
            if col is not None:
                col.set_status(st, err)

    # ------------------------------------------------------------- jobs
    def move(self, job_id: str, printer_id: str):
        self.queue.move_to(job_id, printer_id)
        self.refresh()

    def send_next(self, printer):
        job = self.queue.next_for(printer.id)
        if job is not None:
            self.send_job(printer, job)

    def send_job(self, printer, job, confirm: bool = True):
        if self._send_task is not None and self._send_task.isRunning():
            return
        if not os.path.isfile(job.path):
            self.queue.mark(job.id, "failed", tr("queue_missing_file"))
            self.refresh()
            return
        col = self.columns.get(printer.id)
        if confirm:
            text = tr("queue_confirm").format(job.name, printer.name or printer.host)
            if col is not None and not col.ready:
                text += "\n\n" + tr("queue_not_ready")
            if QMessageBox.question(self, tr("queue_title"), text) != QMessageBox.Yes:
                return
        self.queue.mark(job.id, "sending")
        self.refresh()

        def work(progress, cancelled):
            return network.send(printer, job.path, os.path.basename(job.path), start=network.can_start(printer.kind),
                                progress=lambda f: progress("upload", f), cancelled=cancelled)

        def done(name):
            self.queue.mark(job.id, "sent", tr("queue_sent_note").format(printer.name or printer.host))
            self.refresh()
            self.poll()

        def failed(msg):
            # cancelled (no message): back in line as it was; a real failure says why
            self.queue.mark(job.id, "failed" if msg else "waiting", msg)
            self.refresh()
        self._send_task = Task(work, self)
        self._send_task.succeeded.connect(done)
        self._send_task.failed.connect(failed)
        self._send_task.start()

    def _add_plate(self):
        if not self.printers:
            return
        menu = QMenu(self)
        for p in self.printers:
            menu.addAction(p.name or p.host, lambda pid=p.id: self.main.add_plate_to_queue(pid, self))
        menu.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)
