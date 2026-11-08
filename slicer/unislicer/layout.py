"""Auto layout: place parts on the plate (bounding boxes, true footprints, or a grid), with options as in
PreForm, CHITUBOX, Lychee and Asiga Composer: spacing, edge margin, rotation about Z, which parts to place,
where to gather them, sort order and filling the plate with copies."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPoint, Polygon, box

from .scene import SceneObject, arrange


@dataclass
class LayoutOptions:
    strategy: str = "contour"     # box (shelf rows) | contour (true footprints, tight) | grid (equal cells)
    spacing: float = 2.0          # gap between parts
    margin: float = 2.0           # free border inside the plate
    rotation: str = "free"        # none | 90 | free (15° steps) - turning parts about Z only (their tilt stays)
    scope: str = "all"            # all | selected | outside (only parts off the plate)
    anchor: str = "center"        # center | front_left | back_left | front_right | back_right
    sort: str = "area"            # area | height | name | none
    step: float = 0.0             # search grid for contour placement (mm); 0 = from the plate size




def _raster_outline(m) -> Polygon:
    """The part's real outline seen from above (U-shaped dental arches nest), from a fine raster of its
    triangles; holes inside the outline are filled."""
    from PIL import Image, ImageDraw
    t = m.triangles[:, :, :2]
    if len(t) == 0:
        return Polygon()
    lo = t.reshape(-1, 2).min(axis=0)
    hi = t.reshape(-1, 2).max(axis=0)
    res = float(np.clip(max(hi - lo) / 500.0, 0.25, 1.0))      # 0.25 mm: far finer than the 2 mm gaps
    lo = lo - 2 * res
    w, h = (np.ceil((hi - lo) / res)).astype(int) + 2
    tri = (t - lo) / res
    # a scan has hundreds of thousands of triangles smaller than a pixel: those just mark the pixels of
    # their corners (vectorised); only the bigger ones are drawn one by one
    ext = (tri.max(axis=1) - tri.min(axis=1)).max(axis=1)
    a = np.zeros((int(h), int(w)), dtype=bool)
    lim = [int(w) - 1, int(h) - 1]
    # every vertex (a scan's triangles are mostly under a pixel, so its vertices already cover it) ...
    # pixel k holds the points within half a pixel of k (rounding), so the outline errs equally both ways
    vp = np.clip(np.rint((m.vertices[:, :2] - lo) / res).astype(np.int64), 0, lim)
    a[vp[:, 1], vp[:, 0]] = True
    # ... plus edge midpoints and centres of the 1-2 px triangles, and the bigger ones drawn one by one
    mid = tri[(ext > 1.0) & (ext <= 2.0)]
    if len(mid):
        samples = np.concatenate([(mid + mid[:, [1, 2, 0]]) / 2, mid.mean(axis=1, keepdims=True)], axis=1)
        pts = np.clip(np.rint(samples.reshape(-1, 2)).astype(np.int64), 0, lim)
        a[pts[:, 1], pts[:, 0]] = True
    big = tri[ext > 2.0].reshape(-1, 6)
    if len(big):
        img = Image.new("1", (int(w), int(h)), 0)
        draw = ImageDraw.Draw(img)
        for row in (big + 0.5).tolist():          # PIL pixel k spans [k, k+1): same centres as above
            draw.polygon(row, fill=1, outline=1)
        a |= np.asarray(img, dtype=bool)
    rects = []
    for y in np.flatnonzero(a.any(axis=1)):
        e = np.flatnonzero(np.diff(np.concatenate([[0], a[y].astype(np.int8), [0]])))
        rects += [(x0, y, x1, y + 1) for x0, x1 in zip(e[::2], e[1::2])]
    if not rects:
        return MultiPoint(m.vertices[:, :2]).convex_hull
    r = np.array(rects, dtype=float)
    r = r - [0.5, 0.5, 0.5, 0.5]
    g = shapely.union_all(shapely.box(lo[0] + r[:, 0] * res, lo[1] + r[:, 1] * res,
                                      lo[0] + r[:, 2] * res, lo[1] + r[:, 3] * res)).simplify(res * 0.6)
    out = shapely.union_all([Polygon(p.exterior) for p in getattr(g, "geoms", [g]) if not p.is_empty])
    # never wider than the part itself
    return out.intersection(shapely.box(*t.reshape(-1, 2).min(axis=0), *hi))


def footprint(o: SceneObject) -> Polygon:
    """The part as seen from above, in plate coordinates: its real outline (cached; recomputed only when the
    part is turned, tilted or scaled), so concave parts such as dental arches can nest."""
    mat = o.world_matrix()
    key = tuple(np.round(mat[:3, :3], 6).ravel())
    # kept on the mesh (a few orientations), so the outlines go with it - a global table kept every mesh
    # ever arranged alive
    store = o.mesh.__dict__.setdefault("_footprints", {})
    if store.get("_v") is not o.mesh.vertices:
        store.clear()
        store["_v"] = o.mesh.vertices
    hit = store.get(key)
    if hit is None:
        m = o.world_mesh()
        try:
            shape = _raster_outline(m) if len(m.faces) > 12 else MultiPoint(m.vertices[:, :2]).convex_hull
        except Exception:
            shape = MultiPoint(m.vertices[:, :2]).convex_hull
        if shape.is_empty:
            shape = MultiPoint(m.vertices[:, :2]).convex_hull
        hit = (affinity.translate(shape, -mat[0, 3], -mat[1, 3]),)
        if len(store) > 9:                       # the latest few turns of this part
            store.pop(next(k for k in store if k != "_v"))
        store[key] = hit
    return affinity.translate(hit[0], mat[0, 3], mat[1, 3])


def _rotations(kind: str) -> list[float]:
    return {"none": [0.0], "90": [0.0, 90.0], "free": [float(a) for a in range(0, 180, 15)]}.get(kind, [0.0])


def _scan_order(rect, anchor: str, step: float, w: float, h: float) -> np.ndarray:
    """Candidate centres inside ``rect`` for a footprint of size w x h, nearest to the anchor first."""
    x0, y0, x1, y1 = rect
    xs = np.arange(x0 + w / 2, x1 - w / 2 + 1e-6, step)
    ys = np.arange(y0 + h / 2, y1 - h / 2 + 1e-6, step)
    if len(xs) == 0 or len(ys) == 0:
        return np.zeros((0, 2))
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    target = {"center": ((x0 + x1) / 2, (y0 + y1) / 2), "front_left": (x0, y0), "back_left": (x0, y1),
              "front_right": (x1, y0), "back_right": (x1, y1)}.get(anchor, ((x0 + x1) / 2, (y0 + y1) / 2))
    if anchor == "center":
        d = np.hypot(pts[:, 0] - target[0], pts[:, 1] - target[1])
    else:   # fill rows from the anchored corner (row first, then along the row)
        d = np.abs(pts[:, 1] - target[1]) * 1000 + np.abs(pts[:, 0] - target[0])
    return pts[np.argsort(d, kind="stable")]


def _order(objs: list[SceneObject], sort: str, prints: dict) -> list[SceneObject]:
    if sort == "area":
        return sorted(objs, key=lambda o: -prints[o.uid].area)
    if sort == "height":
        return sorted(objs, key=lambda o: -o.world_size()[2])
    if sort == "name":
        return sorted(objs, key=lambda o: o.name)
    return list(objs)


def _mask(geom, x0: float, y0: float, res: float, w: int, h: int) -> np.ndarray:
    """Cells of a (w x h) grid at ``res`` mm from (x0, y0) that ``geom`` touches (conservative: the outline
    cells count), as a bool array [row = y, column = x]."""
    from PIL import Image, ImageDraw
    img = Image.new("1", (max(1, w), max(1, h)), 0)
    draw = ImageDraw.Draw(img)
    for p in getattr(geom, "geoms", [geom]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        pts = [((x - x0) / res, (y - y0) / res) for x, y in np.asarray(p.exterior.coords)[:, :2]]
        if len(pts) >= 3:
            draw.polygon(pts, fill=1, outline=1)
        for ring in p.interiors:
            hole = [((x - x0) / res, (y - y0) / res) for x, y in np.asarray(ring.coords)[:, :2]]
            if len(hole) >= 3:
                draw.polygon(hole, fill=0, outline=1)
    return np.asarray(img, dtype=bool)


def _place_contour(objs, fixed, rect, opt: LayoutOptions, region=None) -> list[SceneObject]:
    """Placement of the parts' real outlines (each grown by half the gap) on a fine grid: for every turn about Z
    all free positions are found at once (FFT correlation of the plate's occupancy with the part's shape), and
    the one nearest the anchor wins. Returns the parts that did not fit (left where they were).
    ``region`` limits placement to a round plate or a ring tray."""
    half = opt.spacing / 2
    x0, y0, x1, y1 = rect
    res = max(0.25, min(opt.step if opt.step > 0 else 0.5, 1.0))
    W, H = int(math.floor((x1 - x0) / res)), int(math.floor((y1 - y0) / res))
    if W < 2 or H < 2:
        return list(objs)
    occ = np.zeros((H, W), dtype=bool)
    if region is not None:                         # cells outside a round / ring plate are taken
        occ |= ~_mask(region.buffer(-1e-3), x0, y0, res, W, H)[:H, :W]
    for o in fixed:
        occ |= _mask(footprint(o).buffer(half, quad_segs=4), x0, y0, res, W, H)[:H, :W]
    target = {"center": ((x0 + x1) / 2, (y0 + y1) / 2), "front_left": (x0, y0), "back_left": (x0, y1),
              "front_right": (x1, y0), "back_right": (x1, y1)}.get(opt.anchor, ((x0 + x1) / 2, (y0 + y1) / 2))
    prints = {o.uid: footprint(o) for o in objs}
    failed = []
    for o in _order(objs, opt.sort, prints):
        base = prints[o.uid]
        c = tuple(np.asarray(base.centroid.coords[0]))
        occ_f = np.fft.rfft2(occ.astype(np.float32))
        best = None
        for ang in _rotations(opt.rotation):
            shape = affinity.rotate(base, ang, origin=c).buffer(half, quad_segs=4)
            sx0, sy0, sx1, sy1 = shape.bounds
            mw, mh = int(math.ceil((sx1 - sx0) / res)) + 1, int(math.ceil((sy1 - sy0) / res)) + 1
            if mw > W or mh > H:
                continue
            m = _mask(shape, sx0, sy0, res, mw, mh)
            pad = np.zeros((H, W), dtype=np.float32)
            pad[:m.shape[0], :m.shape[1]] = m
            overlap = np.fft.irfft2(occ_f * np.conj(np.fft.rfft2(pad)), s=(H, W))
            ok = overlap[:H - mh + 1, :W - mw + 1] < 0.5
            if not ok.any():
                continue
            rows, cols = np.nonzero(ok)
            cx = x0 + cols * res + (sx1 - sx0) / 2          # shape-box centre at each free position
            cy = y0 + rows * res + (sy1 - sy0) / 2
            if opt.anchor == "center":
                score = np.hypot(cx - target[0], cy - target[1])
            else:                                           # rows from the anchored corner, then along the row
                score = np.abs(cy - target[1]) * 1000 + np.abs(cx - target[0])
            k = int(np.argmin(score))
            if best is None or score[k] < best[0] - 1e-6:
                best = (float(score[k]), ang, (cx[k], cy[k]))
        if best is None:
            failed.append(o)
            continue
        _, ang, (tx, ty) = best
        if ang:
            o.apply_world_rotation(_z_rotation(ang))
        now = footprint(o).buffer(half, quad_segs=4)       # move so the box centre lands on the chosen spot
        nx, ny = (now.bounds[0] + now.bounds[2]) / 2, (now.bounds[1] + now.bounds[3]) / 2
        o.x += tx - nx
        o.y += ty - ny
        occ |= _mask(footprint(o).buffer(half, quad_segs=4), x0, y0, res, W, H)[:H, :W]
    return failed


def _z_rotation(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _place_grid(objs, rect, opt: LayoutOptions) -> list[SceneObject]:
    """Equal cells sized for the largest part (good for many copies of one part)."""
    sizes = np.array([o.world_size()[:2] for o in objs])
    cw, ch = sizes.max(axis=0) + opt.spacing
    x0, y0, x1, y1 = rect
    cols, rows = max(1, int((x1 - x0) // cw)), max(1, int((y1 - y0) // ch))
    cells = [(c, r) for r in range(rows) for c in range(cols)]
    ox = x0 + ((x1 - x0) - cols * cw) / 2 if opt.anchor == "center" else x0
    oy = y0 + ((y1 - y0) - rows * ch) / 2 if opt.anchor == "center" else y0
    if opt.anchor in ("back_left", "back_right"):
        cells = [(c, rows - 1 - r) for c, r in cells]
    if opt.anchor in ("front_right", "back_right"):
        cells = [(cols - 1 - c, r) for c, r in cells]
    failed = []
    for k, o in enumerate(objs):
        if k >= len(cells):
            failed.append(o)
            continue
        c, r = cells[k]
        o.x, o.y = ox + (c + 0.5) * cw, oy + (r + 0.5) * ch
    return failed


def _split_plate(plate, margin: float):
    """(rect, region): a rectangle (x0, y0, x1, y1), or a round / ring shape with its bounding box."""
    if hasattr(plate, "bounds") and hasattr(plate, "contains"):
        region = plate.buffer(-margin) if margin > 0 and not plate.buffer(-margin).is_empty else plate
        return tuple(plate.bounds), region
    return tuple(plate), None


def auto_layout(objects: list[SceneObject], plate, opt: LayoutOptions,
                selected: list[SceneObject] | None = None) -> list[SceneObject]:
    """Lay out parts on ``plate``: a rectangle (x0, y0, x1, y1) or a round / ring shape (shapely).
    Returns the parts that could not be placed."""
    plate, region = _split_plate(plate, opt.margin)
    x0, y0, x1, y1 = plate
    m = opt.margin if (x1 - x0) > 2 * opt.margin and (y1 - y0) > 2 * opt.margin else 0.0
    rect = (x0 + m, y0 + m, x1 - m, y1 - m)
    if opt.step <= 0:
        opt = LayoutOptions(**{**opt.__dict__, "step": max(0.5, min(x1 - x0, y1 - y0) / 120)})
    parts = [o for o in objects if o.role != "cutter"]
    if opt.scope == "selected":
        movable = [o for o in (selected or []) if o.role != "cutter"]
    elif opt.scope == "outside":
        p = region if region is not None else box(*plate)
        movable = [o for o in parts if not p.contains(footprint(o))]
    else:
        movable = parts
    movable = [o for o in movable if not getattr(o, "locked", False)]     # locked parts stay where they are
    moving = {id(o) for o in movable}          # by identity: copies of one part have equal values
    fixed = [o for o in parts if id(o) not in moving]
    if not movable:
        return []
    if region is not None:                  # round plate / ring tray: true footprints inside the shape
        return _place_with_fallback(movable, fixed, rect, opt, region)
    if opt.strategy == "grid" and not fixed:
        return _place_grid(_order(movable, opt.sort, {o.uid: footprint(o) for o in movable}), rect, opt)
    if opt.strategy == "box" and not fixed:
        if opt.rotation != "none":           # stand long parts across the rows
            for o in movable:
                s = o.world_size()
                if s[1] > s[0] * 1.15:
                    o.apply_world_rotation(_z_rotation(90))
        ok = arrange(movable, x1 - x0, y1 - y0, opt.spacing, rect=rect)
        return [] if ok else [o for o in movable if not box(*plate).contains(footprint(o))]
    return _place_with_fallback(movable, fixed, rect, opt)


def _place_with_fallback(movable, fixed, rect, opt: LayoutOptions, region=None) -> list[SceneObject]:
    """Place from the chosen anchor; if some parts do not fit, also try packing from each corner and keep the
    arrangement that fits the most parts (the chosen anchor wins ties)."""
    start = [o.state() for o in movable]
    failed = _place_contour(movable, fixed, rect, opt, region)
    if not failed:
        return failed
    best = (len(failed), [o.state() for o in movable], failed)
    for anchor in ("front_left", "back_left", "front_right", "back_right"):
        if anchor == opt.anchor:
            continue
        for o, st in zip(movable, start):
            o.restore(st)
        trial = LayoutOptions(**{**opt.__dict__, "anchor": anchor})
        f = _place_contour(movable, fixed, rect, trial, region)
        if len(f) < best[0]:
            best = (len(f), [o.state() for o in movable], f)
            if not f:
                break
    for o, st in zip(movable, best[1]):
        o.restore(st)
    return best[2]


def fill_plate(objects: list[SceneObject], source: SceneObject, plate, opt: LayoutOptions,
               limit: int = 200) -> list[SceneObject]:
    """Add copies of ``source`` until the plate is full; returns the copies that fit."""
    plate, region = _split_plate(plate, opt.margin)
    x0, y0, x1, y1 = plate
    if opt.step <= 0:
        opt = LayoutOptions(**{**opt.__dict__, "step": max(0.5, min(x1 - x0, y1 - y0) / 120)})
    added = []
    for _ in range(limit):
        dup = source.duplicate()
        rest = _place_contour([dup], [o for o in objects + added if o.role != "cutter"],
                              _inner(plate, opt.margin), opt, region)
        if rest:
            break
        added.append(dup)
    return added


def _inner(plate, margin):
    x0, y0, x1, y1 = plate
    m = margin if (x1 - x0) > 2 * margin and (y1 - y0) > 2 * margin else 0.0
    return (x0 + m, y0 + m, x1 - m, y1 - m)


def layout_stats(objects: list[SceneObject], plate: tuple) -> dict | None:
    """What a finished layout looks like: smallest gap between parts, smallest distance to the plate edge,
    and how far the group's centre sits from the plate centre (0 = centred, 1 = in a corner)."""
    parts = [o for o in objects if o.role != "cutter"]
    if not parts:
        return None
    prints = [footprint(o) for o in parts]
    gaps = [prints[i].distance(prints[j]) for i in range(len(prints)) for j in range(i + 1, len(prints))]
    x0, y0, x1, y1 = plate
    edge = min(min(p.bounds[0] - x0, p.bounds[1] - y0, x1 - p.bounds[2], y1 - p.bounds[3]) for p in prints)
    cx = np.mean([p.centroid.x for p in prints])
    cy = np.mean([p.centroid.y for p in prints])
    off = math.hypot((cx - (x0 + x1) / 2) / ((x1 - x0) / 2 or 1), (cy - (y0 + y1) / 2) / ((y1 - y0) / 2 or 1)) / math.sqrt(2)
    return {"gap": min(gaps) if gaps else None, "edge": edge, "offcentre": off, "count": len(parts)}


