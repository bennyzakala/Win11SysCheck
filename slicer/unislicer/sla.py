"""Resin (SLA / MSLA) slicing: supports, layer masks and packaged output."""
from __future__ import annotations

import datetime as _dt
import io
import json
import math
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import Point, Polygon

from . import __version__
from .mesh import Mesh
from .settings import Config, cfg_tsmc
from .sla_supports import build_supports
from .sla_timing import layer_light_off, layer_rests
from .slicing import EMPTY, MultiPolygon, as_multipolygon, polygons_of, slice_meshes


@dataclass
class SlaJob:
    layers: list[MultiPolygon]
    layer_height: float                 # nominal height (adaptive jobs vary per layer)
    models: list[Mesh]                 # elevated models
    supports: Mesh | None
    support_points: list[np.ndarray] = field(default_factory=list)
    islands: list[tuple[int, object]] = field(default_factory=list)   # (layer index, polygon)
    cups: list[tuple[int, float]] = field(default_factory=list)       # (layer index, sealed area mm2)
    cup_vents: list[dict] = field(default_factory=list)              # where a vent breaks each cup
    thin_layers: list[int] = field(default_factory=list)
    island_pillars: int = 0
    volume_ml: float = 0.0
    saved_ml: float = 0.0              # resin saved by hollowing
    time_s: float = 0.0
    weight_g: float = 0.0
    cost: float = 0.0
    warnings: list[str] = field(default_factory=list)
    layer_z: list[float] = field(default_factory=list)          # top of each layer
    layer_thickness: list[float] = field(default_factory=list)
    exposures: list[float] = field(default_factory=list)
    support_layers: list = field(default_factory=list)          # PolyJet gel support per layer
    support_ml: float = 0.0
    part_materials: list = field(default_factory=list)          # PolyJet material name per model ("" = default),
    #                                                             or a list with one name per face (shaded regions)

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def island_layers(self) -> list[int]:
        return sorted({i for i, _ in self.islands})


# --------------------------------------------------------------- exposure
def layer_exposure(i: int, cfg: Config) -> float:
    """Exposure time of layer ``i``: bottom layers, then a linear transition, then normal."""
    bottom, trans = int(cfg["bottom_layers"]), int(cfg["transition_layers"])
    hi, lo = cfg["bottom_exposure_time"], cfg["exposure_time"]
    if i < bottom:
        return hi
    if i < bottom + trans:
        return hi - (hi - lo) * (i - bottom + 1) / (trans + 1)
    return lo


def layer_motion_time(i: int, cfg: Config) -> float:
    """Seconds spent lifting, retracting and resting after layer ``i`` is exposed."""
    bottom = i < int(cfg["bottom_layers"])
    lift = cfg["bottom_lift_height"] if bottom else cfg["lift_height"]
    lift_speed = (cfg["bottom_lift_speed"] if bottom else cfg["lift_speed"]) / 60.0
    retract_speed = (cfg["bottom_retract_speed"] if bottom else cfg["retract_speed"]) / 60.0
    if cfg_tsmc(cfg):
        lift2 = cfg["lift_height_2"]
        up = lift / lift_speed + lift2 / (cfg["lift_speed_2"] / 60.0)
        slow = min(cfg["retract_height_2"], lift + lift2)
        down = (lift + lift2 - slow) / retract_speed + slow / (cfg["retract_speed_2"] / 60.0)
    else:
        up, down = lift / lift_speed, lift / retract_speed
    if cfg["wait_mode"] == "light_off":
        # legacy: the delay covers lift, wait and retract; the motion still needs its time
        return max(layer_light_off(i, cfg), up + down)
    return sum(layer_rests(i, cfg)) + up + down


def print_time(n: int, cfg: Config, exposures: list[float] | None = None) -> float:
    return sum((exposures[i] if exposures else layer_exposure(i, cfg)) + layer_motion_time(i, cfg) for i in range(n))


UNIFORM_FORMAT_NOTE = "This output format stores one layer height and exposure; use CTB, GOO or PRZ for varying layers."


def parse_z_ranges(text: str) -> list[tuple[float, float, float | None, float | None]]:
    """Height ranges with their own layer height and/or exposure (Asiga "multi-range").

    Format: ``from-to: layer_mm, exposure_s; ...`` where either value may be ``*`` (keep the default),
    e.g. ``0-0.3: *, 40; 5-12: 0.025, 3``. Malformed entries are ignored."""
    out = []
    for part in (text or "").replace("\n", ";").split(";"):
        if ":" not in part or "-" not in part.split(":")[0]:
            continue
        span, values = part.split(":", 1)
        try:
            z0, z1 = (float(v) for v in span.split("-", 1))
            vals = [v.strip() for v in values.split(",")] + ["*"]
            lh = None if vals[0] in ("", "*") else float(vals[0])
            exp = None if vals[1] in ("", "*") else float(vals[1])
        except ValueError:
            continue
        if z1 > z0 and (lh is None or 0.005 <= lh <= 0.5) and (exp is None or exp > 0):
            out.append((z0, z1, lh, exp))
    return out


def ranged_layers(top: float, lh: float, ranges) -> list[tuple[float, float]]:
    """(top z, thickness) per layer with the layer height of whichever range a layer starts in."""
    out, z = [], 0.0
    while z < top - 1e-6:
        h = next((r[2] for r in ranges if r[2] and r[0] - 1e-9 <= z < r[1] - 1e-9), lh)
        # never step over the start of the next range, so it begins exactly where it is set
        nxt = min((r[0] for r in ranges if r[0] > z + 1e-6), default=None)
        if nxt is not None and z + h > nxt + 1e-6 and nxt - z >= 0.005:
            h = nxt - z
        z = round(z + h, 5)
        out.append((z, round(h, 5)))
    return out


def adaptive_layers(meshes: list[Mesh], top: float, cfg: Config) -> list[tuple[float, float]]:
    """(top z, thickness) per layer: thin where shallow slopes would show steps, thick on steep walls.

    The step a layer of height h leaves on a face with normal n is about h * |n_z| (flat faces leave
    none), so each layer is as thick as the most demanding face it crosses allows."""
    hmin, hmax = cfg["sla_layer_min"], max(cfg["sla_layer_max"], cfg["sla_layer_min"])
    err = cfg["sla_adaptive_error"]
    zmins, zmaxs, limits = [], [], []
    for m in meshes:
        t = m.triangles
        nz = np.abs(m.face_normals()[:, 2])
        sloped = (nz > 1e-3) & (nz < 0.999)
        zmins.append(t[sloped, :, 2].min(axis=1))
        zmaxs.append(t[sloped, :, 2].max(axis=1))
        limits.append(np.clip(err / nz[sloped], hmin, hmax))
    zmin, zmax, limit = (np.concatenate(a) if a else np.zeros(0) for a in (zmins, zmaxs, limits))
    order = np.argsort(zmin)
    zmin, zmax, limit = zmin[order], zmax[order], limit[order]
    out, z = [], 0.0
    step = 0.005
    while z < top - 1e-6:
        hi = np.searchsorted(zmin, z + hmax, side="right")
        cand = np.nonzero(zmax[:hi] > z)[0]
        h = float(limit[cand].min()) if len(cand) else hmax
        h = max(hmin, min(hmax, math.floor(h / step + 1e-9) * step))
        z = round(min(z + h, max(top, z + hmin)), 5)
        out.append((z, h))
    return out


