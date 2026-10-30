"""Resin supports: support points from overhangs, tapered pillars and a raft."""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import scale as shapely_scale
from shapely.geometry import LineString, MultiPoint
from shapely.geometry import Polygon as ShapelyPolygon

from .mesh import Mesh
from .primitives import ball, frustum, tube
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

    def __init__(self, mesh: Mesh, cell: float = 2.0):
        self.tris = mesh.triangles
        self.normals = mesh.face_normals()
        self.tmin = self.tris[:, :, :2].min(axis=1)
        self.tmax = self.tris[:, :, :2].max(axis=1)
        # XY bucket grid: each cell lists the triangles whose footprint touches it, so a vertical ray
        # only looks at a handful of triangles instead of the whole model
        self.cell = cell
        self.origin = self.tmin.min(axis=0) if len(self.tris) else np.zeros(2)
        i0 = np.floor((self.tmin - self.origin) / cell).astype(np.int64)
        i1 = np.floor((self.tmax - self.origin) / cell).astype(np.int64)
        self.ny = int(i1[:, 1].max()) + 1 if len(self.tris) else 1
        w, h = i1[:, 0] - i0[:, 0] + 1, i1[:, 1] - i0[:, 1] + 1
        count = w * h
        tri = np.repeat(np.arange(len(self.tris)), count)
        k = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
        keys = (i0[tri, 0] + k % w[tri]) * self.ny + (i0[tri, 1] + k // w[tri])
        order = np.argsort(keys, kind="stable")
        self.keys, self.bucket = keys[order], tri[order]

    def _near(self, x: float, y: float) -> np.ndarray:
        cx = int(np.floor((x - self.origin[0]) / self.cell))
        cy = int(np.floor((y - self.origin[1]) / self.cell))
        if cx < 0 or cy < 0 or cy >= self.ny:
            return np.zeros(0, dtype=np.int64)
        key = cx * self.ny + cy
        a, b = np.searchsorted(self.keys, [key, key + 1])
        return self.bucket[a:b]

    def hits(self, x: float, y: float, candidates: np.ndarray | None = None):
        """Sorted (z, face index) of all surfaces crossed by the vertical line through (x, y)."""
        idx = candidates if candidates is not None else self._near(x, y)
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


def underside_contact(caster: "_Caster", p: np.ndarray) -> np.ndarray | None:
    """Where a support for the clicked point ``p`` must touch: ``p`` itself when it is on a downward-facing
    surface, otherwise the nearest downward-facing surface below it on the same vertical line (so a click
    on top of a part never gives a support that runs through it). None if there is no such surface."""
    z, faces = caster.hits(p[0], p[1])
    if len(z) == 0:
        return None
    k = int(np.argmin(np.abs(z - p[2])))
    if abs(z[k] - p[2]) < 0.5 and caster.normals[faces[k], 2] < -0.02:
        return np.array([p[0], p[1], z[k]])
    below = (z <= p[2] + 1e-6) & (caster.normals[faces, 2] < -0.02)
    if not np.any(below):
        return None
    return np.array([p[0], p[1], float(z[below].max())])


def effective_spacing(cfg: Config) -> float:
    """Grid spacing of support points: the set spacing, tightened or loosened by the density percentage."""
    return cfg["sla_support_spacing"] / math.sqrt(max(cfg["sla_density"], 1.0) / 100.0)


def auto_support_points(mesh: Mesh, cfg: Config) -> np.ndarray:
    """Contact points (N, 3) on the underside of ``mesh`` that need support."""
    caster = _Caster(mesh)
    threshold = -math.sin(math.radians(cfg["support_angle"]))
    overhang = caster.normals[:, 2] < threshold
    spacing = effective_spacing(cfg)
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

    Returns (segments [(p, q, r_p, r_q, tips)], roots [(point, weight, tips)]); ``tips`` are start indices."""
    tan_a = math.tan(math.radians(min(max(cfg["sla_branch_angle"], 5.0), 75.0)))
    max_d = cfg["sla_branch_reach"] or max(6.0, effective_spacing(cfg) * 4)
    nodes = [np.array(p, dtype=np.float64) for p in starts]
    weights = [1] * len(nodes)
    members = [(k,) for k in range(len(nodes))]     # which tips feed each branch end
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
            segments.append((x[i], m, radius(w[i]), r_m, members[i]))
            segments.append((x[j], m, radius(w[j]), r_m, members[j]))
            joined = members[i] + members[j]
            for k in sorted((i, j), reverse=True):
                del nodes[k]
                del weights[k]
                del members[k]
            nodes.append(m)
            weights.append(int(total))
            members.append(joined)
            merged = True
            break
        if not merged:
            break
    return segments, list(zip(nodes, weights, members))


def _braces(pillars: list, under: "_Underside", cfg: Config, segs: int) -> list[Mesh]:
    """Zig-zag struts between each pillar and its nearest neighbours, every brace interval in height."""
    step = cfg["sla_brace_spacing"]
    reach = max(8.0, effective_spacing(cfg) * 3)
    xy = np.array([[p[0], p[1]] for p in pillars])
    done, out = set(), []
    for i, (x, y, z0, z1, r) in enumerate(pillars):
        d = np.hypot(*(xy - xy[i]).T)
        for j in np.argsort(d)[1:3]:                 # two nearest neighbours
            key = (min(i, j), max(i, j))
            if d[j] > reach or key in done:
                continue
            done.add(key)
            lo = max(z0, pillars[j][2]) + 0.5
            hi = min(z1, pillars[j][3]) - 0.5
            rb = min(r, pillars[j][4]) * 0.5
            z, flip = lo, False
            while z + step <= hi:
                a = np.array([x, y, z + (step if flip else 0)])
                b = np.array([pillars[j][0], pillars[j][1], z + (0 if flip else step)])
                if under.clear(a, b, rb + 0.5) and under.clear(b, a, rb + 0.5):
                    out.append(tube(a, b, rb, rb, max(4, segs // 2)))
                z += step
                flip = not flip
    return out


SHAPE_SEGMENTS = {"cylinder": SEGMENTS, "prism": 6, "cube": 4}


def _sample_convex(poly, n: int = 48) -> np.ndarray:
    """n points on a convex polygon's boundary at evenly spaced angles around its centroid."""
    c = np.array(poly.centroid.coords[0])
    big = max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) * 2 + 1
    pts = []
    ring = poly.exterior
    for a in np.linspace(0, 2 * np.pi, n, endpoint=False):
        far = c + big * np.array([np.cos(a), np.sin(a)])
        hit = LineString([tuple(c), tuple(far)]).intersection(ring)
        coords = [np.array(g.coords[0]) for g in getattr(hit, "geoms", [hit]) if not g.is_empty]
        pts.append(max(coords, key=lambda q: np.hypot(*(q - c))) if coords else c)
    return np.array(pts)


