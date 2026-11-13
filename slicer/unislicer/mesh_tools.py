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


_TEXT_CACHE: dict = {}


def text_polygons(text: str, height: float, font_family: str = "") -> MultiPolygon:
    """Outline of ``text`` as polygons, ``height`` mm tall (capital height), baseline at y = 0. Kept per
    text: made once on the main thread, a background support build takes it from here (fonts are best used
    on the main thread only)."""
    key = (text, float(height), font_family)
    hit = _TEXT_CACHE.get(key)
    if hit is not None:
        return hit
    out = _text_polygons(text, height, font_family)
    if len(_TEXT_CACHE) > 512:
        _TEXT_CACHE.clear()
    _TEXT_CACHE[key] = out
    return out


_FALLBACK_FAMILIES = ("Segoe UI", "Arial", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Helvetica")


def _text_path(text: str, font_family: str):
    """Qt's outline of ``text``. Qt's default font can draw nothing (a bare process on Windows without the
    app's theme): then the first installed family of a few common ones that does draw is used."""
    from PySide6.QtGui import QFont, QFontDatabase, QPainterPath

    def outline(family):
        font = QFont(family) if family else QFont()
        font.setPointSizeF(100.0)
        path = QPainterPath()
        path.addText(0, 0, font, text)
        return path
    path = outline(font_family)
    if path.isEmpty() and text.strip():
        try:
            installed = set(QFontDatabase.families())
        except Exception:
            installed = set()
        for family in [f for f in _FALLBACK_FAMILIES if f in installed] + sorted(installed)[:20]:
            alt = outline(family)
            if not alt.isEmpty():
                return alt
    return path


def _text_polygons(text: str, height: float, font_family: str = "") -> MultiPolygon:
    path = _text_path(text, font_family)
    rings = []
    for poly in path.toSubpathPolygons():
        pts = [(p.x(), -p.y()) for p in poly]           # Qt's y axis points down
        if len(pts) > 1 and pts[0] == pts[-1]:          # Qt closes each outline: drop the repeated point
            pts = pts[:-1]
        if len(set(pts)) >= 3:                          # a sliver of a glyph (2 points) is not a ring
            try:
                rings.append(Polygon(pts).buffer(0))
            except Exception:
                continue
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


_LABEL_CACHE: dict = {}         # label meshes (its own: sharing the letters' table cleared them for the raft code)


def label_mesh(text: str, point, normal, height: float, depth: float, engrave: bool, width: float = 0.0,
               angle: float = 0.0, mirror: bool = False, surface: np.ndarray | None = None,
               flush: bool = False) -> Mesh:
    """``text`` standing on the surface at ``point`` facing out along ``normal``, readable from outside with its
    letters upright (towards +Z, or +Y on a horizontal face). Embossed text rises ``depth`` out of the surface;
    engraved text (a cutter) goes ``depth`` in. Either reaches 0.3 mm past the surface so it joins cleanly.
    ``width`` > 0 shrinks a long text to fit."""
    if surface is not None and len(surface):
        return _label_on_surface(text, point, normal, height, depth, engrave, width, angle, mirror, surface, flush)
    key = (text, round(height, 4))
    flat = _LABEL_CACHE.get(key)
    if flat is None:
        polys = text_polygons(text, height)
        if polys.is_empty:
            raise ValueError("No printable characters")
        minx, miny, _maxx, _maxy = polys.bounds
        polys = shapely.affinity.translate(polys, -minx, -miny)
        # short edges, so the letters can bend with a curved surface (a long straight stroke would not)
        flat = extrude(shapely.segmentize(polys, 0.25), 0.0, 1.0, f"Text: {text}")
        if len(_LABEL_CACHE) > 64:
            _LABEL_CACHE.clear()
        _LABEL_CACHE[key] = flat
    v = flat.vertices.astype(np.float64).copy()
    w, h = float(v[:, 0].max()), float(v[:, 1].max())
    k = min(1.0, width / w) if width > 0 and w > 0 else 1.0
    x, y = (v[:, 0] - w / 2) * k, (v[:, 1] - h / 2) * k
    faces = flat.faces.copy()
    if mirror:                                      # read from the other side (e.g. through clear resin)
        x = -x
        faces = faces[:, ::-1]                      # keep the triangles facing out
    if angle:                                       # turned in the surface plane
        a = np.radians(angle)
        x, y = x * np.cos(a) - y * np.sin(a), x * np.sin(a) + y * np.cos(a)
    z0, z1 = (-depth, 0.3) if engrave else (-0.3, depth)
    if flush and engrave:                           # the look of a groove (3D view): a thin inlay at the surface
        z0, z1 = -0.05, 0.04
    z = z0 + v[:, 2] * (z1 - z0)
    n = np.asarray(normal, dtype=np.float64)
    n = n / (np.linalg.norm(n) or 1.0)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    up = ref - n * (ref @ n)
    up /= np.linalg.norm(up)
    right = np.cross(up, n)
    if surface is not None and len(surface):
        # hug the model: every point of the text keeps its depth from the surface right under it, so on a
        # curved wall (a die, a rounded base) no letter stands in the air or sinks too deep
        z = z + _surface_grid_offsets(surface, np.asarray(point, dtype=np.float64), right, up, n, x, y)
    out = np.asarray(point, dtype=np.float64) + np.outer(x, right) + np.outer(y, up) + np.outer(z, n)
    return Mesh(out, faces, f"Label: {text}")


def _label_frame(normal):
    n = np.asarray(normal, dtype=np.float64)
    n = n / (np.linalg.norm(n) or 1.0)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    up = ref - n * (ref @ n)
    up /= np.linalg.norm(up)
    return np.cross(up, n), up, n


def surface_region(tris: np.ndarray, point, right, up, n, reach: float, bounds=None):
    """The part of the label's plane that has model surface under it (facing out, within ``reach`` of the
    plane): letters are kept inside it, so none hangs in the air past a wall's edge or over a recess."""
    local = (np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3) - point) @ np.stack([right, up, n]).T
    cr = np.cross(local[:, 1] - local[:, 0], local[:, 2] - local[:, 0])
    facing = cr[:, 2] > 0.3 * np.linalg.norm(cr, axis=1)
    close = np.abs(local[:, :, 2]).min(axis=1) <= reach
    if bounds is not None:                           # only what lies under the text
        x0, y0, x1, y1 = bounds
        lo, hi = local[:, :, :2].min(axis=1), local[:, :, :2].max(axis=1)
        close &= (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1)
    keep = local[facing & close]
    if len(keep) == 0:
        return Polygon()
    polys = shapely.polygons(keep[:, :, :2])
    polys = polys[shapely.area(polys) > 1e-9]
    region = shapely.union_all(polys)
    return region.buffer(0.05).buffer(-0.05)       # close hairline cracks between triangles