def align(objects: list[SceneObject], mode: str) -> None:
    """Align parts by their footprints (as CHITUBOX's Align): left / right / front / back edges, centre X / Y,
    or spread evenly along X / Y ('dist_x' / 'dist_y': equal gaps between neighbours). Locked parts are
    the reference and do not move."""
    parts = [o for o in objects if o.role != "cutter"]
    if len(parts) < 2:
        return
    boxes = {o.uid: footprint(o).bounds for o in parts}
    free = [o for o in parts if not o.locked]
    ref = [o for o in parts if o.locked] or parts
    rb = [boxes[o.uid] for o in ref]
    if mode in ("left", "right", "front", "back", "center_x", "center_y"):
        target = {"left": min(b[0] for b in rb), "right": max(b[2] for b in rb),
                  "front": min(b[1] for b in rb), "back": max(b[3] for b in rb),
                  "center_x": (min(b[0] for b in rb) + max(b[2] for b in rb)) / 2,
                  "center_y": (min(b[1] for b in rb) + max(b[3] for b in rb)) / 2}[mode]
        for o in free:
            b = boxes[o.uid]
            if mode == "left":
                o.x += target - b[0]
            elif mode == "right":
                o.x += target - b[2]
            elif mode == "front":
                o.y += target - b[1]
            elif mode == "back":
                o.y += target - b[3]
            elif mode == "center_x":
                o.x += target - (b[0] + b[2]) / 2
            else:
                o.y += target - (b[1] + b[3]) / 2
        return
    if mode in ("dist_x", "dist_y"):
        k = 0 if mode == "dist_x" else 1
        order = sorted(parts, key=lambda o: (boxes[o.uid][k] + boxes[o.uid][k + 2]) / 2)
        lo = boxes[order[0].uid][k]
        hi = boxes[order[-1].uid][k + 2]
        sizes = [boxes[o.uid][k + 2] - boxes[o.uid][k] for o in order]
        gap = (hi - lo - sum(sizes)) / (len(order) - 1)
        pos = lo
        for o, size in zip(order, sizes):
            if not o.locked:
                shift = pos - boxes[o.uid][k]
                if k == 0:
                    o.x += shift
                else:
                    o.y += shift
            pos += size + gap
        return
    raise ValueError(mode)


