"""One-click plate: recognise each part's type, arrange the plate, generate the supports - then a summary to
check before slicing, with a fix next to every warning. The orientation of a part changes only when the user
clicks its fix ("Turn over"); nothing is sent to a printer without a click."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .i18n import tr

TYPE_KEYS = ("cb", "guide", "model", "model_u", "splint_h", "splint_v", "denture")


def type_label(key: str) -> str:
    from ..settings import SCHEMA_BY_KEY
    from .i18n import pick
    for o in SCHEMA_BY_KEY["sla_model_type"].options:
        if o[0] == key:
            return pick(o[1], o[2])
    return key


class PlateSummary(QDialog):
    """``facts`` is what MainWindow.plate_facts() found; ``actions`` maps a fix name to a callable
    (the dialog closes and the window runs it, then shows a fresh summary)."""

    def __init__(self, facts: dict, actions: dict, can_send: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("ocp_title"))
        self.setMinimumWidth(660)
        self.choice = None                   # "slice", "send" or None (back to the plate)
        self.actions = actions
        lay = QVBoxLayout(self)
        head = QLabel(tr("ocp_title"))
        head.setStyleSheet("font-size: 17px; font-weight: 700;")
        lay.addWidget(head)
        sub = QLabel(tr("ocp_sub"))
        sub.setObjectName("Faint")
        lay.addWidget(sub)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        lay.addLayout(grid)
        self.rows = []
        r = 0

        def row(step, text, ok=True, fix=None):
            nonlocal r
            name = QLabel(step)
            name.setStyleSheet("font-weight: 600;")
            val = QLabel(("✓ " if ok else "⚠ ") + text)
            val.setWordWrap(True)
            val.setStyleSheet(f"color: {theme.SUCCESS if ok else theme.WARNING};")
            val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            grid.addWidget(name, r, 0, Qt.AlignTop)
            grid.addWidget(val, r, 1)
            if fix is not None:
                grid.addWidget(fix, r, 2, Qt.AlignTop)
            self.rows.append((step, ok, text))
            r += 1

        # types
        t = facts["types"]
        parts = ", ".join(f"{n} {type_label(k)}" for k, n in sorted(t["counts"].items()) if n)
        row(tr("ocp_types"), tr("ocp_types_ok").format(t["recognised"], parts or "-"), True)
        for uid, name in t["unknown"]:
            box = QComboBox()
            box.addItem(tr("ocp_choose_type"), None)
            for k in TYPE_KEYS:
                box.addItem(type_label(k), k)
            box.activated.connect(lambda i, uid=uid, box=box: box.itemData(i) and self._fix("type", uid, box.itemData(i)))
            row("", tr("ocp_type_unknown").format(name), False, box)
        # placement
        p = facts["placement"]
        if p["outside"]:
            row(tr("ocp_place"), tr("ocp_outside").format(", ".join(p["outside"][:3])), False,
                self._button(tr("ocp_rearrange"), "arrange"))
        if p["collisions"]:
            row(tr("ocp_place") if not p["outside"] else "", tr("ocp_collide").format(p["collisions"]), False,
                self._button(tr("ocp_rearrange"), "arrange"))
        if not p["outside"] and not p["collisions"]:
            row(tr("ocp_place"), tr("ocp_place_ok").format(p["count"], p["used_pct"]), True)
        # supports
        s = facts["supports"]
        text = tr("ocp_supports_ok").format(s["points"], s["models"])
        if s["direct"]:
            text += " · " + tr("ocp_on_plate").format(s["direct"])
        if s["codes"]:
            text += " · " + tr("ocp_codes").format(s["codes"])
        row(tr("ocp_supports"), text, True)
        for uid, name in s["unsupported"]:
            row("", tr("ocp_no_supports").format(name), False, self._button(tr("ocp_generate"), "supports", uid))
        for uid, name in s["fit_down"]:
            row(tr("ocp_warning"), tr("ocp_fit_down").format(name), False, self._button(tr("ocp_turn_over"), "turn", uid))
        # print check, estimate
        row(tr("ocp_check"), tr("ocp_check_text"), True)
        e = facts["estimate"]
        if e is not None:
            row(tr("ocp_estimate"), tr("ocp_estimate_text").format(e["time"], e["ml"], e["material"]), True)
        grid.setColumnStretch(1, 1)
        lay.addSpacing(10)
        # buttons
        bar = QHBoxLayout()
        back = QPushButton(tr("ocp_back"))
        back.clicked.connect(self.reject)
        bar.addWidget(back)
        bar.addStretch(1)
        only = QPushButton(tr("ocp_slice_only"))
        only.clicked.connect(lambda: self._done("slice"))
        bar.addWidget(only)
        if can_send:
            send = QPushButton(tr("ocp_slice_send").format(facts.get("printer", "")))
            send.setObjectName("Primary")
            send.clicked.connect(lambda: self._done("send"))
            send.setDefault(True)
            bar.addWidget(send)
        else:
            only.setObjectName("Primary")
            only.setDefault(True)
        lay.addLayout(bar)

    def _button(self, text, action, uid=None) -> QWidget:
        b = QPushButton(text)
        b.clicked.connect(lambda: self._fix(action, uid))
        return b

    def _fix(self, action, uid=None, value=None):
        self.choice = ("fix", action, uid, value)
        self.accept()

    def _done(self, what):
        self.choice = what
        self.accept()