def _label_on_surface(text, point, normal, height, depth, engrave, width, angle, mirror, surface, flush) -> Mesh:
    """The label fitted to the model: kept to where the surface is (cut at a wall's edge rather than left
    in the air), and every point at its depth from the surface under it."""
    key = ("poly", text, round(height, 4))
    polys = _LABEL_CACHE.get(key)
    if polys is None:
        polys = text_polygons(text, height)
        if polys.is_empty:
            raise ValueError("No printable characters")
        minx, miny, maxx, maxy = polys.bounds
        polys = shapely.affinity.translate(polys, -(minx + maxx) / 2, -(miny + maxy) / 2)
        if len(_LABEL_CACHE) > 64:
            _LABEL_CACHE.clear()
        _LABEL_CACHE[key] = polys
    minx, _miny, maxx, _maxy = polys.bounds
    w = maxx - minx
    k = min(1.0, width / w) if width > 0 and w > 0 else 1.0
    g = shapely.affinity.scale(polys, -k if mirror else k, k, origin=(0, 0))
    if angle:
        g = shapely.affinity.rotate(g, angle, origin=(0, 0))
    right, up, n = _label_frame(normal)
    p0 = np.asarray(point, dtype=np.float64)
    # the surface's height under the text, sampled once on a grid: it gives both where the surface is (the
    # text is kept inside that) and how far each point of the text sits from it
    step = 0.3
    bx = g.bounds
    gx = np.arange(bx[0] - 0.5, bx[2] + 0.5 + step, step)
    gy = np.arange(bx[1] - 0.5, bx[3] + 0.5 + step, step)
    X, Y = np.meshgrid(gx, gy, indexing="ij")
    H = _surface_offsets(surface, p0, right, up, n, X.ravel(), Y.ravel(), reach=max(2.0, depth + 1.5),
                         fill=False).reshape(X.shape)
    found = ~np.isnan(H)
    if not found.any():
        raise ValueError("No room for the label here")
    # a missed sample between found neighbours (on a shared edge) counts as found
    inner = np.zeros_like(found)
    inner[1:-1, :] |= found[:-2, :] & found[2:, :]
    inner[:, 1:-1] |= found[:, :-2] & found[:, 2:]
    found = found | inner
    ii, jj = np.nonzero(found)
    cells = shapely.box(gx[ii] - step / 2, gy[jj] - step / 2, gx[ii] + step / 2, gy[jj] + step / 2)
    region = shapely.coverage_union_all(cells) if hasattr(shapely, "coverage_union_all") else shapely.union_all(cells)
    # close single missed samples (a ray along an edge shared by two triangles can miss both)
    region = region.buffer(step * 1.01, join_style="mitre").buffer(-step * 1.01, join_style="mitre")
    g = as_multipolygon(g.intersection(region.buffer(-0.15)))
    if g.is_empty:
        raise ValueError("No room for the label here")
    if np.isnan(H).any():                            # fill the gaps with the nearest height
        known = ~np.isnan(H)
        idx = np.flatnonzero(known.ravel())
        pts = np.column_stack([X.ravel(), Y.ravel()])
        flat = H.ravel()
        for q in np.flatnonzero(~known.ravel()):
            flat[q] = flat[idx[np.argmin(np.hypot(*(pts[idx] - pts[q]).T))]]
        H = flat.reshape(X.shape)
    mesh = extrude(shapely.segmentize(g, 0.25), 0.0, 1.0, f"Label: {text}")
    v = mesh.vertices.astype(np.float64)
    x, y = v[:, 0], v[:, 1]
    fx = np.clip((x - gx[0]) / step, 0, len(gx) - 1.000001)
    fy = np.clip((y - gy[0]) / step, 0, len(gy) - 1.000001)
    a, b = np.floor(fx).astype(int), np.floor(fy).astype(int)
    a1, b1 = np.minimum(a + 1, len(gx) - 1), np.minimum(b + 1, len(gy) - 1)
    tx, ty = fx - a, fy - b
    hz = (H[a, b] * (1 - tx) + H[a1, b] * tx) * (1 - ty) + (H[a, b1] * (1 - tx) + H[a1, b1] * tx) * ty
    z0, z1 = (-depth, 0.3) if engrave else (-0.3, depth)
    if flush and engrave:
        z0, z1 = -0.05, 0.04
    z = z0 + v[:, 2] * (z1 - z0) + hz
    out = p0 + np.outer(x, right) + np.outer(y, up) + np.outer(z, n)
    return Mesh(out, mesh.faces, f"Label: {text}")


