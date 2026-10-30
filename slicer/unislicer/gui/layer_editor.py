"""Layer pixel editor (as in CHITUBOX Pro): paint or erase pixels on the sliced resin layers before export.

Edits are pixel-exact squares on the printer's pixel grid (or round brushes), kept per layer and merged into
the layer outlines when applied, so every export format (images, CTB/GOO/PRZ, SLC/CLI) uses them."""
from __future__ import annotations

import math

import shapely
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)
from shapely.geometry import Point, box

from .. import sla
from ..slicing import EMPTY, as_multipolygon, polygons_of
from . import theme
from .i18n import tr


class _Canvas(QWidget):
    painted = Signal()

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self._drag = None
        self._cursor = None

    # mm <-> widget
    def _base(self):
        x0, y0, x1, y1 = self.editor.view_rect
        s = min(self.width() / max(x1 - x0, 1e-6), self.height() / max(y1 - y0, 1e-6)) * 0.95 * self.zoom
        cx = self.width() / 2 + self.pan.x()
        cy = self.height() / 2 + self.pan.y()
        return s, cx, cy, (x0 + x1) / 2, (y0 + y1) / 2

    def to_widget(self, x, y):
        s, cx, cy, mx, my = self._base()
        return QPointF(cx + (x - mx) * s, cy - (y - my) * s)

    def to_mm(self, p: QPointF):
        s, cx, cy, mx, my = self._base()
        return mx + (p.x() - cx) / s, my - (p.y() - cy) / s

    def _path(self, geom) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        for poly in polygons_of(geom):
            for ring in [poly.exterior, *poly.interiors]:
                pts = [self.to_widget(x, y) for x, y in ring.coords]
                if pts:
                    path.moveTo(pts[0])
                    for q in pts[1:]:
                        path.lineTo(q)
                    path.closeSubpath()
        return path

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()             # an open painter after an error takes the whole app down

    def _paint(self, p: QPainter):
        ed = self.editor
        p.fillRect(self.rect(), QColor(0, 0, 0))
        p.setRenderHint(QPainter.Antialiasing, self.zoom < 6)
        p.fillPath(self._path(ed.current_geometry()), QBrush(QColor(235, 235, 235)))
        islands = ed.islands.get(ed.layer)
        if islands:
            p.fillPath(self._path(shapely.union_all(islands)), QBrush(QColor(255, 70, 90)))
        # pixel grid when zoomed in far enough to see single pixels
        s = self._base()[0]
        px = ed.pixel
        if s * px >= 7:
            p.setPen(QPen(QColor(60, 60, 70), 1))
            x0, y1 = self.to_mm(QPointF(0, 0))
            x1, y0 = self.to_mm(QPointF(self.width(), self.height()))
            gx = math.floor(x0 / px) * px
            while gx <= x1:
                a = self.to_widget(gx, 0)
                p.drawLine(QPointF(a.x(), 0), QPointF(a.x(), self.height()))
                gx += px
            gy = math.floor(y0 / ed.pixel_y) * ed.pixel_y
            while gy <= y1:
                a = self.to_widget(0, gy)
                p.drawLine(QPointF(0, a.y()), QPointF(self.width(), a.y()))
                gy += ed.pixel_y
        if self._cursor is not None:
            shape = ed.brush_shape(*self._cursor)
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(self._path(shape))

    def wheelEvent(self, e):
        before = self.to_mm(e.position())
        self.zoom = max(0.2, min(200.0, self.zoom * (1.18 ** (e.angleDelta().y() / 120.0))))
        after = self.to_widget(*before)
        self.pan += e.position() - after          # zoom around the cursor
        self.update()

    def mousePressEvent(self, e):
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._drag = e.position()
        elif e.button() == Qt.LeftButton:
            self.editor.begin_stroke()
            self.editor.stroke_at(*self.to_mm(e.position()))
            self.update()

    def mouseMoveEvent(self, e):
        self._cursor = self.to_mm(e.position())
        if self._drag is not None:
            self.pan += e.position() - self._drag
            self._drag = e.position()
        elif e.buttons() & Qt.LeftButton:
            self.editor.stroke_at(*self._cursor)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        if e.button() == Qt.LeftButton:
            self.editor.end_stroke()
            self.painted.emit()

    def leaveEvent(self, _e):
        self._cursor = None
        self.update()


