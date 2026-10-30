"""Model editing: extruded text, cutting by height, splitting into parts and mesh repair."""
from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon

from .mesh import Mesh
from .slicing import _chain, _segments_at, as_multipolygon, loops_to_geometry, polygons_of


# ------------------------------------------------------------- extrusion
def _cap_triangles(poly: Polygon) -> np.ndarray:
    """Triangles (K, 3, 2) filling a polygon with holes, using only its own vertices."""
    tris = shapely.constrained_delaunay_triangles(poly)
    out = []
    for t in getattr(tris, "geoms", []):
        c = np.asarray(t.exterior.coords)[:3, :2]
        out.append(c)
    return np.array(out).reshape(-1, 3, 2)


def extrude(polygons, z0: float, z1: float, name: str = "Extrusion") -> Mesh:
    """Closed prism(s) from 2D polygons (holes allowed) between heights z0 < z1."""
    tris = []
    for poly in polygons_of(as_multipolygon(polygons)):
        poly = shapely.geometry.polygon.orient(poly, 1.0)          # exterior CCW, holes CW
        cap = _cap_triangles(poly)
        for t in cap:
            a, b, c = t
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                b, c = c, b
            tris.append([[*a, z1], [*b, z1], [*c, z1]])             # top faces up
            tris.append([[*a, z0], [*c, z0], [*b, z0]])             # bottom faces down
        for ring in [poly.exterior, *poly.interiors]:
            pts = np.asarray(ring.coords)[:-1, :2]
            for k in range(len(pts)):
                p, q = pts[k], pts[(k + 1) % len(pts)]
                tris.append([[*p, z0], [*q, z0], [*q, z1]])
                tris.append([[*p, z0], [*q, z1], [*p, z1]])
    return Mesh.from_triangles(np.array(tris, dtype=float).reshape(-1, 3, 3), name)


def text_polygons(text: str, height: float, font_family: str = "") -> MultiPolygon:
    """Outline of ``text`` as polygons, ``height`` mm tall (capital height), baseline at y = 0."""
    from PySide6.QtGui import QFont, QPainterPath
    font = QFont(font_family) if font_family else QFont()
    font.setPointSizeF(100.0)
    path = QPainterPath()
    path.addText(0, 0, font, text)
    rings = []
    for poly in path.toSubpathPolygons():
        pts = [(p.x(), -p.y()) for p in poly]           # Qt's y axis points down
        if len(pts) >= 3:
            rings.append(Polygon(pts).buffer(0))
    if not rings:
        return MultiPolygon()
    # even-odd fill: letters with counters (o, a, ...) become polygons with holes
    shape = rings[0]
    for r in rings[1:]:
        shape = shape.symmetric_difference(r)
    shape = as_multipolygon(shape)
    minx, miny, maxx, maxy = shape.bounds
    k = height / ((maxy - miny) or 1.0)
    return as_multipolygon(shapely.affinity.scale(shape, k, k, origin=(minx, miny)).simplify(0.01))


def text_mesh(text: str, height: float = 8.0, depth: float = 1.5, font_family: str = "") -> Mesh:
    polys = text_polygons(text, height, font_family)
    if polys.is_empty:
        raise ValueError("No printable characters")
    minx, miny, maxx, maxy = polys.bounds
    polys = shapely.affinity.translate(polys, -minx, -miny)
    return extrude(polys, 0.0, depth, f"Text: {text}")


