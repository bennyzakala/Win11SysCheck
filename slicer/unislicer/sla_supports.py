"""Resin supports: support points from overhangs, tapered pillars and a raft."""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.affinity import scale as shapely_scale
from shapely.geometry import LineString, MultiPoint
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

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


_CASTERS: dict = {}
CASTER_FACE_BUDGET = 4_000_000


def caster_for(mesh: Mesh) -> "_Caster":
    """The ray caster of a mesh, shared: auto supports and the support preview both need it for the same
    placed model (each build takes ~0.5 s on a big scan). Keyed by content, so a moved or turned model
    gets a new one."""
    key = (len(mesh.faces), hash(mesh.vertices.tobytes()), hash(mesh.faces.tobytes()))
    hit = _CASTERS.pop(key, None)
    if hit is None:
        hit = _Caster(mesh)
    _CASTERS[key] = hit                  # most recently used last
    # a full plate of parts is kept, within a budget of triangles (~70 bytes each): 48 big jaw scans used
    # to stay in memory, ~2 GB
    while len(_CASTERS) > 1 and sum(k[0] for k in _CASTERS) > CASTER_FACE_BUDGET:
        _CASTERS.pop(next(iter(_CASTERS)))
    return hit


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

    def solid_spans(self, x: float, y: float) -> np.ndarray:
        """(k, 2) z ranges where the vertical line through (x, y) is inside the model."""
        z, _ = self.hits(x, y)
        if len(z) > 1:
            z = z[np.concatenate([[True], np.diff(z) > 1e-4])]     # one hit where triangles share an edge
        n = len(z) // 2 * 2
        return z[:n].reshape(-1, 2)

    def column_blocked(self, x: float, y: float, r: float, z0: float, z1: float, tol: float = 0.1) -> bool:
        """True when a vertical column of radius ``r`` from z0 to z1 would run through the model (checked
        on its axis and on a ring around it)."""
        if z1 - z0 <= 2 * tol:
            return False
        ring = [(0.0, 0.0)] + [(r * math.cos(a), r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
        for dx, dy in (ring if r > 1e-6 else ring[:1]):
            spans = self.solid_spans(x + dx, y + dy)
            if len(spans) and np.any((spans[:, 0] < z1 - tol) & (spans[:, 1] > z0 + tol)):
                return True
        return False

    def inside(self, p: np.ndarray) -> bool:
        spans = self.solid_spans(p[0], p[1])
        return bool(len(spans) and np.any((spans[:, 0] < p[2]) & (spans[:, 1] > p[2])))

    def base_below(self, p: np.ndarray) -> float | None:
        """Height where a pillar dropped from ``p`` lands: 0 for the bed, or the top of the model below it."""
        z, faces = self.hits(p[0], p[1])
        below = (z < p[2] - 1e-3) & (self.normals[faces, 2] > 0.3)       # a floor, not a steep wall
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


def auto_support_points(mesh: Mesh, cfg: Config, report: dict | None = None) -> np.ndarray:
    """Contact points (N, 3) on the underside of ``mesh`` that need support."""
    caster = caster_for(mesh)
    threshold = -math.sin(math.radians(cfg["support_angle"]))
    overhang = caster.normals[:, 2] < threshold
    spacing = effective_spacing(cfg)
    lo, hi = mesh.bounds
    xs = np.arange(lo[0] + spacing / 2, hi[0], spacing)
    ys = np.arange(lo[1] + spacing / 2, hi[1], spacing)
    points = []
    for gx in (xs if cfg["sla_support_overhangs"] else []):
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
    if cfg["sla_support_minima"]:
        points += _tip_points(mesh, caster.normals, threshold, spacing, points)
    pts = np.array(points, dtype=np.float64).reshape(-1, 3)
    if len(pts) and cfg["sla_skip_ledges"] and float(np.max(hi[:2] - lo[:2])) > 40.0:
        pts = pts[[not _small_ledge(caster, p) for p in pts]]   # a jaw model: its ledges hold themselves
    if len(pts) and cfg["sla_avoid_fit"]:
        fit = fitting_surface(mesh, pts)                        # never on a crown's / guide's / splint's inside
        if report is not None:
            report["fit_dropped"], report["points"] = int(fit.sum()), len(pts)
        pts = pts[~fit]
    # every part but a jaw model (whose own ledges and minima print fine: the model types skip ledges);
    # by type, not size - a long bridge is not a jaw
    if cfg["sla_support_minima"] and not (cfg["sla_skip_ledges"] and float(np.max(hi[:2] - lo[:2])) > 40.0):
        extra = _island_minima(mesh, caster, cfg, pts)
        if len(extra) and cfg["sla_avoid_fit"]:
            extra = extra[~fitting_surface(mesh, extra)]
        if report is not None:
            report["minima_added"] = int(len(extra))
        if len(extra):
            pts = np.vstack([pts, extra])
    return pts


TIP_AREA = 8.0           # mm2: a region starting in the air at most this big is a tip, edge or cusp


def _island_minima(mesh: Mesh, caster: "_Caster", cfg: Config, pts: np.ndarray, near: float = 0.5) -> np.ndarray:
    """Every place a part starts in mid-air with open space under it (the lowest point of a cusp, of a
    pontic, of an edge), found by slicing the part at the print's layer height: a support right there unless
    one already touches within ``near`` mm. The grid and the tip search could leave a cusp tip a millimetre
    from its support, so its first layers printed in the air (a test print of temporary crowns needed
    supports added by hand). Not for jaw models: their own minima print fine."""
    from .slicing import slice_mesh
    from .sla import find_islands
    lo, hi = mesh.bounds
    lh = max(0.03, float(cfg["sla_layer_height"]))
    first = (math.floor(lo[2] / lh) + 0.5) * lh                # the print's own cut heights (from z = 0)
    planes = [z for z in np.arange(first, hi[2], lh) if z > lo[2]]
    if len(planes) < 2:
        return np.zeros((0, 3))
    try:
        layers = slice_mesh(mesh, planes)
    except Exception:
        return np.zeros((0, 3))
    out = []
    have = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    import shapely
    from shapely.geometry import Point
    verts = np.asarray(mesh.vertices)
    for i, poly in [(0, p) for p in getattr(layers[0], "geoms", [layers[0]]) if not p.is_empty] + find_islands(layers, 0.003):
        z = planes[i]
        if poly.area > TIP_AREA:
            continue                                  # a broad flat underside: the grid supports it
        # the region's first layer can hold several low spots (its middle a millimetre from the tip that
        # prints first): each vertex there gets a support within ``near``, lowest first, a few per region
        x0, y0, x1, y1 = poly.bounds
        sel = np.nonzero((verts[:, 0] >= x0 - 0.05) & (verts[:, 0] <= x1 + 0.05) & (verts[:, 1] >= y0 - 0.05)
                         & (verts[:, 1] <= y1 + 0.05) & (verts[:, 2] <= z + lh))[0]
        if len(sel):
            sel = sel[shapely.contains_xy(poly.buffer(0.05), verts[sel, 0], verts[sel, 1])]
        spots = verts[sel[np.argsort(verts[sel, 2])]] if len(sel) else \
            np.array([[poly.representative_point().x, poly.representative_point().y, z]])
        added = 0
        for spot in spots:
            if added >= 5:
                break
            both = np.vstack([have] + out) if out else have
            if len(both) and np.any((np.hypot(both[:, 0] - spot[0], both[:, 1] - spot[1]) < near)
                                    & (np.abs(both[:, 2] - spot[2]) < 0.3)):
                continue                              # this spot is held already
            hz, _ = caster.hits(spot[0], spot[1])
            if np.any(hz < z - lh - 0.05):
                continue                              # over the part itself: no support from the plate there
            contact = underside_contact(caster, np.array([spot[0], spot[1], z + lh]))
            if contact is None:
                continue
            out.append(contact[None, :])
            added += 1
    return np.vstack(out) if out else np.zeros((0, 3))


NEAR_IGNORE = 0.8        # mm


def fitting_surface(mesh: Mesh, pts: np.ndarray, reach: float = 12.0) -> np.ndarray:
    """Which contact points lie inside a hollow of the part - the fitting (intaglio) surface of a crown,
    bridge, guide, splint or denture, a guide's sleeve holes: looking out from the point, sideways and down
    (where a support would come from), the part closes around it in at least two thirds of 24 directions.
    On the outside of a part most of those directions are open. Rays go against a light copy of the part."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    out = np.zeros(len(pts), dtype=bool)
    if len(pts) == 0:
        return out
    tris = mesh.decimated(5000).triangles
    v0 = tris[:, 0]
    e1, e2 = tris[:, 1] - v0, tris[:, 2] - v0
    tlo, thi = tris.min(axis=1), tris.max(axis=1)
    dirs = []
    for elev in (-15.0, -35.0, -60.0):                           # below the horizon: where supports come from
        c, sz = math.cos(math.radians(elev)), math.sin(math.radians(elev))
        dirs += [(c * math.cos(2 * math.pi * k / 8), c * math.sin(2 * math.pi * k / 8), sz) for k in range(8)]
    dirs = np.array(dirs)
    need = 16
    for i, p in enumerate(pts):
        near = np.all((thi >= p - reach) & (tlo <= p + reach), axis=1)
        if not near.any():
            continue
        a0, a1, a2 = v0[near], e1[near], e2[near]
        sv0 = p - a0
        closed = 0
        for j, d in enumerate(dirs):
            if closed >= need or closed + (len(dirs) - j) < need:
                break                                            # decided either way
            h = np.cross(d, a2)
            det = np.einsum("ij,ij->i", a1, h)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            sv = sv0 + d * 0.05                                  # start just off the surface
            u = np.einsum("ij,ij->i", sv, h) * inv
            q = np.cross(sv, a1)
            w = (q @ d) * inv
            t = np.einsum("ij,ij->i", a2, q) * inv
            # hits closer than NEAR_IGNORE do not count: on fine anatomy (cusps, fissures) the start point can
            # lie inside the light copy of the part, which hit it at once and made a crown's biting surface
            # (opening up) look like its inside - nearly all its supports were dropped. A hollow's walls are
            # millimetres away.
            closed += bool(np.any(ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > NEAR_IGNORE) & (t < reach)))
        out[i] = closed >= need
    return out


LEDGE_REACH = 1.0          # mm: a flat overhang this close to the wall below it holds itself up


def _small_ledge(caster: "_Caster", p: np.ndarray) -> bool:
    """A point under a narrow overhang of a jaw model (the step between its base and the gingiva, the bulge
    of a tooth over the gum): the model goes on just below it, within LEDGE_REACH to the side, so it prints
    held by that wall and a support there would only climb up the model from the plate."""
    probe = p[2] - 0.15
    for a in np.linspace(0, 2 * math.pi, 8, endpoint=False):
        spans = caster.solid_spans(p[0] + LEDGE_REACH * math.cos(a), p[1] + LEDGE_REACH * math.sin(a))
        # a wall (or the gum) going down at least 2 mm under it - not just the next bit of a sloped underside
        if len(spans) and np.any((spans[:, 0] < probe - 2.0) & (spans[:, 1] > probe)):
            return True
    return False


def drop_keepout(points: np.ndarray, zones: np.ndarray | None) -> np.ndarray:
    """Remove contact points that fall inside a support-free zone ((N, 4) centre + radius)."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if zones is None or len(zones) == 0 or len(points) == 0:
        return points
    z = np.asarray(zones, dtype=np.float64).reshape(-1, 4)
    d = np.linalg.norm(points[:, None, :] - z[None, :, :3], axis=2)
    return points[~np.any(d <= z[None, :, 3], axis=1)]


def _tip_points(mesh: Mesh, normals: np.ndarray, threshold: float, spacing: float, existing: list) -> list:
    """Local lowest points that need their own support. A scanned model (intraoral / desktop scans) has
    thousands of tiny dips from scan noise: only tips on overhanging surface count, taken lowest first,
    and none closer than half the spacing to a support already placed (grid or tip) - so the count stays
    about area / spacing², not one per noisy vertex (which used to run out of memory on big scans)."""
    v, f = mesh.vertices, mesh.faces
    if len(f) == 0:
        return []
    neighbour_min = np.full(len(v), np.inf)
    for a, b in ((0, 1), (1, 2), (2, 0), (1, 0), (2, 1), (0, 2)):
        np.minimum.at(neighbour_min, f[:, a], v[f[:, b], 2])
    down = np.zeros(len(v), dtype=bool)                 # vertex touches an overhanging face
    over = normals[:, 2] < threshold
    for c in range(3):
        down[f[over, c]] = True
    tips = np.nonzero((v[:, 2] < neighbour_min - 1e-6) & down)[0]
    if len(tips) == 0:
        return []
    tips = tips[np.argsort(v[tips, 2], kind="stable")]
    r = spacing * 0.5
    cell = max(r, 1e-3)
    grid: dict = {}

    def near(p):
        cx, cy = int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for q in grid.get((i, j), ()):
                    if math.hypot(q[0] - p[0], q[1] - p[1]) + abs(q[2] - p[2]) < r:
                        return True
        return False

    def put(p):
        grid.setdefault((int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))), []).append(p)

    for p in existing:
        put(p)
    out = []
    for t in tips:
        p = v[t]
        if near(p):
            continue
        p = [float(p[0]), float(p[1]), float(p[2])]
        put(p)
        out.append(p)
    return out