# --------------------------------------------------------------- hollowing
def _window_intersection(items: list, k: int) -> list:
    """For each i, the intersection of items[i-k..i+k] (empty near the ends), in O(n) operations
    using the van Herk / Gil-Werman prefix/suffix trick. Blocks are independent, so both passes run on
    all cores."""
    n = len(items)
    m = 2 * k + 1
    out = [EMPTY] * n
    if n < m:
        return out
    from . import parallel
    blocks = [(a, min(n, a + m)) for a in range(0, n, m)]
    per = max(1, len(blocks) // (parallel.workers() * 2))
    groups = [blocks[g:g + per] for g in range(0, len(blocks), per)]
    parts = parallel.run_chunks(_prefix_suffix_chunk, [([items[a:b] for a, b in grp],) for grp in groups],
                                weights=[sum(b - a for a, b in grp) for grp in groups])
    prefix, suffix = [], []
    for part in parts:
        for pre, suf in part:
            prefix += pre
            suffix += suf
    pairs = [(suffix[i - k], prefix[i + k]) for i in range(k, n - k)]
    out[k:n - k] = _per_layer(_and_chunk, pairs)
    return out


def _prefix_suffix_chunk(blocks: list) -> list:
    out = []
    for block in blocks:
        pre, suf = list(block), list(block)
        for j in range(1, len(block)):
            pre[j] = _and(pre[j - 1], block[j])
        for j in range(len(block) - 2, -1, -1):
            suf[j] = _and(suf[j + 1], block[j])
        out.append((pre, suf))
    return out


def _and_chunk(pairs: list) -> list:
    return [_and(a, b) for a, b in pairs]


def _and(a, b):
    if a.is_empty or b.is_empty:
        return EMPTY
    return as_multipolygon(a.intersection(b))


# ------------------------------------------------------------ per-layer work spread over the CPU cores
def _erode_chunk(layers: list, wall: float) -> list:
    return [as_multipolygon(g.buffer(-wall)) if not g.is_empty else EMPTY for g in layers]


def _clean_chunk(layers: list) -> list:
    """Drop slivers thinner than 0.02 mm (numeric leftovers of the window intersection)."""
    return [as_multipolygon(c.buffer(-0.01).buffer(0.01)) if not c.is_empty else c for c in layers]


def _thin_chunk(layers: list, r: float, min_area: float = 0.0) -> list:
    out = []
    for g in layers:
        if g.is_empty:
            out.append(EMPTY)
            continue
        # a coarser arc (4 segments a quarter) is plenty to tell what a disc of radius r fits through,
        # and makes the buffers several times faster
        opened = g.buffer(-r, quad_segs=4).buffer(r, quad_segs=4)
        # the thin part is at most area(g) - area(opening): when that is below the area that counts, skip the
        # costly difference (most layers lose only rounding slivers at their corners)
        if g.area - opened.area < min_area:
            out.append(EMPTY)
            continue
        out.append(as_multipolygon(g.difference(opened.buffer(0.01, quad_segs=4))))
    return out


def _thicken_chunk(layers: list, r: float) -> list:
    """Grow only the parts narrower than 2r (they would not form) by r/2 on each side; the rest is untouched."""
    out = []
    for g in layers:
        if g.is_empty:
            out.append(g)
            continue
        opened = g.buffer(-r, quad_segs=4).buffer(r, quad_segs=4)
        if g.area - opened.area < 0.02:     # at most rounding slivers: nothing worth growing (and cheap to tell)
            out.append(g)
            continue
        thin = g.difference(opened.buffer(0.01, quad_segs=4))
        if thin.is_empty or thin.area < 1e-4:
            out.append(g)
            continue
        # round growth a bit past r: a thin part becomes at least 2·1.25·r wide everywhere, so a disc of
        # radius r fits in every part of it and the thin check (an opening with r) keeps all of it
        # (square corners and a growth of exactly r left spikes and slivers that still read as thin)
        out.append(as_multipolygon(g.union(thin.buffer(r * 1.25, quad_segs=8))))
    return out


def _fill_chunk(cavities: list, vented_flags: list, full, vented) -> list:
    shapely.prepare(full)
    shapely.prepare(vented)
    cache: dict = {}
    out = []
    for c, v in zip(cavities, vented_flags):
        if c.is_empty:
            out.append(EMPTY)
            continue
        key = (c.wkb, v)                       # prismatic cavities repeat: compute each shape once
        fill = cache.get(key)
        if fill is None:
            fill = as_multipolygon(c.intersection(vented if v else full))
            if len(cache) < 200:
                cache[key] = fill
        out.append(fill)
    return out


def _union_chunk(pairs: list) -> list:
    return [as_multipolygon(shapely.union_all([a, b])) if not b.is_empty else a for a, b in pairs]


def _minus_chunk(pairs: list) -> list:
    return [as_multipolygon(g.difference(f)) if not f.is_empty and not g.is_empty else g for g, f in pairs]


def _per_layer(fn, items: list, *extra) -> list:
    """``fn(chunk, *extra)`` over contiguous chunks of ``items`` on all cores; results in order."""
    from . import parallel
    n = len(items)
    if n == 0:
        return []
    ranges = parallel.chunk_ranges(n, 16, parallel.workers() * 2)
    chunks = parallel.run_chunks(fn, [(items[a:b], *extra) for a, b in ranges], weights=[b - a for a, b in ranges])
    return [x for chunk in chunks for x in chunk]


HOLLOW_KEYS = ("hollow_enable", "hollow_wall", "hollow_base_height", "hollow_mode", "hollow_infill",
               "hollow_infill_spacing", "hollow_infill_thickness", "drain_holes", "drain_hole_diameter",
               "drain_hole_depth", "sla_internal_supports", "sla_internal_spacing")


def covered_voids(layers: list[MultiPolygon]) -> list[MultiPolygon]:
    """Empty spaces inside the model that have model material above them: the cavity of a model that was
    already hollow when it came from the CAD (thin shell, open or closed base). A hole that goes right
    through (the middle of a ring, a hole for a pin) has nothing above it and is not included."""
    n = len(layers)
    holes = []
    for g in layers:
        if g.is_empty:
            holes.append(EMPTY)
            continue
        inner = [shapely.Polygon(r) for p in polygons_of(g) for r in p.interiors]
        h = shapely.union_all(inner).difference(g) if inner else EMPTY
        holes.append(as_multipolygon(h) if not h.is_empty else EMPTY)
    out = [EMPTY] * n
    above = EMPTY                                   # model material over this layer, seen from the top
    for i in range(n - 1, -1, -1):
        if not holes[i].is_empty and not above.is_empty:
            v = holes[i].intersection(above)
            if not v.is_empty and v.area > 0.5:
                out[i] = as_multipolygon(v.buffer(-0.05).buffer(0.05)) if v.area > 1 else EMPTY
        if not layers[i].is_empty:
            above = above.union(layers[i]) if not above.is_empty else layers[i]
            if i % 25 == 0:
                above = above.simplify(0.02)
    return out


def hollow(layers: list[MultiPolygon], lh: float, cfg: Config, infill_out: list | None = None
           ) -> tuple[list[MultiPolygon], float, list[str]]:
    """Hollow solid layers to ``hollow_wall`` thickness and cut drain holes. Returns (layers, saved ml, notes).
    With ``infill_out`` (a list), the internal infill of each layer is also put there (for checks that
    should look at the shell only)."""
    wall = cfg["hollow_wall"]
    k = max(1, int(math.ceil(wall / lh)))
    eroded = _per_layer(_erode_chunk, layers, wall)
    cavity = _window_intersection(eroded, k)
    cavity = _per_layer(_clean_chunk, cavity)
    saved = sum(c.area for c in cavity) * lh / 1000.0
    # a model that is already hollow (e.g. a dental model exported as a shell): its own cavity takes the infill
    existing = covered_voids(layers) if cfg["hollow_infill"] != "none" else [EMPTY] * len(layers)
    if saved <= 0 and not any(not v.is_empty for v in existing):
        return layers, 0.0, ["The model is too thin to hollow with this wall thickness."]
    notes = []
    if cfg["hollow_mode"] == "solid_base":
        # keep the bottom solid up to the base height, hollow above it
        first = next((i for i, g in enumerate(layers) if not g.is_empty), 0)
        solid = first + int(math.ceil(cfg["hollow_base_height"] / lh))
        for i in range(min(solid, len(cavity))):
            cavity[i] = EMPTY
        saved = sum(c.area for c in cavity) * lh / 1000.0
    if cfg["hollow_mode"] == "bottom":
        # open base: the cavity continues down through the floor (walls stay)
        first = next((i for i, c in enumerate(cavity) if not c.is_empty), 0)
        for i in range(first):
            if not eroded[i].is_empty:
                cavity[i] = eroded[i]
    out = _per_layer(_minus_chunk, list(zip(layers, cavity)))
    infill_space = [as_multipolygon(c.union(v)) if not v.is_empty else c for c, v in zip(cavity, existing)]
    pattern = _infill_pattern(infill_space, cfg)
    if pattern is not None:
        full, vented = pattern
        # drain windows: a band at the bottom of the cavity (resin flows to the drain holes) and then every
        # few millimetres, so no cell is a sealed chamber; between the bands the walls are whole
        band = max(cfg["hollow_infill_thickness"] * 1.5, 1.2)
        period = max(3.0 * cfg["hollow_infill_spacing"], 8.0)
        first = next((i for i, c in enumerate(infill_space) if not c.is_empty), 0)
        flags = [((i - first) * lh) % period < band for i in range(len(infill_space))]
        from . import parallel
        n = len(infill_space)
        ranges = parallel.chunk_ranges(n, 16, parallel.workers() * 2)
        chunks = parallel.run_chunks(_fill_chunk, [(infill_space[a:b], flags[a:b], full, vented) for a, b in ranges],
                                     weights=[b - a for a, b in ranges])
        fills = [x for chunk in chunks for x in chunk]
        out = [o.union(f) if not f.is_empty else o for o, f in zip(out, fills)]
        if infill_out is not None:
            infill_out[:] = fills
    elif cfg["sla_internal_supports"] and cfg["support_enable"]:
        out = _internal_pillars(out, cavity, cfg)
    holes = 0 if cfg["hollow_mode"] == "bottom" or saved <= 0 else int(cfg["drain_holes"])
    if cfg["hollow_mode"] == "bottom":
        pass
    elif holes > 0:
        bottom = next(i for i, g in enumerate(layers) if not g.is_empty)
        r = cfg["drain_hole_diameter"] / 2
        # the lowest cavity layer that is wide enough for a hole
        first, room = None, EMPTY
        for i, c in enumerate(cavity):
            if not c.is_empty:
                room = c.buffer(-r * 0.6)
                if not room.is_empty:
                    first = i
                    break
        centers = _spread_points(room, holes) if first is not None else []
        if centers:
            cut = shapely.union_all([Point(p).buffer(r, quad_segs=16) for p in centers])
            for i in range(bottom, min(len(out), first + 2)):
                out[i] = as_multipolygon(out[i].difference(cut))
        else:
            notes.append("The cavity is too small for the drain holes.")
    elif saved > 0:
        notes.append("Hollowed without drain holes: uncured resin will stay trapped inside.")
    return [as_multipolygon(g) for g in out], saved, notes


def _infill_pattern(cavity: list, cfg: Config):
    """2D wall pattern (grid or honeycomb) covering all cavities, or None: (walls, vent openings).
    The openings are the middles of the walls; they are cut only in thin bands of height (see ``hollow``)
    so every cell drains while the walls stay continuous and hold the shell."""
    kind = cfg["hollow_infill"]
    if kind == "none":
        return None
    cav = [c for c in cavity if not c.is_empty]
    if not cav:
        return None
    minx = min(c.bounds[0] for c in cav)
    miny = min(c.bounds[1] for c in cav)
    maxx = max(c.bounds[2] for c in cav)
    maxy = max(c.bounds[3] for c in cav)
    sp, t = cfg["hollow_infill_spacing"], cfg["hollow_infill_thickness"]
    lines = []
    vents = []     # slots in the middle of every wall so no cell is a sealed chamber (resin must drain)
    slot = max(1.2, t * 1.5)
    if kind == "grid":
        for x in np.arange(minx - sp, maxx + sp, sp):
            lines.append([(x, miny - sp), (x, maxy + sp)])
            for y in np.arange(miny - sp, maxy + sp, sp):
                vents.append(Point(x, y + sp / 2))
        for y in np.arange(miny - sp, maxy + sp, sp):
            lines.append([(minx - sp, y), (maxx + sp, y)])
            for x in np.arange(minx - sp, maxx + sp, sp):
                vents.append(Point(x + sp / 2, y))
    else:
        r = sp / math.sqrt(3)            # hexagon circumradius so that opposite walls are ``sp`` apart
        w, h = math.sqrt(3) * r, 1.5 * r
        for row, y in enumerate(np.arange(miny - 2 * r, maxy + 2 * r, h)):
            for x in np.arange(minx - 2 * w, maxx + 2 * w, w):
                cx = x + (w / 2 if row % 2 else 0)
                hexagon = [(cx + r * math.cos(math.radians(60 * k + 30)), y + r * math.sin(math.radians(60 * k + 30)))
                           for k in range(7)]
                lines.append(hexagon)
                vents += [Point((hexagon[k][0] + hexagon[k + 1][0]) / 2, (hexagon[k][1] + hexagon[k + 1][1]) / 2)
                          for k in range(6)]
    walls = shapely.union_all([shapely.LineString(l) for l in lines]).buffer(t / 2, cap_style="flat")
    openings = shapely.union_all([v.buffer(slot / 2, quad_segs=4) for v in vents])
    return walls, walls.difference(openings)


def _internal_pillars(layers: list, cavity: list, cfg: Config) -> list:
    """Pillars inside cavities; a pillar only starts on solid material or on its own pillar below."""
    cav = [c for c in cavity if not c.is_empty]
    if not cav:
        return layers
    r = cfg["sla_pillar_diameter"] / 2
    sp = cfg["sla_internal_spacing"]
    minx = min(c.bounds[0] for c in cav)
    miny = min(c.bounds[1] for c in cav)
    maxx = max(c.bounds[2] for c in cav)
    maxy = max(c.bounds[3] for c in cav)
    xs, ys = np.meshgrid(np.arange(minx + sp / 2, maxx, sp), np.arange(miny + sp / 2, maxy, sp))
    px, py = xs.ravel(), ys.ravel()
    if px.size == 0:
        return layers
    circles = [Point(x, y).buffer(r, quad_segs=6) for x, y in zip(px, py)]
    active = np.zeros(px.size, dtype=bool)
    out = list(layers)
    for i, c in enumerate(cavity):
        if c.is_empty:
            active[:] = False
            continue
        room = c.buffer(-r * 0.5)
        if room.is_empty:
            active[:] = False
            continue
        shapely.prepare(room)
        inside = shapely.contains_xy(room, px, py)
        below = out[i - 1] if i else EMPTY
        if not below.is_empty:
            shapely.prepare(below)
            on_solid = shapely.contains_xy(below, px, py)
        else:
            on_solid = np.zeros(px.size, dtype=bool)
        active = inside & (active | on_solid)
        if active.any():
            out[i] = as_multipolygon(out[i].union(shapely.union_all([circles[k] for k in np.flatnonzero(active)])))
    return out


def _spread_points(area, count: int) -> list[tuple[float, float]]:
    """Up to ``count`` points inside ``area``, spread far apart (farthest-point sampling)."""
    if area.is_empty:
        return []
    minx, miny, maxx, maxy = area.bounds
    step = max((maxx - minx), (maxy - miny)) / 40 or 1.0
    xs, ys = np.meshgrid(np.arange(minx, maxx + step, step), np.arange(miny, maxy + step, step))
    cand = np.column_stack([xs.ravel(), ys.ravel()])
    shapely.prepare(area)
    cand = cand[shapely.contains_xy(area, cand[:, 0], cand[:, 1])]
    if len(cand) == 0:
        p = area.representative_point()
        return [(p.x, p.y)]
    centre = np.array(area.centroid.coords[0])
    chosen = [cand[np.argmax(((cand - centre) ** 2).sum(axis=1))]]
    while len(chosen) < count:
        d = np.min([((cand - c) ** 2).sum(axis=1) for c in chosen], axis=0)
        if d.max() <= 0:
            break
        chosen.append(cand[np.argmax(d)])
    return [tuple(c) for c in chosen]


# ----------------------------------------------------------------- islands
def find_islands(layers: list[MultiPolygon], min_area: float = 0.01, only=None) -> list[tuple[int, object]]:
    """Regions that start in mid-air: not connected to anything on the layer below.
    ``only``: check just these layer indices."""
    out = []
    for i in (range(1, len(layers)) if only is None else sorted(k for k in only if 0 < k < len(layers))):
        prev = layers[i - 1]
        if layers[i].is_empty:
            continue
        shapely.prepare(prev)
        for poly in polygons_of(layers[i]):
            if poly.area >= min_area and not prev.intersects(poly):
                out.append((i, poly))
    return out


PILLAR_RADIUS = 0.4


def _add_disk(layer, disk):
    """``layer`` ∪ ``disk``, merging only with the parts the disk touches (much cheaper on a big layer)."""
    parts = polygons_of(layer)
    touch = [p for p in parts if p.intersects(disk)]
    if not touch:
        return MultiPolygon(parts + [disk])
    rest = [p for p in parts if not any(p is t for t in touch)]
    return MultiPolygon(rest + polygons_of(shapely.union_all(touch + [disk])))


def island_pillars(layers: list, radius: float = PILLAR_RADIUS, rounds: int = 3) -> tuple[list, int]:
    """A thin round pillar under every island, from the island down to the first layer that is solid at that
    spot (or the plate). Returns the new layers and how many pillars were added."""
    layers = list(layers)
    added = 0
    check = None                             # first round: every layer; then only what the pillars touched
    for _ in range(rounds):                  # a pillar can leave a smaller piece of the island in the air
        islands = find_islands(layers, only=check)
        if not islands:
            break
        check = set()
        for i, poly in islands:
            p = poly.representative_point()
            disk = p.buffer(radius, quad_segs=8)
            j = i - 1
            check.update((i, i + 1))
            while j >= 0 and not layers[j].contains(p):
                layers[j] = _add_disk(layers[j], disk)
                check.update((j, j + 1))
                j -= 1
            added += 1
    return layers, added


def hole_meshes(holes, cfg: Config, lift: float = 0.0) -> list[Mesh]:
    """Cylinders for manually placed drain holes. ``holes[i]`` is (N, 6): surface point and outward normal."""
    from .primitives import tube
    out = []
    r = cfg["drain_hole_diameter"] / 2
    depth = cfg["drain_hole_depth"] or (cfg["hollow_wall"] + 1.0)
    for group in holes or []:
        if group is None:
            continue
        for row in np.asarray(group, dtype=np.float64).reshape(-1, 6):
            p, n = row[:3] + [0, 0, lift], row[3:]
            n = n / (np.linalg.norm(n) or 1.0)
            out.append(tube(p + n * 1.0, p - n * depth, r, r, 24))
    return out


def vent_hole_rows(vent: dict, cfg: Config, lift: float = 0.0) -> np.ndarray:
    """Drain-hole rows (point, outward normal) that drill a vent from the roof's outer surface up into the
    cavity. Holes are ``drain_hole_depth`` deep, so a thick roof gets a short stack of them."""
    depth = max(0.5, cfg["drain_hole_depth"] or (cfg["hollow_wall"] + 1.0))
    count = int(min(40, max(1, math.ceil((vent["depth"] + 0.6) / depth))))
    n = np.array(vent["normal"], dtype=float)
    p = np.array([vent["x"], vent["y"], vent["z_surface"] - lift])
    return np.array([np.concatenate([p - n * k * depth, n]) for k in range(count)])


def _shrink_compensate(mesh: Mesh, cfg: Config) -> Mesh:
    """Scale up around the model's footprint centre to offset resin shrinkage."""
    sx, sz = 1 + cfg["shrink_xy"] / 100.0, 1 + cfg["shrink_z"] / 100.0
    if sx == 1 and sz == 1:
        return mesh
    lo, hi = mesh.bounds
    c = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    m = np.diag([sx, sx, sz, 1.0])
    m[:3, 3] = c - c * [sx, sx, sz]
    out = mesh.transform(m)
    out.name = mesh.name
    return out


# ------------------------------------------------------------ validation
def _cavity_cells_chunk(pairs: list) -> list:
    """Per (layer, layer below): the enclosed empty cells of the layer, and what of each cell is not over the
    solid of the layer below. Independent per layer, so it runs on all cores (``_sealed_layers``)."""
    out = []
    for g, below in pairs:
        polys = polygons_of(g)
        holes = [Polygon(h) for p in polys for h in p.interiors]
        if not holes:
            out.append(None)
            continue
        hole_area = shapely.union_all(holes)
        # the enclosed space is the holes minus any part standing inside them (a polygon nested in a hole);
        # subtracting only those is far cheaper than subtracting the whole layer
        shapely.prepare(hole_area)
        inner = [p for p in polys if hole_area.contains(p.representative_point())]
        space = hole_area.difference(shapely.union_all(inner)) if inner else hole_area
        cells = np.array(polygons_of(space), dtype=object)
        if len(cells) == 0:
            out.append(None)
            continue
        bx = shapely.bounds(cells)
        if below.is_empty:
            rest = cells
        else:
            # each cell against only the piece of the layer below under its own bounding box
            near = np.array([shapely.clip_by_rect(below, b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1) for b in bx],
                            dtype=object)
            rest = shapely.difference(cells, near)
        out.append((cells, rest, bx))
    return out


def _post_chunk(items: list, first: bool, thicken: bool, r_thin: float, min_area: float) -> list:
    """Everything done per layer once a group is sliced and hollowed, in one pass per chunk (one trip to the
    worker processes instead of four): thicken thin parts, the shell (no honeycomb infill), its thin part,
    and its enclosed cells for the suction-cup check. ``items`` are (layer, infill) and, unless ``first``,
    start with the layer just below the chunk (the cells need the shell under them)."""
    out, prev_shell = [], EMPTY
    for k, (g, fill) in enumerate(items):
        has_fill = fill is not None and not fill.is_empty and not g.is_empty
        shell = as_multipolygon(g.difference(fill)) if has_fill else g
        if thicken:
            shell = _thicken_chunk([shell], 0.15)[0]
            g = as_multipolygon(shell.union(fill)) if has_fill else shell
        if k == 0 and not first:
            prev_shell = shell                   # context only
            continue
        thin = _thin_chunk([shell], r_thin, min_area)[0]
        cells = _cavity_cells_chunk([(shell, prev_shell)])[0]
        out.append((g, shell, thin, cells))
        prev_shell = shell
    return out


def _post_process(layers: list, infill: list | None, thicken: bool, min_width: float = 0.3,
                  min_area: float = 0.05) -> list:
    """``_post_chunk`` over all layers on all cores; returns one (layer, shell, thin, cells) per layer."""
    from . import parallel
    n = len(layers)
    if n == 0:
        return []
    fills = infill if infill and len(infill) == n else [None] * n
    items = list(zip(layers, fills))
    ranges = parallel.chunk_ranges(n, 16, parallel.workers() * 2)
    args = [(items[max(0, a - 1):b], a == 0, thicken, min_width / 2, min_area) for a, b in ranges]
    parts = parallel.run_chunks(_post_chunk, args, weights=[b - a for a, b in ranges])
    return [x for part in parts for x in part]


def _sealed_layers(layers: list[MultiPolygon], per: list | None = None) -> list[MultiPolygon]:
    """Per layer, the empty space that is enclosed by walls and sealed on the build-plate side.

    A cavity is sealed only if all of it sits on solid or on a cavity that is itself sealed; any opening
    (e.g. a drain or vent hole) vents the whole cavity. The allowance only absorbs numeric noise, so even
    a 1 mm vent in a large cavity counts as an opening."""
    if per is None:
        per = _per_layer(_cavity_cells_chunk, [(g, layers[i - 1] if i else EMPTY) for i, g in enumerate(layers)])
    out = []
    sealed = EMPTY
    for i, item in enumerate(per):
        if item is None:
            sealed = EMPTY
            out.append(EMPTY)
            continue
        cells, rest, bx = item
        if i == 0 and sealed.is_empty:
            keep = []                           # nothing under the first layer but the plate
        else:
            if not sealed.is_empty:             # the only step that depends on the layer below's result
                # what is left usually lies right over the sealed cavity below (their outlines are the same
                # edge of the layer below): a quick "covers" settles those without an area difference
                shapely.prepare(sealed)
                todo = np.flatnonzero(~(shapely.is_empty(rest) | shapely.covers(sealed, rest)))
                rest = np.where(shapely.is_empty(rest), rest, shapely.Polygon())
                if len(todo):
                    near = np.array([shapely.clip_by_rect(sealed, *(bx[k, :2] - 0.1), *(bx[k, 2:] + 0.1)) for k in todo],
                                    dtype=object)
                    rest[todo] = shapely.difference(per[i][1][todo], near)
            limit = np.minimum(0.01 * shapely.area(cells), 0.3) + 1e-6
            keep = list(cells[shapely.area(rest) < limit])
        sealed = as_multipolygon(shapely.union_all(keep)) if keep else EMPTY
        out.append(sealed)
    return out


def find_cups(layers: list[MultiPolygon], min_area: float = 10.0, sealed: list | None = None) -> list[tuple[int, float]]:
    """Suction cups: empty space enclosed by walls and sealed on the build-plate side.

    Each peel pulls a vacuum in such a cavity. Returns (layer, area mm2) for layers where one is open
    towards the vat. A drain hole through a wall or the floor breaks the seal and clears the warning."""
    sealed = sealed if sealed is not None else _sealed_layers(layers)
    out = []
    for i, s in enumerate(sealed):
        # only real pockets count: dozens of tiny pits (e.g. the fissures of a scanned tooth, a few mm² each)
        # pull no meaningful vacuum, but added up per layer they looked like a big cup
        area = sum(c.area for c in polygons_of(s) if c.area >= min_area)
        if area >= min_area:
            out.append((i, area))
    return out


def cup_vents(layers: list[MultiPolygon], layer_z: list[float], min_area: float = 10.0, radius: float = 1.5,
              sealed: list | None = None) -> list[dict]:
    """Where to drill a vent that breaks each suction cup: straight up from the plate side, through the
    roof that seals the cavity, at a point well inside the cavity.

    Returns dicts: x, y (mm), z_surface (outer surface of the roof, plate side), z_cavity (where the cavity
    starts), area, layer. A cavity whose roof reaches down to the plate (no outer surface) gets no vent."""
    from shapely.ops import polylabel
    sealed = sealed if sealed is not None else _sealed_layers(layers)
    vents = []
    handled_prev = EMPTY          # cavity area in the layer below that already has its vent (or got one tried)
    for i, s in enumerate(sealed):
        handled = []
        for c in polygons_of(s):
            if not handled_prev.is_empty and c.intersects(handled_prev):
                handled.append(c)              # the same cavity, already vented
                continue
            # a cavity often starts as a dot (a rounded or tilted bottom) and widens: it is vented at the
            # first layer where it is big enough to need it and wide enough for the hole
            if c.area < min_area:
                continue
            inner = c.buffer(-radius)
            if inner.is_empty:
                inner = c.buffer(-radius * 0.5)
                if inner.is_empty:
                    # a narrow slot (e.g. between a base bar and the arch): the hole is wider than the slot,
                    # which still lets air in - drill it on the slot's middle line
                    inner = c
            spots = [polylabel(p, 0.1) for p in polygons_of(as_multipolygon(inner))]
            x0, y0, x1, y1 = inner.bounds
            for gx in np.linspace(x0, x1, 5)[1:-1]:          # more tries, in case a support sits under the first
                for gy in np.linspace(y0, y1, 5)[1:-1]:
                    q = Point(gx, gy)
                    if inner.contains(q):
                        spots.append(q)
            found = None
            for q in spots:
                # down through the cavity below this layer (it may have started lower, narrower) ...
                top = i - 1
                while top >= 0 and sealed[top].contains(q):
                    top -= 1
                # ... to the roof that seals it, and through the roof to its outer surface
                if top < 0 or not layers[top].contains(q):
                    continue
                j = top
                while j >= 0 and layers[j].contains(q):
                    j -= 1
                if j < 0:
                    continue                   # the roof sits on the plate here
                found = {"kind": "roof", "x": float(q.x), "y": float(q.y), "z_surface": float(layer_z[j]),
                         "z_cavity": float(layer_z[top]), "normal": (0.0, 0.0, -1.0),
                         "depth": float(layer_z[top] - layer_z[j]), "area": float(c.area), "layer": top + 1}
                break
            if found is None:        # roof on the plate (or blocked): drill sideways through the wall, at the roof
                found = _side_vent(layers, layer_z, i, c, spots[0], radius)
            if found is not None:
                vents.append(found)
            handled.append(c)
        handled_prev = shapely.union_all(handled) if handled else EMPTY
    return vents


def _side_vent(layers, layer_z, i, cavity, q, radius):
    """Horizontal vent from the cavity through the thinnest wall, just above the roof."""
    from shapely.geometry import LineString
    zc = float(layer_z[i - 1]) if i else 0.0
    zh = zc + radius * 0.9                          # the hole's lower edge touches the roof
    k = next((n for n in range(i, len(layers)) if layer_z[n] >= zh), i)
    solid = layers[k]
    reach = 2 * max(cavity.bounds[2] - cavity.bounds[0], cavity.bounds[3] - cavity.bounds[1]) + 50
    best = None
    for ang in np.radians(np.arange(0, 360, 15)):
        d = np.array([math.cos(ang), math.sin(ang)])
        ray = LineString([(q.x, q.y), (q.x + d[0] * reach, q.y + d[1] * reach)])
        pieces = sorted((g for g in polygons_of_lines(ray.intersection(solid))),
                        key=lambda g: Point(q.x, q.y).distance(g))
        if not pieces:
            continue
        wall = pieces[0]
        ends = [Point(c) for c in wall.coords]
        far = max(ends, key=lambda e: e.distance(Point(q.x, q.y)))
        if Point(q.x, q.y).distance(far) >= reach - 1e-6:
            continue                                 # never leaves the part this way
        if best is None or wall.length < best[0]:
            best = (wall.length, far, d)
    if best is None:
        return None
    t, far, d = best
    return {"kind": "side", "x": float(far.x), "y": float(far.y), "z_surface": zh, "z_cavity": zc,
            "normal": (float(d[0]), float(d[1]), 0.0), "depth": float(t), "area": float(cavity.area), "layer": i}


def polygons_of_lines(g):
    """Line pieces of a ray/solid intersection."""
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "LineString" and p.length > 1e-6]


