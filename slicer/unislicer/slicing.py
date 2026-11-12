"""Cut a triangle mesh into horizontal cross-sections (shapely polygons)."""
from __future__ import annotations

from typing import Callable, Iterable

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .mesh import Mesh

KEY_SCALE = 1e4          # endpoints are matched on a 0.1 micron grid
GAP_TOLERANCE = 0.5      # mm, open contours whose ends are this close get joined/closed

EMPTY = MultiPolygon()


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """Flatten any geometry into a list of non-empty polygons."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out += polygons_of(g)
        return out
    return []


def as_multipolygon(geom: BaseGeometry | None) -> MultiPolygon:
    polys = polygons_of(geom)
    return MultiPolygon(polys) if polys else EMPTY


def layer_heights(max_z: float, layer_height: float, first_layer_height: float) -> list[tuple[float, float]]:
    """Return (top_z, thickness) for each layer of an object max_z tall."""
    layers = []
    if max_z <= 0:
        return layers
    layers.append((first_layer_height, first_layer_height))
    z = first_layer_height
    while z + layer_height * 0.5 < max_z:
        z = round(z + layer_height, 6)
        layers.append((z, layer_height))
    return layers


def _segments_at(tris: np.ndarray, normals: np.ndarray, z: float, groups: np.ndarray | None = None):
    """Intersection segments of triangles (k,3,3) with the plane at height z. With ``groups`` (a piece number
    per triangle) also returns the piece of every segment."""
    above = tris[:, :, 2] >= z
    count = above.sum(axis=1)
    keep = (count == 1) | (count == 2)
    tris, normals, above = tris[keep], normals[keep], above[keep]
    if groups is not None:
        groups = groups[keep]
    if len(tris) == 0:
        return np.zeros((0, 2, 2)) if groups is None else (np.zeros((0, 2, 2)), np.zeros(0, dtype=np.int64))
    a = tris
    b = np.roll(tris, -1, axis=1)
    ab_above = np.roll(above, -1, axis=1)
    crosses = above != ab_above                                   # (k,3) edges crossing the plane
    za, zb = a[:, :, 2], b[:, :, 2]
    denom = np.where(zb - za == 0, 1.0, zb - za)
    t = (z - za) / denom
    pts = a[:, :, :2] + (b[:, :, :2] - a[:, :, :2]) * t[:, :, None]
    seg = pts[crosses].reshape(-1, 2, 2)
    # orient so that the solid lies to the left (outer contours become counter-clockwise)
    d = seg[:, 1] - seg[:, 0]
    wrong = (d[:, 1] * normals[:, 0] - d[:, 0] * normals[:, 1]) < 0
    seg[wrong] = seg[wrong][:, ::-1]
    return seg if groups is None else (seg, groups)


def _chain_fast(keys: np.ndarray, segments: np.ndarray):
    """Vectorised linking for the usual clean layer: every end point is the start of exactly one other
    segment (closed, consistently oriented loops). Returns the loops, or None to use the general path."""
    n = len(segments)
    flat = keys.reshape(-1, 2)
    one = (flat[:, 0] - flat[:, 0].min()) * (int(flat[:, 1].max() - flat[:, 1].min()) + 1) + (flat[:, 1] - flat[:, 1].min())
    _, ids = np.unique(one, return_inverse=True)
    ids = ids.reshape(n, 2)
    keep = ids[:, 0] != ids[:, 1]                     # zero-length pieces (the plane grazes a vertex)
    if not keep.all():
        ids, segments = ids[keep], segments[keep]
        n = len(segments)
        if n == 0:
            return []
    start, end = ids[:, 0], ids[:, 1]
    m = int(ids.max()) + 1
    owner = np.full(m, -1, dtype=np.int64)
    owner[start] = np.arange(n)
    if np.bincount(start, minlength=m).max() != 1 or np.bincount(end, minlength=m).max() != 1:
        return None
    nxt = owner[end]
    if np.any(nxt < 0):
        return None
    # loop label = smallest segment index in the loop (pointer doubling: the window doubles each round)
    label = np.arange(n)
    jump = nxt.copy()
    while True:
        label = np.minimum(label, label[jump])
        if np.all(label[nxt] == label):
            break
        jump = jump[jump]
    # position in its loop: steps from the loop's first segment (list ranking with the loop cut there)
    root = label
    is_root = np.arange(n) == root
    succ = np.where(is_root[nxt], -1, nxt)            # the segment before the root ends the list
    rank = np.where(succ >= 0, 1, 0).astype(np.int64)
    ptr = succ.copy()
    while np.any(ptr >= 0):
        ok = ptr >= 0
        rank[ok] += rank[ptr[ok]]
        ptr[ok] = ptr[ptr[ok]]
    order = np.lexsort((-rank, root))                 # each loop from its first segment onwards
    labs = root[order]
    cuts = np.flatnonzero(np.diff(labs)) + 1
    pts = segments[order, 0]
    return [(p, len(p)) for p in np.split(pts, cuts)]


def _chain(segments: np.ndarray, report: dict | None = None, raw: list | None = None, groups=None):
    """Link segments into loops. Yields (points (n,2), orientation_vote).
    ``report["messy"]`` is set when the layer has open contours or non-manifold junctions.
    ``groups``: a piece number per segment; segments of different pieces are never linked (supports are many
    closed pieces touching each other: linked separately, each closes on its own)."""
    n = len(segments)
    if n == 0:
        return []
    keys = np.round(segments * KEY_SCALE).astype(np.int64)
    if groups is not None:
        keys[:, :, 0] += np.asarray(groups, dtype=np.int64)[:, None] * 1_000_000_000
    fast = _chain_fast(keys, segments)
    if fast is not None:
        if raw is not None:
            raw.extend((np.vstack([p, p[:1]]), True) for p, _ in fast)
        return fast
    kp = list(zip(keys[:, 0, 0].tolist(), keys[:, 0, 1].tolist()))
    kq = list(zip(keys[:, 1, 0].tolist(), keys[:, 1, 1].tolist()))
    adjacency: dict = {}
    for i in range(n):
        if kp[i] == kq[i]:
            continue
        adjacency.setdefault(kp[i], []).append(i)
        adjacency.setdefault(kq[i], []).append(i)
    if report is not None and any(len(v) != 2 for v in adjacency.values()):
        report["messy"] = True                    # a vertex with 1 or 3+ segments: holes / non-manifold
    used = bytearray(n)
    loops, open_chains = [], []
    for s0 in range(n):
        if used[s0] or kp[s0] == kq[s0]:
            continue
        used[s0] = 1
        pts = [segments[s0, 0], segments[s0, 1]]
        vote = 1
        start, cur = kp[s0], kq[s0]
        closed = False
        while True:
            if cur == start:
                closed = True
                break
            nxt = None
            for c in adjacency.get(cur, ()):
                if not used[c]:
                    nxt = c
                    break
            if nxt is None:
                break
            used[nxt] = 1
            if kp[nxt] == cur:
                cur = kq[nxt]
                pts.append(segments[nxt, 1])
                vote += 1
            else:
                cur = kp[nxt]
                pts.append(segments[nxt, 0])
                vote -= 1
        arr = np.array(pts)
        if raw is not None:                       # majority direction: a few flipped triangles do not matter
            raw.append((arr if vote >= 0 else arr[::-1], closed))
        if closed:
            loops.append((arr[:-1], vote))
        else:
            open_chains.append([arr, vote])
    if open_chains and report is not None:
        report["messy"] = True
    loops += _join_open_chains(open_chains)
    return loops


def _oriented_segments(raw) -> np.ndarray:
    """Segments of the linked chains, each chain in its majority direction (closed ones closed again)."""
    parts = []
    for pts, closed in raw:
        if len(pts) < 2:
            continue
        a, b = pts[:-1], pts[1:]
        parts.append(np.stack([a, b], axis=1))
    return np.vstack(parts) if parts else np.zeros((0, 2, 2))


def even_odd_geometry(segments: np.ndarray, gap: float = 4 * GAP_TOLERANCE) -> MultiPolygon:
    """A layer of a mesh with holes, broken edges or overlapping shells, filled by winding number.

    All section segments (oriented with the solid on their left) are noded where they cross and every
    enclosed face is found; a face is solid when its winding number is not zero, so overlapping bodies stay
    solid where they overlap. Open ends closer than ``gap`` are bridged first (an end to the next start, which
    keeps the direction). Pieces that enclose nothing are ignored instead of being forced shut with a straight
    line, which made wedges and spikes outside the part."""
    # snap to the same grid the loop builder uses: neighbouring triangles compute a shared point with tiny
    # float differences, and lines that miss each other by 1e-12 mm would never close a face
    seg = np.round(np.asarray(segments, dtype=np.float64).reshape(-1, 2, 2) * KEY_SCALE) / KEY_SCALE
    seg = seg[np.hypot(*(seg[:, 1] - seg[:, 0]).T) > 1e-9]
    if len(seg) < 3:
        return EMPTY
    # bridge small gaps: dangling chain ends to dangling chain starts
    kstart = np.round(seg[:, 0] * KEY_SCALE).astype(np.int64)
    kend = np.round(seg[:, 1] * KEY_SCALE).astype(np.int64)
    starts_set = set(map(tuple, kstart.tolist()))
    ends_set = set(map(tuple, kend.tolist()))
    tails = seg[[tuple(k) not in starts_set for k in kend.tolist()], 1]     # chain ends nothing continues from
    heads = seg[[tuple(k) not in ends_set for k in kstart.tolist()], 0]     # chain starts nothing leads into
    extra = []
    if len(tails) and len(heads) and len(tails) * len(heads) <= 4_000_000:
        d = np.hypot(tails[:, None, 0] - heads[None, :, 0], tails[:, None, 1] - heads[None, :, 1])
        used_t = np.zeros(len(tails), dtype=bool)
        used_h = np.zeros(len(heads), dtype=bool)
        for flat in np.argsort(d, axis=None):
            ti, hi = divmod(int(flat), len(heads))
            if d[ti, hi] > gap:
                break
            if used_t[ti] or used_h[hi]:
                continue
            used_t[ti] = used_h[hi] = True
            extra.append([tails[ti], heads[hi]])
    if extra:
        seg = np.vstack([seg, np.array(extra)])
    lines = shapely.union_all(shapely.linestrings(seg))          # nodes every crossing
    faces = [f for f in polygons_of(shapely.polygonize([lines])) if f.area > 1e-6]
    if not faces:
        return EMPTY
    a, b = seg[:, 0], seg[:, 1]
    ya, yb = a[:, 1], b[:, 1]
    winding = []
    for f in faces:
        px, py = f.representative_point().coords[0]
        up = (ya <= py) & (yb > py)
        down = (yb <= py) & (ya > py)
        m = up | down
        if not m.any():
            winding.append(0)
            continue
        t = (py - ya[m]) / (yb[m] - ya[m])
        xs = a[m, 0] + t * (b[m, 0] - a[m, 0])
        right = xs > px
        winding.append((int(up[m][right].sum()) - int(down[m][right].sum()), int(right.sum()) % 2))
    # solid: a non-zero winding (overlapping bodies stay solid) or an odd crossing count (still right where
    # some triangles of a broken mesh are flipped inside out)
    solid = [f for f, (w, odd) in zip(faces, winding) if w != 0 or odd]
    return as_multipolygon(shapely.union_all(solid)) if solid else EMPTY


def _join_open_chains(chains):
    """Greedily stitch open contours (from non-manifold meshes) into loops."""
    loops = []
    while chains:
        pts, vote = chains.pop()
        merged = True
        while merged and chains:
            merged = False
            end = pts[-1]
            best, best_d, best_rev = None, GAP_TOLERANCE, False
            for i, (other, _) in enumerate(chains):
                d0 = np.hypot(*(other[0] - end))
                d1 = np.hypot(*(other[-1] - end))
                if d0 < best_d:
                    best, best_d, best_rev = i, d0, False
                if d1 < best_d:
                    best, best_d, best_rev = i, d1, True
            if best is not None:
                other, ov = chains.pop(best)
                pts = np.vstack([pts, other[::-1] if best_rev else other])
                vote += -ov if best_rev else ov
                merged = True
        if len(pts) >= 3 and np.hypot(*(pts[0] - pts[-1])) < max(GAP_TOLERANCE * 4, 2.0):
            loops.append((pts, vote))
    return loops


def _signed_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _clean_ring(pts) -> np.ndarray | None:
    """The loop without repeated points (consecutive or the closing one); None when fewer than 3 are left
    (a ring needs at least 3 distinct points, or GEOS refuses it)."""
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < 3:
        return None
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(pts[1:] != pts[:-1], axis=1)
    pts = pts[keep]
    while len(pts) > 1 and np.all(pts[0] == pts[-1]):
        pts = pts[:-1]
    return pts if len(pts) >= 3 else None


def _safe_polygon(pts):
    try:
        return Polygon(pts)
    except Exception:                       # a degenerate loop: leave it out rather than fail the layer
        return Polygon()


def union_many(polys):
    """The union of many polygons - only the ones that overlap are merged (with GEOS); the rest are collected
    as they are. A support layer is hundreds of separate pillar sections: merging them all was most of the
    supports' slicing time (~3x slower, the same result)."""
    polys = [p for p in polys if p is not None and not p.is_empty]
    n = len(polys)
    if n < 8:
        return shapely.union_all(polys)
    arr = np.array(polys, dtype=object)
    a, b = shapely.STRtree(arr).query(arr, predicate="intersects")
    keep = a < b
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in zip(a[keep].tolist(), b[keep].tolist()):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    parts = []
    for g in groups.values():
        u = arr[g[0]] if len(g) == 1 else shapely.union_all(arr[g])
        parts += [q for q in getattr(u, "geoms", [u]) if q.geom_type == "Polygon" and not q.is_empty]
    if not parts:
        return Polygon()
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def loops_to_geometry(loops) -> MultiPolygon:
    """Combine oriented loops (outer = CCW, hole = CW) into polygons, honoring nesting."""
    pts_list, signs, areas = [], [], []
    for pts, vote in loops:
        pts = _clean_ring(pts)
        if pts is None:
            continue
        area = _signed_area(pts)
        if abs(area) < 1e-6:
            continue
        pts_list.append(pts)
        signs.append(np.sign(area) * (1 if vote >= 0 else -1))
        areas.append(abs(area))
    if not pts_list:
        return EMPTY
    # build every loop's polygon in one go; only the invalid ones (self-touching) need repair
    polys = np.array([_safe_polygon(p) for p in pts_list], dtype=object)
    bad = ~shapely.is_valid(polys)
    if bad.any():
        polys[bad] = [shapely.union_all(polygons_of(shapely.make_valid(p))) for p in polys[bad]]
    items = [(a, sg, p) for a, sg, p in zip(areas, signs, polys) if not p.is_empty]
    if not items:
        return EMPTY
    items.sort(key=lambda it: -it[0])
    if items[0][1] < 0:  # largest contour is a "hole": normals are flipped, invert everything
        items = [(a, -s, p) for a, s, p in items]
    result: BaseGeometry = Polygon()
    i = 0
    while i < len(items):
        sign = items[i][1]
        batch = []
        while i < len(items) and items[i][1] == sign:
            batch.append(items[i][2])
            i += 1
        group = union_many(batch)
        result = (group if result.is_empty else result.union(group)) if sign > 0 else result.difference(group)
    return as_multipolygon(result)