def _surface_grid_offsets(tris, point, right, up, n, x, y, step: float = 0.3) -> np.ndarray:
    """``_surface_offsets`` sampled on a ``step`` grid under the text and interpolated: thousands of text
    points cost a few hundred rays, so a label follows the mouse smoothly."""
    x0, x1, y0, y1 = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    gx = np.arange(x0, x1 + step, step)
    gy = np.arange(y0, y1 + step, step)
    if len(gx) * len(gy) >= len(x):                  # tiny text: no gain
        return _surface_offsets(tris, point, right, up, n, x, y)
    X, Y = np.meshgrid(gx, gy, indexing="ij")
    H = _surface_offsets(tris, point, right, up, n, X.ravel(), Y.ravel()).reshape(X.shape)
    fx = np.clip((x - x0) / step, 0, len(gx) - 1.000001)
    fy = np.clip((y - y0) / step, 0, len(gy) - 1.000001)
    i, j = np.floor(fx).astype(int), np.floor(fy).astype(int)
    i1, j1 = np.minimum(i + 1, len(gx) - 1), np.minimum(j + 1, len(gy) - 1)
    tx, ty = fx - i, fy - j
    return ((H[i, j] * (1 - tx) + H[i1, j] * tx) * (1 - ty) + (H[i, j1] * (1 - tx) + H[i1, j1] * tx) * ty)


def label_room(tris: np.ndarray, point, normal, height: float, max_sag: float = 0.8, reach: float = 40.0) -> float:
    """How wide a label can be at ``point``: along the label's line, how far the surface stays within
    ``max_sag`` of the label's plane on both sides (a small round die leaves little room, a flat base a lot)."""
    n = np.asarray(normal, dtype=np.float64)
    n = n / (np.linalg.norm(n) or 1.0)
    ref = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    up = ref - n * (ref @ n)
    up /= np.linalg.norm(up)
    right = np.cross(up, n)
    xs = np.arange(0.0, reach, 0.4)
    room = []
    for sign in (1.0, -1.0):
        ys = np.zeros(len(xs))
        hs = _surface_offsets(tris, np.asarray(point, dtype=np.float64), right, up, n, sign * xs, ys,
                              reach=max(3.0, height), fill=False)
        ok = ~np.isnan(hs) & (np.abs(hs) <= max_sag)
        bad = np.flatnonzero(~ok)
        room.append(xs[bad[0]] if len(bad) else xs[-1])
    return 2.0 * float(min(room))


