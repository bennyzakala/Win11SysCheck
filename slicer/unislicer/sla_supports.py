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


def support_points(mesh: Mesh, cfg: Config) -> list[tuple[np.ndarray, float]]:
    """Points on the underside of ``mesh`` that need support, with the z where each pillar lands."""
    tris = mesh.triangles
    normals = mesh.face_normals()
    threshold = -math.sin(math.radians(cfg["support_angle"]))
    overhang = normals[:, 2] < threshold
    spacing = cfg["sla_support_spacing"]
    lo, hi = mesh.bounds
    xs = np.arange(lo[0] + spacing / 2, hi[0], spacing)
    ys = np.arange(lo[1] + spacing / 2, hi[1], spacing)
    tmin = tris[:, :, :2].min(axis=1)
    tmax = tris[:, :, :2].max(axis=1)
    points = []
    for gx in xs:
        col = (tmin[:, 0] <= gx) & (tmax[:, 0] >= gx)
        if not col.any():
            continue
        col_idx = np.nonzero(col)[0]
        for gy in ys:
            sel = col_idx[(tmin[col_idx, 1] <= gy) & (tmax[col_idx, 1] >= gy)]
            if len(sel) == 0:
                continue
            z, idx = _ray_hits(tris[sel], np.array([gx, gy]))
            if len(z) == 0:
                continue
            order = np.argsort(z)
            z, faces = z[order], sel[idx[order]]
            for k in range(len(z)):
                if not overhang[faces[k]]:
                    continue
                if k > 0 and z[k] - z[k - 1] < 1e-3:
                    continue  # duplicate hit on a shared edge
                base = 0.0
                below = z[:k]
                up_facing = normals[faces[:k], 2] > 0
                if np.any(up_facing):
                    base = float(below[up_facing].max())
                if z[k] - base > 1.0:
                    points.append((np.array([gx, gy, z[k]]), base))
    # lowest tips (vertices below all their neighbours) would be missed by a coarse grid
    v = mesh.vertices
    neighbour_min = np.full(len(v), np.inf)
    f = mesh.faces
    for a, b in ((0, 1), (1, 2), (2, 0), (1, 0), (2, 1), (0, 2)):
        np.minimum.at(neighbour_min, f[:, a], v[f[:, b], 2])
    tips = np.nonzero(v[:, 2] < neighbour_min - 1e-6)[0]
    existing = np.array([p[0] for p in points]) if points else np.zeros((0, 3))
    for t in tips:
        p = v[t]
        if p[2] < 1.0:
            continue
        if len(existing) and np.min(np.hypot(*(existing[:, :2] - p[:2]).T) + np.abs(existing[:, 2] - p[2])) < spacing * 0.5:
            continue
        points.append((p.copy(), 0.0))
    return points


def build_supports(mesh: Mesh, cfg: Config) -> tuple[Mesh, list[np.ndarray]]:
    """Support geometry for a model that already sits at its elevated height."""
    pts = support_points(mesh, cfg)
    tip_r = cfg["sla_tip_diameter"] / 2
    pillar_r = cfg["sla_pillar_diameter"] / 2
    raft = cfg["sla_raft_thickness"]
    head_len = max(1.0, pillar_r * 3)
    parts = []
    feet = []
    for p, base in pts:
        x, y, top = p
        top += 0.2  # penetrate slightly into the model for a solid bond
        on_bed = base <= 1e-6
        bottom = raft if on_bed else base
        height = top - bottom
        if height < 0.5:
            continue
        head = min(head_len, height * 0.5)
        if on_bed:
            foot = min(1.0, height * 0.2)
            parts.append(frustum(pillar_r * 2, pillar_r, foot, SEGMENTS, bottom - 0.05, (x, y)))
            feet.append(np.array([x, y]))
        else:
            foot = min(head, height * 0.25)
            parts.append(frustum(tip_r, pillar_r, foot, SEGMENTS, bottom - 0.2, (x, y)))
        body_z = bottom + foot
        body_h = top - head - body_z
        if body_h > 0:
            parts.append(frustum(pillar_r, pillar_r, body_h + 0.01, SEGMENTS, body_z, (x, y)))
        parts.append(frustum(pillar_r, tip_r, head, SEGMENTS, top - head, (x, y)))
    if raft > 0 and feet:
        hull = MultiPoint([tuple(f) for f in feet]).buffer(pillar_r * 2 + 2.0, quad_segs=4).convex_hull
        ring = np.asarray(hull.exterior.coords)[:-1]
        if _signed_area(ring) < 0:
            ring = ring[::-1]
        parts.append(prism(ring, 0.0, raft))
    merged = Mesh.merge(parts, "Supports")
    return merged, [p for p, _ in pts]


def _signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
