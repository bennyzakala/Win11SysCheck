"""A playful 'busy' overlay for a button: while a long action runs, a little figure (a snake, a comet, a
Pac-Man, a rocket, a caterpillar, a train...) runs around the button's edge and a new nonsense two-word phrase
shows every so often ("baking clouds", "frying toast"...). A different figure is picked each time the phrase
changes. It draws on the GUI thread with a light timer, so it never touches the speed of the work itself.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPolygonF
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
def _dot(p: QPainter, x: float, y: float, r: float, col: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.drawEllipse(QPointF(x, y), r, r)


def _dir(path: QPainterPath, t: float, eps: float = 0.004):
    """Unit travel direction at fraction ``t`` (robust - from two near points, not angleAtPercent)."""
    a = path.pointAtPercent((t - eps) % 1.0)
    b = path.pointAtPercent((t + eps) % 1.0)
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def _star(p: QPainter, x: float, y: float, r: float, col: QColor) -> None:
    pts = []
    for k in range(10):
        rr = r if k % 2 == 0 else r * 0.45
        a = math.pi / 2 + k * math.pi / 5
        pts.append(QPointF(x + rr * math.cos(a), y - rr * math.sin(a)))
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.drawPolygon(QPolygonF(pts))


def _heart(p: QPainter, x: float, y: float, s: float, col: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(col)
    p.drawEllipse(QPointF(x - s * 0.5, y - s * 0.3), s * 0.6, s * 0.6)
    p.drawEllipse(QPointF(x + s * 0.5, y - s * 0.3), s * 0.6, s * 0.6)
    p.drawPolygon(QPolygonF([QPointF(x - s, y - s * 0.1), QPointF(x + s, y - s * 0.1), QPointF(x, y + s)]))


def fig_snake(p, path, pos, tick):
    for i in range(16):
        pt = path.pointAtPercent((pos - i * 0.012) % 1.0)
        frac = 1 - i / 16
        col = QColor(255, 240, 150) if i == 0 else QColor(235, 255, 235)
        col.setAlphaF(0.25 + 0.65 * frac)
        _dot(p, pt.x(), pt.y(), 1.6 + 3.2 * frac, col)


def fig_comet(p, path, pos, tick):
    for i in range(15):
        pt = path.pointAtPercent((pos - i * 0.01) % 1.0)
        frac = 1 - i / 15
        col = QColor(130, 205, 255)
        col.setAlphaF(0.12 + 0.6 * frac)
        _dot(p, pt.x(), pt.y(), 1.0 + 3.6 * frac, col)
    head = path.pointAtPercent(pos)
    _dot(p, head.x(), head.y(), 4.3, QColor(255, 255, 255))
    for _ in range(2):
        a, d = random.uniform(0, 6.28), random.uniform(3, 7)
        _dot(p, head.x() + d * math.cos(a), head.y() + d * math.sin(a), 1.0, QColor(210, 240, 255, 180))


def fig_caterpillar(p, path, pos, tick):
    for i in range(11):
        t = (pos - i * 0.017) % 1.0
        pt = path.pointAtPercent(t)
        dx, dy = _dir(path, t)
        off = 2.1 * math.sin(tick * 0.3 + i * 0.9)
        x, y = pt.x() - dy * off, pt.y() + dx * off          # wiggle along the normal
        _dot(p, x, y, 4.0 if i == 0 else 3.2, QColor(175, 230, 95) if i % 2 == 0 else QColor(120, 195, 60))
    head = path.pointAtPercent(pos)
    _dot(p, head.x() - 1.3, head.y() - 1.0, 0.9, QColor(20, 20, 20))
    _dot(p, head.x() + 1.3, head.y() - 1.0, 0.9, QColor(20, 20, 20))


def _oriented(p, path, pos, draw):
    pt = path.pointAtPercent(pos)
    dx, dy = _dir(path, pos)
    p.save()
    p.translate(pt.x(), pt.y())
    p.rotate(math.degrees(math.atan2(dy, dx)))               # local +x points the way it travels
    draw(p)
    p.restore()


def fig_car(p, path, pos, tick):
    def draw(q):
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(255, 95, 120))
        q.drawRoundedRect(QRectF(-8, -4, 16, 7), 2.5, 2.5)
        q.setBrush(QColor(255, 150, 170))
        q.drawRoundedRect(QRectF(-3.5, -7.5, 8, 4.5), 2.0, 2.0)   # cabin
        q.setBrush(QColor(30, 30, 40))
        for wx in (-4.5, 4.5):
            q.drawEllipse(QPointF(wx, 3.5), 2.4, 2.4)
        q.setBrush(QColor(220, 230, 240))
        for wx in (-4.5, 4.5):
            q.drawEllipse(QPointF(wx, 3.5), 0.9, 0.9)
    _oriented(p, path, pos, draw)


def fig_rocket(p, path, pos, tick):
    def draw(q):
        q.setPen(Qt.NoPen)
        flame = QColor(255, 200, 70) if tick % 2 else QColor(255, 140, 40)
        q.setBrush(flame)
        q.drawPolygon(QPolygonF([QPointF(-6, -2.5), QPointF(-6, 2.5), QPointF(-11 - random.uniform(0, 3), 0)]))
        q.setBrush(QColor(240, 245, 255))
        q.drawPolygon(QPolygonF([QPointF(9, 0), QPointF(-6, -4.5), QPointF(-6, 4.5)]))   # body, nose at +x
        q.setBrush(QColor(255, 95, 120))
        q.drawPolygon(QPolygonF([QPointF(-6, -4.5), QPointF(-9, -6), QPointF(-6, -1)]))  # fins
        q.drawPolygon(QPolygonF([QPointF(-6, 4.5), QPointF(-9, 6), QPointF(-6, 1)]))
        q.setBrush(QColor(90, 170, 240))
        q.drawEllipse(QPointF(1.5, 0), 2.0, 2.0)                                         # window
    _oriented(p, path, pos, draw)


def fig_pacman(p, path, pos, tick):
    # dots ahead, being eaten
    for i in range(1, 5):
        pt = path.pointAtPercent((pos + i * 0.025) % 1.0)
        _dot(p, pt.x(), pt.y(), 1.6, QColor(255, 255, 255, 200))
    mouth = 8 + 30 * abs(math.sin(tick * 0.4))               # chomp

    def draw(q):
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(255, 224, 70))
        rect = QRectF(-7, -7, 14, 14)
        q.drawPie(rect, int((mouth) * 16), int((360 - 2 * mouth) * 16))
        q.setBrush(QColor(40, 40, 50))
        q.drawEllipse(QPointF(-1, -3.5), 1.2, 1.2)           # eye
    _oriented(p, path, pos, draw)


def fig_ladybug(p, path, pos, tick):
    def draw(q):
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(30, 30, 35))
        q.drawEllipse(QPointF(5.5, 0), 3.0, 3.0)             # head at front
        q.setBrush(QColor(230, 60, 60))
        q.drawEllipse(QRectF(-7, -5.5, 13, 11))              # shell
        q.setBrush(QColor(30, 30, 35))
        q.drawRect(QRectF(-0.5, -5.5, 1.0, 11))              # middle line
        for sx, sy in ((-3, -2.5), (-3, 2.5), (1.5, -3), (1.5, 3), (-0.5, 0)):
            q.drawEllipse(QPointF(sx, sy), 1.1, 1.1)
    _oriented(p, path, pos, draw)


def fig_train(p, path, pos, tick):
    cols = [QColor(90, 170, 240), QColor(255, 95, 120), QColor(255, 200, 70), QColor(130, 215, 130)]
    for i, c in enumerate(cols):
        t = (pos - i * 0.03) % 1.0
        pt = path.pointAtPercent(t)
        dx, dy = _dir(path, t)

        def draw(q, c=c, first=(i == 0)):
            q.setPen(Qt.NoPen)
            q.setBrush(c)
            q.drawRoundedRect(QRectF(-4.5, -4, 9, 7), 1.8, 1.8)
            if first:
                q.setBrush(QColor(255, 255, 255))
                q.drawRoundedRect(QRectF(-2, -6.5, 4.5, 3.5), 1.2, 1.2)   # chimney/cab
            q.setBrush(QColor(30, 30, 40))
            q.drawEllipse(QPointF(-2.5, 3), 1.6, 1.6)
            q.drawEllipse(QPointF(2.5, 3), 1.6, 1.6)
        p.save()
        p.translate(pt.x(), pt.y())
        p.rotate(math.degrees(math.atan2(dy, dx)))
        draw(p)
        p.restore()


def fig_stars(p, path, pos, tick):
    for i in range(9):
        pt = path.pointAtPercent((pos - i * 0.016) % 1.0)
        frac = 1 - i / 9
        col = QColor(255, 240, 150)
        col.setAlphaF((0.3 + 0.6 * frac) * (0.6 + 0.4 * math.sin(tick * 0.4 + i)))
        _star(p, pt.x(), pt.y(), 2.2 + 2.5 * frac, col)


def fig_hearts(p, path, pos, tick):
    for i in range(8):
        pt = path.pointAtPercent((pos - i * 0.017) % 1.0)
        frac = 1 - i / 8
        col = QColor(255, 105, 140)
        col.setAlphaF(0.3 + 0.6 * frac)
        _heart(p, pt.x(), pt.y(), 2.0 + 1.8 * frac, col)


# emoji runners: a colourful little picture bobs along the border. One figure per emoji, so the list alone is
# dozens of figures (on Windows they render in colour via Segoe UI Emoji).
_EMOJI = [
    "🐌", "🦄", "🐙", "🐧", "🐢", "🦋", "🐝", "🐳", "🦈", "🐬", "🦖", "🐊", "🦎", "🦀", "🐇", "🦔",
    "🦦", "🦩", "🦜", "🐸", "🐥", "🐞", "🦕", "🐿", "🦭", "🐛", "🦥", "🦧",
    "🍕", "🍰", "🎂", "🍩", "🍔", "🍟", "🧁", "🍪", "🍉", "🍓", "🍄", "🌮", "🥐", "🍦", "🍿", "🥨",
    "🚗", "🚂", "🚀", "🛸", "🚁", "⛵", "🛼", "🛹", "🎈", "🎉", "🎁", "🪄", "🧲", "🔧", "🔨", "🧪",
    "🔭", "🎨", "🧵", "⭐", "🌈", "🔥", "❄", "💧", "🫧", "🎃", "👻", "🤖", "👾", "🦷",
]


def make_emoji_figure(ch):
    def draw(p, path, pos, tick):
        pt = path.pointAtPercent(pos)
        bob = 1.6 * math.sin(tick * 0.35)
        f = QFont(p.font())
        f.setPointSizeF(15.0)
        p.setFont(f)
        p.setPen(QColor(0, 0, 0, 70))                        # faint shadow for legibility on the button
        p.drawText(QRectF(pt.x() - 13 + 1, pt.y() - 13 + bob + 1, 26, 26), Qt.AlignCenter, ch)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(pt.x() - 13, pt.y() - 13 + bob, 26, 26), Qt.AlignCenter, ch)
    draw.__name__ = "fig_emoji_" + ch
    return draw


_DRAWN = [fig_snake, fig_comet, fig_caterpillar, fig_car, fig_rocket,
          fig_pacman, fig_ladybug, fig_train, fig_stars, fig_hearts]
FIGURES = _DRAWN + [make_emoji_figure(ch) for ch in _EMOJI]


class SnakeBusy(QWidget):
    """A see-through overlay sized to its button. While running it hides the button's own text, shows a random
    phrase in its place, and runs a little figure around the button's border (a new figure each phrase);
    stopping puts the button back as it was."""

    STEP = 0.013             # how far the figure's head moves each tick (fraction of the perimeter)
    PHRASE_TICKS = 26       # ticks between phrase + figure changes (~1.5 s at 60 ms)

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
        inset, radius = 5.0, 9.0
        path = QPainterPath()
        w, h = self.width(), self.height()
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
            fig_snake(p, self._perimeter(), self._pos, self._tick_n)
        p.end()


def attach(button: QWidget) -> SnakeBusy:
    """Give a button a reusable busy overlay (one per button); start()/stop() drive it."""
    busy = getattr(button, "_snake_busy", None)
    if busy is None:
        busy = SnakeBusy(button)
        button._snake_busy = busy
    return busy