def unsupported_faces(mesh: Mesh, cfg: Config, points: np.ndarray | None, reach: float | None = None) -> np.ndarray:
    """Indices of overhanging faces with no support contact within ``reach`` (default: 1.2 x spacing),
    like PreForm's "show minima": the red areas that may fail."""
    normals = mesh.face_normals()
    over = np.flatnonzero(normals[:, 2] < -math.sin(math.radians(cfg["support_angle"])))
    if len(over) == 0:
        return over
    if points is None or len(points) == 0:
        return over
    reach = reach or effective_spacing(cfg) * 1.2
    centres = mesh.triangles[over].mean(axis=1)
    pts = np.asarray(points, dtype=float)
    far = np.ones(len(over), dtype=bool)
    for a in range(0, len(pts), 256):                 # blocks keep memory small on big models
        d = np.linalg.norm(centres[:, None, :] - pts[None, a:a + 256, :], axis=2).min(axis=1)
        far &= d > reach
    return over[far]


def island_points(layers, layer_z, islands) -> np.ndarray:
    """One contact point at the centre of each detected island (layer, polygon), for "support all islands"."""
    out = []
    for i, poly in islands:
        c = poly.representative_point()
        out.append([c.x, c.y, layer_z[i] - 0.001 if i < len(layer_z) else 0.0])
    return np.array(out, dtype=float).reshape(-1, 3)