def _slab(poly, z0: float, z1: float, slope_deg: float) -> Mesh:
    """Convex raft slab; with a slope below 90 degrees its top edge is set back (chamfered sides)."""
    inset = 0.0 if slope_deg >= 89.9 else (z1 - z0) / math.tan(math.radians(slope_deg))
    bottom = poly
    top = poly.buffer(-inset) if inset > 0 else poly
    if top.is_empty or top.area < poly.area * 0.2:
        top = poly
    b, t = _sample_convex(bottom), _sample_convex(top)
    n = len(b)
    v = np.vstack([np.column_stack([b, np.full(n, z0)]), np.column_stack([t, np.full(n, z1)]),
                   [[*b.mean(axis=0), z0], [*t.mean(axis=0), z1]]])
    i = np.arange(n)
    j = (i + 1) % n
    sides = np.vstack([np.column_stack([i, j, i + n]), np.column_stack([j, j + n, i + n])])
    caps = np.vstack([np.column_stack([np.full(n, 2 * n), j, i]), np.column_stack([np.full(n, 2 * n + 1), i + n, j + n])])
    return Mesh(v, np.vstack([sides, caps]), "Raft")


def _rafts(feet: list, cfg: Config, pad: float) -> list[Mesh]:
    """Raft geometry under the support feet for the chosen raft type."""
    kind = cfg["sla_raft_type"]
    raft = cfg["sla_raft_thickness"]
    if kind == "none" or raft <= 0 or not feet:
        return []
    pts = np.array(feet)
    margin = pad + cfg["sla_raft_margin"]
    slope = cfg["sla_raft_slope"]
    scale = math.sqrt(max(cfg["sla_raft_area_ratio"], 1.0) / 100.0)

    def outline(group):
        hull = MultiPoint([tuple(q) for q in group]).buffer(margin, quad_segs=6).convex_hull
        return shapely_scale(hull, scale, scale, origin="centroid") if scale != 1 else hull

    if kind == "mini":
        # group feet that are close together; each group gets its own small raft
        parent = list(range(len(pts)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        reach = max(cfg["sla_raft_group_reach"], 2 * margin + 2)
        for a in range(len(pts)):
            d = np.hypot(*(pts - pts[a]).T)
            for b in np.flatnonzero(d < reach):
                parent[find(a)] = find(int(b))
        groups: dict = {}
        for a in range(len(pts)):
            groups.setdefault(find(a), []).append(pts[a])
        return [_slab(outline(g), 0.0, raft, slope) for g in groups.values()]
    hull = outline(pts)
    if kind == "grid":
        # outline band plus parallel bars, every piece convex so it can be a closed slab
        out = []
        ring = _sample_convex(hull, 32)
        for k in range(len(ring)):
            a, b = ring[k], ring[(k + 1) % len(ring)]
            d = b - a
            n = np.array([-d[1], d[0]]) / (np.hypot(*d) or 1) * max(cfg["sla_raft_bar_width"] * 0.66, 0.3)
            quad = ShapelyPolygon([tuple(a - n), tuple(b - n), tuple(b + n), tuple(a + n)]).convex_hull
            out.append(_slab(quad, 0.0, raft, 90))
        for q in pts:   # every foot stands on a pad, never over a gap of the grid
            out.append(_slab(ShapelyPolygon(MultiPoint([tuple(q)]).buffer(pad * 0.6 + 0.5, quad_segs=4).exterior.coords),
                             0.0, raft, 90))
        minx, miny, maxx, maxy = hull.bounds
        step, half = cfg["sla_raft_bar_spacing"], cfg["sla_raft_bar_width"] / 2
        for y in np.arange(miny + step / 2, maxy, step):
            bar = hull.intersection(ShapelyPolygon([(minx, y - half), (maxx, y - half), (maxx, y + half), (minx, y + half)]))
            if not bar.is_empty and bar.area > 0.5:
                out.append(_slab(bar.convex_hull, 0.0, raft, 90))
        for x in np.arange(minx + step / 2, maxx, step):
            bar = hull.intersection(ShapelyPolygon([(x - half, miny), (x + half, miny), (x + half, maxy), (x - half, maxy)]))
            if not bar.is_empty and bar.area > 0.5:
                out.append(_slab(bar.convex_hull, 0.0, raft, 90))
        return out
    return [_slab(hull, 0.0, raft, slope)]


def build_supports(mesh: Mesh, cfg: Config, points: np.ndarray | None = None, owners: dict | None = None,
                   cache: dict | None = None):
    """Support geometry for a model that already sits at its elevated height.

    ``points`` are contact points in the same coordinates (None = generate them automatically).
    Returns the support mesh and the points that actually got a support. With ``owners`` (a dict),
    it is filled with "faces": the owner group of every support face (-1 = raft) and "groups": for each
    group the indices of ``points`` it belongs to, so a click on any part of a support can find it.
    ``cache`` (a dict kept by the caller for this exact mesh) keeps the ray and underside indexes
    between calls, which makes re-building after adding or removing one support quick."""
    cache = {} if cache is None else cache
    if points is None:
        points = auto_support_points(mesh, cfg)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if "caster" not in cache:
        cache["caster"] = _Caster(mesh)
    caster = cache["caster"]
    tip_r = cfg["sla_tip_diameter"] / 2
    pillar_r = max(cfg["sla_pillar_diameter"] / 2, tip_r)
    raft = 0.0 if cfg["sla_raft_type"] == "none" else cfg["sla_raft_thickness"]
    depth = cfg["sla_contact_depth"]
    on_model = cfg["sla_supports_on_model"]
    head_len = cfg["sla_head_length"] or max(1.0, pillar_r * 3)
    segs = SHAPE_SEGMENTS.get(cfg["sla_pillar_shape"], SEGMENTS)
    base_shape = cfg["sla_base_shape"]
    sphere_tip = cfg["sla_tip_shape"] == "sphere"
    tree = cfg["sla_support_type"] == "tree"
    max_tilt = math.radians(cfg["sla_head_tilt"])
    widen = cfg["sla_pillar_widening"] / 100.0 / 10.0      # extra radius fraction per mm of height
    parts, feet, used, tree_tips, pillars = [], [], [], [], []
    part_owner, groups, tip_index = [], [], []
    cur = [-1]                         # owner group of the parts being added

    class _Parts(list):
        def append(self, m):           # every part remembers which support it belongs to
            super().append(m)
            part_owner.append(cur[0])

        def __iadd__(self, more):
            for m in more:
                self.append(m)
            return self

    parts = _Parts()

    def own(indices) -> None:
        groups.append(tuple(int(i) for i in indices))
        cur[0] = len(groups) - 1

    def widened(r, height):
        return r * (1.0 + widen * max(height, 0.0))

    def head_direction(p):
        """Unit vector from the contact point down along the head: the surface normal, limited to ``max_tilt``."""
        if max_tilt <= 0:
            return None
        z, faces = caster.hits(p[0], p[1])
        if len(z) == 0:
            return None
        n = caster.normals[faces[int(np.argmin(np.abs(z - p[2])))]]
        if n[2] >= -1e-3:
            return None
        horiz = math.hypot(n[0], n[1])
        ang = min(math.atan2(horiz, -n[2]), max_tilt)
        if ang < math.radians(2) or horiz < 1e-9:
            return None
        return np.array([n[0] / horiz * math.sin(ang), n[1] / horiz * math.sin(ang), -math.cos(ang)])

    def foot_on_bed(x, y, r, top):
        """Base standing on the raft (or the plate), from there up to at most ``top``. Returns its top height."""
        feet.append(np.array([x, y]))
        if base_shape == "none":
            return raft - 0.05
        base_r = cfg["sla_base_diameter"] / 2 or r * 2
        h = min(cfg["sla_base_thickness"], (top - raft) * 0.5)
        if base_shape == "disk":
            parts.append(frustum(base_r, base_r, h + 0.05, segs, raft - 0.05, (x, y)))
        elif base_shape == "cube":
            parts.append(frustum(base_r * 1.2, base_r * 1.2, h + 0.05, 4, raft - 0.05, (x, y)))
        else:
            parts.append(frustum(base_r, r, h + 0.05, segs, raft - 0.05, (x, y)))
        return raft + h

    def head(x, y, top, h, r_body, tip=None):
        if tip is None:
            parts.append(frustum(r_body, tip_r, h, segs, top - h, (x, y)))
            tip = np.array([x, y, top])
        else:      # leaning head from the top of the pillar to the contact point
            parts.append(tube([x, y, top - h], tip, r_body, tip_r, segs))
            parts.append(ball([x, y, top - h], r_body, 3, segs))
        if sphere_tip:
            parts.append(ball(tip - [0, 0, tip_r * 0.3], tip_r * 1.25, 4, 10))

    for index, p in enumerate(points):
        contact = underside_contact(caster, p)
        if contact is None:
            continue                     # nothing to hold from below here (e.g. a point on top of the part)
        p = contact
        own([index])
        d = head_direction(p)
        tip = None
        if d is not None:
            # the pillar stands under the far end of the leaning head
            tip = p - d * depth
            q = p + d * head_len
            if caster.base_below(q) > 0 and caster.base_below(p) <= 0:
                d, tip = None, None        # leaning would put the pillar on the model: stay vertical
        base = caster.base_below(p if d is None else q)
        if base > 0 and not on_model:
            continue  # the pillar would have to stand on the model
        if d is None:
            x, y, top = p
            top += depth  # penetrate into the model for a solid bond
        else:
            x, y = q[0], q[1]
            top = q[2] + head_len
        on_bed = base <= 1e-6
        bottom = raft if on_bed else base
        height = top - bottom
        if height < 0.5:
            continue
        h_len = head_len if d is not None else min(head_len, height * 0.5)
        if d is not None and height - h_len < 0.5:     # too close to the plate to lean: vertical head
            d, tip = None, None
            x, y, top = p[0], p[1], p[2] + depth
            base = caster.base_below(p)
            on_bed = base <= 1e-6
            bottom = raft if on_bed else base
            height = top - bottom
            if height < 0.5 or (not on_bed and not on_model):
                continue
            h_len = min(head_len, height * 0.5)
        used.append(p)
        if tree and on_bed and height > h_len + 3.0:
            # the head is built now; the branch below it is decided when the trees are grown
            head(x, y, top, h_len, pillar_r * 0.8, tip)
            tree_tips.append(np.array([x, y, top - h_len]))
            tip_index.append(index)
            continue
        r_low = widened(pillar_r, top - h_len - bottom) if on_bed else pillar_r
        if on_bed:
            body_z = foot_on_bed(x, y, r_low, top - h_len)
        else:
            foot = min(h_len, height * 0.25)
            parts.append(frustum(tip_r, pillar_r, foot + depth, segs, bottom - depth, (x, y)))
            body_z = bottom + foot
        body_h = top - h_len - body_z
        if body_h > 0:
            parts.append(frustum(r_low, pillar_r, body_h + 0.01, segs, body_z, (x, y)))
            if on_bed:
                pillars.append((x, y, body_z, top - h_len, pillar_r))
        head(x, y, top, h_len, pillar_r, tip)

    def underside():
        if "under" not in cache:
            cache["under"] = _Underside(mesh)
        return cache["under"]

    if tree_tips:
        under = underside()
        branch_r = pillar_r * 0.8

        def radius(weight):
            return min(pillar_r * 2.0, branch_r * math.sqrt(weight))

        segments, roots = _grow_trees(np.array(tree_tips), under, cfg, raft + 3.0, radius)
        for a, b, ra, rb, tips in segments:
            own([tip_index[t] for t in tips])
            parts.append(tube(a, b, ra, rb, segs))
            parts.append(ball(a, ra, 3, SEGMENTS))
            parts.append(ball(b, rb, 3, SEGMENTS))
        for node, weight, tips in roots:
            own([tip_index[t] for t in tips])
            r = radius(weight)
            r_low = widened(r, node[2] - raft)
            body_z = foot_on_bed(node[0], node[1], r_low, node[2])
            if node[2] > body_z:
                parts.append(frustum(r_low, r, node[2] - body_z + 0.01, segs, body_z, (node[0], node[1])))
                pillars.append((node[0], node[1], body_z, node[2], r))
            parts.append(ball(node, r, 3, SEGMENTS))

    cur[0] = -1                        # braces and rafts are shared
    if cfg["sla_bracing"] and len(pillars) > 1:
        parts += _braces(pillars, underside(), cfg, segs)

    parts += _rafts(feet, cfg, pillar_r * 4)
    merged = Mesh.merge(list(parts), "Supports")
    if owners is not None:
        owners["faces"] = np.concatenate([np.full(len(m.faces), o, dtype=np.int64) for m, o in zip(parts, part_owner)]) \
            if parts else np.zeros(0, dtype=np.int64)
        owners["groups"] = groups
    return merged, np.array(used).reshape(-1, 3)


def _signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