def capture_template(objects: list[SceneObject], plate) -> dict | None:
    """The user's own arrangement as a template ("Learn layout"): where each part sits on the plate (as a
    fraction of it), its outline seen from above (so its turn about Z can be matched later), the usual gap
    between neighbours, the border and which side of the plate the group keeps to."""
    parts = [o for o in objects if o.role != "cutter"]
    if len(parts) < 2:
        return None
    if not isinstance(plate, tuple):
        plate = tuple(plate.bounds)
    x0, y0, x1, y1 = plate
    prints = [footprint(o) for o in parts]
    near = []
    for i, p in enumerate(prints):
        near.append(min(p.distance(q) for j, q in enumerate(prints) if j != i))
    if min(near) < 0.3:
        return None                          # touching parts are a mistake, not a layout to copy
    st = layout_stats(parts, plate)
    cx = float(np.mean([p.centroid.x for p in prints]))
    cy = float(np.mean([p.centroid.y for p in prints]))
    if st["offcentre"] < 0.35:
        anchor = "center"
    else:
        anchor = ("front" if cy < (y0 + y1) / 2 else "back") + "_" + ("left" if cx < (x0 + x1) / 2 else "right")
    slots = []
    for p in prints:
        c = p.centroid
        shape = affinity.translate(p, -c.x, -c.y).simplify(0.3)
        ring = shape.exterior if hasattr(shape, "exterior") else shape.convex_hull.exterior
        slots.append({"fx": (c.x - x0) / ((x1 - x0) or 1), "fy": (c.y - y0) / ((y1 - y0) or 1),
                      "area": float(p.area), "outline": [[round(a, 2), round(b, 2)] for a, b in ring.coords]})
    return {"count": len(parts), "spacing": round(float(np.median(near)), 2), "min_gap": round(float(min(near)), 2),
            "margin": round(max(0.0, float(st["edge"])), 2), "anchor": anchor, "plate": [x0, y0, x1, y1], "slots": slots}