def _surface_offsets(tris: np.ndarray, point, right, up, n, x, y, reach: float = 6.0, fill: bool = True) -> np.ndarray:
    """Height of the model's surface above the label's plane at each (x, y) of the text, along ``n``. Where
    no surface is found near (the text runs past the model's edge) it follows the nearest found point, or
    is NaN with ``fill`` off."""
    from .sla_supports import _Caster
    frame = np.stack([right, up, n])                 # world -> label frame
    local = (np.asarray(tris, dtype=np.float64) - point) @ frame.T
    local = local.reshape(-1, 3, 3)
    caster = _Caster(Mesh(local.reshape(-1, 3), np.arange(len(local) * 3).reshape(-1, 3)))   # no weld needed
    out = np.full(len(x), np.nan)
    cache: dict = {}
    for i, (px, py) in enumerate(zip(x, y)):
        key = (round(float(px), 4), round(float(py), 4))
        hit = cache.get(key)
        if hit is None:
            zs, faces = caster.hits(float(px), float(py))
            outward = caster.normals[faces, 2] > 0 if len(zs) else np.zeros(0, dtype=bool)
            near = zs[outward & (np.abs(zs) <= reach)] if len(zs) else zs
            hit = float(near[np.argmin(np.abs(near))]) if len(near) else np.nan
            cache[key] = hit
        out[i] = hit
    if not fill:
        return out
    found = ~np.isnan(out)
    if not found.any():
        return np.zeros(len(x))
    if not found.all():                              # past the model's edge: follow the nearest found point
        xy = np.column_stack([x, y])
        idx = np.flatnonzero(found)
        for i in np.flatnonzero(~found):
            out[i] = out[idx[np.argmin(np.hypot(*(xy[idx] - xy[i]).T))]]
    return out


def label_spot(mesh: Mesh) -> tuple[np.ndarray, np.ndarray, float, float] | None:
    """Where a label fits on a model by itself: the biggest flat, near-vertical side in the lower part of the
    model (on a dental model, the side of its base). Returns (point on the surface, outward normal, free width,
    free height), or None."""
    t = mesh.triangles.astype(np.float64)
    if len(t) == 0:
        return None
    cr = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    area = 0.5 * np.linalg.norm(cr, axis=1)
    nrm = cr / np.maximum(2 * area, 1e-12)[:, None]
    c = t.mean(axis=1)
    lo, hi = mesh.bounds
    side = (np.abs(nrm[:, 2]) < 0.3) & (area > 0) & (c[:, 2] <= lo[2] + 0.6 * (hi[2] - lo[2]))
    if not side.any():
        side = (np.abs(nrm[:, 2]) < 0.5) & (area > 0)
    # outer walls only: near the edge of the model's outline and facing out of it (not the inside of a
    # horseshoe-shaped arch)
    hull = shapely.MultiPoint(mesh.vertices[:, :2]).convex_hull
    if hull.geom_type == "Polygon" and side.any():
        idx = np.flatnonzero(side)
        edge = shapely.distance(shapely.points(c[idx, :2]), hull.exterior)
        out = ~shapely.contains_xy(hull, c[idx, 0] + nrm[idx, 0] * 3.0, c[idx, 1] + nrm[idx, 1] * 3.0)
        keep = idx[(edge < 2.5) & out]
        side = np.zeros(len(t), dtype=bool)
        side[keep] = True
    if not side.any():
        return None
    ang = np.arctan2(nrm[:, 1], nrm[:, 0])
    bins = ((ang + np.pi) / (2 * np.pi) * 12).astype(int) % 12
    totals = np.bincount(bins[side], weights=area[side], minlength=12)
    b = int(np.argmax(totals))
    pick = side & (bins == b)
    wa = area[pick]
    n = (nrm[pick] * wa[:, None]).sum(axis=0)
    n[2] = 0.0
    n /= np.linalg.norm(n) or 1.0
    # the flattest, best-aligned stretch: upright faces of that side close to the dominant normal
    good = pick & (nrm @ n > 0.9) & (np.abs(nrm[:, 2]) < 0.15)
    if not good.any():
        good = pick
    verts = t[good].reshape(-1, 3)
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(up, n)
    depth = verts @ n
    # only the part that stays flat: within 0.3 mm of the label's plane (a curved wall falls away from it)
    plane = float(np.percentile(depth, 75))
    flat = np.abs(depth - plane) < 0.3
    if flat.sum() >= 3:
        verts = verts[flat]
    along, height = verts @ right, verts[:, 2]
    a0, a1 = np.percentile(along, [3, 97])
    z0, z1 = np.percentile(height, [3, 97])
    span_x, span_z = float(a1 - a0), float(z1 - z0)
    surface = right * (a0 + a1) / 2 + up * (z0 + z1) / 2 + n * plane
    return surface, n, span_x, span_z


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
def shell_labels(mesh: Mesh) -> np.ndarray:
    """Per face, the number of the shell (vertex-connected piece) it belongs to: 0, 1, 2..."""
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
    _, labels = np.unique(roots[f[:, 0]], return_inverse=True)
    return labels.ravel()


def shell_count(mesh: Mesh) -> int:
    """How many separate shells (parts that share no vertices) the mesh has."""
    f = mesh.faces
    if len(f) == 0:
        return 0
    return len(np.unique(_vertex_components(max(len(mesh.vertices), int(f.max()) + 1), f)[f[:, 0]]))


