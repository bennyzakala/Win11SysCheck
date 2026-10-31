"""Remote control window: link this computer to a Google account and let the owner watch and control the
network printers from a phone or browser (see ``unislicer.remote``)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout)

from .. import remote
from . import i18n, theme
from .i18n import tr


def _lang() -> str:
    return "he" if i18n.LANG == "he" else "en"


class RemoteController(QObject):
    """Owns the background agent for the whole program; its signals arrive on the GUI thread."""
    stateChanged = Signal(str, str)
    claimRequested = Signal(str)
    commandDone = Signal(object, bool, str)

    def __init__(self, folder: str | None = None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.agent: remote.Agent | None = None
        self.claimRequested.connect(self._ask_claim, Qt.QueuedConnection)
        self.window = parent

    # ---- configuration
    def state(self) -> dict:
        return remote.load_state(self.folder)

    def config(self) -> dict | None:
        cfg = self.state().get("config")
        if isinstance(cfg, dict) and cfg.get("apiKey") and cfg.get("databaseURL"):
            return cfg
        return remote.builtin_config()

    def page_url(self) -> str:
        st = self.state()
        if st.get("page_url"):
            return st["page_url"]
        cfg = self.config() or {}
        if cfg.get("pageURL"):
            return cfg["pageURL"]
        return f"https://{cfg['projectId']}.web.app" if cfg.get("projectId") else ""

    def save(self, **values):
        st = self.state()
        st.update(values)
        remote.save_state(st, self.folder)

    # ---- agent
    def start(self):
        cfg = self.config()
        if cfg is None:
            return False
        if self.agent is not None and self.agent.running():
            return True
        self.agent = remote.Agent(cfg, self.folder,
                                  on_state=lambda s, d: self.stateChanged.emit(s, d),
                                  on_claim=lambda email: self.claimRequested.emit(email),
                                  on_command=lambda c, ok, m: self.commandDone.emit(c, ok, m))
        self.save(enabled=True)
        self.agent.start()
        return True

    def stop(self, remember: bool = True):
        if self.agent is not None:
            self.agent.stop(1.5)
        if remember:
            self.save(enabled=False)
        self.stateChanged.emit("idle", "")

    def unlink(self):
        if self.agent is None:
            cfg = self.config()
            if cfg is None:
                return
            self.agent = remote.Agent(cfg, self.folder)
            try:
                self.agent.fb = remote.Firebase(cfg, self.state().get("refresh_token", ""))
                self.agent.fb.sign_in()
            except remote.RemoteError:
                pass
        self.agent.unlink()
        self.save(enabled=False)
        self.stateChanged.emit("idle", "")

    def start_if_enabled(self):
        if self.state().get("enabled") and self.config() is not None:
            self.start()

    def shutdown(self):
        if self.agent is not None and self.agent.running():
            self.agent.stop(1.0)

    def current(self) -> tuple[str, str]:
        if self.agent is None or not self.agent.running():
            return "idle", ""
        return self.agent.state, self.agent.detail

    def _ask_claim(self, email: str):
        if self.agent is None:
            return
        box = QMessageBox(QMessageBox.Question, tr("remote_title"), tr("remote_claim").format(email or "?"),
                          QMessageBox.Yes | QMessageBox.No, self.window)
        box.button(QMessageBox.Yes).setText(tr("remote_allow"))
        box.button(QMessageBox.No).setText(tr("remote_deny"))
        box.setDefaultButton(QMessageBox.No)
        self.agent.answer_claim(box.exec() == QMessageBox.Yes)


class RemoteDialog(QDialog):
    def __init__(self, ctl: RemoteController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("remote_title"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(tr("remote_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.code = QLabel("")
        f = QFont(self.font())
        f.setPointSize(max(f.pointSize(), 10) * 2 + 4)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self.code.setFont(f)
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.code)

        row = QHBoxLayout()
        self.on_btn = QPushButton(theme.icon("wifi", theme.TEXT, 16), tr("remote_on"))
        self.on_btn.clicked.connect(self._toggle)
        self.unlink_btn = QPushButton(tr("remote_unlink"))
        self.unlink_btn.clicked.connect(self._unlink)
        self.page_btn = QPushButton(tr("remote_open_page"))
        self.page_btn.clicked.connect(self._open_page)
        row.addWidget(self.on_btn)
        row.addWidget(self.unlink_btn)
        row.addStretch(1)
        row.addWidget(self.page_btn)
        lay.addLayout(row)

        more = QToolButton()
        more.setText(tr("remote_setup"))
        more.setCheckable(True)
        lay.addWidget(more)
        self.setup = QVBoxLayout()
        steps = QLabel(tr("remote_steps"))
        steps.setWordWrap(True)
        steps.setOpenExternalLinks(True)
        steps.setTextFormat(Qt.RichText)
        self.cfg_edit = QPlainTextEdit()
        self.cfg_edit.setPlaceholderText("const firebaseConfig = {\n  apiKey: \"…\",\n  authDomain: \"…\",\n"
                                         "  databaseURL: \"https://…firebasedatabase.app\",\n  projectId: \"…\"\n};")
        self.cfg_edit.setMinimumHeight(120)
        cfg = (ctl.state().get("config") or {})
        if cfg:
            self.cfg_edit.setPlainText("\n".join(f'{k}: "{v}"' for k, v in cfg.items()).join(("{\n", "\n}")))
        self.url_edit = QLineEdit(ctl.state().get("page_url", ""))
        self.url_edit.setPlaceholderText(ctl.page_url() or "https://…")
        save = QPushButton(tr("remote_save"))
        save.clicked.connect(self._save_config)
        self.cfg_msg = QLabel("")
        self.cfg_msg.setWordWrap(True)
        for w in (steps, self.cfg_edit, QLabel(tr("remote_page_url")), self.url_edit, save, self.cfg_msg):
            w.setVisible(False)
            self.setup.addWidget(w)
        self._setup_widgets = [steps, self.cfg_edit, self.setup.itemAt(2).widget(), self.url_edit, save, self.cfg_msg]
        more.toggled.connect(lambda on: [w.setVisible(on) for w in self._setup_widgets])
        lay.addLayout(self.setup)
        if ctl.config() is None:
            more.setChecked(True)

        close = QPushButton(tr("close"))
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)
        ctl.stateChanged.connect(self._show)
        self._show(*ctl.current())

    def _show(self, state: str, detail: str):
        lang = _lang()
        self.code.setText("")
        running = state not in ("idle",)
        configured = self.ctl.config() is not None
        if not configured:
            text = tr("remote_need_setup")
        elif state == "idle":
            linked = self.ctl.state().get("owner_email")
            text = tr("remote_off_linked").format(linked) if linked else tr("remote_off")
        elif state == "connecting":
            text = tr("remote_connecting")
        elif state == "pairing":
            text = tr("remote_pairing").format(self.ctl.page_url() or tr("remote_page"))
            self.code.setText(remote.pretty_code(detail))
        elif state == "confirm":
            text = tr("remote_confirming").format(detail)
        elif state == "linked":
            text = tr("remote_linked").format(detail)
        elif state == "unlinked":
            text = tr("remote_was_unlinked")
        else:
            text = tr("remote_error") + "\n" + remote.explain(detail, lang)
        self.status.setText(text)
        self.on_btn.setEnabled(configured)
        self.on_btn.setText(tr("remote_off_btn") if running else tr("remote_on"))
        self.unlink_btn.setEnabled(configured and bool(self.ctl.state().get("owner")))
        self.page_btn.setEnabled(bool(self.ctl.page_url()))

    def _toggle(self):
        if self.ctl.agent is not None and self.ctl.agent.running():
            self.ctl.stop()
        else:
            self.ctl.start()

    def _unlink(self):
        email = self.ctl.state().get("owner_email", "")
        if QMessageBox.question(self, tr("remote_title"), tr("remote_unlink_q").format(email)) != QMessageBox.Yes:
            return
        self.ctl.unlink()

    def _open_page(self):
        url = self.ctl.page_url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _save_config(self):
        text = self.cfg_edit.toPlainText().strip()
        try:
            cfg = remote.parse_config(text) if text else None
        except remote.RemoteError as e:
            self.cfg_msg.setText(remote.explain(str(e), _lang()))
            self.cfg_msg.setStyleSheet(f"color:{theme.WARNING}")
            return
        restart = self.ctl.agent is not None and self.ctl.agent.running()
        if restart:
            self.ctl.stop(remember=False)
        old = self.ctl.state().get("config")
        values = {"config": cfg, "page_url": self.url_edit.text().strip()}
        if cfg != old:                       # another project: the old device account means nothing there
            values.update({"refresh_token": "", "uid": "", "owner": "", "owner_email": ""})
        self.ctl.save(**values)
        self.cfg_msg.setStyleSheet("")
        self.cfg_msg.setText(tr("remote_saved"))
        if cfg or remote.builtin_config():
            self.ctl.start()
        self._show(*self.ctl.current())
