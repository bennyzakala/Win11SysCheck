"""Chat with Claude about the current plate; proposed setting changes appear as buttons."""
from __future__ import annotations

import html
import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

from . import theme
from .i18n import label, tr
from .worker import Task

KEY_SETTING = "claude_api_key"

ERRORS = {"auth": "claude_err_auth", "permission": "claude_err_auth", "rate": "claude_err_rate",
          "network": "claude_err_network", "refusal": "claude_err_refusal", "credit": "claude_err_credit"}


def stored_key() -> str:
    """The user's API key: the one saved in UniSlicer, else ANTHROPIC_API_KEY from the environment."""
    return str(QSettings("UniSlicer", "UniSlicer").value(KEY_SETTING, "") or "") or os.environ.get("ANTHROPIC_API_KEY", "")


class ClaudeDialog(QDialog):
    def __init__(self, context_fn, apply_fn, parent=None):
        """``context_fn()`` returns the plate summary text; ``apply_fn(key, value)`` applies one proposed change."""
        super().__init__(parent)
        from ..claude_agent import ClaudeAssistant
        self.setWindowTitle(tr("claude_title"))
        self.resize(560, 640)
        self._context_fn = context_fn
        self._apply_fn = apply_fn
        self._task = None
        self.assistant = ClaudeAssistant(stored_key())
        lay = QVBoxLayout(self)

        # API key (the user's own; saved on this computer only)
        self.key_row = QWidget()
        kr = QHBoxLayout(self.key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        kl = QLabel(tr("claude_key"))
        kl.setToolTip(tr("claude_key_tip"))
        kr.addWidget(kl)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-ant-…")
        kr.addWidget(self.key_edit, 1)
        save = QPushButton(tr("claude_key_save"))
        save.clicked.connect(self._save_key)
        kr.addWidget(save)
        lay.addWidget(self.key_row)
        self.key_note = QLabel(tr("claude_key_tip"))
        self.key_note.setObjectName("Faint")
        self.key_note.setWordWrap(True)
        lay.addWidget(self.key_note)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)
        self.proposals = QVBoxLayout()
        self.proposals.setSpacing(4)
        lay.addLayout(self.proposals)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("claude_placeholder"))
        self.input.returnPressed.connect(self.send)
        row.addWidget(self.input, 1)
        self.send_btn = QPushButton(theme.icon("sparkle", "#ffffff", 16), " " + tr("claude_send"))
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        foot = QHBoxLayout()
        new = QPushButton(tr("claude_new"))
        new.clicked.connect(self.new_conversation)
        foot.addWidget(new)
        change = QPushButton(tr("claude_key_change"))
        change.clicked.connect(lambda: self._show_key(True))
        foot.addWidget(change)
        foot.addStretch(1)
        privacy = QLabel(tr("claude_privacy"))
        privacy.setObjectName("Faint")
        privacy.setWordWrap(True)
        lay.addLayout(foot)
        lay.addWidget(privacy)
        self._html = []
        self._show_key(not stored_key())

    # ------------------------------------------------------------------ key
    def _show_key(self, show: bool):
        self.key_row.setVisible(show)
        self.key_note.setVisible(show)
        if show:
            self.key_edit.setFocus()
        else:
            self.input.setFocus()

    def _save_key(self):
        key = self.key_edit.text().strip()
        if not key:
            return
        QSettings("UniSlicer", "UniSlicer").setValue(KEY_SETTING, key)
        self.key_edit.clear()
        from ..claude_agent import ClaudeAssistant
        old = self.assistant.messages
        self.assistant = ClaudeAssistant(key)
        self.assistant.messages = old
        self._show_key(False)
        self._add("note", tr("claude_key_saved"))

    # ------------------------------------------------------------------ chat
    def _add(self, who: str, text: str):
        body = html.escape(text).replace("\n", "<br>")
        if who == "user":
            self._html.append(f"<p><b>{html.escape(tr('claude_you'))}:</b> {body}</p>")
        elif who == "claude":
            self._html.append(f"<p><b style='color:{theme.ACCENT}'>Claude:</b> {body}</p>")
        else:
            self._html.append(f"<p style='color:{theme.TEXT_DIM}'><i>{body}</i></p>")
        rtl = self.layoutDirection() == Qt.RightToLeft
        self.view.setHtml(f"<div dir='{'rtl' if rtl else 'ltr'}'>" + "".join(self._html) + "</div>")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def new_conversation(self):
        if self._task is not None and self._task.isRunning():
            return
        self.assistant.reset()
        self._html = []
        self.view.clear()
        self._clear_proposals()

    def busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def send(self, question: str | None = None):
        question = (question if isinstance(question, str) else self.input.text()).strip()
        if not question or self.busy():
            return
        if not stored_key():
            self._show_key(True)
            self._add("note", tr("claude_err_nokey"))
            return
        self.input.clear()
        self._add("user", question)
        self._add("note", tr("claude_thinking"))
        self.send_btn.setEnabled(False)
        try:
            context = self._context_fn() or ""
        except Exception:
            context = ""
        assistant = self.assistant
        self._task = Task(lambda progress, cancelled: assistant.ask(question, context), self)
        self._task.succeeded.connect(self._on_reply)
        self._task.failed.connect(lambda msg: self._on_reply(None, msg))
        self._task.start()

    def _on_reply(self, reply, failure: str = ""):
        self.send_btn.setEnabled(True)
        if self._html and tr("claude_thinking") in self._html[-1]:
            self._html.pop()
        if reply is None:
            self._add("note", tr("claude_err_other").format(failure))
            return
        if reply.error:
            code = reply.error.split(":")[0]
            msg = tr(ERRORS[code]) if code in ERRORS else tr("claude_err_other").format(reply.error)
            self._add("note", msg)
            if code == "auth":
                self._show_key(True)
        if reply.text:
            self._add("claude", reply.text)
        self._show_proposals(reply.proposals)

    # ------------------------------------------------------------------ proposals
    def _clear_proposals(self):
        while self.proposals.count():
            item = self.proposals.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _show_proposals(self, proposals):
        self._clear_proposals()
        if not proposals:
            return
        from ..settings import SCHEMA_BY_KEY
        head = QLabel(tr("claude_proposals"))
        head.setStyleSheet("font-weight: 600;")
        self.proposals.addWidget(head)
        for p in proposals:
            meta = SCHEMA_BY_KEY.get(p.key)
            name = label(meta) if meta is not None else p.key
            unit = f" {meta.unit}" if meta is not None and meta.unit else ""
            b = QPushButton(f"{tr('claude_apply')}: {name} = {p.value}{unit}")
            b.setToolTip(p.reason)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, prop=p, btn=b: self._apply(prop, btn))
            self.proposals.addWidget(b)
            if p.reason:
                why = QLabel(p.reason)
                why.setObjectName("Faint")
                why.setWordWrap(True)
                self.proposals.addWidget(why)

    def _apply(self, p, button):
        try:
            self._apply_fn(p.key, p.value)
        except Exception as exc:                     # a bad value must not crash the window
            self._add("note", tr("claude_err_other").format(exc))
            return
        button.setEnabled(False)
        button.setText("✓ " + button.text())