def split_parts(mesh: Mesh) -> list[Mesh]:
    """Separate shells that share no vertices."""
    f = mesh.faces
    if len(f) == 0:
        return []
    face_root = _vertex_components(max(len(mesh.vertices), int(f.max()) + 1), f)[f[:, 0]]
    order = np.argsort(face_root, kind="stable")
    cuts = np.flatnonzero(np.diff(face_root[order])) + 1
    out = []
    for k, idx in enumerate(np.split(order, cuts)):
        sub = f[idx]
        used, inv = np.unique(sub, return_inverse=True)       # keep only this shell's vertices
        out.append(Mesh(mesh.vertices[used], inv.reshape(-1, 3), f"{mesh.name} ({k + 1})"))
    out.sort(key=lambda m: -len(m.faces))
    return out


# ----------------------------------------------------------------- repair
def _vertex_components(n_vertices: int, f: np.ndarray) -> np.ndarray:
    """Connected-component label per vertex (min-label propagation with pointer jumping, all in numpy)."""
    lab = np.arange(n_vertices)
    a = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    b = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    for _ in range(10000):
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m)
        np.minimum.at(new, b, m)
        new = new[new]                       # pointer jumping: follow labels to their own labels
        new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    return lab


def _lid(v: np.ndarray, loop: list, centre_index: int):
    """Faces that close one hole ``loop`` (vertex indices, in the order that faces it): a fan from its centre
    (index ``centre_index``, a new vertex at the loop's mean) when every fan triangle faces the way the hole
    does - else the fan folds over itself (a horseshoe-shaped open base: a web of resin between its arms)
    and the loop is ear-clipped. Returns (faces, uses_centre)."""
    pts = v[loop]
    c = pts.mean(axis=0)
    try:
        _, _, vt = np.linalg.svd(pts - c)
        u, w = vt[0], vt[1]
        p2 = np.column_stack([(pts - c) @ u, (pts - c) @ w])
        q = np.roll(p2, -1, axis=0)
        cross = p2[:, 0] * q[:, 1] - p2[:, 1] * q[:, 0]          # each fan triangle (centre, k, k+1), twice its area
        total = cross.sum()
        star = abs(total) > 1e-12 and np.all(cross * np.sign(total) > -1e-9 * abs(total))
    except Exception:
        star = True
    if star or len(loop) > 2000:                                  # (a huge loop: ear clipping would take too long)
        return [[loop[k], loop[(k + 1) % len(loop)], centre_index] for k in range(len(loop))], True
    tris = _triangulate_loop(pts)
    faces = [[loop[a], loop[b], loop[cc]] for a, b, cc in tris]
    # ear clipping works in the loop's own winding: keep the winding the fan would have had
    if faces:
        a, b, cc = faces[0]
        n_tri = np.cross(v[b] - v[a], v[cc] - v[a])
        ref = np.zeros(3)
        for k in range(len(loop)):
            ref += np.cross(v[loop[k]] - c, v[loop[(k + 1) % len(loop)]] - c)
        if n_tri @ ref < 0:
            faces = [[x, z, y] for x, y, z in faces]
    return faces, False


def _repair_fast(v: np.ndarray, f: np.ndarray, report: dict, fill_holes: bool, name: str):
    """``repair`` for the usual case, vectorised: every edge shared by at most two faces that already wind
    consistently (a scan with holes). Returns None when the mesh needs the general repair."""
    if len(f) == 0:
        return None
    n = int(max(int(f.max()) + 1, len(v)))
    u = np.concatenate([f[:, 0], f[:, 1], f[:, 2]]).astype(np.int64)
    w = np.concatenate([f[:, 1], f[:, 2], f[:, 0]]).astype(np.int64)
    # sort-based checks (much faster than np.unique on a few hundred thousand keys)
    directed = np.sort(u * n + w)
    if np.any(directed[1:] == directed[:-1]):
        return None                          # two faces run along an edge the same way: inconsistent winding
    lo, hi = np.minimum(u, w), np.maximum(u, w)
    und = np.sort(lo * n + hi)
    if len(und) > 2 and np.any(und[2:] == und[:-2]):
        return None                          # non-manifold edges
    # each closed shell should have positive volume (normals outward)
    lab = _vertex_components(n, f)
    face_lab = lab[f[:, 0]]
    t = v[f]
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))
    per = np.bincount(face_lab, weights=vol, minlength=n)
    flip = per[face_lab] < 0
    f = np.where(flip[:, None], f[:, ::-1], f)
    report["flipped"] = int(flip.sum())
    if not fill_holes:
        out = Mesh(v, f, name)
        out.remove_degenerate()
        return out
    # fill holes: boundary edges (whose reverse is missing) form loops; close each with a fan
    u = np.concatenate([f[:, 0], f[:, 1], f[:, 2]]).astype(np.int64)
    w = np.concatenate([f[:, 1], f[:, 2], f[:, 0]]).astype(np.int64)
    fwd = np.sort(u * n + w)
    rev = w * n + u
    pos = np.minimum(np.searchsorted(fwd, rev), len(fwd) - 1)
    open_ = fwd[pos] != rev
    nxt: dict = {}
    for a, b in zip(u[open_].tolist(), w[open_].tolist()):
        nxt.setdefault(b, []).append(a)      # walk the hole opposite to the faces around it
    new_faces, extra = [], []
    used: set = set()
    for start in list(nxt):
        if start in used:
            continue
        loop, cur = [], start
        while cur not in used and cur in nxt and nxt[cur]:
            used.add(cur)
            loop.append(cur)
            cur = nxt[cur][0]
        if len(loop) >= 3 and cur == start:
            centre = len(v) + len(extra)
            faces, fanned = _lid(v, loop, centre)
            if fanned:
                extra.append(v[loop].mean(axis=0))
            new_faces += faces
            report["holes_filled"] += 1
    if new_faces:
        f = np.vstack([f, np.array(new_faces)])
        if extra:
            v = np.vstack([v, np.array(extra)])
    out = Mesh(v, f, name)
    out.remove_degenerate()
    return out