# -------------------------------------------------------------------- cut
def cut_mesh(mesh: Mesh, z: float) -> tuple[Mesh, Mesh]:
    """Split a closed mesh at height ``z`` into (below, above), both closed with a flat cap."""
    tris = mesh.triangles
    if len(tris) == 0:
        raise ValueError("Empty mesh")
    zs = tris[:, :, 2]
    if np.any(np.abs(zs - z) < 1e-7):   # avoid vertices exactly on the plane
        z += 1e-5
    below, above = [], []
    for t in tris:
        side = t[:, 2] > z
        n_above = int(side.sum())
        if n_above == 0:
            below.append(t)
            continue
        if n_above == 3:
            above.append(t)
            continue
        # one vertex alone on one side: split into a triangle and a quad
        lone = int(np.flatnonzero(side if n_above == 1 else ~side)[0])
        a, b, c = t[lone], t[(lone + 1) % 3], t[(lone + 2) % 3]
        pab = a + (b - a) * (z - a[2]) / (b[2] - a[2])
        pac = a + (c - a) * (z - a[2]) / (c[2] - a[2])
        tri = [a, pab, pac]
        quad = [[pab, b, c], [pab, c, pac]]
        if n_above == 1:
            above.append(tri)
            below.extend(quad)
        else:
            below.append(tri)
            above.extend(quad)
    normals = mesh.face_normals()
    section = loops_to_geometry(_chain(_segments_at(tris, normals, z)))
    caps = [(_cap_triangles(p)) for p in polygons_of(section)]
    for cap in caps:
        for t in cap:
            a, b, c = t
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
                b, c = c, b
            below.append([[*a, z], [*b, z], [*c, z]])      # lid of the lower part faces up
            above.append([[*a, z], [*c, z], [*b, z]])      # floor of the upper part faces down
    lo = Mesh.from_triangles(np.array(below).reshape(-1, 3, 3), f"{mesh.name} (bottom)")
    hi = Mesh.from_triangles(np.array(above).reshape(-1, 3, 3), f"{mesh.name} (top)")
    return lo, hi


# ------------------------------------------------------------------ split
def split_parts(mesh: Mesh) -> list[Mesh]:
    """Separate shells that share no vertices."""
    f = mesh.faces
    parent = np.arange(len(mesh.vertices))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in np.vstack([f[:, [0, 1]], f[:, [1, 2]]]):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(len(parent))])
    face_root = roots[f[:, 0]]
    out = []
    for k, r in enumerate(np.unique(face_root)):
        out.append(Mesh.from_triangles(mesh.triangles[face_root == r], f"{mesh.name} ({k + 1})"))
    out.sort(key=lambda m: -len(m.faces))
    return out


# ----------------------------------------------------------------- repair
def repair(mesh: Mesh) -> tuple[Mesh, dict]:
    """Remove duplicate/degenerate faces, make the winding consistent and outward, fill holes."""
    report = {"duplicates": 0, "flipped": 0, "holes_filled": 0}
    f = mesh.faces.copy()
    key = np.sort(f, axis=1)
    _, keep = np.unique(key, axis=0, return_index=True)
    report["duplicates"] = len(f) - len(keep)
    f = f[np.sort(keep)]
    v = mesh.vertices

    # consistent winding: neighbours across an edge must traverse it in opposite directions
    edges = {}
    for fi, (a, b, c) in enumerate(f):
        for u, w in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(u, w), max(u, w)), []).append(fi)
    flip = np.zeros(len(f), dtype=bool)
    seen = np.zeros(len(f), dtype=bool)

    def directed(fi):
        a, b, c = f[fi][::-1] if flip[fi] else f[fi]
        return {(a, b), (b, c), (c, a)}
    components = []
    for start in range(len(f)):
        if seen[start]:
            continue
        stack, comp = [start], []
        seen[start] = True
        while stack:
            fi = stack.pop()
            comp.append(fi)
            mine = directed(fi)
            a, b, c = f[fi]
            for u, w in ((a, b), (b, c), (c, a)):
                for nb in edges[(min(u, w), max(u, w))]:
                    if nb == fi or seen[nb]:
                        continue
                    if mine & directed(nb):      # same direction on the shared edge: flip the neighbour
                        flip[nb] = True
                    seen[nb] = True
                    stack.append(nb)
        components.append(comp)
    f = np.where(flip[:, None], f[:, ::-1], f)
    # each closed shell should have positive volume (normals outward)
    for comp in components:
        t = v[f[comp]]
        vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()
        if vol < 0:
            f[comp] = f[comp][:, ::-1]
            flip[comp] = ~flip[comp]
    report["flipped"] = int(flip.sum())

    # fill holes: boundary edges (used once) form loops; close each with a fan
    count = {}
    for a, b, c in f:
        for u, w in ((a, b), (b, c), (c, a)):
            count[(u, w)] = count.get((u, w), 0) + 1
    boundary = {(u, w) for (u, w) in count if (w, u) not in count}
    nxt = {}
    for u, w in boundary:
        nxt.setdefault(w, []).append(u)              # walk the hole opposite to the faces around it
    new_faces, verts = [], list(v)
    used = set()
    for start in list(nxt):
        if start in used:
            continue
        loop, cur = [], start
        while cur not in used and cur in nxt and nxt[cur]:
            used.add(cur)
            loop.append(cur)
            cur = nxt[cur][0]
        if len(loop) >= 3 and cur == start:
            centre = len(verts)
            verts.append(v[loop].mean(axis=0))
            for k in range(len(loop)):
                new_faces.append([loop[k], loop[(k + 1) % len(loop)], centre])
            report["holes_filled"] += 1
    if new_faces:
        f = np.vstack([f, np.array(new_faces)])
    out = Mesh(np.array(verts), f, mesh.name)
    out.remove_degenerate()
    return out, report