def find_thin(layers: list[MultiPolygon], min_width: float = 0.3, min_area: float = 0.05,
              layer_height: float = 0.05, persist: float = 0.5) -> list[int]:
    """Layers with model features narrower than ``min_width`` (likely to break or not form).

    Only regions that stay thin through ``persist`` mm of height count (a real thin wall or fin), so the
    small slivers at the tips of tilted parts are not reported."""
    r = min_width / 2
    return _lasting_thin(_per_layer(_thin_chunk, layers, r, min_area), min_area, layer_height, persist)


def _lasting_thin(thin: list, min_area: float = 0.05, layer_height: float = 0.05, persist: float = 0.5) -> list[int]:
    """Layers whose thin part (from ``_thin_chunk``) stays thin through ``persist`` mm of height."""
    k = max(1, int(round(persist / layer_height / 2)))
    lasting = _window_intersection(thin, k)
    return [i for i, t in enumerate(lasting) if t.area > min_area]


# ----------------------------------------------------------------- prepare
def prepare(meshes: list[Mesh], cfg: Config, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] | None = None,
            support_points: list[np.ndarray | None] | None = None, direct: list[bool] | None = None,
            holes: list[np.ndarray | None] | None = None, cutters: list[Mesh] | None = None,
            materials: list[str] | None = None, filled: list[bool] | None = None,
            model_settings: list[dict | None] | None = None) -> SlaJob:
    """Slice resin models. ``support_points[i]`` holds manual contact points for ``meshes[i]``
    (same coordinates, before elevation), or None to place supports automatically."""
    def report(stage, f):
        if cancelled and cancelled():
            raise InterruptedError
        if progress:
            progress(stage, f)

    if not meshes:
        raise ValueError("Nothing to slice")
    warnings = [f"'{m.name}' is not watertight; check the layer preview." for m in meshes if not m.is_watertight()]
    # each model may carry its own settings (supports, hollowing / infill) over the plate's
    cfgs = [cfg.copy().update(model_settings[i]) if model_settings and i < len(model_settings) and model_settings[i]
            else cfg for i in range(len(meshes))]
    models = [_shrink_compensate(m, cfg) for m in meshes]
    process = cfg["sla_process"]
    jetting = process == "polyjet"
    lift = 0.0 if jetting else cfg["sla_elevation"] + cfg["sla_raft_thickness"]
    supports = None
    points = []
    if cfg["support_enable"] and not jetting:     # PolyJet support is gel filling, computed per layer below
        # models marked "direct" stay on the plate without supports (e.g. flat-based dental models)
        on_plate = [bool(direct and i < len(direct) and direct[i]) for i in range(len(models))]
        models = [m if on_plate[i] else m.translated([0, 0, lift]) for i, m in enumerate(models)]
        parts = []
        for i, m in enumerate(models):
            report("support", i / len(models))
            if on_plate[i]:
                continue
            manual = support_points[i] if support_points and i < len(support_points) else None
            if manual is not None:
                manual = np.asarray(manual, dtype=np.float64).reshape(-1, 3) + [0, 0, lift]
            s, p = build_supports(m, cfgs[i], manual, label=meshes[i].name if cfg["sla_raft_label"] else "")
            parts.append(s)
            points += list(p)
        supports = Mesh.merge(parts, "Supports")
    lh = cfg["sla_layer_height"]
    support_meshes = [supports] if supports is not None and len(supports.faces) else []
    top = max(float(m.bounds[1, 2]) for m in models + support_meshes)
    ranges = parse_z_ranges(cfg["sla_z_ranges"])
    if cfg["sla_adaptive"]:
        stack = adaptive_layers(models, top, cfg)
    elif any(r[2] for r in ranges):
        stack = ranged_layers(top, lh, ranges)
    else:
        n = max(1, int(np.ceil(top / lh - 1e-9)))
        stack = [(round((k + 1) * lh, 5), lh) for k in range(n)]
    n = len(stack)
    planes = [z - h / 2 for z, h in stack]
    split = 0.85 if support_meshes else 1.0
    # models with the same hollowing settings are sliced and hollowed together; a model with its own
    # hollowing / infill is done on its own and merged in
    groups: dict = {}
    for i in range(len(models)):
        groups.setdefault(tuple(cfgs[i][k] for k in HOLLOW_KEYS), []).append(i)
    layers = [EMPTY] * len(planes)
    infill: list = []
    saved = 0.0
    hollowing = False
    for gi, idx in enumerate(groups.values()):
        gcfg = cfgs[idx[0]]
        g_layers = slice_meshes([models[i] for i in idx], planes, cfg["resolution"],
                                progress=lambda f, gi=gi: report("slice", (gi + f) / len(groups) * split),
                                cancelled=cancelled)
        if cfg["xy_compensation"]:
            g_layers = [as_multipolygon(g.buffer(cfg["xy_compensation"], join_style="mitre", mitre_limit=3))
                        if not g.is_empty else g for g in g_layers]
        g_fill = [i for i in idx if filled and i < len(filled) and filled[i]]
        if g_fill:
            # "Fill cavity": the cavity of these models (an open-base or closed shell) is printed solid
            voids = covered_voids(slice_meshes([models[i] for i in g_fill], planes, cfg["resolution"]))
            g_layers = [as_multipolygon(g.union(v)) if not v.is_empty else g for g, v in zip(g_layers, voids)]
        # choosing an internal infill means "hollow it and fill the cavity": it turns hollowing on by itself
        if (gcfg["hollow_enable"] or gcfg["hollow_infill"] != "none") and not jetting:
            report("hollow", gi / len(groups))
            g_infill: list = []
            g_layers, g_saved, notes = hollow(g_layers, lh, gcfg, g_infill)
            saved += g_saved
            warnings += [n for n in notes if n not in warnings]
            if g_infill:
                infill = g_infill if not infill else [a.union(b) if not b.is_empty else a for a, b in zip(infill, g_infill)]
            hollowing = True
        layers = g_layers if len(groups) == 1 else _per_layer(_union_chunk, list(zip(layers, g_layers)))
    lifted = lift if cfg["support_enable"] and not jetting else 0.0
    hole_tubes = []
    for i, group in enumerate(holes or []):
        flat = jetting or (cfg["support_enable"] and direct and i < len(direct) and direct[i])
        hole_tubes += hole_meshes([group], cfg, 0.0 if flat else lifted)
    cutter_meshes = hole_tubes + [c.translated([0, 0, lifted]) for c in (cutters or [])]
    if cutter_meshes:
        cut_layers = slice_meshes(cutter_meshes, planes, cfg["resolution"])
        layers = [as_multipolygon(g.difference(c)) if not c.is_empty and not g.is_empty else g
                  for g, c in zip(layers, cut_layers)]
    # one pass on all cores: thicken thin parts (the model, never the honeycomb walls inside it), the shell
    # without that infill (its walls are thin on purpose), the shell's thin parts and its enclosed cells
    post = _post_process(layers, infill if hollowing else None, bool(cfg["sla_thicken_thin"]))
    layers = [x[0] for x in post]
    shell = [x[1] for x in post]
    thin = _lasting_thin([x[2] for x in post], layer_height=lh)
    shell_cells = [x[3] for x in post]
    if support_meshes:
        sup_layers = slice_meshes(support_meshes, planes, cfg["resolution"],
                                  progress=lambda f: report("slice", split + f * (1 - split)), cancelled=cancelled)
        layers = _per_layer(_union_chunk, list(zip(layers, sup_layers)))
    pillars = 0
    if cfg["sla_island_pillars"] and not jetting:
        layers, pillars = island_pillars(layers)
    foot = cfg["bottom_xy_compensation"]
    if foot > 0:
        for i in range(min(int(cfg["bottom_layers"]), len(layers))):
            if not layers[i].is_empty:
                layers[i] = as_multipolygon(layers[i].buffer(-foot, join_style="mitre", mitre_limit=3))
    job = SlaJob(layers, lh, models, supports, points, warnings=warnings)
    job.part_materials = list(materials or [])
    job.layer_z = [z for z, _ in stack]
    job.layer_thickness = [h for _, h in stack]
    # thicker layers need more light: scale exposure with thickness (linear approximation)
    varied = cfg["sla_adaptive"] or any(r[2] for r in ranges)
    job.exposures = []
    for i, (z, h) in enumerate(stack):
        fixed = next((r[3] for r in ranges if r[3] and r[0] - 1e-9 <= z - h < r[1] - 1e-9), None)
        job.exposures.append(fixed if fixed else layer_exposure(i, cfg) * (min(1.8, max(0.5, h / lh)) if varied else 1.0))
    if (varied or any(r[3] for r in ranges)) and cfg["sla_format"] in ("sl1", "png_zip"):
        job.warnings.append(UNIFORM_FORMAT_NOTE)
    job.thin_layers = thin
    job.island_pillars = pillars
    job.volume_ml = sum(g.area * h for g, h in zip(layers, job.layer_thickness)) / 1000.0
    job.saved_ml = saved
    job.weight_g = job.volume_ml * cfg["resin_density"]
    job.cost = job.volume_ml / 1000.0 * cfg["resin_cost"]
    if jetting:
        report("support", 0.0)
        job.support_layers = polyjet_support(layers, cfg) if cfg["support_enable"] or cfg["pj_finish"] == "matte" else []
        job.support_ml = sum(g.area * h for g, h in zip(job.support_layers, job.layer_thickness)) / 1000.0
        job.weight_g = job.volume_ml * cfg["resin_density"] + job.support_ml * 1.1
        job.cost = (job.volume_ml * cfg["resin_density"] * cfg["pj_model_cost"] + job.support_ml * 1.1 * cfg["pj_support_cost"]) / 1000.0
        job.time_s = top / max(cfg["pj_build_speed"], 0.1) * 3600.0
        return job        # no peel, so no suction cups or unsupported islands
    job.islands = find_islands(layers)
    # suction cups are cavities of the model itself: look at the shell (no supports, no honeycomb infill,
    # whose hundreds of cells per layer made this the slowest step)
    cup_layers = shell if len(shell) == len(layers) else layers
    sealed = _sealed_layers(cup_layers, shell_cells if len(shell_cells) == len(cup_layers) else None)
    job.cups = find_cups(cup_layers, sealed=sealed)
    if job.cups:
        job.cup_vents = cup_vents(cup_layers, job.layer_z, radius=max(0.5, cfg["drain_hole_diameter"] / 2), sealed=sealed)
    if process == "laser":
        from .vector_formats import scan_length
        job.time_s = sum(scan_length(g, cfg) / max(cfg["laser_scan_speed"], 1.0) + cfg["laser_recoat_time"]
                         + layer_motion_time(i, cfg) for i, g in enumerate(layers))
    else:
        job.time_s = print_time(n, cfg, job.exposures)
    return job