def repair(mesh: Mesh, fill_holes: bool = True) -> tuple[Mesh, dict]:
    """Remove duplicate/degenerate faces, make the winding consistent and outward, fill holes (rims that run
    side by side - a crown's unjoined margin - are stitched together first, not lidded)."""
    report = {"duplicates": 0, "flipped": 0, "holes_filled": 0}
    if fill_holes:
        try:
            mesh, report["rims_stitched"] = stitch_paired_rims(mesh)
        except Exception:
            report["rims_stitched"] = 0
    f = mesh.faces.copy()
    key = np.sort(f, axis=1)
    nv = int(f.max()) + 1 if len(f) else 1
    if nv ** 3 < 2 ** 62:
        # one integer per face, then sort: far faster than np.unique(axis=0)
        k1 = (key[:, 0].astype(np.int64) * nv + key[:, 1]) * nv + key[:, 2]
        order = np.argsort(k1, kind="stable")
        ks = k1[order]
        first = np.ones(len(ks), dtype=bool)
        first[1:] = ks[1:] != ks[:-1]
        keep = order[first]
    else:
        _, keep = np.unique(key, axis=0, return_index=True)
    report["duplicates"] = len(f) - len(keep)
    f = f[np.sort(keep)]
    v = mesh.vertices
    fast = _repair_fast(v, f, report, fill_holes, mesh.name)
    if fast is not None:
        return fast, report

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
                around = edges[(min(u, w), max(u, w))]
                if len(around) != 2:
                    # a non-manifold edge (3+ faces): "the neighbour" means nothing there - spreading the
                    # winding across it turned whole good regions of a scan inside out
                    continue
                for nb in around:
                    if nb == fi or seen[nb]:
                        continue
                    if mine & directed(nb):      # same direction on the shared edge: flip the neighbour
                        flip[nb] = True
                    seen[nb] = True
                    stack.append(nb)
        components.append(comp)
    # a patch is consistent in itself; of its two windings keep the one most of its faces already had
    for comp in components:
        if 2 * int(flip[comp].sum()) > len(comp):
            flip[comp] = ~flip[comp]
    f = np.where(flip[:, None], f[:, ::-1], f)
    # normals outward: judged on each whole shell (connected through its vertices), not on a patch -
    # an open patch has no inside, its "volume" was noise and flipped good patches
    lab = _vertex_components(int(max(int(f.max()) + 1, len(v))), f)
    face_lab = lab[f[:, 0]]
    t = v[f]
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))
    per = np.bincount(face_lab, weights=vol, minlength=int(lab.max()) + 1)
    out_flip = per[face_lab] < 0
    f = np.where(out_flip[:, None], f[:, ::-1], f)
    flip ^= out_flip
    report["flipped"] = int(flip.sum())

    # fill holes: boundary edges (used once) form loops; close each with a fan
    if not fill_holes:
        out = Mesh(v, f, mesh.name)
        out.remove_degenerate()
        return out, report
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
            faces, fanned = _lid(v, loop, centre)
            if fanned:
                verts.append(v[loop].mean(axis=0))
            new_faces += faces
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
    keys = np.sort(e[:, 0].astype(np.int64) * (int(e.max()) + 1) + e[:, 1])
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])     # sort + run lengths: faster than unique
    counts = np.diff(np.r_[starts, len(keys)])
    t = mesh.triangles
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    degenerate = int(((f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2]) | (area < 1e-10)).sum())
    fs = np.sort(f, axis=1).astype(np.int64)
    nv = int(fs.max()) + 1
    if nv ** 3 < 2 ** 62:
        fk = np.sort((fs[:, 0] * nv + fs[:, 1]) * nv + fs[:, 2])
        duplicates = int(np.count_nonzero(fk[1:] == fk[:-1]))
    else:
        duplicates = len(f) - len(np.unique(fs, axis=0))
    return {"faces": len(f), "open_edges": int((counts == 1).sum()), "nonmanifold_edges": int((counts > 2).sum()),
            "degenerate": degenerate, "duplicates": duplicates, "shells": shell_count(mesh),
            "watertight": bool(np.all(counts == 2))}