def _slice_sorted(tris: np.ndarray, normals: np.ndarray, z_planes: list[float], resolution: float,
                  progress=None, cancelled=None, groups=None) -> list[MultiPolygon]:
    """Slice triangles already sorted by their lowest z."""
    zmin = tris[:, :, 2].min(axis=1)
    zmax = tris[:, :, 2].max(axis=1)
    out = []
    for li, z in enumerate(z_planes):
        if cancelled and cancelled():
            raise InterruptedError
        hi = np.searchsorted(zmin, z, side="right")
        idx = np.nonzero(zmax[:hi] > z)[0]
        local = tris[idx]
        zc = z
        # a plane passing exactly through vertices creates degenerate contours: nudge it
        while len(local) and np.any(np.abs(local[:, :, 2] - zc) < 1e-7):
            zc += 1e-5
        report, raw = {}, []
        if groups is None:
            seg = _segments_at(local, normals[idx], zc)
            loops = _chain(seg, report, raw)
        else:
            seg, seg_groups = _segments_at(local, normals[idx], zc, groups[idx])
            loops = _chain(seg, report, raw, seg_groups)
        # a broken layer (holes, non-manifold edges): fill by winding / crossing parity instead of stitching
        try:
            geom = even_odd_geometry(_oriented_segments(raw)) if report.get("messy") else loops_to_geometry(loops)
        except Exception:                   # never fail a whole slice on one odd layer: use the other path
            try:
                geom = even_odd_geometry(_oriented_segments(raw)) if not report.get("messy") else loops_to_geometry(loops)
            except Exception:
                geom = EMPTY
        if resolution > 0 and not geom.is_empty:
            # plain Douglas-Peucker is ~10x faster than the topology-preserving one; the rare invalid result
            # (a ring touching itself) is repaired
            simple = geom.simplify(resolution, preserve_topology=False)
            if not simple.is_valid:
                simple = shapely.make_valid(simple)
            geom = as_multipolygon(simple)
        out.append(geom)
        if progress and (li % 5 == 0 or li == len(z_planes) - 1):
            progress((li + 1) / len(z_planes))
    return out


