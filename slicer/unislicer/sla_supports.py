"""Resin supports: support points from overhangs, tapered pillars and a raft."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import MultiPoint

from .mesh import Mesh
from .primitives import ball, frustum, prism, tube
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


class _Underside:
    """Height map of the lowest model surface over each XY cell (conservative), for keeping branches clear."""

    def __init__(self, mesh: Mesh, cell: float = 1.0):
        tris = mesh.triangles
        # split large triangles so a bounding-box rasterisation stays tight
        for _ in range(8):
            edges = np.linalg.norm(tris[:, [1, 2, 0], :2] - tris[:, :, :2], axis=2).max(axis=1)
            big = edges > cell * 2
            if not big.any():
                break
            t = tris[big]
            m01, m12, m20 = (t[:, 0] + t[:, 1]) / 2, (t[:, 1] + t[:, 2]) / 2, (t[:, 2] + t[:, 0]) / 2
            split = np.concatenate([np.stack([t[:, 0], m01, m20], 1), np.stack([m01, t[:, 1], m12], 1),
                                    np.stack([m20, m12, t[:, 2]], 1), np.stack([m01, m12, m20], 1)])
            tris = np.concatenate([tris[~big], split])
        self.cell = cell
        lo = tris[:, :, :2].min(axis=(0, 1)) - cell
        hi = tris[:, :, :2].max(axis=(0, 1)) + cell
        self.origin = lo
        self.shape = (int(np.ceil((hi[0] - lo[0]) / cell)) + 1, int(np.ceil((hi[1] - lo[1]) / cell)) + 1)
        grid = np.full(self.shape, np.inf)
        i0 = np.floor((tris[:, :, 0].min(axis=1) - lo[0]) / cell).astype(int)
        i1 = np.floor((tris[:, :, 0].max(axis=1) - lo[0]) / cell).astype(int)
        j0 = np.floor((tris[:, :, 1].min(axis=1) - lo[1]) / cell).astype(int)
        j1 = np.floor((tris[:, :, 1].max(axis=1) - lo[1]) / cell).astype(int)
        w, h = i1 - i0 + 1, j1 - j0 + 1
        count = w * h
        tri = np.repeat(np.arange(len(tris)), count)
        k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
        ii = i0[tri] + k % w[tri]
        jj = j0[tri] + k // w[tri]
        np.minimum.at(grid, (ii, jj), tris[:, :, 2].min(axis=1)[tri])
        # dilate by one cell so branch thickness and diagonal neighbours are covered
        pad = np.pad(grid, 1, constant_values=np.inf)
        self.grid = np.min([pad[1 + dx:1 + dx + self.shape[0], 1 + dy:1 + dy + self.shape[1]]
                            for dx in (-1, 0, 1) for dy in (-1, 0, 1)], axis=0)

    def at(self, xy: np.ndarray) -> np.ndarray:
        idx = np.floor((np.asarray(xy) - self.origin) / self.cell).astype(int)
        inside = (idx[..., 0] >= 0) & (idx[..., 0] < self.shape[0]) & (idx[..., 1] >= 0) & (idx[..., 1] < self.shape[1])
        out = np.full(idx.shape[:-1], np.inf)
        out[inside] = self.grid[idx[inside][:, 0], idx[inside][:, 1]]
        return out

    def clear(self, a: np.ndarray, b: np.ndarray, margin: float) -> bool:
        """True if the straight segment a->b stays ``margin`` below the model everywhere (start excluded)."""
        t = np.linspace(0.15, 1.0, 8)[:, None]
        pts = a + (b - a) * t
        return bool(np.all(pts[:, 2] < self.at(pts[:, :2]) - margin))


def _grow_trees(starts: np.ndarray, under: _Underside, cfg: Config, floor: float, radius):
    """Greedily merge branch ends pairwise (highest meeting point first) into trees.

    Returns (segments [(p, q, r_p, r_q)], roots [(point, weight)])."""
    tan_a = math.tan(math.radians(min(max(cfg["sla_branch_angle"], 5.0), 75.0)))
    max_d = max(6.0, cfg["sla_support_spacing"] * 4)
    nodes = [np.array(p, dtype=np.float64) for p in starts]
    weights = [1] * len(nodes)
    segments = []
    while len(nodes) > 1:
        x = np.array(nodes)
        w = np.array(weights)
        d = np.linalg.norm(x[:, None, :2] - x[None, :, :2], axis=2)
        mz = np.minimum(x[:, None, 2], x[None, :, 2]) - d / 2 / tan_a
        ok = (d <= max_d) & (mz > floor) & np.triu(np.ones_like(d, dtype=bool), 1)
        if not ok.any():
            break
        cand = np.argwhere(ok)
        order = np.argsort(-mz[ok])[:200]
        merged = False
        for ci in order:
            i, j = cand[ci]
            total = w[i] + w[j]
            m = np.array([(x[i, 0] + x[j, 0]) / 2, (x[i, 1] + x[j, 1]) / 2, mz[i, j]])
            r_m = radius(total)
            if not (under.clear(x[i], m, r_m + 0.5) and under.clear(x[j], m, r_m + 0.5)):
                continue
            segments.append((x[i], m, radius(w[i]), r_m))
            segments.append((x[j], m, radius(w[j]), r_m))
            for k in sorted((i, j), reverse=True):
                del nodes[k]
                del weights[k]
            nodes.append(m)
            weights.append(int(total))
            merged = True
            break
        if not merged:
            break
    return segments, list(zip(nodes, weights))


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
    tree = cfg["sla_support_type"] == "tree"
    parts, feet, used, tree_tips = [], [], [], []

    def foot_on_bed(x, y, r, top):
        """Wide cone sitting on the raft, from the raft up to height ``top``."""
        foot = min(1.0, (top - raft) * 0.5)
        parts.append(frustum(r * 2, r, foot + 0.05, SEGMENTS, raft - 0.05, (x, y)))
        feet.append(np.array([x, y]))
        return raft + foot

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
        used.append(p)
        if tree and on_bed and height > head + 3.0:
            # the head is built now; the branch below it is decided when the trees are grown
            parts.append(frustum(pillar_r * 0.8, tip_r, head, SEGMENTS, top - head, (x, y)))
            tree_tips.append(np.array([x, y, top - head]))
            continue
        if on_bed:
            body_z = foot_on_bed(x, y, pillar_r, top - head)
        else:
            foot = min(head, height * 0.25)
            parts.append(frustum(tip_r, pillar_r, foot + depth, SEGMENTS, bottom - depth, (x, y)))
            body_z = bottom + foot
        body_h = top - head - body_z
        if body_h > 0:
            parts.append(frustum(pillar_r, pillar_r, body_h + 0.01, SEGMENTS, body_z, (x, y)))
        parts.append(frustum(pillar_r, tip_r, head, SEGMENTS, top - head, (x, y)))

    if tree_tips:
        under = _Underside(mesh)
        branch_r = pillar_r * 0.8

        def radius(weight):
            return min(pillar_r * 2.0, branch_r * math.sqrt(weight))

        segments, roots = _grow_trees(np.array(tree_tips), under, cfg, raft + 3.0, radius)
        for a, b, ra, rb in segments:
            parts.append(tube(a, b, ra, rb, SEGMENTS))
            parts.append(ball(a, ra, 3, SEGMENTS))
            parts.append(ball(b, rb, 3, SEGMENTS))
        for node, weight in roots:
            r = radius(weight)
            body_z = foot_on_bed(node[0], node[1], r, node[2])
            if node[2] > body_z:
                parts.append(frustum(r, r, node[2] - body_z + 0.01, SEGMENTS, body_z, (node[0], node[1])))
            parts.append(ball(node, r, 3, SEGMENTS))

    if raft > 0 and feet:
        hull = MultiPoint([tuple(f) for f in feet]).buffer(pillar_r * 4 + cfg["sla_raft_margin"], quad_segs=4).convex_hull
        ring = np.asarray(hull.exterior.coords)[:-1]
        if _signed_area(ring) < 0:
            ring = ring[::-1]
        parts.append(prism(ring, 0.0, raft))
    return Mesh.merge(parts, "Supports"), np.array(used).reshape(-1, 3)


def _signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
