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
