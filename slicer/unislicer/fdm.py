"""FDM toolpath planning: walls, skins, infill, supports, skirt/brim and travel ordering."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon
from shapely.geometry.base import BaseGeometry

from .infill import line_fill, pattern_fill
from .mesh import Mesh
from .settings import Config
from .slicing import EMPTY, as_multipolygon, layer_heights, polygons_of, slice_meshes

# feature kinds, in the order they are listed in the preview legend
KINDS = ("outer_wall", "inner_wall", "skin", "infill", "support", "interface", "skirt")
JOIN = dict(join_style="mitre", mitre_limit=3.0)


@dataclass
class Path:
    kind: str
    points: np.ndarray          # (n, 2)
    closed: bool
    retract: bool = True        # retract before travelling to this path


@dataclass
class Layer:
    index: int
    z: float                    # top of the layer
    height: float
    paths: list[Path] = field(default_factory=list)


@dataclass
class FdmPlan:
    layers: list[Layer]
    line_width: float
    warnings: list[str] = field(default_factory=list)


class _Progress:
    def __init__(self, callback, cancelled):
        self.callback, self.cancelled = callback, cancelled

    def __call__(self, stage: str, fraction: float):
        if self.cancelled and self.cancelled():
            raise InterruptedError
        if self.callback:
            self.callback(stage, fraction)


# ----------------------------------------------------------------- geometry
def _rings(geom: BaseGeometry) -> list[np.ndarray]:
    out = []
    for poly in polygons_of(geom):
        out.append(np.asarray(poly.exterior.coords)[:-1, :2])
        for hole in poly.interiors:
            out.append(np.asarray(hole.coords)[:-1, :2])
    return [r for r in out if len(r) >= 3]


def _ring_length(r: np.ndarray) -> float:
    return float(np.hypot(*np.diff(np.vstack([r, r[:1]]), axis=0).T).sum())


def _intersect_all(geoms: list[BaseGeometry]) -> BaseGeometry:
    if not geoms:
        return EMPTY
    return shapely.intersection_all(geoms)


def _clean(geom: BaseGeometry, amount: float) -> MultiPolygon:
    """Morphological opening: drops slivers narrower than 2 * amount."""
    if geom.is_empty:
        return EMPTY
    return as_multipolygon(geom.buffer(-amount, **JOIN).buffer(amount, **JOIN))


# ----------------------------------------------------------------- supports
def compute_supports(regions: list[MultiPolygon], heights, cfg: Config, w: float):
    n = len(regions)
    support = [EMPTY] * n
    interface = [EMPTY] * n
    if n < 2:
        return support, interface
    angle = math.radians(min(cfg["support_angle"], 89.0))
    gap = cfg["support_xy_gap"]
    zgap = int(cfg["support_z_gap_layers"])
    n_iface = int(cfg["support_interface_layers"])
    buildplate = cfg["support_type"] == "buildplate"

    overhang = [EMPTY] * n
    for i in range(1, n):
        if regions[i].is_empty:
            continue
        allowed = heights[i][1] * math.tan(angle)
        overhang[i] = _clean(regions[i].difference(regions[i - 1].buffer(allowed, **JOIN)), w * 0.15)

    shadow = [EMPTY] * n
    if buildplate:
        acc = EMPTY
        for i in range(n):
            acc = acc.union(regions[i]) if not regions[i].is_empty else acc
            shadow[i] = acc

    current: BaseGeometry = EMPTY
    for i in range(n - 1, -1, -1):
        j = i + 1 + zgap
        if j < n and not overhang[j].is_empty:
            current = current.union(overhang[j].buffer(gap * 0.5, **JOIN))
        if current.is_empty:
            continue
        blocker = shadow[i] if buildplate else regions[i]
        if not blocker.is_empty:
            current = current.difference(blocker.buffer(gap, **JOIN))
        current = current.simplify(0.05)
        cleaned = _clean(current, w * 0.75)
        support[i] = cleaned
        current = as_multipolygon(current)
        if n_iface and not cleaned.is_empty:
            above = [regions[k] for k in range(i + 1 + zgap, min(n, i + 1 + zgap + n_iface))]
            if above:
                iface = cleaned.intersection(shapely.union_all(above))
                interface[i] = as_multipolygon(iface)
    return support, interface


# --------------------------------------------------------------- ordering
def _seam_start(ring: np.ndarray, mode: str, pos: np.ndarray, rng: random.Random) -> int:
    if mode == "aligned":
        return int(np.argmax(ring[:, 1] * 1000 - np.abs(ring[:, 0] - ring[:, 0].mean())))
    if mode == "random":
        return rng.randrange(len(ring))
    return int(np.argmin(((ring - pos) ** 2).sum(axis=1)))


def _order_open(lines: list[np.ndarray], pos: np.ndarray) -> list[np.ndarray]:
    """Greedy nearest-neighbour ordering of open polylines, reversing them when useful."""
    if not lines:
        return []
    starts = np.array([l[0] for l in lines])
    ends = np.array([l[-1] for l in lines])
    remaining = np.ones(len(lines), dtype=bool)
    out = []
    p = pos
    for _ in range(len(lines)):
        ds = ((starts - p) ** 2).sum(axis=1)
        de = ((ends - p) ** 2).sum(axis=1)
        ds[~remaining] = np.inf
        de[~remaining] = np.inf
        i_s, i_e = int(np.argmin(ds)), int(np.argmin(de))
        if ds[i_s] <= de[i_e]:
            line = lines[i_s]
            remaining[i_s] = False
        else:
            line = lines[i_e][::-1]
            remaining[i_e] = False
        out.append(line)
        p = line[-1]
    return out


def _order_by_proximity(items: list, key_points: list[np.ndarray], pos: np.ndarray) -> list:
    pts = np.array(key_points)
    left = list(range(len(items)))
    out = []
    p = pos
    while left:
        d = ((pts[left] - p) ** 2).sum(axis=1)
        k = left.pop(int(np.argmin(d)))
        out.append(items[k])
        p = pts[k]
    return out


class _LayerBuilder:
    def __init__(self, cfg: Config, w: float, rng: random.Random):
        self.cfg, self.w, self.rng = cfg, w, rng
        self.paths: list[Path] = []
        self.pos = np.zeros(2)
        self.comb: BaseGeometry | None = None   # travel inside this region needs no retraction
        self.min_travel = cfg["retract_min_travel"]

    def _needs_retract(self, start: np.ndarray) -> bool:
        dist = float(np.hypot(*(start - self.pos)))
        if dist <= self.min_travel:
            return False
        if self.comb is not None and self.comb.contains(LineString([self.pos, start])):
            return False
        return True

    def loops(self, rings: list[np.ndarray], kind: str):
        mode = self.cfg["seam_position"]
        for ring in _order_by_proximity(rings, [r.mean(axis=0) for r in rings], self.pos):
            if _ring_length(ring) < self.w * 2:
                continue
            k = _seam_start(ring, mode, self.pos, self.rng)
            ring = np.roll(ring, -k, axis=0)
            self.paths.append(Path(kind, ring, True, self._needs_retract(ring[0])))
            self.pos = ring[0]

    def lines(self, lines: list[np.ndarray], kind: str):
        lines = [l for l in lines if np.hypot(*(l[-1] - l[0])) > self.w * 0.5 or len(l) > 2]
        for line in _order_open(lines, self.pos):
            self.paths.append(Path(kind, line, False, self._needs_retract(line[0])))
            self.pos = line[-1]


# ------------------------------------------------------------------ main
def plan(meshes: list[Mesh], cfg: Config, progress: Callable[[str, float], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> FdmPlan:
    report = _Progress(progress, cancelled)
    w = cfg.line_width
    warnings = []
    if not meshes:
        raise ValueError("Nothing to slice")
    for m in meshes:
        if not m.is_watertight():
            warnings.append(f"'{m.name}' is not watertight; the slicer will try to repair open contours.")
    max_z = max(float(m.bounds[1, 2]) for m in meshes)
    heights = layer_heights(max_z, cfg["layer_height"], cfg["first_layer_height"])
    planes = [top - th / 2 for top, th in heights]

    report("slice", 0.0)
    regions = slice_meshes(meshes, planes, cfg["resolution"],
                           progress=lambda f: report("slice", f), cancelled=cancelled)
    n = len(regions)

    support, interface = ([EMPTY] * n, [EMPTY] * n)
    if cfg["support_enable"]:
        report("support", 0.0)
        support, interface = compute_supports(regions, heights, cfg, w)

    from . import parallel

    n_top, n_bottom = int(cfg["top_layers"]), int(cfg["bottom_layers"])
    ranges = parallel.chunk_ranges(n, 6, parallel.workers() * 3) if n >= 30 else [(0, n)]
    args = []
    for a, b in ranges:
        lo, hi = max(0, a - n_bottom), min(n, b + n_top)   # neighbours needed for top/bottom skins
        args.append((a, b, lo, regions[lo:hi], n, heights, support[a:b], interface[a:b], cfg.values, w))
    report("paths", 0.0)
    chunks = parallel.run_chunks(_plan_layers, args, lambda f: report("paths", f), cancelled,
                                 [b - a for a, b in ranges])
    report("paths", 1.0)
    return FdmPlan([layer for chunk in chunks for layer in chunk], w, warnings)


def _plan_layers(start: int, end: int, offset: int, regions_window: list, n: int, heights, support, interface,
                 values: dict, w: float) -> list[Layer]:
    """Toolpaths for layers start..end-1. ``regions_window[k]`` is layer ``offset + k`` (process-pool entry point)."""
    cfg = Config(dict(values))
    walls = int(cfg["wall_count"])
    n_top, n_bottom = int(cfg["top_layers"]), int(cfg["bottom_layers"])
    overlap = cfg["skin_overlap"] / 100.0 * w
    density = cfg["infill_density"] / 100.0
    pattern = cfg["infill_pattern"]
    base_angle = cfg["infill_angle"]
    rng = random.Random(start + 1)

    def region_at(k):
        return regions_window[k - offset]

    layers = []
    pos = np.array([0.0, 0.0])
    for i in range(start, end):
        top_z, th = heights[i]
        b = _LayerBuilder(cfg, w, rng)
        # centre-to-centre distance of touching extrusions (rounded-edge model, as in Slic3r)
        sp = w - th * (1 - math.pi / 4)
        b.pos = pos
        region = region_at(i)
        sup_i, iface_i = support[i - start], interface[i - start]

        # area that is solid both below and above -> sparse infill allowed there
        below = [region_at(k) for k in range(i - n_bottom, i)] if i - n_bottom >= 0 else None
        above = [region_at(k) for k in range(i + 1, i + 1 + n_top)] if i + n_top < n else None
        if below is None or above is None:
            sparse_ok = EMPTY
        else:
            sparse_ok = _intersect_all(below + above + [region])

        if i == 0:
            _adhesion(b, region.union(sup_i) if not sup_i.is_empty else region, cfg, w)

        islands = polygons_of(region)
        islands = _order_by_proximity(islands, [np.asarray(p.representative_point().coords[0]) for p in islands], b.pos)
        for island in islands:
            b.comb = island.buffer(-w * 0.5)
            shapely.prepare(b.comb)
            shells = []
            for k in range(walls):
                shell = island.buffer(-(w / 2 + k * sp), **JOIN)
                if shell.is_empty:
                    break
                shells.append(_rings(shell.simplify(0.01)))
            order = list(range(len(shells)))
            if not cfg["outer_wall_first"]:
                order.reverse()
            for k in order:
                b.loops(shells[k], "outer_wall" if k == 0 else "inner_wall")

            inner = island.buffer(-(w / 2 + (walls - 0.5) * sp) + overlap, **JOIN) if walls else island.buffer(-w / 2, **JOIN)
            if inner.is_empty:
                continue
            sparse = inner.intersection(sparse_ok) if not sparse_ok.is_empty else EMPTY
            sparse = _clean(sparse, w) if not sparse.is_empty else EMPTY
            skin = inner.difference(sparse) if not sparse.is_empty else inner
            if not skin.is_empty:
                skin_angle = 45.0 if i % 2 == 0 else 135.0
                b.lines(line_fill(skin, sp, skin_angle), "skin")
            if not sparse.is_empty and density > 0:
                b.lines(pattern_fill(sparse, pattern, density, sp, i, top_z, base_angle), "infill")

        b.comb = None
        if not sup_i.is_empty:
            body = sup_i.difference(iface_i) if not iface_i.is_empty else sup_i
            sup_density = max(cfg["support_density"], 1.0) / 100.0
            if not body.is_empty:
                b.comb = body.buffer(w)
                b.lines(line_fill(body, w / sup_density, 0.0), "support")
            if not iface_i.is_empty:
                b.comb = iface_i.buffer(w)
                b.lines(line_fill(iface_i, w / 0.75, 90.0 if i % 2 else 0.0), "interface")
        layers.append(Layer(i, top_z, th, b.paths))
        pos = b.pos
    return layers


def _adhesion(b: _LayerBuilder, first: BaseGeometry, cfg: Config, w: float):
    kind = cfg["adhesion_type"]
    if first.is_empty or kind == "none":
        return
    loops = []
    if kind == "skirt":
        base = first.convex_hull
        for k in range(int(cfg["skirt_loops"])):
            loops += [np.asarray(p.exterior.coords)[:-1, :2]
                      for p in polygons_of(base.buffer(cfg["skirt_distance"] + w / 2 + k * w))]
        loops.reverse()  # print outermost first so the nozzle primes away from the part
    else:
        count = max(1, int(round(cfg["brim_width"] / w)))
        for k in range(count, 0, -1):
            loops += [np.asarray(p.exterior.coords)[:-1, :2]
                      for p in polygons_of(first.buffer(w / 2 + (k - 1) * w, **JOIN))]
    mode = "nearest"
    for ring in loops:
        if len(ring) < 3:
            continue
        k = _seam_start(ring, mode, b.pos, b.rng)
        ring = np.roll(ring, -k, axis=0)
        b.paths.append(Path("skirt", ring, True, b._needs_retract(ring[0])))
        b.pos = ring[0]