def defect_edges(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Where the surface is broken, as line segments in the model's own coordinates:
    (open edges - the rim of a hole, used by one face only; non-manifold edges - shared by 3+ faces),
    each an (n, 2, 3) array. Shown in red / orange on the model so the user sees where it is not closed."""
    f = np.asarray(mesh.faces)
    v = np.asarray(mesh.vertices, dtype=np.float32)
    empty = np.zeros((0, 2, 3), dtype=np.float32)
    if len(f) == 0:
        return empty, empty
    e = np.sort(f[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1).astype(np.int64)
    keys = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    order = np.argsort(keys, kind="stable")
    ks = keys[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    counts = np.diff(np.r_[starts, len(ks)])
    first = order[starts]
    open_e = e[first[counts == 1]]
    bad_e = e[first[counts > 2]]
    return (v[open_e] if len(open_e) else empty), (v[bad_e] if len(bad_e) else empty)


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
    if fill_holes:
        try:
            mesh, _n = stitch_paired_rims(mesh)       # a crown's unjoined margin: stitched, not lidded
        except Exception:
            pass
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
    # the faces of each repeated group, found once by sorting (a search per group was quadratic: a file with
    # every triangle twice took a minute)
    by_group = np.argsort(inv, kind="stable")
    starts = np.concatenate([[0], np.cumsum(cnt)])
    for g in np.flatnonzero(cnt > 1):
        members = by_group[starts[g]:starts[g + 1]]
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
        cr = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        farea = 0.5 * np.linalg.norm(cr, axis=1)
        unit = cr / np.maximum(2 * farea, 1e-30)[:, None]
        drop = set()
        for s, n in zip(starts[sizes > 2], sizes[sizes > 2]):
            slots = order[s:s + n]
            faces_on_edge = sorted(set((slots // 3).tolist()))
            # keep the pair that carries the surface on: crossing the edge in opposite directions (wound
            # alike) and closest to facing the same way - a sheet inside the model or a fin standing off
            # it goes. "The two largest" kept the sheet and cut the model's own wall
            fwd = {}
            for sl in slots.tolist():
                fi, k = divmod(sl, 3)
                a, b = f[fi][k], f[fi][(k + 1) % 3]
                fwd[fi] = a < b
            best, best_score = None, None
            for x in range(len(faces_on_edge)):
                for y in range(x + 1, len(faces_on_edge)):
                    i, j = faces_on_edge[x], faces_on_edge[y]
                    score = (fwd[i] != fwd[j], float(unit[i] @ unit[j]), farea[i] + farea[j])
                    if best_score is None or score > best_score:
                        best, best_score = (i, j), score
            drop.update(fi for fi in faces_on_edge if fi not in best)
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
    m, basic = repair(m, fill_holes=False) if not fill_holes else _repair_with_triangulated_holes(m)
    report["flipped"] = basic["flipped"]
    report["holes_filled"] = basic["holes_filled"]
    report["after"] = analyze(m)
    m.name = mesh.name
    return m, report


def repair_advanced_closed(mesh: Mesh, weld: float = 1e-4, remove_shells_below: float = 0.0,
                           fix_nonmanifold: bool = True, fill_holes: bool = True) -> tuple[Mesh, dict]:
    """:func:`repair_advanced` with the chosen options; when that leaves the model open, the full closing
    (:func:`make_watertight`: a second pass with a coarser weld) on the file's mesh - taken only if it
    closes it. ``report["closed_further"]`` says so."""
    fixed, rep = repair_advanced(mesh, weld, remove_shells_below, fix_nonmanifold, fill_holes)
    rep["closed_further"] = False
    if fill_holes and not rep["after"]["watertight"]:
        try:
            closed, _steps, ok = make_watertight(mesh)
        except Exception:
            ok = False
        if ok:
            fixed = closed
            rep["after"] = analyze(closed)
            rep["closed_further"] = True
    return fixed, rep


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


def _boundary_cycles(f: np.ndarray) -> list[np.ndarray]:
    """The open rims of a mesh as cycles of vertex indices, each in the direction of its faces' edges."""
    d = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    have = set(map(tuple, d.tolist()))
    nxt = {}
    for a, b in d.tolist():
        if (b, a) not in have:
            nxt.setdefault(a, []).append(b)
    cycles, used = [], set()
    for start in list(nxt):
        if start in used or len(nxt[start]) != 1:
            continue
        cyc, v = [], start
        while v not in used and v in nxt and len(nxt[v]) == 1:
            used.add(v)
            cyc.append(v)
            v = nxt[v][0]
        if v == start and len(cyc) >= 3:
            cycles.append(np.array(cyc))
    return cycles


def stitch_paired_rims(mesh: Mesh, gap: float = 0.1) -> tuple[Mesh, int]:
    """Join rims that run side by side - the outer and the inner surface of a crown exported unjoined at its
    margin, a hair apart - with a strip of triangles, instead of closing each with a flat lid (which covered
    every opening of a bridge). Only rims whose nearest rim is each other and no more than ``gap`` mm away
    (median) are joined. Returns (mesh, number of rims joined)."""
    v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    cycles = _boundary_cycles(f)
    if len(cycles) < 2:
        return mesh, 0
    centres = np.array([v[c].mean(axis=0) for c in cycles])

    def closeness(i, j):
        """Distances from points of rim i to rim j's line (not its vertices: the two rims have different
        vertex counts) - median and largest."""
        a = v[cycles[i]][::max(1, len(cycles[i]) // 80)]
        p0 = v[cycles[j]]
        seg = np.roll(p0, -1, axis=0) - p0
        t = np.clip(np.einsum("ijk,jk->ij", a[:, None, :] - p0[None], seg) / np.maximum((seg * seg).sum(1), 1e-18),
                    0.0, 1.0)
        dist = np.linalg.norm(p0[None] + t[..., None] * seg[None] - a[:, None, :], axis=2).min(axis=1)
        return float(np.median(dist)), float(dist.max())
    partner = {}
    for i in range(len(cycles)):
        order = np.argsort(np.linalg.norm(centres - centres[i], axis=1))
        j = int(order[1]) if len(order) > 1 else -1
        if j >= 0:
            med, worst = closeness(i, j)
            if med <= gap and worst <= 3 * gap:
                partner[i] = j
    new_faces, joined = [], 0
    for i, j in partner.items():
        if partner.get(j) != i or i > j:
            continue
        a = cycles[i]
        b = cycles[j][::-1]                     # the two rims of one seam run opposite ways
        k0 = int(np.argmin(np.linalg.norm(v[b] - v[a[0]], axis=1)))
        b = np.roll(b, -k0)
        n, m = len(a), len(b)
        ia = ib = 0
        tris = []
        while ia < n or ib < m:
            a0, a1 = a[ia % n], a[(ia + 1) % n]
            b0, b1 = b[ib % m], b[(ib + 1) % m]
            if ib >= m or (ia < n and np.linalg.norm(v[a1] - v[b0]) <= np.linalg.norm(v[a0] - v[b1])):
                tris.append((a1, a0, b0))          # uses rim edge a0->a1 the other way round
                ia += 1
            else:
                tris.append((b0, b1, a0))          # rim b runs reversed: its edges are b1->b0 in its faces
                ib += 1
        new_faces += tris
        joined += 2
    if not new_faces:
        return mesh, 0
    out = Mesh(v, np.vstack([f, np.array(new_faces, dtype=np.int64)]), mesh.name)
    return out, joined


def make_watertight(mesh: Mesh) -> tuple[Mesh, dict, bool]:
    """Close a mesh as far as possible: the fast repair first (holes, winding); when that leaves it open, the
    advanced repair (weld near vertices, inner double walls, non-manifold edges, holes by triangulation), then
    the same with a coarser weld and without specks of scanner noise. Returns (mesh, report, watertight)."""
    if mesh.is_watertight():
        return mesh, {"steps": []}, True
    steps = []
    try:                                         # rims a hair apart (a crown's margin): joined, not lidded
        stitched, n = stitch_paired_rims(mesh)
        if n:
            steps.append(("stitch", {"rims": n}))
            mesh = stitched
            if mesh.is_watertight():
                return mesh, {"steps": steps}, True
    except Exception:
        pass
    best = mesh
    try:
        fixed, rep = repair(mesh)
        steps.append(("repair", rep))
        best = fixed
        if fixed.is_watertight():
            return fixed, {"steps": steps}, True
    except Exception:
        pass
    for weld, specks in ((1e-4, 0.0), (1e-3, 0.0005)):
        try:
            fixed, rep = repair_advanced(mesh, weld=weld, remove_shells_below=specks)
        except Exception:
            continue
        steps.append(("advanced", {k: v for k, v in rep.items() if k not in ("before", "after")}))
        best = fixed
        if fixed.is_watertight():
            return fixed, {"steps": steps}, True
    return best, {"steps": steps}, False


def watertight_job(vertices, faces, name: str = ""):
    """Process-pool entry point: (vertices, faces, watertight) of the closed mesh."""
    m, _rep, ok = make_watertight(Mesh(vertices, faces, name))
    return m.vertices, m.faces, ok
