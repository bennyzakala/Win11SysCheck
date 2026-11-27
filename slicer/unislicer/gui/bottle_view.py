"""The resin's bottle, large: a click on the bottle in the Material card shows it at a size its label can be read
(the maker's photo, or the lab's own). A click anywhere or Esc closes it."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout

from .i18n import tr


class BottleView(QDialog):
    def __init__(self, title: str, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("BottleView")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        name = QLabel(title)
        f = QFont(name.font())
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        name.setFont(f)
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        lay.addWidget(name)
        src = QPixmap(path)
        screen = (parent.screen() if parent is not None else None) or QApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else None
        max_h = min(720, int(room.height() * 0.75)) if room is not None else 720
        max_w = min(620, int(room.width() * 0.8)) if room is not None else 620
        if not src.isNull() and (src.height() > max_h or src.width() > max_w):
            src = src.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.picture = QLabel()
        self.picture.setPixmap(src)
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setStyleSheet("background: #ffffff; border-radius: 10px; padding: 8px;")
        lay.addWidget(self.picture, 1)
        hint = QLabel(tr("bottle_close_hint"))
        hint.setObjectName("Faint")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

    def mousePressEvent(self, e):
        self.accept()
