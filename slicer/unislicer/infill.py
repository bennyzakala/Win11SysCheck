"""Fill patterns: clip line families / curves against a region."""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from .slicing import polygons_of

# length of gyroid curve per unit area is GYROID_K / period; measured numerically
GYROID_K = 2.27


def _lines_of(geom: BaseGeometry) -> list[np.ndarray]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [np.asarray(geom.coords)[:, :2]]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out += _lines_of(g)
        return out
    return []


def _valid(geom: BaseGeometry) -> BaseGeometry:
    """The polygonal part of ``geom``, repaired if it is invalid. Rotating a valid outline in floating point
    can make tiny edges cross in near-degenerate spots, and clipping lines against such a shape returns
    lines outside it (fill strands sticking out of the part)."""
    if geom.is_empty or geom.is_valid:
        return geom
    fixed = shapely.make_valid(geom)
    polys = [g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(polys) if polys else fixed.buffer(0)


def line_fill(area: BaseGeometry, spacing: float, angle: float, phase: float = 0.0) -> list[np.ndarray]:
    """Parallel lines ``spacing`` apart at ``angle`` degrees, aligned to a global grid."""
    if area.is_empty or spacing <= 0:
        return []
    rotated = _valid(affinity.rotate(area, -angle, origin=(0, 0)))
    if rotated.is_empty:
        return []
    minx, miny, maxx, maxy = rotated.bounds
    start = math.floor((miny - phase) / spacing) * spacing + phase
    ys = np.arange(start, maxy + spacing, spacing)
    if len(ys) == 0:
        return []
    coords = np.zeros((len(ys), 2, 2))
    coords[:, 0, 0] = minx - 1
    coords[:, 1, 0] = maxx + 1
    coords[:, :, 1] = ys[:, None]
    lines = shapely.linestrings(coords)
    shapely.prepare(rotated)
    clipped = shapely.intersection(lines, rotated)
    out = []
    for g in clipped:
        for seg in _lines_of(g):
            out.append(seg)
    if not out:
        return []
    back = affinity.rotate(MultiLineString(out), angle, origin=(0, 0))
    return [s for s in _lines_of(back) if len(s) >= 2]


def gyroid_fill(area: BaseGeometry, spacing: float, z: float) -> list[np.ndarray]:
    """Cross-section of a gyroid surface at height z, clipped to ``area``."""
    if area.is_empty or spacing <= 0:
        return []
    period = GYROID_K * spacing
    k = 2 * math.pi / period
    minx, miny, maxx, maxy = area.bounds
    step = period / 48.0
    xs = np.arange(minx - step, maxx + 2 * step, step)
    X = xs * k
    Z = z * k
    A = np.sin(X)
    B = np.full_like(X, math.cos(Z))
    C = -math.sin(Z) * np.cos(X)
    R = np.hypot(A, B)
    ratio = np.divide(C, R, out=np.full_like(C, 2.0), where=R > 1e-9)
    valid = np.abs(ratio) <= 1.0
    phi = np.arctan2(B, A)
    acos = np.arccos(np.clip(ratio, -1, 1))
    curves = []
    n_min = math.floor(miny * k / (2 * math.pi)) - 1
    n_max = math.ceil(maxy * k / (2 * math.pi)) + 1
    for sign in (1.0, -1.0):
        base = phi + sign * acos
        base = np.mod(base, 2 * math.pi)  # keep the branch continuous within one period band
        jumps = np.abs(np.diff(base)) > math.pi
        breaks = np.nonzero(~valid[:-1] | ~valid[1:] | jumps)[0]
        bounds = np.concatenate([[-1], breaks, [len(xs) - 1]])
        for a, b in zip(bounds[:-1] + 1, bounds[1:] + 1):
            if b - a < 2 or not valid[a]:
                continue
            seg_x = xs[a:b]
            seg_y = base[a:b]
            for n in range(n_min, n_max + 1):
                y = (seg_y + 2 * math.pi * n) / k
                if y.max() < miny - period or y.min() > maxy + period:
                    continue
                curves.append(np.column_stack([seg_x, y]))
    if not curves:
        return []
    lines = np.array([LineString(c) for c in curves], dtype=object)
    area = _valid(area)
    if area.is_empty:
        return []
    shapely.prepare(area)
    out = []
    for g in shapely.intersection(lines, area):
        out += [s for s in _lines_of(g) if len(s) >= 2]
    return out


def concentric_fill(area: BaseGeometry, spacing: float, max_rings: int = 500) -> list[np.ndarray]:
    out = []
    current = area.buffer(-spacing / 2, join_style="mitre", mitre_limit=3)
    rings = 0
    while not current.is_empty and rings < max_rings:
        for poly in polygons_of(current):
            out.append(np.asarray(poly.exterior.coords)[:, :2])
            for hole in poly.interiors:
                out.append(np.asarray(hole.coords)[:, :2])
        current = current.buffer(-spacing, join_style="mitre", mitre_limit=3)
        rings += 1
    return out


def pattern_fill(area: BaseGeometry, pattern: str, density: float, line_width: float,
                 layer_index: int, z: float, base_angle: float = 45.0) -> list[np.ndarray]:
    """Sparse infill. ``density`` is a fraction 0..1."""
    if density <= 0 or area.is_empty:
        return []
    density = min(density, 1.0)
    spacing = line_width / density
    if pattern == "lines":
        return line_fill(area, spacing, base_angle + (90 if layer_index % 2 else 0))
    if pattern == "grid":
        return line_fill(area, spacing * 2, base_angle) + line_fill(area, spacing * 2, base_angle + 90)
    if pattern == "triangles":
        out = []
        for a in (0.0, 60.0, 120.0):
            out += line_fill(area, spacing * 3, base_angle + a)
        return out
    if pattern == "gyroid":
        return gyroid_fill(area, spacing, z)
    if pattern == "concentric":
        return concentric_fill(area, spacing)
    raise ValueError(f"Unknown infill pattern '{pattern}'")