def polyjet_support(layers: list[MultiPolygon], cfg: Config) -> list[MultiPolygon]:
    """Gel support per layer: everything under the model's overhangs, down to the tray (it fills, it is
    not a structure). Matte finish also coats every surface with a thin support layer."""
    out = [EMPTY] * len(layers)
    above = EMPTY
    coat = cfg["pj_coat"] if cfg["pj_finish"] == "matte" else 0.0
    for i in range(len(layers) - 1, -1, -1):
        g = layers[i]
        sup = above.difference(g) if not above.is_empty else EMPTY
        if coat > 0 and not g.is_empty:
            sup = shapely.union_all([sup, g.buffer(coat, quad_segs=2).difference(g)])
        out[i] = as_multipolygon(sup)
        if not g.is_empty:
            above = shapely.union_all([above, g]).simplify(0.02)
    return out


def render_layer(geom: MultiPolygon, cfg: Config) -> Image.Image:
    """8-bit mask of one layer at the printer's native resolution (white = cure)."""
    rx, ry = int(cfg["resolution_x"]), int(cfg["resolution_y"])
    aa = int(cfg["antialiasing"])
    image = Image.new("L", (rx, ry), 0)
    polys = sorted(polygons_of(geom), key=lambda p: -p.area)
    if not polys:
        return image
    sx = rx / cfg["bed_x"]
    sy = ry / cfg["bed_y"]

    def to_px(coords):
        c = np.asarray(coords)[:, :2]
        px = c[:, 0] * sx
        py = (cfg["bed_y"] - c[:, 1]) * sy
        return px, py

    xs, ys = [], []
    for p in polys:
        px, py = to_px(p.exterior.coords)
        xs += [px.min(), px.max()]
        ys += [py.min(), py.max()]
    blur = int(cfg["image_blur"])
    pad = 2 + blur * 3
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(rx, int(max(xs)) + pad + 1), min(ry, int(max(ys)) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return image
    canvas = Image.new("L", ((x1 - x0) * aa, (y1 - y0) * aa), 0)
    draw = ImageDraw.Draw(canvas)

    def ring(coords):
        px, py = to_px(coords)
        return list(zip(((px - x0) * aa).tolist(), ((py - y0) * aa).tolist()))

    for p in polys:  # largest first, so islands inside holes are painted after the hole
        draw.polygon(ring(p.exterior.coords), fill=255)
        for hole in p.interiors:
            draw.polygon(ring(hole.coords), fill=0)
    if aa > 1:
        canvas = canvas.resize((x1 - x0, y1 - y0), Image.BOX)
    if blur > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    image.paste(canvas, (x0, y0))
    if cfg["mirror_x"]:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    if cfg["mirror_y"]:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    return image


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue()


def _rle_chunk(layers: list, values: dict, fmt: str) -> list[bytes]:
    """Run-length layer data in the printer's format, for a run of layers (process-pool entry point)."""
    if fmt == "goo":
        from .goo import encode_layer
    else:
        from .ctb import encode_layer
    cfg = Config(dict(values))
    return [encode_layer(np.asarray(render_layer(g, cfg))) for g in layers]


def _render_chunk(layers: list, values: dict) -> list[bytes]:
    """PNG data for a run of layers (process-pool entry point)."""
    cfg = Config(dict(values))
    return [_png_bytes(render_layer(g, cfg)) for g in layers]


def export(job: SlaJob, cfg: Config, path: str, job_name: str = "print",
           progress: Callable[[float], None] | None = None, cancelled: Callable[[], bool] | None = None) -> str:
    fmt = cfg["sla_format"]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_name) or "print"
    n = job.layer_count
    bottom = min(int(cfg["bottom_layers"]), n)
    from . import parallel

    if fmt in ("slc", "cli", "3mf"):
        return _export_vector(job, cfg, path, fmt, progress)
    ranges = parallel.chunk_ranges(n, 8, parallel.workers() * 4)
    args = [(job.layers[a:b], cfg.values) for a, b in ranges]
    if fmt in ("ctb", "goo", "prz"):
        from . import ctb, goo
        chunks = parallel.run_chunks(_rle_chunk, [a + ("ctb" if fmt == "ctb" else "goo",) for a in args],
                                     lambda f: progress(f * 0.9) if progress else None,
                                     cancelled, [b - a for a, b in ranges])
        rle = ((i, data) for i, data in enumerate(d for chunk in chunks for d in chunk))
        common = dict(layer_height=job.layer_height, exposures=job.exposures, layer_z=job.layer_z,
                      volume_ml=job.volume_ml, weight_g=job.weight_g, cost=job.cost, print_time_s=job.time_s,
                      progress=lambda f: progress(0.9 + 0.1 * f) if progress else None)
        if fmt == "ctb":
            ctb.write(path, rle, n, cfg, preview_layers=job.layers, preview_models=job.models,
                      preview_supports=job.supports, **common)
        else:
            goo.write(path, rle, n, cfg, preview=ctb.make_preview(job.layers, cfg, (290, 290), job.models, job.supports),
                      **common)
        return path
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        # layer images are drawn and PNG-encoded on all cores; the zip is written here in order
        def write_chunk(k, pngs):
            a = ranges[k][0]
            for j, data in enumerate(pngs):
                name = (f"{safe}{a + j:05d}.png" if fmt == "sl1" else f"{a + j + 1}.png" if fmt == "zip"
                        else f"layers/layer_{a + j:05d}.png")
                zf.writestr(name, data)

        chunks = parallel.run_chunks(_render_chunk, args, progress, cancelled, [b - a for a, b in ranges])
        for k, pngs in enumerate(chunks):
            write_chunk(k, pngs)
        if fmt == "sl1":
            zf.writestr("config.ini", _sl1_config(job, cfg, safe, bottom))
            zf.writestr("prusaslicer.ini", _sl1_printer_ini(job, cfg))
            from .ctb import make_preview
            for size in ((400, 400), (800, 480)):           # what the SL1 / SL1S screen and PrusaLink show
                buf = io.BytesIO()
                make_preview(job.layers, cfg, size, job.models, job.supports).save(buf, "PNG")
                zf.writestr(f"thumbnail/thumbnail{size[0]}x{size[1]}.png", buf.getvalue())
        elif fmt == "zip":
            from .ctb import make_preview
            zf.writestr("run.gcode", _chitu_zip_gcode(job, cfg, safe, bottom))
            for name, size in (("preview.png", (400, 300)), ("preview_cropping.png", (200, 125))):
                buf = io.BytesIO()
                make_preview(job.layers, cfg, size, job.models, job.supports).save(buf, "PNG")
                zf.writestr(name, buf.getvalue())
        else:
            keys = ("exposure_time", "bottom_exposure_time", "bottom_layers", "transition_layers", "light_pwm",
                    "lift_height", "lift_speed", "bottom_lift_height", "bottom_lift_speed", "retract_speed",
                    "bottom_retract_speed", "wait_mode", "rest_before_lift", "rest_after_lift", "rest_after_retract",
                    "bottom_rest_before_lift", "bottom_rest_after_lift", "bottom_rest_after_retract", "light_off_delay",
                    "bottom_light_off_delay", "light_pwm_bottom", "tsmc_enable",
                    "lift_height_2", "lift_speed_2", "retract_height_2", "retract_speed_2", "antialiasing",
                    "image_blur", "mirror_x", "mirror_y", "material_name")
            meta = {
                "generator": f"UniSlicer {__version__}", "job": job_name, "layer_count": n,
                "layer_height_mm": job.layer_height, "resolution": [cfg["resolution_x"], cfg["resolution_y"]],
                "display_mm": [cfg["bed_x"], cfg["bed_y"]], "volume_ml": round(job.volume_ml, 3),
                "estimated_time_s": round(job.time_s), "settings": {k: cfg[k] for k in keys},
                "layer_exposure_s": [round(layer_exposure(i, cfg), 3) for i in range(n)],
            }
            zf.writestr("metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return path


def _export_vector(job: SlaJob, cfg: Config, path: str, fmt: str, progress) -> str:
    from . import vector_formats as vf
    meshes = list(job.models) + ([job.supports] if job.supports is not None and len(job.supports.faces) else [])
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    extents = (tuple(lo), tuple(hi))
    if fmt == "slc":
        return vf.write_slc(path, job.layers, job.layer_z, job.layer_thickness, extents, progress=progress)
    if fmt == "cli":
        return vf.write_cli(path, job.layers, job.layer_z, cfg, extents, progress=progress)
    default = cfg["pj_material"] if cfg["sla_process"] == "polyjet" else cfg["material_name"]
    def mat_of(k):
        v = job.part_materials[k] if k < len(job.part_materials) else ""
        if isinstance(v, (list, tuple)):
            return [x or default for x in v] if len(v) == len(job.models[k].faces) else default
        return v or default
    parts = [(m.name or f"part {k + 1}", m, mat_of(k)) for k, m in enumerate(job.models)]
    if job.supports is not None and len(job.supports.faces):
        parts.append(("Supports", job.supports, default))
    from . import dental_shades
    names = {x for _, _, mat in parts for x in (mat if isinstance(mat, list) else [mat])}
    colors = {mat: dental_shades.hex_color(mat) for mat in names if dental_shades.find(mat)}
    meta = {"DentalIncisalTranslucency": cfg["pj_incisal"]} if cfg["sla_process"] == "polyjet" and colors else {}
    return vf.write_3mf(path, parts, colors, meta)


def _chitu_zip_gcode(job: SlaJob, cfg: Config, safe: str, bottom: int) -> str:
    """run.gcode of a CHITUBOX ZIP job (layer images 1.png, 2.png, ... next to it), as read by Phrozen
    Sonic XL 4K and other ChiTu-board printers: a settings header, then per layer show image, lift and
    expose."""
    from .sla_timing import layer_light_off
    n = job.layer_count
    head = {
        "fileName": safe, "machineType": "UniSlicer",
        "estimatedPrintTime": int(job.time_s), "volume": round(job.volume_ml, 3), "resin": cfg["material_name"],
        "weight": round(job.weight_g, 2), "price": round(job.cost, 2), "layerHeight": job.layer_height,
        "resolutionX": int(cfg["resolution_x"]), "resolutionY": int(cfg["resolution_y"]),
        "machineX": cfg["bed_x"], "machineY": cfg["bed_y"], "machineZ": cfg["max_z"], "projectType": "Normal",
        "normalExposureTime": cfg["exposure_time"], "bottomLayExposureTime": cfg["bottom_exposure_time"],
        "bottomLayerExposureTime": cfg["bottom_exposure_time"], "normalDropSpeed": cfg["retract_speed"],
        "normalLayerLiftHeight": cfg["lift_height"], "normalLayerLiftSpeed": cfg["lift_speed"],
        "bottomLayCount": bottom, "bottomLayerCount": bottom, "mirror": 1 if cfg["mirror_x"] else 0,
        "totalLayer": n, "bottomLayerLiftHeight": cfg["bottom_lift_height"],
        "bottomLayerLiftSpeed": cfg["bottom_lift_speed"], "bottomLightOffTime": cfg["bottom_light_off_delay"],
        "lightOffTime": cfg["light_off_delay"], "bottomPWMLight": int(cfg["light_pwm_bottom"]),
        "PWMLight": int(cfg["light_pwm"]), "antiAliasLevel": int(cfg["antialiasing"]),
    }
    out = [f";{k}:{v}" for k, v in head.items()]
    out += ["", "G21;", "G90;", "M106 S0;", "G28 Z0;", ""]
    exposures = job.exposures or [layer_exposure(i, cfg) for i in range(n)]
    for i in range(n):
        z = job.layer_z[i] if job.layer_z else (i + 1) * job.layer_height
        lift = cfg["bottom_lift_height"] if i < bottom else cfg["lift_height"]
        up = cfg["bottom_lift_speed"] if i < bottom else cfg["lift_speed"]
        down = cfg["bottom_retract_speed"] if i < bottom else cfg["retract_speed"]
        out += [f";LAYER_START:{i}", f";currPos:{z:.3f}", f'M6054 "{i + 1}.png";show Image',
                f"G0 Z{z + lift:.3f} F{up:g};", f"G0 Z{z:.3f} F{down:g};",
                f"G4 P{int(layer_light_off(i, cfg) * 1000)};", f";L:{i + 1};",
                f"M106 S{int(cfg['light_pwm_bottom'] if i < bottom else cfg['light_pwm'])};light on",
                f"G4 P{int(exposures[i] * 1000)};", "M106 S0;light off", ";LAYER_END", ""]
    out += ["M106 S0;", f"G1 Z{min(cfg['max_z'], (job.layer_z[-1] if job.layer_z else 0) + 40):.1f} F{cfg['lift_speed']:g};",
            "M18;", ""]
    return "\n".join(out)


def _sl1_config(job: SlaJob, cfg: Config, safe: str, bottom: int) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d at %H:%M:%S UTC")
    lines = {
        "action": "print",
        "expTime": f"{cfg['exposure_time']:g}",
        "expTimeFirst": f"{cfg['bottom_exposure_time']:g}",
        "fileCreationTimestamp": stamp,
        "hollow": "0",
        "jobDir": safe,
        "layerHeight": f"{job.layer_height:g}",
        "materialName": cfg["material_name"],
        "numFade": str(bottom),   # UVtools reads this as the bottom layer count
        "numFast": str(job.layer_count),
        "numSlow": "0",
        "printProfile": f"{job.layer_height:g}mm",
        "printTime": str(int(job.time_s)),
        "printerModel": "SL1",
        "printerProfile": "UniSlicer",
        "printerVariant": "default",
        "prusaSlicerVersion": f"UniSlicer {__version__}",
        "usedMaterial": f"{job.volume_ml:.3f}",
    }
    return "".join(f"{k} = {v}\n" for k, v in lines.items())


def _sl1_printer_ini(job: SlaJob, cfg: Config) -> str:
    orientation = "landscape" if cfg["resolution_x"] >= cfg["resolution_y"] else "portrait"
    lines = {
        "display_width": f"{cfg['bed_x']:g}",
        "display_height": f"{cfg['bed_y']:g}",
        "display_pixels_x": str(cfg["resolution_x"]),
        "display_pixels_y": str(cfg["resolution_y"]),
        "display_mirror_x": "1" if cfg["mirror_x"] else "0",
        "display_mirror_y": "1" if cfg["mirror_y"] else "0",
        "display_orientation": orientation,
        "exposure_time": f"{cfg['exposure_time']:g}",
        "initial_exposure_time": f"{cfg['bottom_exposure_time']:g}",
        "faded_layers": str(int(cfg["bottom_layers"])),
        "layer_height": f"{job.layer_height:g}",
        "max_print_height": f"{cfg['max_z']:g}",
        "material_name": cfg["material_name"],
        "printer_technology": "SLA",
    }
    return "".join(f"{k} = {v}\n" for k, v in lines.items())
