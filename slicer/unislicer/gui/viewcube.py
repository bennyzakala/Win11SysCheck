"""View cube: a small cube in the corner of the 3D view that turns with the camera.
Clicking a face, an edge or a corner turns the view to look at the model from that side."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import theme
from .i18n import tr

# face: (normal, label key, corner indices counter-clockwise seen from outside)
CORNERS = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=float)
FACES = [
    ((0, 0, 1), "vc_top", (1, 5, 7, 3)),
    ((0, 0, -1), "vc_bottom", (0, 2, 6, 4)),
    ((0, -1, 0), "vc_front", (0, 4, 5, 1)),
    ((0, 1, 0), "vc_back", (2, 3, 7, 6)),
    ((1, 0, 0), "vc_right", (4, 6, 7, 5)),
    ((-1, 0, 0), "vc_left", (0, 1, 3, 2)),
]


def direction_angles(d) -> tuple[float, float]:
    """Camera (yaw, pitch) that looks at the target from direction ``d``."""
    d = np.asarray(d, dtype=float)
    d = d / (np.linalg.norm(d) or 1.0)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))
    pitch = max(-89.9, min(89.9, pitch))
    yaw = math.degrees(math.atan2(d[1], d[0])) if abs(d[2]) < 0.999 else -90.0
    return yaw, pitch


class ViewCube(QWidget):
    SIZE = 116

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("vc_tip"))
        self._hover = None
        self._anim = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    # ---------------------------------------------------------- geometry
    def _rotation(self) -> np.ndarray:
        return self.viewer.camera.view()[:3, :3]

    def _project(self, pts: np.ndarray) -> np.ndarray:
        r = self._rotation()
        v = pts @ r.T
        s = self.SIZE * 0.25
        return np.column_stack([self.SIZE / 2 + v[:, 0] * s, self.SIZE / 2 - v[:, 1] * s, v[:, 2]])

    def _targets(self):
        """Clickable spots: visible faces (polygons), then edges and corners (points), nearest first."""
        r = self._rotation()
        p = self._project(CORNERS)
        faces = []
        for normal, key, idx in FACES:
            if (r @ np.array(normal))[2] > 0.05:
                faces.append((np.array(normal, dtype=float), key, p[list(idx)]))
        spots = []
        for c in CORNERS:                                   # corners: isometric views
            q = self._project(c[None, :])[0]
            if q[2] > -0.2:
                spots.append((c, q[:2], 9.0))
        for i in range(8):                                  # edge midpoints
            for j in range(i + 1, 8):
                if np.abs(CORNERS[i] - CORNERS[j]).sum() == 2:
                    m = (CORNERS[i] + CORNERS[j]) / 2
                    q = self._project(m[None, :])[0]
                    if q[2] > -0.2:
                        spots.append((m, q[:2], 7.0))
        return faces, spots

    def _hit(self, pos: QPointF):
        faces, spots = self._targets()
        x, y = pos.x(), pos.y()
        for d, q, radius in spots:
            if math.hypot(q[0] - x, q[1] - y) <= radius:
                return d
        for normal, _, poly in faces:
            if QPolygonF([QPointF(*a[:2]) for a in poly]).containsPoint(pos, Qt.OddEvenFill):
                return normal
        return None

    # ---------------------------------------------------------- painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        faces, spots = self._targets()
        r = self._rotation()
        faces.sort(key=lambda f: -(r @ f[0])[2])
        accent = QColor(theme.ACCENT)
        base = QColor(theme.SURFACE)
        base.setAlpha(235)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        for normal, key, poly in reversed(faces):
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(*a[:2]) for a in poly]))
            path.closeSubpath()
            fill = QColor(accent) if self._hover is not None and np.allclose(self._hover, normal) else QColor(base)
            if fill == accent:
                fill.setAlpha(200)
            shade = 0.82 + 0.18 * max(0.0, float((r @ normal)[2]))
            fill = QColor.fromRgbF(fill.redF() * shade, fill.greenF() * shade, fill.blueF() * shade, fill.alphaF())
            p.fillPath(path, fill)
            p.setPen(QPen(QColor(theme.BORDER_HOVER), 1.2))
            p.drawPath(path)
            facing = float((r @ normal)[2])
            if facing > 0.35:
                c = poly[:, :2].mean(axis=0)
                p.save()
                p.setClipPath(path)               # labels on oblique faces are cut at the edge
                f = QFont(font)
                f.setPixelSize(max(7, int(11 * min(1.0, facing + 0.25))))
                p.setFont(f)
                p.setPen(QColor(theme.ON_ACCENT) if fill.hue() == accent.hue() and self._hover is not None
                         and np.allclose(self._hover, normal) else QColor(theme.TEXT))
                p.drawText(QRectF(c[0] - 40, c[1] - 9, 80, 18), Qt.AlignCenter, tr(key))
                p.restore()
        if self._hover is not None and not any(np.allclose(self._hover, f[0]) for f in faces):
            q = self._project(np.asarray(self._hover)[None, :])[0]
            p.setBrush(accent)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(q[0], q[1]), 5, 5)
        # axis hints at the bottom-left
        p.setPen(QPen(QColor(theme.TEXT_FAINT), 1))
        o = self._project(np.array([[-1.25, -1.25, -1.25]]))[0]
        for axis, color, label in ((0, "#ff5d6c", "X"), (1, "#2ecc8f", "Y"), (2, "#4f8cff", "Z")):
            e = np.array([-1.25, -1.25, -1.25])
            e[axis] += 0.8
            q = self._project(e[None, :])[0]
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(QPointF(o[0], o[1]), QPointF(q[0], q[1]))
        p.end()

    # ---------------------------------------------------------- interaction
    def mouseMoveEvent(self, e):
        hit = self._hit(e.position())
        if (hit is None) != (self._hover is None) or (hit is not None and not np.allclose(hit, self._hover)):
            self._hover = hit
            self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        d = self._hit(e.position())
        if d is not None:
            self.turn_to(*direction_angles(d))

    def turn_to(self, yaw: float, pitch: float):
        """Turn the camera smoothly to (yaw, pitch)."""
        cam = self.viewer.camera
        dy = (yaw - cam.yaw + 180.0) % 360.0 - 180.0
        self._anim = (cam.yaw, cam.pitch, cam.yaw + dy, pitch, 0)
        self._timer.start()

    def _step(self):
        y0, p0, y1, p1, k = self._anim
        k += 1
        t = min(1.0, k / 14)
        t = t * t * (3 - 2 * t)
        cam = self.viewer.camera
        cam.yaw = y0 + (y1 - y0) * t
        cam.pitch = p0 + (p1 - p0) * t
        self._anim = (y0, p0, y1, p1, k)
        self.viewer.update()
        self.update()
        if t >= 1.0:
            self._timer.stop()