# ------------------------------------------------------- advanced repair
def analyze(mesh: Mesh) -> dict:
    """Health report: open edges (holes), non-manifold edges, degenerate and duplicate faces, shells."""
    f = mesh.faces
    if len(f) == 0:
        return {"faces": 0, "open_edges": 0, "nonmanifold_edges": 0, "degenerate": 0, "duplicates": 0,
                "shells": 0, "watertight": False}
    e = np.sort(f[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    keys = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    _, counts = np.unique(keys, return_counts=True)
    t = mesh.triangles
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    degenerate = int(((f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2]) | (area < 1e-10)).sum())
    duplicates = len(f) - len(np.unique(np.sort(f, axis=1), axis=0))
    return {"faces": len(f), "open_edges": int((counts == 1).sum()), "nonmanifold_edges": int((counts > 2).sum()),
            "degenerate": degenerate, "duplicates": duplicates, "shells": len(split_parts(mesh)),
            "watertight": bool(np.all(counts == 2))}


def _triangulate_loop(points: np.ndarray) -> list[tuple[int, int, int]]:
    """Ear-clipping of a hole loop projected on its best-fit plane (falls back to a fan)."""
    n = len(points)
    if n == 3:
        return [(0, 1, 2)]
    c = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - c)
    u, v = vt[0], vt[1]
    p2 = np.column_stack([(points - c) @ u, (points - c) @ v])
    area = 0.5 * np.sum(p2[:, 0] * np.roll(p2[:, 1], -1) - p2[:, 1] * np.roll(p2[:, 0], -1))
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]

    def inside(p, a, b, cc):
        d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
        d2 = (p[0] - cc[0]) * (b[1] - cc[1]) - (b[0] - cc[0]) * (p[1] - cc[1])
        d3 = (p[0] - a[0]) * (cc[1] - a[1]) - (cc[0] - a[0]) * (p[1] - a[1])
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    tris, guard = [], 0
    while len(idx) > 3 and guard < n * n:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, cc = p2[i0], p2[i1], p2[i2]
            if (b[0] - a[0]) * (cc[1] - a[1]) - (b[1] - a[1]) * (cc[0] - a[0]) <= 1e-12:
                continue                                        # reflex corner: not an ear
            if any(inside(p2[j], a, b, cc) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    elif len(idx) > 3:                                          # not a simple polygon: fan the rest
        tris += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
    if area <= 0:
        tris = [(a, cc, b) for a, b, cc in tris]
    return tris


def repair_advanced(mesh: Mesh, weld: float = 1e-4, remove_shells_below: float = 0.0,
                    fix_nonmanifold: bool = True, fill_holes: bool = True) -> tuple[Mesh, dict]:
    """Deeper repair than :func:`repair`: weld near vertices, drop degenerate, duplicate and inner
    double-wall faces, cut away faces that make edges non-manifold, drop tiny floating shells
    (``remove_shells_below`` = fraction of the largest shell's faces), fix winding and normals, and
    close holes with a proper triangulation. Returns the mesh and a report with before/after counts."""
    before = analyze(mesh)
    tris = mesh.triangles
    m = Mesh.from_triangles(tris, mesh.name, tolerance=max(weld, 1e-9))
    f = m.faces
    v = m.vertices
    report = {"before": before}
    # degenerate (repeated index or zero area)
    t = v[f]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2]) & (area > 1e-10)
    report["degenerate_removed"] = int((~keep).sum())
    f = f[keep]
    # duplicates, and pairs facing each other on the same three vertices (an inner double wall)
    key = np.sort(f, axis=1)
    uniq, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    first = np.full(len(uniq), -1)
    keep = np.zeros(len(f), dtype=bool)
    for i in range(len(f)):
        g = inv[i]
        if cnt[g] == 1:
            keep[i] = True
        elif first[g] < 0:
            first[g] = i
    # a group of 2 with opposite winding is an internal wall: drop both; otherwise keep one
    walls = 0
    for g in np.flatnonzero(cnt > 1):
        members = np.flatnonzero(inv == g)
        a, b = f[members[0]], f[members[1]]
        opposite = len(members) == 2 and ({(a[0], a[1]), (a[1], a[2]), (a[2], a[0])}
                                         & {(b[1], b[0]), (b[2], b[1]), (b[0], b[2])})
        if opposite:
            walls += 1
        else:
            keep[members[0]] = True
    report["duplicates_removed"] = int(len(f) - keep.sum() - 2 * walls)
    report["inner_walls_removed"] = walls
    f = f[keep]
    # non-manifold edges: keep the two largest faces on each such edge
    nm = 0
    if fix_nonmanifold and len(f):
        e = np.sort(f[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        ek = e[:, 0] * (len(v) + 1) + e[:, 1]
        order = np.argsort(ek, kind="stable")
        ks = ek[order]
        starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
        sizes = np.diff(np.r_[starts, len(ks)])
        t = v[f]
        farea = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
        drop = set()
        for s, n in zip(starts[sizes > 2], sizes[sizes > 2]):
            faces_on_edge = order[s:s + n] // 3
            ranked = sorted(set(faces_on_edge.tolist()), key=lambda i: -farea[i])
            drop.update(ranked[2:])
            nm += 1
        if drop:
            mask = np.ones(len(f), dtype=bool)
            mask[list(drop)] = False
            f = f[mask]
        report["nonmanifold_fixed"] = nm
    m = Mesh(v, f, mesh.name)
    # tiny floating shells (scanner noise)
    shells_removed = 0
    if remove_shells_below > 0:
        parts = split_parts(m)
        if len(parts) > 1:
            big = len(parts[0].faces)
            keep_parts = [p for p in parts if len(p.faces) >= big * remove_shells_below]
            shells_removed = len(parts) - len(keep_parts)
            m = Mesh.merge(keep_parts, mesh.name)
    report["shells_removed"] = shells_removed
    # winding, outward normals and simple hole filling from the basic repair
    m, basic = repair(m) if not fill_holes else _repair_with_triangulated_holes(m)
    report["flipped"] = basic["flipped"]
    report["holes_filled"] = basic["holes_filled"]
    report["after"] = analyze(m)
    m.name = mesh.name
    return m, report


def _repair_with_triangulated_holes(mesh: Mesh) -> tuple[Mesh, dict]:
    """Basic repair for winding, then close each boundary loop by ear clipping instead of a fan."""
    m, rep = repair(Mesh(mesh.vertices, mesh.faces, mesh.name))      # fan-filled holes are redone below
    if rep["holes_filled"] == 0:
        return m, rep
    # redo the holes: undo the fan (faces that use the added centre vertices) and triangulate the loops
    n_orig = len(mesh.vertices)
    f = m.faces
    fan = np.any(f >= n_orig, axis=1)
    base = f[~fan]
    out = [base]
    count = {}
    for a, b, c in base:
        for u, w in ((a, b), (b, c), (c, a)):
            count[(u, w)] = count.get((u, w), 0) + 1
    boundary = [(u, w) for (u, w) in count if (w, u) not in count]
    nxt = {}
    for u, w in boundary:
        nxt.setdefault(w, []).append(u)
    used, filled = set(), 0
    verts = m.vertices[:n_orig]
    for start in list(nxt):
        if start in used:
            continue
        loop, cur = [], start
        while cur not in used and cur in nxt and nxt[cur]:
            used.add(cur)
            loop.append(cur)
            cur = nxt[cur][0]
        if len(loop) >= 3 and cur == start:
            tri = _triangulate_loop(verts[loop])
            out.append(np.array([[loop[a], loop[b], loop[c]] for a, b, c in tri]))
            filled += 1
    res = Mesh(verts, np.vstack(out), mesh.name)
    res.remove_degenerate()
    # keep the outward orientation found by the basic repair
    fixed, again = repair(res)
    rep["holes_filled"] = max(filled, again["holes_filled"])
    return fixed, rep
