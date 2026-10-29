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


def _segments_at(tris: np.ndarray, normals: np.ndarray, z: float):
    """Intersection segments of triangles (k,3,3) with the plane at height z."""
    above = tris[:, :, 2] >= z
    count = above.sum(axis=1)
    keep = (count == 1) | (count == 2)
    tris, normals, above = tris[keep], normals[keep], above[keep]
    if len(tris) == 0:
        return np.zeros((0, 2, 2))
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
    return seg


def _chain(segments: np.ndarray):
    """Link segments into loops. Yields (points (n,2), orientation_vote)."""
    n = len(segments)
    if n == 0:
        return []
    keys = np.round(segments * KEY_SCALE).astype(np.int64)
    kp = list(zip(keys[:, 0, 0].tolist(), keys[:, 0, 1].tolist()))
    kq = list(zip(keys[:, 1, 0].tolist(), keys[:, 1, 1].tolist()))
    adjacency: dict = {}
    for i in range(n):
        if kp[i] == kq[i]:
            continue
        adjacency.setdefault(kp[i], []).append(i)
        adjacency.setdefault(kq[i], []).append(i)
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
        if closed:
            loops.append((arr[:-1], vote))
        else:
            open_chains.append([arr, vote])
    loops += _join_open_chains(open_chains)
    return loops


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


def loops_to_geometry(loops) -> MultiPolygon:
    """Combine oriented loops (outer = CCW, hole = CW) into polygons, honoring nesting."""
    items = []
    for pts, vote in loops:
        if len(pts) < 3:
            continue
        area = _signed_area(pts)
        if abs(area) < 1e-6:
            continue
        sign = np.sign(area) * (1 if vote >= 0 else -1)
        poly = shapely.make_valid(Polygon(pts))
        poly = shapely.union_all(polygons_of(poly))
        if poly.is_empty:
            continue
        items.append((abs(area), sign, poly))
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
        group = shapely.union_all(batch)
        result = result.union(group) if sign > 0 else result.difference(group)
    return as_multipolygon(result)


def _slice_sorted(tris: np.ndarray, normals: np.ndarray, z_planes: list[float], resolution: float,
                  progress=None, cancelled=None) -> list[MultiPolygon]:
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
        seg = _segments_at(local, normals[idx], zc)
        geom = loops_to_geometry(_chain(seg))
        if resolution > 0 and not geom.is_empty:
            geom = as_multipolygon(geom.simplify(resolution, preserve_topology=True))
        out.append(geom)
        if progress and (li % 5 == 0 or li == len(z_planes) - 1):
            progress((li + 1) / len(z_planes))
    return out


def _slice_chunk(tris: np.ndarray, normals: np.ndarray, z_planes: list[float], resolution: float):
    """Process-pool entry point."""
    return _slice_sorted(tris, normals, z_planes, resolution)


def slice_mesh(mesh: Mesh, z_planes: Iterable[float], resolution: float = 0.01,
               progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> list[MultiPolygon]:
    """Cross-sections of ``mesh`` at each height in ``z_planes`` (uses all CPU cores for big jobs)."""
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
    n_workers = parallel.workers()
    if n_workers < 2 or len(z_planes) < 40 or len(z_planes) * len(tris) < 2_000_000:
        return _slice_sorted(tris, normals, z_planes, resolution, progress, cancelled)
    # each chunk only receives the triangles that reach its height range
    args, weights = [], []
    for a, b in parallel.chunk_ranges(len(z_planes), 8, n_workers * 4):
        lo, hi = z_planes[a] - 1e-4, z_planes[b - 1] + 1e-4
        sel = np.nonzero((zmax >= lo) & (zmin <= hi))[0]
        args.append((tris[sel], normals[sel], z_planes[a:b], resolution))
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
    return [as_multipolygon(shapely.union_all([layers[k] for layers in per_object]))
            for k in range(len(z_planes))]
