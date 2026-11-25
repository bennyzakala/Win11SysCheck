"""Pictures for the support head styles (Adaptive / Vertical / Upright), drawn here so they follow the theme:
a sloped surface at the top and one support under it, leaning straight, curving, or straight down."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

STYLES = ("adaptive", "vertical", "upright")


def picture(style: str, size: int = 64, support: str = "#4f8de8", surface: str = "#8a8f98") -> QPixmap:
    """The support head style as a small drawing."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    # the sloped surface (the underside of a part), top left to the right
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(surface)))
    p.drawPolygon(QPolygonF([QPointF(6 * s, 4 * s), QPointF(60 * s, 4 * s), QPointF(60 * s, 10 * s),
                             QPointF(6 * s, 26 * s)]))
    contact = QPointF(24 * s, 20.7 * s)                  # on the sloped edge
    pillar_x = 34 * s if style != "upright" else contact.x()
    top = 30 * s if style != "upright" else contact.y() + 1.5 * s
    col = QColor(support)
    pen = QPen(col, 4.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if style == "adaptive":                              # a straight arm, square to the surface
        p.drawLine(contact, QPointF(pillar_x, top))
    elif style == "vertical":                            # leaves the surface square-on and curves down
        path = QPainterPath(contact)
        path.cubicTo(QPointF(28 * s, 26 * s), QPointF(pillar_x, 24 * s), QPointF(pillar_x, top + 4 * s))
        p.drawPath(path)
    p.drawLine(QPointF(pillar_x, top), QPointF(pillar_x, 52 * s))
    # the foot on the plate
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(col))
    p.drawPolygon(QPolygonF([QPointF(pillar_x - 2 * s, 50 * s), QPointF(pillar_x + 2 * s, 50 * s),
                             QPointF(pillar_x + 7 * s, 58 * s), QPointF(pillar_x - 7 * s, 58 * s)]))
    p.setBrush(QBrush(QColor(surface).darker(115)))
    p.drawRect(QRectF(4 * s, 58 * s, 56 * s, 3 * s))    # the plate
    p.end()
    return pm


class StylePicker(QWidget):
    """Three picture buttons in place of a drop-down (the setting row treats it like one: currentData,
    findData, setCurrentIndex and currentIndexChanged)."""
    currentIndexChanged = Signal(int)

    def __init__(self, options, value, support: str = "#4f8de8", surface: str = "#8a8f98", parent=None):
        super().__init__(parent)
        from .i18n import option_label
        self._data = [o[0] for o in options]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        for i, opt in enumerate(options):
            b = QToolButton()
            b.setObjectName("StylePick")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIcon(QIcon(picture(opt[0], 96, support, surface)))
            b.setIconSize(picture(opt[0], 48).size())
            text = option_label(opt)
            b.setText(text.split(" (")[0])
            b.setToolTip(text)
            b.setAutoRaise(False)
            b.setMinimumWidth(78)
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
        self.setCurrentIndex(max(0, self.findData(value)))
        self.group.idClicked.connect(self._clicked)

    def _clicked(self, i: int):
        self.currentIndexChanged.emit(i)

    def findData(self, value) -> int:
        return self._data.index(value) if value in self._data else -1

    def currentIndex(self) -> int:
        return max(0, self.group.checkedId())

    def currentData(self):
        return self._data[self.currentIndex()]

    def itemData(self, i: int):
        return self._data[i]

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)