class _Underside:
    """Height map of the lowest model surface over each XY cell (conservative), for keeping branches clear."""

    def __init__(self, mesh: Mesh, cell: float = 1.0):
        tris = mesh.triangles
        # a scan's triangles are mostly smaller than a cell: the bounding box of such a triangle touches at
        # most 2 x 2 cells, marked directly below; only the bigger ones go through the general rasterisation
        ext = (tris[:, :, :2].max(axis=1) - tris[:, :, :2].min(axis=1)).max(axis=1)
        corners = np.asarray(mesh.vertices)
        small = tris[ext <= cell]
        tris = tris[ext > cell]
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
        lo = corners[:, :2].min(axis=0) - cell
        hi = corners[:, :2].max(axis=0) + cell
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
        if len(small):
            zmin = small[:, :, 2].min(axis=1)
            si = np.floor((small[:, :, 0] - lo[0]) / cell).astype(int)
            sj = np.floor((small[:, :, 1] - lo[1]) / cell).astype(int)
            for ii in (si.min(axis=1), si.max(axis=1)):
                for jj in (sj.min(axis=1), sj.max(axis=1)):
                    np.minimum.at(grid, (ii, jj), zmin)
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


def _code_on_raft(text: str, room, raft: float, engrave: float, avoid=None):
    """Where the code goes on the part's own raft. Underside (``engrave`` > 0): mirrored, cut into the
    side on the plate - returns ("under", polygons). Top: standing up on the raft, clear of the support
    feet (``avoid``) - returns ("top", polygons). None when it does not fit at a readable size (or there
    are no fonts here)."""
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:     # fonts need the GUI; the command line skips the label
            return None
        from shapely import affinity
        from .mesh_tools import text_polygons
        base = text_polygons(text[:24], 2.0)
    except Exception:          # pragma: no cover - no Qt fonts here
        return None
    if base.is_empty or room is None or room.is_empty:
        return None
    if engrave > 0:
        placed = fit_text(affinity.scale(base, -1.0, 1.0, origin=(0, 0)), room.buffer(-0.4), min_height=1.0)
        if placed is not None:
            return "under", placed
    top_room = room.buffer(-0.4)
    placed = None
    if avoid is not None and not avoid.is_empty:          # clear of the support feet when there is room
        placed = fit_text(base, top_room.difference(avoid), min_height=1.0)
    if placed is None:
        placed = fit_text(base, top_room)
    return ("top", placed) if placed is not None else None


def fit_text(text, room, min_height: float = 0.7, max_height: float = 3.0):
    """``text`` (polygons, 2 mm tall, horizontal) placed wholly inside ``room`` as big as fits (up to
    ``max_height``), or None: straight along the room's long axis, or - on an arch-shaped or otherwise
    bent room (a bridge's raft) - bent along the room's centre line when that lets it be bigger."""
    if text.is_empty or room.is_empty:
        return None
    room = room.buffer(0)
    shapely.prepare(room)
    straight = _fit_straight(text, room, min_height, max_height)
    if straight is not None and straight[1] >= max_height * 0.999:
        return straight[0]
    # a room that fills most of its rectangle is not bent: bending cannot do better, skip the costly search
    if room.area >= 0.7 * room.minimum_rotated_rectangle.area:
        return straight[0] if straight is not None else None
    curved = _fit_curved(text, room, min_height, max_height)
    if curved is not None and (straight is None or curved[1] > straight[1] * 1.3):
        return curved[0]
    return straight[0] if straight is not None else None


