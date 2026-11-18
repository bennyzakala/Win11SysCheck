"""A thin indeterminate 'activity' bar: a glowing segment slides across a faint track while the program is
busy (like an installer's progress strip). It draws on the GUI thread with a light timer, so it never touches
the speed of the work it reports."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


class MarqueeBar(QWidget):
    """Full-width, a few pixels tall. start() runs a sliding glow; stop() hides it. The colour follows the
    theme's accent, so it fits every theme."""

    SPEED = 0.011            # fraction of the way across each tick
    SEG = 0.30              # bright segment width, as a fraction of the bar

    def __init__(self, parent: QWidget | None = None, height: int = 3):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pos = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if not self.running():
            self._pos = 0.0
            self.show()
            self.raise_()
            self._timer.start()

    def stop(self) -> None:
        if self.running():
            self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._pos = (self._pos + self.SPEED) % 1.0
        self.update()

    def _accent(self) -> QColor:
        c = theme.ACCENT
        return QColor(c) if isinstance(c, str) else QColor(235, 26, 145)

    def paintEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        if w < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(255, 255, 255, 16))   # faint track
        seg = w * self.SEG
        x = self._pos * (w + seg) - seg                      # slides in from the left and off the right
        c = self._accent()
        grad = QLinearGradient(x, 0, x + seg, 0)
        grad.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 0))
        grad.setColorAt(0.5, QColor(c.red(), c.green(), c.blue(), 235))
        grad.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.fillRect(QRectF(x, 0, seg, h), grad)
        p.end()
