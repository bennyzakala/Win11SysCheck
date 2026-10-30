"""Floating panel for the learning assistant: suggestions, print feedback and what it has learned."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from ..assistant import Suggestion
from ..settings import SCHEMA_BY_KEY
from . import i18n, theme
from .i18n import tr

KIND_STYLE = {
    "problem": ("info", theme.DANGER),
    "improve": ("sparkle", theme.ACCENT),
    "habit": ("save", theme.SUCCESS),
    "tip": ("info", theme.TEXT_DIM),
    "learned": ("layers", theme.ACCENT),
}
PRINT_OUTCOMES = ("success", "fail_supports", "fail_suction", "fail_adhesion", "fail_other")


def suggestion_text(s: Suggestion) -> str:
    args = list(s.args)
    if s.text == "sg_make_default":
        key, value = args
        meta = SCHEMA_BY_KEY[key]
        shown = value
        if meta.options:
            shown = next((i18n.option_label(o) for o in meta.options if o[0] == value), value)
        elif isinstance(value, bool):
            shown = tr("on") if value else tr("off")
        args = [i18n.label(meta), f"{shown}{(' ' + meta.unit) if meta.unit else ''}"]
    elif s.text == "sg_shortcut":
        args = [tr(f"action_{args[0]}"), args[1]]
    return tr(s.text).format(*args)


class AssistantPanel(QFrame):
    applyRequested = Signal(object)        # Suggestion
    dismissed = Signal(object)             # Suggestion
    printReported = Signal(str)            # outcome
    learningToggled = Signal(bool)
    resetRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(f"QFrame#Card {{ background: {theme.overlay(0.97)}; }}")
        self.setFixedWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon("sparkle", theme.ACCENT, 18).pixmap(18, 18))
        head.addWidget(ic)
        t = QLabel(tr("assistant"))
        t.setStyleSheet("font-weight: 700; font-size: 14px;")
        head.addWidget(t, 1)
        close = QToolButton()
        close.setIcon(theme.icon("x", theme.TEXT_DIM, 16))
        close.clicked.connect(self.closeRequested.emit)
        head.addWidget(close)
        lay.addLayout(head)

        self.feedback = QFrame()
        self.feedback.setStyleSheet(f"QFrame {{ background: {theme.SURFACE_2}; border-radius: 10px; }}")
        fl = QVBoxLayout(self.feedback)
        fl.setContentsMargins(10, 8, 10, 10)
        q = QLabel(tr("print_feedback_q"))
        q.setWordWrap(True)
        q.setStyleSheet("font-weight: 600;")
        fl.addWidget(q)
        grid = QGridLayout()
        grid.setSpacing(6)
        for k, outcome in enumerate(PRINT_OUTCOMES):
            b = QPushButton(tr(f"outcome_{outcome}"))
            b.setCursor(Qt.PointingHandCursor)
            if outcome == "success":
                b.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: 600;")
            b.clicked.connect(lambda _=False, o=outcome: self._report(o))
            grid.addWidget(b, k // 2, k % 2)
        fl.addLayout(grid)
        self.feedback.hide()
        lay.addWidget(self.feedback)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMaximumHeight(420)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        lay.addWidget(self.scroll)
        self.empty = QLabel(tr("assistant_empty"))
        self.empty.setObjectName("Faint")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(sep)
        self.learned = QLabel("")
        self.learned.setObjectName("Faint")
        self.learned.setWordWrap(True)
        lay.addWidget(self.learned)
        foot = QHBoxLayout()
        self.toggle = QCheckBox(tr("learning_on"))
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self.learningToggled.emit)
        foot.addWidget(self.toggle, 1)
        reset = QPushButton(tr("learning_reset"))
        reset.clicked.connect(self.resetRequested.emit)
        foot.addWidget(reset)
        lay.addLayout(foot)
        privacy = QLabel(tr("learning_privacy"))
        privacy.setObjectName("Faint")
        privacy.setWordWrap(True)
        lay.addWidget(privacy)

    def _report(self, outcome: str):
        self.feedback.hide()
        self.printReported.emit(outcome)

    def ask_print_feedback(self):
        self.feedback.show()

    def set_learning(self, enabled: bool, summary: dict):
        self.toggle.blockSignals(True)
        self.toggle.setChecked(enabled)
        self.toggle.blockSignals(False)
        ex = summary["orient_examples"]
        dens = summary["support_density"]
        lines = [tr("learned_orient").format(ex.get("sla", 0) + ex.get("fdm", 0))]
        if summary["support_sessions"]:
            lines.append(tr("learned_supports").format(f"{dens * 100:+.0f}%", f"{summary['angle_offset']:+.1f}°"))
        if summary.get("layout_samples") or summary.get("layout_runs"):
            lines.append(tr("learned_layout").format(summary.get("layout_runs", 0), summary.get("layout_samples", 0)))
        prints = summary["prints"]
        total = sum(prints.values())
        if total:
            lines.append(tr("learned_prints").format(total, prints.get("success", 0)))
        self.learned.setText("\n".join(lines))

    def set_suggestions(self, items: list[Suggestion]):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(6)
        for s in items:
            v.addWidget(self._row(s))
        v.addStretch(1)
        theme.replace_scroll_widget(self.scroll, box)
        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)

    def _row(self, s: Suggestion) -> QWidget:
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background: {theme.SURFACE_2}; border-radius: 10px; }}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 6, 8)
        h.setSpacing(8)
        icon_name, color = KIND_STYLE.get(s.kind, ("info", theme.TEXT_DIM))
        ic = QLabel()
        ic.setPixmap(theme.icon(icon_name, color, 16).pixmap(16, 16))
        h.addWidget(ic, 0, Qt.AlignTop)
        text = QLabel(suggestion_text(s))
        text.setWordWrap(True)
        h.addWidget(text, 1)
        col = QVBoxLayout()
        col.setSpacing(4)
        if s.action:
            apply = QPushButton(tr("crash_open") if s.action == "show_crash" else tr("apply"))
            apply.setObjectName("Primary")
            apply.setStyleSheet("padding: 4px 10px; font-size: 12px;")
            apply.setCursor(Qt.PointingHandCursor)
            apply.clicked.connect(lambda _=False, s=s: self.applyRequested.emit(s))
            col.addWidget(apply)
        dismiss = QToolButton()
        dismiss.setIcon(theme.icon("x", theme.TEXT_FAINT, 14))
        dismiss.setIconSize(QSize(14, 14))
        dismiss.setToolTip(tr("dismiss"))
        dismiss.clicked.connect(lambda _=False, s=s: self.dismissed.emit(s))
        col.addWidget(dismiss, 0, Qt.AlignRight)
        col.addStretch(1)
        h.addLayout(col)
        return row