def apply_template(objects: list[SceneObject], plate, tpl: dict | None) -> bool:
    """Lay the parts out as the learned template: each part goes to the slot of the most similar size,
    turned about Z (15° steps, tilt kept) to best match the outline it had there. Returns False and leaves
    everything as it was if the parts do not fit that way (other sizes, or they would touch)."""
    parts = [o for o in objects if o.role != "cutter"]
    if not tpl or len(parts) != int(tpl.get("count", -1)) or any(o.locked for o in parts):
        return False
    if not isinstance(plate, tuple):
        plate_shape = plate
        plate = tuple(plate.bounds)
    else:
        plate_shape = box(*plate)
    x0, y0, x1, y1 = plate
    saved = [(o, o.x, o.y, o.base_rotation.copy(), o.rotation.copy()) for o in parts]
    slots = sorted(tpl["slots"], key=lambda q: -q["area"])
    order = sorted(parts, key=lambda o: -footprint(o).area)
    try:
        for o, slot in zip(order, slots):
            target = Polygon(slot["outline"])
            fp = footprint(o)
            c = fp.centroid
            base = affinity.translate(fp, -c.x, -c.y)
            best, best_angle = -1.0, 0.0
            for ang in range(0, 360, 15):
                cand = affinity.rotate(base, ang, origin=(0, 0))
                iou = cand.intersection(target).area / (cand.union(target).area or 1.0)
                if iou > best + 1e-9:
                    best, best_angle = iou, float(ang)
            if best_angle:
                o.apply_world_rotation(_z_rotation(best_angle))
            fp = footprint(o)
            c = fp.centroid
            o.x += x0 + slot["fx"] * (x1 - x0) - c.x
            o.y += y0 + slot["fy"] * (y1 - y0) - c.y
        prints = [footprint(o) for o in parts]
        gap = min(0.5, float(tpl.get("min_gap", 0.5)) * 0.8)
        ok = all(plate_shape.buffer(1e-6).contains(p) for p in prints) and all(
            prints[i].distance(prints[j]) >= gap for i in range(len(prints)) for j in range(i + 1, len(prints)))
    except Exception:
        ok = False
    if not ok:
        for o, x, y, base_rot, rot in saved:          # turns are kept in rotation too (the Rotation fields)
            o.x, o.y, o.base_rotation, o.rotation = x, y, base_rot, rot
    return ok