def _slice_chunk(tris: np.ndarray, normals: np.ndarray, z_planes: list[float], resolution: float, groups=None):
    """Process-pool entry point."""
    return _slice_sorted(tris, normals, z_planes, resolution, groups=groups)


def slice_mesh(mesh: Mesh, z_planes: Iterable[float], resolution: float = 0.01,
               progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None, groups: np.ndarray | None = None) -> list[MultiPolygon]:
    """Cross-sections of ``mesh`` at each height in ``z_planes`` (uses all CPU cores for big jobs).
    ``groups``: a piece number per face (see ``_chain``)."""
    from . import parallel

    z_planes = list(z_planes)
    tris = mesh.triangles
    if len(tris) == 0:
        return [EMPTY for _ in z_planes]
    normals = mesh.face_normals()
    zmin = tris[:, :, 2].min(axis=1)
    zmax = tris[:, :, 2].max(axis=1)
    order = np.argsort(zmin)
    tris, normals, zmin, zmax = tris[order], normals[order], zmin[order], zmax[order]
    if groups is not None:
        groups = np.asarray(groups)[order]
    n_workers = parallel.workers()
    if n_workers < 2 or len(z_planes) < 40 or len(z_planes) * len(tris) < 2_000_000:
        return _slice_sorted(tris, normals, z_planes, resolution, progress, cancelled, groups)
    # each chunk only receives the triangles that reach its height range
    args, weights = [], []
    for a, b in parallel.chunk_ranges(len(z_planes), 8, n_workers * 4):
        lo, hi = z_planes[a] - 1e-4, z_planes[b - 1] + 1e-4
        sel = np.nonzero((zmax >= lo) & (zmin <= hi))[0]
        args.append((tris[sel], normals[sel], z_planes[a:b], resolution, None if groups is None else groups[sel]))
        weights.append(len(sel) * (b - a))
    parts = parallel.run_chunks(_slice_chunk, args, progress, cancelled, weights)
    return [g for part in parts for g in part]


def slice_meshes(meshes: list[Mesh], z_planes, resolution: float = 0.01, progress=None,
                 cancelled=None) -> list[MultiPolygon]:
    """Slice several objects and union them per layer (overlapping objects merge)."""
    z_planes = list(z_planes)
    per_object = []
    for i, m in enumerate(meshes):
        sub = (lambda f, i=i: progress((i + f) / len(meshes))) if progress else None
        per_object.append(slice_mesh(m, z_planes, resolution, sub, cancelled))
    if len(per_object) == 1:
        return per_object[0]
    return [as_multipolygon(union_many([q for layers in per_object for q in polygons_of(layers[k])]))
            for k in range(len(z_planes))]