def _fit_straight(text, room, min_height: float, max_height: float):
    from shapely import affinity
    rect = room.minimum_rotated_rectangle
    xy = np.asarray(rect.exterior.coords)[:4]
    edges = [xy[(k + 1) % 4] - xy[k] for k in range(2)]
    long_edge = max(edges, key=lambda e: float(np.hypot(*e)))
    angle = float(np.degrees(np.arctan2(long_edge[1], long_edge[0])))
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180                                   # read left to right, never upside down
    length = float(np.hypot(*long_edge))
    width = float(min(np.hypot(*e) for e in edges))
    tx0, ty0, tx1, ty1 = text.bounds
    tw, th = tx1 - tx0, ty1 - ty0
    centred = affinity.translate(text, -(tx0 + tx1) / 2, -(ty0 + ty1) / 2)
    c = np.asarray(rect.centroid.coords[0])
    axis = long_edge / max(np.hypot(*long_edge), 1e-9)
    k = min(0.92 * length / max(tw, 1e-6), 0.8 * width / max(th, 1e-6), max_height / max(th, 1e-6))
    while k * th >= min_height:
        piece = affinity.rotate(affinity.scale(centred, k, k, origin=(0, 0)), angle, origin=(0, 0))
        span = max(0.0, (length - k * tw) / 2)
        for off in (0.0, -0.25, 0.25, -0.5, 0.5, -0.75, 0.75):
            p = c + axis * off * span
            cand = affinity.translate(piece, p[0], p[1])
            if room.contains(cand):
                return cand, k * th
        k *= 0.9
    return None


