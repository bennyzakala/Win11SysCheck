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
    spacing: float = 6.0          # gap between parts
    margin: float = 2.0           # free border inside the plate
    rotation: str = "none"        # none | 90 | free (15° steps)  - turning parts about Z to fit more
    scope: str = "all"            # all | selected | outside (only parts off the plate)
    anchor: str = "center"        # center | front_left | back_left | front_right | back_right
    sort: str = "area"            # area | height | name | none
    step: float = 0.0             # search grid for contour placement (mm); 0 = from the plate size


def footprint(o: SceneObject) -> Polygon:
    """Convex hull of the part as seen from above, in plate coordinates."""
    m = o.world_mesh()
    return MultiPoint(m.vertices[:, :2]).convex_hull


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


def _place_contour(objs, fixed, rect, opt: LayoutOptions) -> list[SceneObject]:
    """Greedy placement of true footprints; returns the parts that did not fit (left where they were)."""
    half = opt.spacing / 2
    prints = {o.uid: footprint(o) for o in objs}
    occupied = shapely.union_all([footprint(o).buffer(half, quad_segs=4) for o in fixed]) if fixed else Polygon()
    failed = []
    for o in _order(objs, opt.sort, prints):
        base = prints[o.uid]
        c = np.array(base.centroid.coords[0])
        best = None
        for ang in _rotations(opt.rotation):
            shape = affinity.rotate(base, ang, origin=tuple(c)).buffer(half, quad_segs=4)
            minx, miny, maxx, maxy = shape.bounds
            local = affinity.translate(shape, -(minx + maxx) / 2, -(miny + maxy) / 2)
            if not occupied.is_empty:
                shapely.prepare(occupied)
            for px, py in _scan_order(rect, opt.anchor, opt.step, maxx - minx, maxy - miny):
                cand = affinity.translate(local, px, py)
                if occupied.is_empty or not occupied.intersects(cand):
                    score = (np.hypot(px - (rect[0] + rect[2]) / 2, py - (rect[1] + rect[3]) / 2)
                             if opt.anchor == "center" else 0.0)
                    if best is None or score < best[0] - 1e-9:
                        best = (score, ang, cand)
                    break        # nearest free spot for this rotation found
        if best is None:
            failed.append(o)
            continue
        _, ang, cand = best
        if ang:
            o.apply_world_rotation(_z_rotation(ang))
        # move so the footprint's bounding-box centre lands on the chosen spot
        now = footprint(o).buffer(half, quad_segs=4)
        nx, ny = (now.bounds[0] + now.bounds[2]) / 2, (now.bounds[1] + now.bounds[3]) / 2
        tx, ty = (cand.bounds[0] + cand.bounds[2]) / 2, (cand.bounds[1] + cand.bounds[3]) / 2
        o.x += tx - nx
        o.y += ty - ny
        occupied = shapely.union_all([occupied, footprint(o).buffer(half, quad_segs=4)])
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


def auto_layout(objects: list[SceneObject], plate: tuple, opt: LayoutOptions,
                selected: list[SceneObject] | None = None) -> list[SceneObject]:
    """Lay out parts on ``plate`` (x0, y0, x1, y1). Returns the parts that could not be placed."""
    x0, y0, x1, y1 = plate
    m = opt.margin if (x1 - x0) > 2 * opt.margin and (y1 - y0) > 2 * opt.margin else 0.0
    rect = (x0 + m, y0 + m, x1 - m, y1 - m)
    if opt.step <= 0:
        opt = LayoutOptions(**{**opt.__dict__, "step": max(0.5, min(x1 - x0, y1 - y0) / 120)})
    parts = [o for o in objects if o.role != "cutter"]
    if opt.scope == "selected":
        movable = [o for o in (selected or []) if o.role != "cutter"]
    elif opt.scope == "outside":
        p = box(*plate)
        movable = [o for o in parts if not p.contains(footprint(o))]
    else:
        movable = parts
    fixed = [o for o in parts if o not in movable]
    if not movable:
        return []
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
    return _place_contour(movable, fixed, rect, opt)


def fill_plate(objects: list[SceneObject], source: SceneObject, plate: tuple, opt: LayoutOptions,
               limit: int = 200) -> list[SceneObject]:
    """Add copies of ``source`` until the plate is full; returns the copies that fit."""
    x0, y0, x1, y1 = plate
    if opt.step <= 0:
        opt = LayoutOptions(**{**opt.__dict__, "step": max(0.5, min(x1 - x0, y1 - y0) / 120)})
    added = []
    for _ in range(limit):
        dup = source.duplicate()
        rest = _place_contour([dup], [o for o in objects + added if o.role != "cutter"],
                              _inner(plate, opt.margin), opt)
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
