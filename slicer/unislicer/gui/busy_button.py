"""A playful 'busy' overlay for a button: while a long action runs, a snake with a tooth for a head - a molar
with a winking, grinning face and blushing cheeks on a gum-pink body - slithers round the button's edge, and a
new nonsense two-word phrase shows every so often ("baking clouds", "polishing crowns"...). It draws on the GUI
thread with a light timer, so it never touches the speed of the work itself.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import i18n

# nonsense two-word phrases: a verb + an unrelated thing, combined at random, per language so they follow the
# chosen interface language. Each language has ~20 x ~20 = hundreds of combinations, so no two are alike for a
# long time. Every word is a single token, so a phrase is always exactly two words.
_PHRASES = {
    "he": (["אופה", "מטגן", "מלטש", "מצייר", "סורק", "מגהץ", "קודח", "אורג", "מבשל", "מצלם",
            "שוקל", "מלחים", "מבריש", "מנגן", "שותל", "מודד", "מלקק", "מקפל", "צובע", "חופר"],
           ["עוגה", "טוסטים", "עננים", "גרביים", "מקרונים", "כתרים", "פנגווינים", "חלומות", "בועות",
            "זיקוקים", "קקטוסים", "שוקולד", "כוכבים", "רעיונות", "ג'ירפות", "דרקונים", "מלפפונים", "זחלים", "ברווזים", "פילים"]),
    "en": (["Baking", "Frying", "Polishing", "Painting", "Scanning", "Ironing", "Drilling", "Weaving", "Cooking",
            "Photographing", "Weighing", "Soldering", "Brushing", "Playing", "Planting", "Measuring", "Folding",
            "Juggling", "Tickling", "Recycling"],
           ["clouds", "toast", "cakes", "socks", "macarons", "crowns", "penguins", "dreams", "bubbles",
            "fireworks", "cacti", "chocolate", "stars", "ideas", "giraffes", "dragons", "cucumbers", "ducks",
            "waffles", "rainbows"]),
    "ru": (["Печёт", "Жарит", "Полирует", "Рисует", "Сканирует", "Гладит", "Сверлит", "Плетёт", "Варит",
            "Фотографирует", "Взвешивает", "Паяет", "Чистит", "Играет", "Сажает", "Измеряет", "Складывает",
            "Жонглирует", "Красит", "Копает"],
           ["облака", "тосты", "торты", "носки", "макаруны", "короны", "пингвинов", "мечты", "пузыри",
            "фейерверки", "кактусы", "шоколад", "звёзды", "идеи", "жирафов", "драконов", "огурцы", "уток",
            "вафли", "радуги"]),
    "ar": (["يخبز", "يقلي", "يلمّع", "يرسم", "يمسح", "يكوي", "يثقب", "ينسج", "يطبخ", "يصوّر",
            "يزن", "يلحم", "ينظف", "يعزف", "يزرع", "يقيس", "يطوي", "يتلاعب", "يصبغ", "يحفر"],
           ["غيوماً", "توستاً", "كعكاً", "جوارب", "ماكروناً", "تيجاناً", "بطاريق", "أحلاماً", "فقاعات",
            "صبّاراً", "شوكولاتة", "نجوماً", "أفكاراً", "زرافات", "تنانين", "خياراً", "بطّاً", "وافلاً", "نجوماً", "فطراً"]),
}


def random_phrase(avoid: str = "") -> str:
    lang = getattr(i18n, "LANG", "he")
    verbs, nouns = _PHRASES.get(lang, _PHRASES["en"])
    p = f"{random.choice(verbs)} {random.choice(nouns)}"
    for _ in range(8):
        if p != avoid:
            return p
        p = f"{random.choice(verbs)} {random.choice(nouns)}"
    return p


# ---------------------------------------------------------------- figure drawing helpers
def _dir(path: QPainterPath, t: float, eps: float = 0.004):
    """Unit travel direction at fraction ``t`` (robust - from two near points, not angleAtPercent)."""
    a = path.pointAtPercent((t - eps) % 1.0)
    b = path.pointAtPercent((t + eps) % 1.0)
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def _molar(s: float) -> QPainterPath:
    """A molar - a crown with two cusps on top, two roots below - about 2s wide, 2.4s tall, centred at 0."""
    path = QPainterPath()
    path.moveTo(-s, -0.35 * s)
    path.cubicTo(-s, -1.25 * s, -0.25 * s, -1.25 * s, 0, -0.85 * s)
    path.cubicTo(0.25 * s, -1.25 * s, s, -1.25 * s, s, -0.35 * s)
    path.cubicTo(s, 0.35 * s, 0.85 * s, 0.75 * s, 0.7 * s, 1.15 * s)
    path.cubicTo(0.6 * s, 1.4 * s, 0.3 * s, 1.35 * s, 0.25 * s, 1.0 * s)
    path.cubicTo(0.15 * s, 0.75 * s, -0.15 * s, 0.75 * s, -0.25 * s, 1.0 * s)
    path.cubicTo(-0.3 * s, 1.35 * s, -0.6 * s, 1.4 * s, -0.7 * s, 1.15 * s)
    path.cubicTo(-0.85 * s, 0.75 * s, -s, 0.35 * s, -s, -0.35 * s)
    path.closeSubpath()
    return path


def _eye(p: QPainter, x: float, y: float, r: float, look, shut: bool = False) -> None:
    if shut:                                   # a wink
        p.setPen(QPen(QColor(30, 30, 40), r * 0.45, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
        return
    p.setPen(QPen(QColor(30, 30, 40), r * 0.22))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QPointF(x, y), r, r)
    cx, cy = x + look[0] * r * 0.4, y + look[1] * r * 0.4
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(25, 25, 35))
    p.drawEllipse(QPointF(cx, cy), r * 0.5, r * 0.5)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QPointF(cx - r * 0.15, cy - r * 0.18), r * 0.15, r * 0.15)


BODY_COLOURS = ("#ff8fab", "#f06b8f", "#ffb3c6", "#f06b8f")     # gum pink


def fig_tooth_snake(p, path, pos, tick):
    """The snake: a wiggling gum-pink body, and a molar head with a winking, grinning, blushing face. The head
    stays upright (the face always reads) and looks the way it travels."""
    # sized to the room the path leaves to the button's edge (a low button: a smaller snake, never cut off)
    s = max(3.5, min(7.5, (path.boundingRect().top() - 0.6) / 1.3))
    k = s / 7.5
    n, gap, r0, r1 = 18, 0.0115, 4.4 * k, 1.6 * k
    pts = []
    for i in range(n):
        t = (pos - (i + 1.2) * gap) % 1.0
        pt = path.pointAtPercent(t)
        dx, dy = _dir(path, t)
        off = 1.8 * k * math.sin(tick * 0.45 - i * 0.8) * (0.3 + 0.7 * i / n)
        pts.append((pt.x() - dy * off, pt.y() + dx * off, r0 + (r1 - r0) * i / (n - 1)))
    p.setPen(Qt.NoPen)
    for i in reversed(range(n)):
        x, y, r = pts[i]
        c = QColor(BODY_COLOURS[i % len(BODY_COLOURS)])
        p.setBrush(c.darker(130))
        p.drawEllipse(QPointF(x + 0.4, y + 0.6), r, r)
        p.setBrush(c)
        p.drawEllipse(QPointF(x, y), r, r)
    head = path.pointAtPercent(pos)
    dx, dy = _dir(path, pos)
    look = (1.0, 0.0) if abs(dx) > 0.2 else (0.0, dy)
    p.save()
    p.translate(head.x(), head.y() - k + 0.8 * k * math.sin(tick * 0.5))
    if dx < -0.2:
        p.scale(-1, 1)
    g = QLinearGradient(QPointF(-s, -s), QPointF(s, s))
    g.setColorAt(0, QColor(255, 255, 255))
    g.setColorAt(1, QColor(222, 228, 236))
    p.setPen(QPen(QColor(70, 80, 100), max(0.7, s * 0.11)))
    p.setBrush(QBrush(g))
    p.drawPath(_molar(s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 230))                  # a shine
    p.drawEllipse(QPointF(-0.5 * s, -0.75 * s), 0.18 * s, 0.11 * s)
    _eye(p, -0.38 * s, -0.5 * s, 0.32 * s, look, shut=(tick // 9) % 3 == 0)
    _eye(p, 0.38 * s, -0.5 * s, 0.32 * s, look)
    p.setPen(QPen(QColor(40, 40, 50), 0.8 * k))             # an open grin with two little teeth
    p.setBrush(QColor(120, 20, 40))
    mouth = QPainterPath()
    mouth.moveTo(-0.45 * s, -0.05 * s)
    mouth.cubicTo(-0.4 * s, 0.5 * s, 0.4 * s, 0.5 * s, 0.45 * s, -0.05 * s)
    mouth.closeSubpath()
    p.drawPath(mouth)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRect(QRectF(-0.18 * s, -0.05 * s, 0.16 * s, 0.16 * s))
    p.drawRect(QRectF(0.02 * s, -0.05 * s, 0.16 * s, 0.16 * s))
    p.setBrush(QColor(255, 120, 150, 150))                  # blush
    p.drawEllipse(QPointF(-0.68 * s, -0.12 * s), 0.17 * s, 0.1 * s)
    p.drawEllipse(QPointF(0.68 * s, -0.12 * s), 0.17 * s, 0.1 * s)
    p.restore()


# the lab chose one figure: the tooth snake (it replaced ~80 others - snakes, comets, emoji...)
FIGURES = [fig_tooth_snake]


class SnakeBusy(QWidget):
    """A see-through overlay sized to its button. While running it hides the button's own text, shows a random
    phrase in its place, and runs a little figure around the button's border (a new figure each phrase);
    stopping puts the button back as it was."""

    STEP = 0.013             # how far the figure's head moves each tick (fraction of the perimeter)
    PHRASE_TICKS = 38       # ticks between phrases (~2.3 s at 60 ms; ~1.5 s read too fast)

    def __init__(self, button: QWidget):
        super().__init__(button)
        self.button = button
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # clicks still go to the button
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pos = 0.0
        self._tick_n = 0
        self._phrase = ""
        self._since_phrase = 0
        self._saved_text = ""
        self._figure = FIGURES[0]
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self.running():
            return
        self._saved_text = self.button.text()
        self.button.setText("")                              # the overlay draws the phrase instead
        self._phrase = random_phrase()
        self._figure = random.choice(FIGURES)
        self._since_phrase = 0
        self._fit()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self) -> None:
        if not self.running():
            return
        self._timer.stop()
        self.hide()
        self.button.setText(self._saved_text)

    def _fit(self) -> None:
        self.setGeometry(self.button.rect())

    def _tick(self) -> None:
        self._pos = (self._pos + self.STEP) % 1.0
        self._tick_n += 1
        self._since_phrase += 1
        if self._since_phrase >= self.PHRASE_TICKS:
            self._phrase = random_phrase(self._phrase)
            self._figure = random.choice(FIGURES)            # a new random figure with each new phrase
            self._since_phrase = 0
        self._fit()                                          # follow the button if it is resized
        self.update()

    def _perimeter(self) -> QPainterPath:
        w, h = self.width(), self.height()
        inset = min(11.0, max(5.0, h * 0.32))   # room for the tooth head inside the button's edge
        radius = 8.0
        path = QPainterPath()
        path.addRoundedRect(QRectF(inset, inset, max(1.0, w - 2 * inset), max(1.0, h - 2 * inset)), radius, radius)
        return path

    def paintEvent(self, _event) -> None:
        if self.width() < 8 or self.height() < 8:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(8.0, f.pointSizeF() if f.pointSizeF() > 0 else 10.0))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(self.rect(), Qt.AlignCenter, self._phrase)
        try:
            self._figure(p, self._perimeter(), self._pos, self._tick_n)
        except Exception:
            pass
        p.end()


def attach(button: QWidget) -> SnakeBusy:
    """Give a button a reusable busy overlay (one per button); start()/stop() drive it."""
    busy = getattr(button, "_snake_busy", None)
    if busy is None:
        busy = SnakeBusy(button)
        button._snake_busy = busy
    return busy
