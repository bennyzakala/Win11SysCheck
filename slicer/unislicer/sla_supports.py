"""Resin supports: support points from overhangs, tapered pillars and a raft."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import MultiPoint

from .mesh import Mesh
from .primitives import frustum, prism
from .settings import Config

SEGMENTS = 10


def _ray_hits(tris: np.ndarray, xy: np.ndarray):
    """Vertical-ray hits of point ``xy`` against triangles. Returns (z values, face indices)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    v0 = b[:, :2] - a[:, :2]
    v1 = c[:, :2] - a[:, :2]
    v2 = xy - a[:, :2]
    den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    ok = np.abs(den) > 1e-12
    den = np.where(ok, den, 1.0)
    u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
    v = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
    inside = ok & (u >= 0) & (v >= 0) & (u + v <= 1)
    idx = np.nonzero(inside)[0]
    z = a[idx, 2] + u[idx] * (b[idx, 2] - a[idx, 2]) + v[idx] * (c[idx, 2] - a[idx, 2])
    return z, idx


class _Caster:
    """Vertical rays against one mesh, with a per-triangle XY bounding-box prefilter."""

    def __init__(self, mesh: Mesh):
        self.tris = mesh.triangles
        self.normals = mesh.face_normals()
        self.tmin = self.tris[:, :, :2].min(axis=1)
        self.tmax = self.tris[:, :, :2].max(axis=1)

    def hits(self, x: float, y: float, candidates: np.ndarray | None = None):
        """Sorted (z, face index) of all surfaces crossed by the vertical line through (x, y)."""
        idx = candidates if candidates is not None else np.arange(len(self.tris))
        sel = idx[(self.tmin[idx, 0] <= x) & (self.tmax[idx, 0] >= x) & (self.tmin[idx, 1] <= y) & (self.tmax[idx, 1] >= y)]
        if len(sel) == 0:
            return np.zeros(0), np.zeros(0, dtype=int)
        z, local = _ray_hits(self.tris[sel], np.array([x, y]))
        order = np.argsort(z)
        return z[order], sel[local[order]]

    def base_below(self, p: np.ndarray) -> float | None:
        """Height where a pillar dropped from ``p`` lands: 0 for the bed, or the top of the model below it."""
        z, faces = self.hits(p[0], p[1])
        below = (z < p[2] - 0.5) & (self.normals[faces, 2] > 0)
        return float(z[below].max()) if np.any(below) else 0.0


def auto_support_points(mesh: Mesh, cfg: Config) -> np.ndarray:
    """Contact points (N, 3) on the underside of ``mesh`` that need support."""
    caster = _Caster(mesh)
    threshold = -math.sin(math.radians(cfg["support_angle"]))
    overhang = caster.normals[:, 2] < threshold
    spacing = cfg["sla_support_spacing"]
    lo, hi = mesh.bounds
    xs = np.arange(lo[0] + spacing / 2, hi[0], spacing)
    ys = np.arange(lo[1] + spacing / 2, hi[1], spacing)
    points = []
    for gx in xs:
        column = np.nonzero((caster.tmin[:, 0] <= gx) & (caster.tmax[:, 0] >= gx))[0]
        if len(column) == 0:
            continue
        for gy in ys:
            z, faces = caster.hits(gx, gy, column)
            for k in range(len(z)):
                if not overhang[faces[k]]:
                    continue
                if k > 0 and z[k] - z[k - 1] < 1e-3:
                    continue  # duplicate hit on a shared edge
                points.append([gx, gy, z[k]])
    # lowest tips (vertices below all their neighbours) would be missed by a coarse grid
    v = mesh.vertices
    neighbour_min = np.full(len(v), np.inf)
    f = mesh.faces
    for a, b in ((0, 1), (1, 2), (2, 0), (1, 0), (2, 1), (0, 2)):
        np.minimum.at(neighbour_min, f[:, a], v[f[:, b], 2])
    tips = np.nonzero(v[:, 2] < neighbour_min - 1e-6)[0]
    existing = np.array(points) if points else np.zeros((0, 3))
    for t in tips:
        p = v[t]
        if len(existing) and np.min(np.hypot(*(existing[:, :2] - p[:2]).T) + np.abs(existing[:, 2] - p[2])) < spacing * 0.5:
            continue
        points.append(p.copy())
    return np.array(points, dtype=np.float64).reshape(-1, 3)


def build_supports(mesh: Mesh, cfg: Config, points: np.ndarray | None = None) -> tuple[Mesh, np.ndarray]:
    """Support geometry for a model that already sits at its elevated height.

    ``points`` are contact points in the same coordinates (None = generate them automatically).
    Returns the support mesh and the points that actually got a support."""
    if points is None:
        points = auto_support_points(mesh, cfg)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    caster = _Caster(mesh)
    tip_r = cfg["sla_tip_diameter"] / 2
    pillar_r = max(cfg["sla_pillar_diameter"] / 2, tip_r)
    raft = cfg["sla_raft_thickness"]
    depth = cfg["sla_contact_depth"]
    on_model = cfg["sla_supports_on_model"]
    head_len = max(1.0, pillar_r * 3)
    parts, feet, used = [], [], []
    for p in points:
        base = caster.base_below(p)
        if base > 0 and not on_model:
            continue  # the pillar would have to stand on the model
        x, y, top = p
        top += depth  # penetrate into the model for a solid bond
        on_bed = base <= 1e-6
        bottom = raft if on_bed else base
        height = top - bottom
        if height < 0.5:
            continue
        head = min(head_len, height * 0.5)
        if on_bed:
            foot = min(1.0, height * 0.2)
            # sink slightly into the raft; the foot ends exactly where the pillar body starts
            parts.append(frustum(pillar_r * 2, pillar_r, foot + 0.05, SEGMENTS, bottom - 0.05, (x, y)))
            feet.append(np.array([x, y]))
        else:
            foot = min(head, height * 0.25)
            parts.append(frustum(tip_r, pillar_r, foot + depth, SEGMENTS, bottom - depth, (x, y)))
        body_z = bottom + foot
        body_h = top - head - body_z
        if body_h > 0:
            parts.append(frustum(pillar_r, pillar_r, body_h + 0.01, SEGMENTS, body_z, (x, y)))
        parts.append(frustum(pillar_r, tip_r, head, SEGMENTS, top - head, (x, y)))
        used.append(p)
    if raft > 0 and feet:
        hull = MultiPoint([tuple(f) for f in feet]).buffer(pillar_r * 2 + cfg["sla_raft_margin"], quad_segs=4).convex_hull
        ring = np.asarray(hull.exterior.coords)[:-1]
        if _signed_area(ring) < 0:
            ring = ring[::-1]
        parts.append(prism(ring, 0.0, raft))
    return Mesh.merge(parts, "Supports"), np.array(used).reshape(-1, 3)


def _signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