def centre_line(room, step: float | None = None) -> np.ndarray | None:
    """The longest path through the room's medial axis (the line midway between its sides), smoothed:
    for an arch-shaped raft, the arch's middle curve. Points in order; None for a degenerate room."""
    import heapq
    if room.is_empty or room.area <= 1e-6:
        return None
    if room.geom_type == "MultiPolygon":
        room = max(room.geoms, key=lambda g: g.area)
    x0, y0, x1, y1 = room.bounds
    if step is None:
        step = max(0.1, min(0.4, min(x1 - x0, y1 - y0) / 12))
    step = max(step, room.length / 400)                    # at most ~400 boundary points: the Voronoi stays fast
    room = room.simplify(step * 0.25)
    ring_pts = []
    for ring in [room.exterior, *room.interiors]:
        ring_pts.append(np.asarray(shapely.segmentize(ring, step).coords)[:-1])
    pts = np.vstack(ring_pts)
    if len(pts) < 4:
        return None
    edges = shapely.voronoi_polygons(MultiPoint(pts), only_edges=True)
    inner = room.buffer(-step * 0.5)
    if inner.is_empty:
        return None
    shapely.prepare(inner)
    graph: dict = {}
    for e in getattr(edges, "geoms", [edges]):
        if not inner.contains(e):
            continue
        cs = np.asarray(e.coords)
        a, b = tuple(np.round(cs[0], 6)), tuple(np.round(cs[-1], 6))
        w = float(np.hypot(*(cs[-1] - cs[0])))
        graph.setdefault(a, []).append((b, w))
        graph.setdefault(b, []).append((a, w))
    if len(graph) < 2:
        return None

    def farthest(start):
        dist, prev, heap = {start: 0.0}, {start: None}, [(0.0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for v, w in graph[u]:
                if d + w < dist.get(v, float("inf")):
                    dist[v], prev[v] = d + w, u
                    heapq.heappush(heap, (d + w, v))
        end = max(dist, key=dist.get)
        return end, prev

    # the medial axis of the largest connected piece: start from its middle
    start = max(graph, key=lambda n: room.boundary.distance(shapely.Point(n)))
    a, _ = farthest(start)
    b, prev = farthest(a)
    path = []
    while b is not None:
        path.append(b)
        b = prev[b]
    path = np.array(path)
    if len(path) < 2:
        return None
    # resample evenly, then smooth over about a tooth's width: the bumps of each tooth along the outline
    # must not make the text wave, only the arch's own bend should remain
    seg = np.hypot(*np.diff(path, axis=0).T)
    sl = np.concatenate([[0.0], np.cumsum(seg)])
    if sl[-1] <= 1e-6:
        return None
    u = np.linspace(0, sl[-1], max(8, int(sl[-1] / 0.25)))
    path = np.column_stack([np.interp(u, sl, path[:, 0]), np.interp(u, sl, path[:, 1])])
    win = max(3, int(min(8.0, sl[-1] / 3) / 0.25) | 1)
    kernel = np.hanning(win + 2)[1:-1]
    kernel /= kernel.sum()
    for _ in range(2):
        padded = np.vstack([np.repeat(path[:1], win // 2, axis=0), path, np.repeat(path[-1:], win // 2, axis=0)])
        path = np.column_stack([np.convolve(padded[:, k], kernel, mode="valid") for k in range(2)])
    return path


def _bend(text, path: np.ndarray, start: float, k: float):
    """``text`` (centred on y = 0, starting at x = 0) scaled by ``k`` and bent along ``path`` from arc
    length ``start``: x runs along the path, y along its left normal."""
    seg = np.diff(path, axis=0)
    seglen = np.hypot(seg[:, 0], seg[:, 1])
    s = np.concatenate([[0.0], np.cumsum(seglen)])
    tang = seg / np.maximum(seglen, 1e-12)[:, None]
    # tangent at each path point (averaged), so the bend has no kinks at the joints
    tp = np.vstack([tang[:1], tang[:-1] + tang[1:], tang[-1:]])
    tp /= np.maximum(np.hypot(tp[:, 0], tp[:, 1]), 1e-12)[:, None]

    def warp(xy):
        u = start + xy[:, 0] * k
        px = np.interp(u, s, path[:, 0])
        py = np.interp(u, s, path[:, 1])
        tx = np.interp(u, s, tp[:, 0])
        ty = np.interp(u, s, tp[:, 1])
        n = np.hypot(tx, ty)
        tx, ty = tx / np.maximum(n, 1e-12), ty / np.maximum(n, 1e-12)
        v = xy[:, 1] * k
        return np.column_stack([px - ty * v, py + tx * v])

    bent = shapely.transform(text, warp)
    return bent if bent.is_valid else bent.buffer(0)


def _fit_curved(text, room, min_height: float, max_height: float):
    from shapely import affinity
    path = centre_line(room)
    if path is None:
        return None
    seg = np.diff(path, axis=0)
    normal_y = float(np.sum(seg[:, 0]))                    # left normal of (dx, dy) has y = dx
    if normal_y < 0:
        path = path[::-1]                                  # letters stand up, read left to right
    s_total = float(np.sum(np.hypot(*np.diff(path, axis=0).T)))
    tx0, ty0, tx1, ty1 = text.bounds
    tw, th = tx1 - tx0, ty1 - ty0
    if tw <= 1e-6 or th <= 1e-6 or s_total <= 1e-6:
        return None
    base = affinity.translate(text, -tx0, -(ty0 + ty1) / 2)
    base = shapely.segmentize(base, max(th / 10, 0.02))   # enough points for the letters to bend smoothly
    edge = room.boundary                                   # (a raft in several pieces has no single outline)
    half = float(np.min([edge.distance(shapely.Point(p)) for p in path[len(path) // 4: 3 * len(path) // 4 + 1]]))
    k = min(0.95 * s_total / tw, max_height / th, max(1.9 * half, min_height) / th)
    while k * th >= min_height:
        span = max(0.0, s_total - k * tw)
        for off in (0.5, 0.375, 0.625, 0.25, 0.75, 0.125, 0.875):
            cand = _bend(base, path, span * off, k)
            if not cand.is_empty and room.contains(cand):
                return cand, k * th
        k *= 0.92
    return None


def hex_edges(bounds, cell: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Wall segments of a honeycomb (flat-topped hexagons, ``cell`` = distance between opposite walls)
    covering ``bounds`` = (minx, miny, maxx, maxy); each wall is listed once."""
    minx, miny, maxx, maxy = bounds
    r = cell / math.sqrt(3.0)                       # centre-to-corner
    dx, dy = 1.5 * r, cell                          # column and row spacing
    edges = {}
    col = 0
    x = minx - r
    while x <= maxx + r:
        y = miny - cell + (cell / 2 if col % 2 else 0.0)
        while y <= maxy + cell:
            corners = [(round(x + r * math.cos(math.radians(60 * k)), 6), round(y + r * math.sin(math.radians(60 * k)), 6))
                       for k in range(6)]
            for k in range(6):
                a, b = corners[k], corners[(k + 1) % 6]
                edges[(min(a, b), max(a, b))] = True
            y += dy
        x += dx
        col += 1
    return list(edges)


def _hex_walls(hull, cell: float, width: float) -> list:
    """Honeycomb walls covering the raft outline (2D wall rectangles, slightly overlapping at the corners)."""
    out = []
    half = max(width, 0.3) / 2
    for a, b in hex_edges(hull.bounds, max(cell, 1.0)):
        a, b = np.asarray(a), np.asarray(b)
        d = b - a
        length = float(np.hypot(*d))
        if length < 1e-6:
            continue
        u = d / length
        n = np.array([-u[1], u[0]]) * half
        a2, b2 = a - u * half, b + u * half          # overlap at the corners: walls join without gaps
        out.append(ShapelyPolygon([tuple(a2 - n), tuple(b2 - n), tuple(b2 + n), tuple(a2 + n)]))
    return out


def tooth_outline(mesh: Mesh, inset: float, levels: int = 16):
    """The part's outline seen from above (the union of its cross-sections at several heights), set back by
    ``inset``: a "mini tiny" raft never reaches past the tooth it carries."""
    from .slicing import slice_mesh
    lo, hi = mesh.bounds
    if hi[2] - lo[2] <= 1e-6:
        return ShapelyPolygon()
    planes = list(np.linspace(lo[2], hi[2], levels + 2)[1:-1])
    shape = unary_union([g for g in slice_mesh(mesh, planes, 0.05) if not g.is_empty])
    if shape.is_empty:
        return ShapelyPolygon()
    shape = shape.buffer(0.2).buffer(-0.2)          # close slivers between neighbouring sections
    shape = shape.buffer(-inset) if inset > 0 else shape
    return shape.simplify(0.02) if not shape.is_empty else shape


def _rafts(feet: list, cfg: Config, pad: float, radii: list | None = None, outline_shape=None,
           label: str = "", frame=None) -> list[Mesh]:
    """Raft geometry under the support feet for the chosen raft type, with ``label`` (a tooth's case code
    or the model's name) on the raft itself: engraved, mirrored, into its underside or standing on top."""
    if cfg["sla_raft_type"] == "raft_label":
        return _raft_and_label(feet, cfg, pad, radii, outline_shape, label, frame)
    plan = _raft_plan(feet, cfg, pad, radii, outline_shape)
    if plan is None:
        return []
    raft = cfg["sla_raft_thickness"]
    shape, room, hull = plan
    code = None
    if label and raft >= 0.4:
        depth = cfg["sla_raft_label_depth"]
        engrave = depth if cfg["sla_raft_label_side"] == "bottom" and raft >= depth + 0.3 else 0.0
        default_r = max(cfg["sla_base_diameter"] / 2, pad / 2)
        feet_zone = unary_union([MultiPoint([tuple(np.asarray(q)[:2])]).buffer(
            (radii[k] if radii is not None and k < len(radii) else default_r) + 0.3, quad_segs=6)
            for k, q in enumerate(feet)])
        if hull is not None and cfg["sla_raft_slope"] < 89.9:   # the solid raft's top is set back
            room_top = hull.buffer(-raft / math.tan(math.radians(cfg["sla_raft_slope"])))
        else:
            room_top = room
        try:
            code = _code_on_raft(label, room if engrave else room_top, raft, engrave, feet_zone)
            if code is not None and code[0] == "top" and engrave:
                code = _code_on_raft(label, room_top, raft, 0.0, feet_zone)
        except Exception:              # a label must never cost the part its supports and raft
            code = None
    from .mesh_tools import extrude
    if code is None:
        if hull is not None:
            return [_slab(hull, 0.0, raft, cfg["sla_raft_slope"])]
        return [extrude(shape, 0.0, raft, "Raft")] if not shape.is_empty else []
    where, text = code
    # a patterned or mini raft gets a solid floor under the code, inside its own outline
    if hull is not None:                               # the slab's own (sampled) outline, edge to edge
        floor = ShapelyPolygon(_sample_convex(hull)).buffer(0)
    else:
        floor = unary_union([shape, text.buffer(0.8).intersection(room)]).buffer(0)
    if where == "under":
        depth = cfg["sla_raft_label_depth"]
        top = _slab(hull, depth, raft, cfg["sla_raft_slope"]) if hull is not None else extrude(floor, depth, raft, "Raft")
        return [extrude(floor.difference(text), 0.0, depth, "Raft"), top]
    body = _slab(hull, 0.0, raft, cfg["sla_raft_slope"]) if hull is not None else extrude(floor, 0.0, raft, "Raft")
    return [body, extrude(text, raft - 0.01, raft + 0.4, "Raft label")]


LABEL_RAFT_MARGIN = 0.8     # how far the "raft & label" raft reaches past the model's outline (mm)
MOON_WIDTH = 5.0            # width of its crescent tab
MOON_WRAP = 3.0             # how far back along the model's sides the crescent reaches


def _label_raft_core(feet: list, cfg: Config, pad: float, radii: list | None, outline_shape):
    """The "raft & label" raft without its tab: the model's outline seen from above plus a little, and a
    pad under every foot (no foot is left standing on the plate)."""
    default_r = max(cfg["sla_base_diameter"] / 2, pad / 2)
    pieces = [MultiPoint([tuple(np.asarray(q)[:2])]).buffer(
        max(radii[k] if radii is not None and k < len(radii) else default_r, 0.3) + 0.4, quad_segs=8)
        for k, q in enumerate(feet)]
    if outline_shape is not None and not outline_shape.is_empty:
        pieces.append(outline_shape.buffer(LABEL_RAFT_MARGIN, quad_segs=8))
    core = unary_union(pieces).buffer(1.0, quad_segs=8).buffer(-1.0, quad_segs=8)   # one smooth piece
    return core.simplify(0.02) if not core.is_empty else core


KNOB_BULGE = 2.2          # how far a "solid lite" raft reaches past a small round part of the outline (mm)


def round_knobs(outline, max_r: float = 5.0, min_turn: float = 100.0, step: float = 0.2, win: float = 0.8,
                min_len: float = 2.5) -> list:
    """Stretches of a part's outline (seen from above) that stand out in a small round bulge - a ball at the
    edge of a bridge, a round pontic: convex, curving tighter than ``max_r`` mm, turning ``min_turn`` degrees
    or more (a plain corner turns 90) along at least ``min_len`` mm, and not the whole outline (a small round
    crown is not a bulge). Each as a LineString along the outline."""
    out = []
    for poly in getattr(outline.simplify(0.005), "geoms", [outline.simplify(0.005)]):
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        ring = poly.exterior
        coords = list(ring.coords) if ring.is_ccw else list(ring.coords)[::-1]
        line = LineString(coords)
        length = line.length
        n = max(16, int(length / step))
        if n < 16:
            continue
        pts = np.array([line.interpolate(i * length / n).coords[0] for i in range(n)])
        d = np.roll(pts, -1, axis=0) - pts
        ang = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
        turn = np.diff(np.r_[ang, ang[0] + 2 * math.pi])          # ccw: + where the outline bends outward
        seg = length / n
        k = max(1, int(win / seg))
        smooth = np.convolve(np.r_[turn[-k:], turn, turn[:k]], np.ones(2 * k + 1) / (2 * k + 1), "same")[k:-k]
        mark = (smooth > 0) & (seg / np.maximum(smooth, 1e-9) < max_r)
        if mark.all() or not mark.any():
            continue
        start = int(np.argmin(mark))                   # from a sample outside every bulge: runs never wrap
        run = []
        for i in list(np.roll(np.arange(n), -start)) + [start]:
            if mark[i]:
                run.append(i)
                continue
            if run and len(run) >= 2 and math.degrees(float(turn[run].sum())) >= min_turn \
                    and len(run) * seg >= min_len and math.degrees(float(turn[run].sum())) <= 300:
                out.append(LineString(pts[run]))
            run = []
    return out


def _solid_lite(feet: list, cfg: Config, pad: float, radii: list | None, outline_shape):
    """The "solid lite" raft: solid, along the part's outline (+0.8 mm, a pad under every foot), reaching
    ``KNOB_BULGE`` mm further out around each small round part of the outline, so a ball at the edge stands
    well inside the raft instead of over its edge."""
    core = _label_raft_core(feet, cfg, pad, radii, outline_shape)
    if core.is_empty or outline_shape is None or outline_shape.is_empty:
        return core
    knobs = round_knobs(outline_shape)
    if not knobs:
        return core
    bulge = unary_union([k.buffer(KNOB_BULGE + LABEL_RAFT_MARGIN, quad_segs=12) for k in knobs])
    shape = unary_union([core, bulge]).buffer(0.6, quad_segs=8).buffer(-0.6, quad_segs=8)
    return shape.simplify(0.02) if not shape.is_empty else core


def tab_direction(outline, frame=None) -> np.ndarray:
    """Which way (a unit vector on the plate) a raft & label tab sticks out: along the part's own x, or its
    own y when the part is clearly longer that way, toward + (``frame`` = ``SceneObject.plate_frame``;
    None = the plate's X / Y). Taken from the outline's extent along those two directions only, so the
    auto layout (a coarse outline) and the support build (the exact one) pick the same end."""
    fx, fy = (np.asarray(frame[0], float), np.asarray(frame[1], float)) if frame is not None else \
        (np.array([1.0, 0.0]), np.array([0.0, 1.0]))
    pts = np.asarray([c for g in getattr(outline, "geoms", [outline]) if not g.is_empty
                      for c in g.exterior.coords]) if not outline.is_empty else np.zeros((0, 2))
    if len(pts) < 3:
        return fx
    ext_x, ext_y = float(np.ptp(pts @ fx)), float(np.ptp(pts @ fy))
    return fy if ext_y > 1.15 * ext_x else fx


def label_moon(core, outline_shape=None, frame=None):
    """The crescent tab for the code: a band ``MOON_WIDTH`` wide hugging the end of the model's long axis,
    reaching ``MOON_WRAP`` back along its sides, round at the tips. Empty when there is no room.
    ``frame``: the part's own (x, y) directions on the plate (``SceneObject.plate_frame``); the tab goes at
    its +x end (+y for a part lying along its y), so it turns with the part. None = the plate's X / Y."""
    body = outline_shape if outline_shape is not None and not outline_shape.is_empty else core
    if core.is_empty or body.is_empty:
        return ShapelyPolygon()
    axis = tab_direction(body, frame)       # the same end every time: +x (or +y on a part lying along y)
    lo, hi = np.asarray(body.bounds[:2]), np.asarray(body.bounds[2:])
    n = float(np.hypot(*(hi - lo)))
    if n < 1e-6:
        return ShapelyPolygon()
    side = np.array([-axis[1], axis[0]])
    c = np.asarray(core.centroid.coords[0])
    end = float(np.max(np.asarray(core.exterior.coords if core.geom_type == "Polygon" else
                                  [p for g in core.geoms for p in g.exterior.coords]) @ axis))
    base = c + axis * (end - float(c @ axis) - MOON_WRAP)
    far = 4 * (MOON_WIDTH + n)
    half = ShapelyPolygon([tuple(base + side * far), tuple(base + side * far + axis * far),
                           tuple(base - side * far + axis * far), tuple(base - side * far)])
    ring = core.buffer(MOON_WIDTH, quad_segs=12).difference(core.buffer(-0.01))
    moon = ring.intersection(half).buffer(-1.2, quad_segs=8).buffer(1.2, quad_segs=8)
    if moon.is_empty:
        return ShapelyPolygon()
    moon = max(getattr(moon, "geoms", [moon]), key=lambda g: g.area)
    return moon


def _raft_and_label(feet: list, cfg: Config, pad: float, radii: list | None, outline_shape, label: str,
                    frame=None) -> list[Mesh]:
    """The "raft & label" raft: narrow, along the model's outline, with a crescent tab at the model's end
    carrying ``label`` embossed on top (above the long-exposed bottom layers, so it prints sharp)."""
    from .mesh_tools import extrude
    raft = cfg["sla_raft_thickness"]
    if raft <= 0 or not feet:
        return []
    core = _label_raft_core(feet, cfg, pad, radii, outline_shape)
    if core.is_empty:
        return []
    text = None
    if label and raft >= 0.4:
        moon = label_moon(core, outline_shape, frame)
        if not moon.is_empty:
            try:
                from PySide6.QtGui import QGuiApplication
                if QGuiApplication.instance() is not None:   # fonts need the GUI; the command line skips the code
                    from .mesh_tools import text_polygons
                    base = text_polygons(label[:24], 2.0)
                    text = fit_text(base, moon.buffer(-0.5), min_height=1.0) if not base.is_empty else None
            except Exception:              # a label must never cost the part its supports and raft
                text = None
        if text is not None:
            core = unary_union([core, moon]).buffer(0)
    parts = [extrude(core, 0.0, raft, "Raft")]
    if text is not None:
        parts.append(extrude(text, raft - 0.01, raft + 0.4, "Raft label"))
    return parts


def _raft_plan(feet: list, cfg: Config, pad: float, radii: list | None = None, outline_shape=None):
    """(raft outline as built, the area inside its outer edge, the solid raft's hull or None) for the
    chosen raft type; None for no raft."""
    kind = cfg["sla_raft_type"]
    raft = cfg["sla_raft_thickness"]
    if kind == "none" or raft <= 0 or not feet:
        return None
    if kind == "raft_label":
        core = _label_raft_core(feet, cfg, pad, radii, outline_shape)
        return (core, core, None) if not core.is_empty else None
    if kind == "solid_lite":
        shape = _solid_lite(feet, cfg, pad, radii, outline_shape)
        return (shape, shape, None) if not shape.is_empty else None
    if kind == "tiny":
        # the tooth's own outline, a little smaller; feet outside it stand on the plate as they are
        if outline_shape is None or outline_shape.is_empty:
            return None
        return outline_shape, outline_shape, None
    pts = np.array(feet)
    margin = pad + cfg["sla_raft_margin"]
    scale = math.sqrt(max(cfg["sla_raft_area_ratio"], 1.0) / 100.0)

    def outline(group):
        hull = MultiPoint([tuple(q) for q in group]).convex_hull.buffer(margin, quad_segs=6)  # hull first: buffering thousands of points was ~1 s
        return shapely_scale(hull, scale, scale, origin="centroid") if scale != 1 else hull

    if kind == "mini":
        # a small pad under every support foot (as PreForm's mini rafts); overlapping pads join, so the raft
        # follows the supports with open gaps between them instead of one slab under the whole model
        default_r = max(cfg["sla_base_diameter"] / 2, pad / 2)
        pads = []
        for k, q in enumerate(pts):
            foot_r = radii[k] if radii is not None and k < len(radii) else default_r
            pads.append(MultiPoint([tuple(q[:2])]).buffer(max(foot_r, 0.3) * scale + cfg["sla_raft_margin"] * 0.5,
                                                         quad_segs=8))
        shape = unary_union(pads)
        # feet closer than the joining distance are tied by a bar, so a cluster prints as one piece
        reach = cfg["sla_raft_group_reach"]
        bars = []
        for a in range(len(pts)):
            d = np.hypot(*(pts[:, :2] - pts[a, :2]).T)
            for b in np.flatnonzero((d > 0) & (d < reach)):
                if b > a and not shape.contains(LineString([tuple(pts[a, :2]), tuple(pts[b, :2])])):
                    bars.append(LineString([tuple(pts[a, :2]), tuple(pts[b, :2])]).buffer(
                        max(cfg["sla_raft_bar_width"], 0.6) / 2, cap_style="flat"))
        if bars:
            shape = unary_union([shape, *bars])
        shape = shape.simplify(0.01)
        return (shape, shape, None) if not shape.is_empty else None
    hull = outline(pts)
    if kind in ("grid", "hexagon"):
        # outline band, a pad under every foot and the pattern, merged in 2D and extruded once: one clean
        # solid slices fast (overlapping slabs made every raft layer take the slow path for messy layers)
        width = max(cfg["sla_raft_bar_width"], 0.3)
        band = hull.exterior.buffer(max(width * 0.66, 0.3), join_style="mitre").intersection(hull.buffer(0.01))
        pieces = [band]
        # every foot stands on a pad just wider than its own base, never over a gap of the pattern
        default_r = max(cfg["sla_base_diameter"] / 2, pad / 2)
        for k, q in enumerate(pts):
            foot_r = radii[k] if radii is not None and k < len(radii) else default_r
            pieces.append(MultiPoint([tuple(q)]).buffer(max(foot_r, 0.3) + 0.4, quad_segs=6))
        if kind == "hexagon":
            pieces += _hex_walls(hull, cfg["sla_raft_bar_spacing"], width)
        else:
            minx, miny, maxx, maxy = hull.bounds
            step, half = cfg["sla_raft_bar_spacing"], width / 2
            for y in np.arange(miny + step / 2, maxy, step):
                pieces.append(ShapelyPolygon([(minx, y - half), (maxx, y - half), (maxx, y + half), (minx, y + half)]))
            for x in np.arange(minx + step / 2, maxx, step):
                pieces.append(ShapelyPolygon([(x - half, miny), (x + half, miny), (x + half, maxy), (x - half, maxy)]))
        shape = unary_union(pieces).intersection(hull).buffer(0)
        shape = shape.simplify(0.01)
        return (shape, hull, None) if not shape.is_empty else None
    return hull, hull, hull


def build_supports(mesh: Mesh, cfg: Config, points: np.ndarray | None = None, owners: dict | None = None,
                   cache: dict | None = None, label: str = "", label_frame=None):
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
        cache["caster"] = caster_for(mesh)
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
    foot_radii = []                    # footprint radius of each foot's base (pads under it on patterned rafts)
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
        base_r = cfg["sla_base_diameter"] / 2 or r * 2
        foot_radii.append(r if base_shape == "none" else base_r * 1.2 if base_shape == "cube" else base_r)
        if base_shape == "none":
            return raft - 0.05
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

    def blocked(x, y, top, h_len, bottom, contact, tip):
        """Would this support run through the model anywhere but its own tip? (its column, the wide part
        of its head, or a leaning head's arm)"""
        # the ring is checked at half the radius: a pillar's edge grazing the edge of a wall leaves a sliver
        # nobody sees, a pillar that really runs through the model is caught on its core
        # a pillar standing on the model sinks its foot into the (often sloped) surface it stands on
        start = bottom + (0.05 if bottom <= raft + 1e-6 else 2.0 * pillar_r)
        if caster.column_blocked(x, y, 0.5 * widened(pillar_r, top - h_len - bottom), start, top - h_len):
            return True
        if tip is None:
            if caster.column_blocked(x, y, 0.5 * pillar_r, top - h_len, top - h_len / 2):
                return True
            return caster.column_blocked(x, y, 0.0, top - h_len / 2, contact[2] - 0.05)
        a = np.array([x, y, top - h_len])
        return any(caster.inside(a + (tip - a) * t) for t in (0.0, 0.2, 0.4, 0.6, 0.8))

    arm = head_len

    def clear_side(p):
        """A slanted head from the contact down and out to a pillar beside it, in the first direction whose
        arm and pillar stay clear of the model (closest to vertical first); None if there is none."""
        for tilt in (25.0, 40.0, 55.0):
            t = math.radians(tilt)
            for k in range(8):
                f = 2 * math.pi * k / 8
                dd = np.array([math.sin(t) * math.cos(f), math.sin(t) * math.sin(f), -math.cos(t)])
                q = p + dd * arm
                b = caster.base_below(q)
                if b > 0 and not on_model:
                    continue
                bed = b <= 1e-6
                lo = raft if bed else b
                tp = q[2] + arm
                if tp - lo - arm < 0.5:
                    continue
                tip = p - dd * depth
                if not blocked(q[0], q[1], tp, arm, lo, p, tip):
                    return dd, tip, q[0], q[1], tp, lo, bed, tp - lo, arm
        return None

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
        if d is not None and (height - h_len < 0.5 or blocked(x, y, top, h_len, bottom, p, tip)):
            # too close to the plate to lean, or the leaning arm / its pillar would cut through the
            # model: a vertical head instead
            d, tip = None, None
            x, y, top = p[0], p[1], p[2] + depth
            base = caster.base_below(p)
            on_bed = base <= 1e-6
            bottom = raft if on_bed else base
            height = top - bottom
            if height < 0.5 or (not on_bed and not on_model):
                continue
            h_len = min(head_len, height * 0.5)
        if d is None and blocked(x, y, top, h_len, bottom, p, None):
            # no straight way down: stand the pillar a little to the side, where the way is clear, and
            # reach the contact with a slanted head (as a leaning head does)
            way = clear_side(p)
            if way is None:
                continue                 # no way down that stays clear of the model
            d, tip, x, y, top, bottom, on_bed, height, h_len = way
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

    tiny = None
    if feet and cfg["sla_raft_type"] in ("tiny", "raft_label", "solid_lite"):
        tiny = tooth_outline(mesh, cfg["sla_raft_tiny_inset"] if cfg["sla_raft_type"] == "tiny" else 0.0)
    # the caller chooses the text (the model's name, or a tooth's SOD case code); "" = no label
    parts += _rafts(feet, cfg, pillar_r * 4, foot_radii, tiny, label, label_frame)
    merged = Mesh.merge(list(parts), "Supports")
    if owners is not None:
        owners["faces"] = np.concatenate([np.full(len(m.faces), o, dtype=np.int64) for m, o in zip(parts, part_owner)]) \
            if parts else np.zeros(0, dtype=np.int64)
        owners["groups"] = groups
    return merged, np.array(used).reshape(-1, 3)


def _signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