class LayerEditor(QDialog):
    """Paint (add) or erase pixels on resin layers. ``job.layers`` is updated when the dialog is accepted."""

    def __init__(self, job, cfg, layer: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("pixel_edit"))
        self.job, self.cfg = job, cfg
        self.layer = max(0, min(layer, job.layer_count - 1))
        self.pixel = cfg["bed_x"] / max(1, int(cfg["resolution_x"]))       # mm per pixel
        self.pixel_y = cfg["bed_y"] / max(1, int(cfg["resolution_y"]))
        self.edits: dict[int, object] = {}                 # layer -> edited geometry
        self.history: list = []                            # (layer, geometry before) for undo
        self.islands: dict[int, list] = {}
        for i, poly in job.islands:
            self.islands.setdefault(i, []).append(poly)
        bounds = [g.bounds for g in job.layers if not g.is_empty]
        if bounds:
            x0 = min(b[0] for b in bounds) - 3
            y0 = min(b[1] for b in bounds) - 3
            x1 = max(b[2] for b in bounds) + 3
            y1 = max(b[3] for b in bounds) + 3
        else:
            x0, y0, x1, y1 = 0, 0, cfg["bed_x"], cfg["bed_y"]
        self.view_rect = (x0, y0, x1, y1)
        screen = (parent.screen() if parent else self.screen()).availableGeometry()
        self.resize(int(screen.width() * 0.8), int(screen.height() * 0.8))

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.paint_btn = QPushButton(theme.icon("plus", theme.TEXT, 16), tr("pixel_paint"))
        self.erase_btn = QPushButton(theme.icon("x", theme.TEXT, 16), tr("pixel_erase"))
        group = QButtonGroup(self)
        for b in (self.paint_btn, self.erase_btn):
            b.setCheckable(True)
            group.addButton(b)
            bar.addWidget(b)
        self.paint_btn.setChecked(True)
        bar.addSpacing(12)
        bar.addWidget(QLabel(tr("pixel_brush")))
        self.size = QSpinBox()
        self.size.setRange(1, 200)
        self.size.setValue(3)
        self.size.setSuffix(" px")
        bar.addWidget(self.size)
        self.round = QCheckBox(tr("pixel_round"))
        bar.addWidget(self.round)
        bar.addSpacing(12)
        bar.addWidget(QLabel(tr("pixel_layers_too")))
        self.extra = QSpinBox()
        self.extra.setRange(0, 500)
        self.extra.setToolTip(tr("pixel_layers_too_tip"))
        bar.addWidget(self.extra)
        bar.addStretch(1)
        undo = QPushButton(theme.icon("undo", theme.TEXT, 16), tr("undo"))
        undo.clicked.connect(self.undo)
        bar.addWidget(undo)
        clean = QPushButton(tr("pixel_delete_islands"))
        clean.setToolTip(tr("pixel_delete_islands_tip"))
        clean.clicked.connect(self.delete_islands)
        bar.addWidget(clean)
        lay.addLayout(bar)

        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 1)

        nav = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, job.layer_count - 1)
        self.slider.setValue(self.layer)
        self.slider.valueChanged.connect(self.set_layer)
        self.info = QLabel("")
        nav.addWidget(self.slider, 1)
        nav.addWidget(self.info)
        lay.addLayout(nav)
        hint = QLabel(tr("pixel_hint"))
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("pixel_apply"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.set_layer(self.layer)

    # ---------------------------------------------------------- editing
    def current_geometry(self, layer=None):
        layer = self.layer if layer is None else layer
        return self.edits.get(layer, self.job.layers[layer])

    def brush_shape(self, x, y):
        n = self.size.value()
        if self.round.isChecked():
            return Point(x, y).buffer(n * self.pixel / 2, quad_segs=8)
        # pixel-exact square, snapped to the printer's pixel grid
        i = math.floor(x / self.pixel - n / 2 + 0.5)
        j = math.floor(y / self.pixel_y - n / 2 + 0.5)
        return box(i * self.pixel, j * self.pixel_y, (i + n) * self.pixel, (j + n) * self.pixel_y)

    def _targets(self):
        return range(self.layer, min(self.job.layer_count, self.layer + 1 + self.extra.value()))

    def begin_stroke(self):
        self.history.append({i: self.edits.get(i) for i in self._targets()})
        del self.history[:-100]
        self._last = None

    def stroke_at(self, x, y):
        # fill the path from the previous mouse position, so a fast drag leaves no gaps
        last = getattr(self, "_last", None)
        self._last = (x, y)
        step = max(self.pixel, self.size.value() * self.pixel * 0.5)
        shapes = []
        if last is not None:
            n = int(math.hypot(x - last[0], y - last[1]) / step)
            shapes = [self.brush_shape(last[0] + (x - last[0]) * k / (n + 1), last[1] + (y - last[1]) * k / (n + 1))
                      for k in range(1, n + 1)]
        shape = shapely.union_all(shapes + [self.brush_shape(x, y)])
        for i in self._targets():
            g = self.current_geometry(i)
            g = g.union(shape) if self.paint_btn.isChecked() else (g.difference(shape) if not g.is_empty else g)
            self.edits[i] = as_multipolygon(g)

    def end_stroke(self):
        self._update_info()

    def undo(self):
        if not self.history:
            return
        for i, g in self.history.pop().items():
            if g is None:
                self.edits.pop(i, None)
            else:
                self.edits[i] = g
        self.canvas.update()
        self._update_info()

    def delete_islands(self):
        """Remove the island areas on this layer (CHITUBOX: only safe for stray pixels, not real overhangs)."""
        isl = self.islands.get(self.layer)
        if not isl:
            return
        self.history.append({self.layer: self.edits.get(self.layer)})
        g = self.current_geometry().difference(shapely.union_all(isl).buffer(self.pixel * 0.1))
        self.edits[self.layer] = as_multipolygon(g)
        self.islands.pop(self.layer, None)
        self.canvas.update()
        self._update_info()

    def set_layer(self, i):
        self.layer = int(i)
        self.canvas.update()
        self._update_info()

    def _update_info(self):
        z = self.job.layer_z[self.layer] if self.job.layer_z else (self.layer + 1) * self.job.layer_height
        edited = len(self.edits)
        self.info.setText(tr("pixel_info").format(self.layer + 1, self.job.layer_count, z, edited,
                                                   self.pixel * 1000))

    def apply(self) -> int:
        """Write the edits into the job. Returns how many layers changed."""
        for i, g in self.edits.items():
            self.job.layers[i] = g if not g.is_empty else EMPTY
        if self.edits:
            self.job.islands = sla.find_islands(self.job.layers)
            self.job.volume_ml = sum(g.area * h for g, h in zip(self.job.layers, self.job.layer_thickness)) / 1000.0 \
                if self.job.layer_thickness else self.job.volume_ml
        return len(self.edits)
