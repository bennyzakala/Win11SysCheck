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
    pillars: Mesh | None = None         # the cavity pillars as a mesh, for the 3D preview
    cups_checked: bool = False          # the suction-cup check ran (only with "Suggest drain / vent holes")
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
    model_ml: float = 0.0               # resin of the models alone (the rest of volume_ml is supports and raft)
    raft_ml: float = 0.0                # resin of the raft (part of the supports)
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
        try:
            thin = as_multipolygon(g.difference(opened.buffer(0.01, quad_segs=4)))
            out.append(_without_groove_walls(thin, g, r))
        except shapely.errors.GEOSException:
            out.append(EMPTY)                # a check only: a layer the geometry library cannot read is not
    return out                               # reported - it never stops the slice


def _without_groove_walls(thin, g, r: float):
    """The thin parts that are features of their own: not the strips of material between the narrow grooves
    of the model itself - the letters of a label engraved in its side are 0.3-0.5 mm apart, so every layer
    through the label read as "thinner than 0.3 mm" and the print check offered to thicken them (which
    filled the label). A strip touching such a groove (a gap a little wider than the growth would fill) is
    left out; a thin wall or margin standing out is still reported."""
    if thin.is_empty:
        return thin
    grooves = _own_grooves(g, r * 2.5)
    if grooves is None or grooves.is_empty or grooves.area < 1e-4:
        return thin
    try:
        near = grooves.buffer(0.02, quad_segs=2)
        kept = [p for p in polygons_of(thin) if not p.intersects(near)]
    except shapely.errors.GEOSException:
        return thin                       # a check only: never let it stop the slice
    return as_multipolygon(shapely.union_all(kept)) if kept else EMPTY


def _own_grooves(g, c: float):
    """The model's own narrow grooves and gaps in a layer (what a closing by ``c`` fills), or None when the
    geometry library cannot work it out. A scan's outline with an edge doubled onto itself made GEOS fail
    ("non-noded intersection") and the whole slice stopped (reported from a lab: "Slicing failed"); the
    layer is made valid and tried again, and if that fails too the groove rule is simply not applied."""
    for shape in (g, None):
        try:
            if shape is None:
                shape = shapely.make_valid(g).buffer(0)
            closed = shape.buffer(c, join_style="mitre").buffer(-c, join_style="mitre")
            return closed.difference(shape)
        except shapely.errors.GEOSException:
            continue
    return None


def _thicken_chunk(layers: list, r: float, min_area: float = 0.05) -> list:
    """Grow only the parts narrower than 2r (they would not form) by r/2 on each side; the rest is untouched."""
    out = []
    for g in layers:
        if g.is_empty:
            out.append(g)
            continue
        opened = g.buffer(-r, quad_segs=4).buffer(r, quad_segs=4)
        # less than the thin check counts (same gate as _thin_chunk): nothing worth growing, and a layer the
        # thin check found clean is never changed here (the fix can then redo only the thin layers)
        if g.area - opened.area < min_area:
            out.append(g)
            continue
        thin = g.difference(opened.buffer(0.01, quad_segs=4))
        if thin.is_empty or thin.area < 1e-4:
            out.append(g)
            continue
        # round growth a bit past r: a thin part becomes at least 2·1.25·r wide everywhere, so a disc of
        # radius r fits in every part of it and the thin check (an opening with r) keeps all of it
        # (square corners and a growth of exactly r left spikes and slivers that still read as thin)
        grown = thin.buffer(r * 1.25, quad_segs=8)
        # never into the model's own narrow grooves: the strips between the letters of an engraved label are
        # "thin" too, and grown they filled the letters (the label printed blurred); a groove or a gap a
        # little wider than the growth is kept open, the part grows outwards only
        grooves = _own_grooves(g, r * 2.5)
        if grooves is not None and not grooves.is_empty:
            try:
                grown = grown.difference(grooves)
            except shapely.errors.GEOSException:
                pass                      # grown as before rather than no slice at all
        out.append(as_multipolygon(g.union(grown)))
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
    # the model's sections and the supports' meet only at the tips: only the ones that overlap are merged
    from .slicing import union_many
    return [as_multipolygon(union_many(polygons_of(a) + polygons_of(b))) if not b.is_empty else a for a, b in pairs]


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


HOLLOW_KEYS = ("sla_cavity_pillars", "sla_cavity_outside", "sla_cavity_spacing", "sla_cavity_diameter", "hollow_enable", "hollow_wall", "hollow_base_height", "hollow_mode", "hollow_infill",
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


def _pillar_mesh(segments: list, layer_z: list, layer_h: list) -> Mesh:
    """Cylinders for the cavity pillars (x, y, radius, first layer, last layer), to show them in 3D."""
    from .primitives import tube
    parts = []
    for x, y, r, a, b in segments:
        if b < a or a >= len(layer_z):
            continue
        b = min(b, len(layer_z) - 1)
        z0, z1 = layer_z[a] - layer_h[a], layer_z[b]
        parts.append(tube([x, y, z0], [x, y, z1], r, r, 10))
    return Mesh.merge(parts, "Cavity pillars") if parts else None


def _overhang_points(under: list, cavity: list, r: float, spacing: float) -> list:
    """Pillar spots in the parts that hang in the air outside the cavity (often narrow strips the even grid
    misses): at least one in each, more in larger ones."""
    parts = [u for u in under[::2] if not u.is_empty]
    if not parts:
        return []
    cav = [c for c in cavity[::2] if not c.is_empty]
    # outside = under the model but nowhere in the cavity (the cavity's open bottom layers count as cavity)
    outside = shapely.union_all(parts).simplify(0.05)
    if cav:
        outside = outside.difference(shapely.union_all(cav).buffer(spacing * 0.3))
    pts = []
    for comp in polygons_of(outside):
        inner = comp.buffer(-r * 0.6)
        if inner.is_empty or comp.area < 0.5:
            continue
        n = max(1, int(round(comp.area / (spacing * spacing))))
        pts += _spread_points(as_multipolygon(inner), n)
    return pts


def under_model(layers: list[MultiPolygon], min_area: float = 0.5) -> list[MultiPolygon]:
    """Per layer, the empty space that has model material somewhere above it: the cavity and every part of
    the model that hangs in the air (what a model standing on the plate needs held up)."""
    n = len(layers)
    out = [EMPTY] * n
    above = EMPTY
    for i in range(n - 1, -1, -1):
        g = layers[i]
        if not above.is_empty:
            v = above.difference(g) if not g.is_empty else above
            if not v.is_empty and v.area > min_area:
                v = v.buffer(-0.05).buffer(0.05)
                out[i] = as_multipolygon(v) if not v.is_empty else EMPTY
        if not g.is_empty:
            above = above.union(g) if not above.is_empty else g
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
    elif saved > 0 and cfg["sla_hole_advice"]:
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


def _internal_pillars(layers: list, cavity: list, cfg: Config, from_plate: bool = False,
                      radius: float | None = None, spacing: float | None = None, extra_points=None,
                      segments: list | None = None) -> list:
    """Pillars inside cavities; a pillar only starts on solid material or on its own pillar below. With
    ``from_plate`` (a model printed straight on the plate) a pillar may also stand on the build plate: where
    nothing of the model lies under that spot, it goes straight down to the plate."""
    cav = [c for c in cavity if not c.is_empty]
    if not cav:
        return layers
    r = radius if radius is not None else cfg["sla_pillar_diameter"] / 2
    sp = spacing if spacing is not None else cfg["sla_internal_spacing"]
    minx = min(c.bounds[0] for c in cav)
    miny = min(c.bounds[1] for c in cav)
    maxx = max(c.bounds[2] for c in cav)
    maxy = max(c.bounds[3] for c in cav)
    xs, ys = np.meshgrid(np.arange(minx + sp / 2, maxx, sp), np.arange(miny + sp / 2, maxy, sp))
    px, py = xs.ravel(), ys.ravel()
    if extra_points:
        ex = np.asarray(extra_points, dtype=float).reshape(-1, 2)
        px, py = np.concatenate([px, ex[:, 0]]), np.concatenate([py, ex[:, 1]])
    if px.size == 0:
        return layers
    circles = [Point(x, y).buffer(r, quad_segs=6) for x, y in zip(px, py)]
    first_layer = next((i for i, g in enumerate(layers) if not g.is_empty), 0)
    active = np.zeros(px.size, dtype=bool)
    open_below = np.ones(px.size, dtype=bool)        # nothing of the model under this spot so far
    started = np.full(px.size, -1)                   # layer where a pillar standing on the plate begins
    seg_start = np.full(px.size, -1)                 # for ``segments``: first layer of the running pillar
    out = list(layers)
    for i, c in enumerate(cavity):
        if not c.is_empty:
            room = c.buffer(-r * 0.5)
        else:
            room = EMPTY
        if room.is_empty:
            active[:] = False
        else:
            shapely.prepare(room)
            inside = shapely.contains_xy(room, px, py)
            below = out[i - 1] if i else EMPTY
            if not below.is_empty:
                shapely.prepare(below)
                on_solid = shapely.contains_xy(below, px, py)
            else:
                on_solid = np.zeros(px.size, dtype=bool)
            if from_plate:
                if i <= first_layer:
                    on_solid = np.ones(px.size, dtype=bool)     # standing on the build plate
                plate = inside & ~active & open_below & ~on_solid
                started[plate & (started < 0)] = i
                on_solid = on_solid | plate
            active = inside & (active | on_solid)
            if active.any():
                out[i] = as_multipolygon(out[i].union(shapely.union_all([circles[k] for k in np.flatnonzero(active)])))
        if segments is not None:                     # where each pillar runs (for the 3D preview)
            new = active & (seg_start < 0)
            seg_start[new] = np.where(started[new] == i, first_layer, i)
            done = (seg_start >= 0) & ~active
            for k in np.flatnonzero(done):
                segments.append((float(px[k]), float(py[k]), r, int(seg_start[k]), i - 1))
            seg_start[done] = -1
        g = layers[i]
        if from_plate and not g.is_empty:
            shapely.prepare(g)
            open_below &= ~shapely.contains_xy(g, px, py)
    if segments is not None:
        for k in np.flatnonzero(seg_start >= 0):
            segments.append((float(px[k]), float(py[k]), r, int(seg_start[k]), len(cavity) - 1))
    if from_plate:
        # the part of each plate-standing pillar under the cavity: from the plate up to where it started
        for i in range(first_layer, len(out)):
            k = np.flatnonzero(started > i)
            if len(k):
                out[i] = as_multipolygon(out[i].union(shapely.union_all([circles[j] for j in k])))
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


def island_pillars(layers: list, radius: float = PILLAR_RADIUS, rounds: int = 3) -> tuple[list, int, list]:
    """A thin round pillar under every island, from the island down to the first layer that is solid at that
    spot (or the plate). Returns the new layers, how many pillars were added and any island still left."""
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
    else:
        islands = find_islands(layers, only=check)
    return layers, added, islands


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
def _same(a, b) -> bool:
    """Cheap equality for two results of the same computation (area and bounds)."""
    if a.is_empty or b.is_empty:
        return a.is_empty and b.is_empty
    return abs(a.area - b.area) < 1e-9 and np.allclose(a.bounds, b.bounds, atol=1e-9)


def layer_triangles(layer, z: float) -> np.ndarray:
    """A sliced layer as flat triangles (n, 3, 3) at height ``z``, holes and honeycomb kept (for the 3D
    preview). Empty when the layer is empty or shapely is too old to triangulate polygons with holes."""
    if layer is None or layer.is_empty or not hasattr(shapely, "constrained_delaunay_triangles"):
        return np.zeros((0, 3, 3), dtype=np.float32)
    try:
        tri = shapely.constrained_delaunay_triangles(layer)
    except shapely.errors.GEOSException:
        return np.zeros((0, 3, 3), dtype=np.float32)
    parts = shapely.get_parts(tri)
    if len(parts) == 0:
        return np.zeros((0, 3, 3), dtype=np.float32)
    xy = shapely.get_coordinates(shapely.get_exterior_ring(parts)).reshape(len(parts), 4, 2)[:, :3]
    a, b, c = xy[:, 0], xy[:, 1], xy[:, 2]
    cw = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]) < 0
    xy[cw] = xy[cw][:, ::-1]                       # every triangle faces up (lit from above, not "inside")
    out = np.empty((len(parts), 3, 3), dtype=np.float32)
    out[:, :, :2] = xy
    out[:, :, 2] = z
    return out


def _clip(g, x0: float, y0: float, x1: float, y1: float):
    """``g`` cut to a rectangle, only to make the next step cheaper. GEOS's rectangle clip can trip over some
    contours ("Invalid number of points in LinearRing found 3"); then the whole of ``g`` gives the same
    result, a little slower, instead of failing the slice."""
    try:
        return shapely.clip_by_rect(g, x0, y0, x1, y1)
    except shapely.errors.GEOSException:
        return g


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
            near = np.array([_clip(below, b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1) for b in bx],
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
    for k, item in enumerate(items):
        g, fill = item[0], item[1]
        grow = thicken if len(item) < 3 else (thicken and item[2])
        has_fill = fill is not None and not fill.is_empty and not g.is_empty
        shell = as_multipolygon(g.difference(fill)) if has_fill else g
        if grow:
            shell = _thicken_chunk([shell], r_thin, min_area)[0]
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


def _post_thicken_incremental(base: list, layers: list, infill: list | None, changed: list, min_width: float = 0.3,
                              min_area: float = 0.05) -> tuple[list, list]:
    """The thickened ``_post_process`` result from the plain one, thickening only the ``changed`` layers (those
    the thin check flags, with their neighbours) and redoing the cells right above them. Returns the result
    and the recomputed layer indices."""
    from . import parallel
    n = len(base)
    redo = sorted(set(changed) | {i + 1 for i in changed if i + 1 < n})
    out = list(base)
    fills = infill if infill and len(infill) == n else [None] * n
    runs, start = [], None
    for k, i in enumerate(redo):
        if start is None:
            start = i
        if k == len(redo) - 1 or redo[k + 1] != i + 1:
            runs.append((start, i))
            start = None
    thick = set(changed)
    args = []
    for a, b in runs:
        items = [(layers[k], fills[k], k in thick) for k in range(max(0, a - 1), b + 1)]
        args.append((items, a == 0, True, min_width / 2, min_area))
    parts = parallel.run_chunks(_post_chunk, args, weights=[b - a + 1 for a, b in runs]) if args else []
    for (a, b), part in zip(runs, parts):
        out[a:b + 1] = part
    return out, redo


def _sealed_layers(layers: list[MultiPolygon], per: list | None = None, previous: list | None = None,
                   changed: list | None = None) -> list[MultiPolygon]:
    """Per layer, the empty space that is enclosed by walls and sealed on the build-plate side.

    A cavity is sealed only if all of it sits on solid or on a cavity that is itself sealed; any opening
    (e.g. a drain or vent hole) vents the whole cavity. The allowance only absorbs numeric noise, so even
    a 1 mm vent in a large cavity counts as an opening."""
    if per is None:
        per = _per_layer(_cavity_cells_chunk, [(g, layers[i - 1] if i else EMPTY) for i, g in enumerate(layers)])
    out = []
    sealed = EMPTY
    incremental = previous is not None and changed is not None and len(previous) == len(per)
    marks = sorted(set(changed)) if incremental else []
    if incremental:
        # only some layers changed (a fix): a layer's sealed space depends only on its own cells and the
        # layer below, so recompute from each changed layer up until the result matches the old one again
        if not marks:
            return list(previous)
        out = list(previous[:marks[0]])
        sealed = previous[marks[0] - 1] if marks[0] else EMPTY
    i = marks[0] if incremental else 0
    while i < len(per):
        if incremental:
            pending = [c for c in marks if c >= i]
            if (not pending or pending[0] > i) and i and not any(c == i - 1 for c in marks) \
                    and _same(sealed, previous[i - 1]):
                nxt = pending[0] if pending else len(per)          # in step again: copy up to the next change
                out += previous[i:nxt]
                if nxt >= len(per):
                    break
                sealed = previous[nxt - 1]
                i = nxt
        item = per[i]
        if item is None:
            sealed = EMPTY
            out.append(EMPTY)
            i += 1
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
                    near = np.array([_clip(sealed, *(bx[k, :2] - 0.1), *(bx[k, 2:] + 0.1)) for k in todo],
                                    dtype=object)
                    rest[todo] = shapely.difference(per[i][1][todo], near)
            limit = np.minimum(0.01 * shapely.area(cells), 0.3) + 1e-6
            keep = list(cells[shapely.area(rest) < limit])
        sealed = as_multipolygon(shapely.union_all(keep)) if keep else EMPTY
        out.append(sealed)
        i += 1
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


# ----------------------------------------------------------------- result cache
# Slicing again after a fix or a setting change usually leaves the models alone: their contours, the hollowed
# layers and the per-layer checks are then taken from here instead of being computed again. Keys are content
# hashes of the meshes plus every setting a step depends on, so a changed model or setting never hits.
_CACHE_SIZE = 12
_cache: "dict" = {}


def _cache_get(key):
    hit = _cache.pop(key, None)
    if hit is not None:
        _cache[key] = hit                    # most recently used last
    return hit


def _cache_put(key, value):
    _cache[key] = value
    while len(_cache) > _CACHE_SIZE:
        _cache.pop(next(iter(_cache)))


def clear_cache():
    _cache.clear()


def mesh_key(mesh: Mesh) -> str:
    cached = getattr(mesh, "_content_key", None)
    if cached is not None and cached[0] == (id(mesh.vertices), id(mesh.faces)):
        return cached[1]
    import hashlib
    h = hashlib.blake2b(digest_size=16)
    h.update(np.ascontiguousarray(mesh.vertices).tobytes())
    h.update(np.ascontiguousarray(mesh.faces).tobytes())
    key = h.hexdigest()
    mesh._content_key = ((id(mesh.vertices), id(mesh.faces)), key)
    return key


def _cached_slice(meshes: list, planes: list, resolution: float, **kw) -> list:
    key = ("slice", tuple(mesh_key(m) for m in meshes), tuple(round(p, 6) for p in planes), resolution)
    hit = _cache_get(key)
    if hit is None:
        hit = slice_meshes(meshes, planes, resolution, **kw)
        _cache_put(key, hit)
    return list(hit)


class _Done:
    """A finished result with the ``.result()`` of a future."""

    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _closed_copy(mesh: Mesh) -> Mesh | None:
    """The mesh with its holes filled (cached), or None when the repair cannot make it watertight."""
    key = ("closed", mesh_key(mesh))
    hit = _cache_get(key)
    if hit is None:
        from .mesh_tools import make_watertight
        try:
            fixed, _rep, ok = make_watertight(mesh)
            fixed.name = mesh.name
            hit = (fixed if ok else False,)
        except Exception:
            hit = (False,)
        _cache_put(key, hit)
    return hit[0] or None


def _mesh_components(mesh: Mesh) -> list[tuple[str, np.ndarray, float, float]]:
    """(content key, triangles, zmin, zmax) of every separate piece of a mesh (supports are many pieces)."""
    import hashlib
    from .mesh_tools import _vertex_components
    f = mesh.faces
    if len(f) == 0:
        return []
    lab = _vertex_components(len(mesh.vertices), f)[f[:, 0]]
    order = np.argsort(lab, kind="stable")
    cuts = np.flatnonzero(np.diff(lab[order])) + 1
    tris = mesh.vertices[f]
    out = []
    for idx in np.split(order, cuts):
        t = np.ascontiguousarray(tris[idx])
        key = hashlib.blake2b(t.tobytes(), digest_size=16).hexdigest()
        out.append((key, t, float(t[:, :, 2].min()), float(t[:, :, 2].max())))
    return out


def _slice_pieces(mesh: Mesh, planes: list, resolution: float, **kw) -> list:
    """Slice a mesh made of many closed pieces (supports): each piece's contours are linked on their own, then
    merged, which avoids the slow fallback for touching pieces."""
    from .mesh_tools import _vertex_components
    from .slicing import slice_mesh
    if len(mesh.faces) == 0:
        return [EMPTY] * len(planes)
    groups = _vertex_components(len(mesh.vertices), mesh.faces)[mesh.faces[:, 0]]
    return slice_mesh(mesh, planes, resolution, groups=groups, **kw)


def _slice_supports(mesh: Mesh, planes: list, resolution: float, **kw) -> list:
    """Support layers, incrementally: after a fix adds a few supports, only the new pieces are sliced and
    merged into the cached layers; layers at the height of a piece that went away (e.g. a raft that grew)
    are sliced again in full. Falls back to a full slice when most of the supports changed."""
    pk = ("supports", tuple(round(p, 6) for p in planes), resolution)
    comps = _mesh_components(mesh)
    keys = {c[0]: c for c in comps}
    prev = _cache_get(pk)
    if prev is not None and set(prev["keys"]) == set(keys):
        return list(prev["layers"])
    if prev is None or len(set(keys) - set(prev["keys"])) > 0.5 * max(1, len(keys)):
        layers = _slice_pieces(mesh, planes, resolution, **kw)
    else:
        added = [keys[k] for k in keys if k not in prev["keys"]]
        removed = [prev["keys"][k] for k in prev["keys"] if k not in keys]
        layers = list(prev["layers"])
        z = np.asarray(planes)
        full = np.zeros(len(planes), dtype=bool)
        for _, z0, z1 in removed:
            full |= (z >= z0 - 1e-6) & (z <= z1 + 1e-6)
        idx = np.flatnonzero(full)
        if len(idx):                          # where something went away, slice everything there again
            redo = _slice_pieces(mesh, [planes[i] for i in idx], resolution)
            for i, g in zip(idx, redo):
                layers[i] = g
        if added:
            tris = np.concatenate([c[1] for c in added])
            z0, z1 = min(c[2] for c in added), max(c[3] for c in added)
            sel = [i for i in range(len(planes)) if not full[i] and z0 - 1e-6 <= planes[i] <= z1 + 1e-6]
            if sel:
                add_mesh = Mesh.from_triangles(tris, "added supports")
                add = slice_meshes([add_mesh], [planes[i] for i in sel], resolution)
                for i, g in zip(sel, add):
                    if not g.is_empty:
                        layers[i] = as_multipolygon(layers[i].union(g)) if not layers[i].is_empty else g
    _cache_put(pk, {"keys": {k: (k, c[2], c[3]) for k, c in keys.items()}, "layers": list(layers)})
    return layers


AUTO_FOOT = 0.2      # mm: how much the bottom layers of a part printed straight on the plate are pulled in


def _auto_bottom_compensation(layers: list, models: list, planes: list, cfg: Config, on_plate: list) -> list:
    """The long-exposed bottom layers of the parts printed straight on the plate pulled in by ``AUTO_FOOT``
    (the transition layers by less and less): the strong light spreads there, so a label engraved in the base
    filled up and printed blurred, and the base came out wider. Only those parts' own area is touched - the
    rafts of supported parts on the same plate stay as they are. Off when a compensation is set by hand."""
    nb, nt = int(cfg["bottom_layers"]), int(cfg["transition_layers"])
    n = min(nb + nt, len(layers), len(planes))
    if not on_plate or n <= 0:
        return layers
    from .slicing import slice_mesh
    plate_parts = Mesh.merge([models[i] for i in on_plate])
    sections = slice_mesh(plate_parts, planes[:n], cfg["resolution"])
    xy = float(cfg["xy_compensation"])
    if xy > 0:
        # the layers were grown by the XY compensation already: grown the same, the parts' sections hold that
        # rim too (it fell outside them, stayed as it was and stood apart as a separate ring)
        sections = [sec.buffer(xy, join_style="mitre", mitre_limit=3).buffer(1e-3) if not sec.is_empty else sec
                    for sec in sections]
    out = list(layers)
    for i in range(n):
        f = AUTO_FOOT if i < nb else AUTO_FOOT * (1.0 - (i - nb + 1) / (nt + 1))
        sec = sections[i]
        if f <= 1e-4 or sec.is_empty or out[i].is_empty:
            continue
        own = out[i].intersection(sec)                  # the part's layer as it is (holes, engraving, cavity)
        rest = out[i].difference(sec)
        out[i] = as_multipolygon(rest.union(own.buffer(-f, join_style="mitre", mitre_limit=3)))
    return out


# ----------------------------------------------------------------- prepare
def raft_joints_for(models, on_plate, own, cfgs, cfg):
    """The joints of "join all rafts" for these parts: ``own`` = [(index, its support structure)]. Rafts are
    joined; parts on the plate and the feet of parts with no raft are kept clear of."""
    from shapely.ops import unary_union
    from .sla_supports import raft_joints, raft_outline
    outlines, thick, block = [], [], []
    for i, sm in own:
        t = cfgs[i]["sla_raft_thickness"]
        if cfgs[i]["sla_raft_type"] != "none" and t > 0:
            outlines.append(raft_outline(sm, t))
            thick.append(t)
        else:
            block.append(raft_outline(sm, max(cfg["sla_raft_thickness"], 0.5)))
    for i, m in enumerate(models):
        if on_plate[i]:
            block.append(raft_outline(m, max(cfg["sla_raft_thickness"], 0.5)))
    blocked = unary_union([b for b in block if not b.is_empty]) if block else None
    joints, _pieces = raft_joints(outlines, thick, cfg["sla_raft_join_width"], cfg["sla_raft_join_reach"],
                                  obstacles=blocked)
    return joints


def prepare(meshes: list[Mesh], cfg: Config, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] | None = None,
            support_points: list[np.ndarray | None] | None = None, direct: list[bool] | None = None,
            keepout: list[np.ndarray] | None = None, labels: list | None = None, raft_labels: list | None = None,
            holes: list[np.ndarray | None] | None = None, cutters: list[Mesh] | None = None,
            materials: list[str] | None = None, filled: list[bool] | None = None,
            model_settings: list[dict | None] | None = None,
            prebuilt_supports: list[Mesh | None] | None = None, raft_label_frames: list | None = None) -> SlaJob:
    """Slice resin models. ``support_points[i]`` holds manual contact points for ``meshes[i]``
    (same coordinates, before elevation), or None to place supports automatically.
    ``prebuilt_supports[i]``: the support structure the window already built for ``meshes[i]`` (bed
    coordinates, raised), used as it is instead of building it again (None = build it here).
    ``raft_label_frames[i]``: the part's own directions on the plate, where a raft & label tab goes."""
    def report(stage, f):
        if cancelled and cancelled():
            raise InterruptedError
        if progress:
            progress(stage, f)

    if not meshes:
        raise ValueError("Nothing to slice")
    # a model with holes (open mesh) is closed on a copy for slicing: the layers come out right and slicing
    # stays on the fast path; only a model that cannot be closed this way is reported
    warnings = []
    pitch_x = cfg["bed_x"] / max(1, int(cfg["resolution_x"]))
    pitch_y = cfg["bed_y"] / max(1, int(cfg["resolution_y"]))
    if cfg["sla_process"] in ("lcd", "dlp") and max(pitch_x, pitch_y) > 1.5 * min(pitch_x, pitch_y):
        # (12K screens have pixels up to ~1.3x as tall as wide; much more is a profile error, e.g. a portrait
        # resolution on a landscape plate - the print would come out stretched)
        warnings.append(f"The printer's screen resolution ({int(cfg['resolution_x'])} x {int(cfg['resolution_y'])} px) "
                        f"does not fit its build area ({cfg['bed_x']:g} x {cfg['bed_y']:g} mm): pixels of "
                        f"{pitch_x * 1000:.0f} x {pitch_y * 1000:.0f} um. Check the printer profile before printing.")
    closed = []
    for m in meshes:
        if m.is_watertight():
            closed.append(m)
            continue
        fixed = _closed_copy(m)
        if fixed is not None:
            closed.append(fixed)
        else:
            closed.append(m)
            warnings.append(f"'{m.name}' is not watertight; check the layer preview.")
    meshes = closed
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

    def build_all_supports():
        """Supports for every model (cached)."""
        parts, pts, own = [], [], []
        for i, m in enumerate(models):
            if cancelled and cancelled():
                raise InterruptedError
            if on_plate[i]:
                continue
            manual = support_points[i] if support_points and i < len(support_points) else None
            if manual is not None:
                manual = np.asarray(manual, dtype=np.float64).reshape(-1, 3) + [0, 0, lift]
            ready = prebuilt_supports[i] if prebuilt_supports and i < len(prebuilt_supports) else None
            if ready is not None and manual is not None:   # what the window shows: no second build
                parts.append(ready)
                own.append((i, ready))
                pts += list(manual)
                continue
            zones = keepout[i] if keepout and i < len(keepout) else None
            if zones is not None and len(zones):
                from .sla_supports import auto_support_points, drop_keepout
                zones = np.asarray(zones, dtype=np.float64).reshape(-1, 4) + [0, 0, lift, 0]
                # automatic or edited points alike: none inside a support-free zone or under a label
                manual = drop_keepout(auto_support_points(m, cfgs[i]) if manual is None else manual, zones)
            if raft_labels is not None:               # chosen per model by the caller (e.g. SOD code on teeth)
                label = raft_labels[i] if i < len(raft_labels) else ""
            else:
                label = meshes[i].name if cfg["sla_raft_label"] else ""
            frame = raft_label_frames[i] if raft_label_frames and i < len(raft_label_frames) else None
            skey = ("supports-build", mesh_key(m), None if manual is None else np.ascontiguousarray(manual).tobytes(),
                    label, None if frame is None else np.round(np.asarray(frame, float), 6).tobytes(),
                    tuple(sorted((k, v) for k, v in cfgs[i].values.items() if k.startswith(("sla_", "support"))
                                 and not k.startswith("sla_raft_join"))))
            hit = _cache_get(skey)
            if hit is None:
                hit = build_supports(m, cfgs[i], manual, label=label, label_frame=frame)
                _cache_put(skey, hit)
            sm, sp = hit
            parts.append(sm)
            own.append((i, sm))
            pts += list(sp)
        if cfg["sla_raft_join"]:
            # "join all rafts into one piece": solid joints between neighbouring rafts, printed with the supports
            joints = raft_joints_for(models, on_plate, own, cfgs, cfg)
            if joints is not None and len(joints.faces):
                parts.append(joints)
        return Mesh.merge(parts, "Supports"), pts

    support_job = None
    if cfg["support_enable"] and not jetting:
        report("support", 0.0)
        # built before slicing: in a thread next to the slicing it was no faster (both need the same CPU)
        support_job = _Done(build_all_supports())
    lh = cfg["sla_layer_height"]
    support_meshes = []
    top = max(float(m.bounds[1, 2]) for m in models)
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
    split = 0.85 if support_job is not None else 1.0
    # models with the same hollowing settings are sliced and hollowed together; a model with its own
    # hollowing / infill is done on its own and merged in
    pillar_segments: list = []          # (x, y, radius, first layer, last layer) of the cavity pillars
    # models that stand on the plate (not raised onto supports): all of them without supports, else the
    # ones marked "directly on the plate"
    lifted_all = bool(cfg["support_enable"]) and not jetting
    on_plate_all = [not lifted_all or bool(direct and i < len(direct) and direct[i]) for i in range(len(models))]
    groups: dict = {}
    for i in range(len(models)):
        groups.setdefault(tuple(cfgs[i][k] for k in HOLLOW_KEYS) + (on_plate_all[i],), []).append(i)
    layers = [EMPTY] * len(planes)
    infill: list = []
    saved = 0.0
    hollowing = False
    for gi, idx in enumerate(groups.values()):
        gcfg = cfgs[idx[0]]
        g_keys = tuple(mesh_key(models[i]) for i in idx)
        g_layers = _cached_slice([models[i] for i in idx], planes, cfg["resolution"],
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
            hkey = ("hollow", g_keys, tuple(round(p, 6) for p in planes), cfg["resolution"], cfg["xy_compensation"],
                    tuple(bool(filled and i < len(filled) and filled[i]) for i in idx), lh,
                    tuple(gcfg[k] for k in HOLLOW_KEYS), gcfg["support_enable"], gcfg["sla_pillar_diameter"])
            hit = _cache_get(hkey)
            if hit is None:
                g_infill: list = []
                out_layers, g_saved, notes = hollow(g_layers, lh, gcfg, g_infill)
                hit = (out_layers, g_saved, notes, g_infill)
                _cache_put(hkey, hit)
            g_layers, g_saved, notes, g_infill = list(hit[0]), hit[1], list(hit[2]), list(hit[3])
            saved += g_saved
            warnings += [n for n in notes if n not in warnings]
            if g_infill:
                infill = g_infill if not infill else [a.union(b) if not b.is_empty else a for a, b in zip(infill, g_infill)]
            hollowing = True
        if gcfg["sla_cavity_pillars"] and on_plate_all[idx[0]] and gcfg["hollow_infill"] == "none" and not jetting:
            # pillars only inside the model's cavity, from the plate / inner floor up to the ceiling
            ckey = ("cavity-pillars", g_keys, tuple(round(p, 6) for p in planes), cfg["resolution"],
                    tuple(gcfg[k] for k in HOLLOW_KEYS), cfg["xy_compensation"],
                    gcfg["support_enable"], gcfg["sla_pillar_diameter"],     # the hollowing's pillars depend on them
                    tuple(bool(filled and i < len(filled) and filled[i]) for i in idx),     # "Fill cavity" changes the layers
                    tuple(gcfg[k] for k in ("sla_cavity_outside", "sla_cavity_diameter", "sla_cavity_spacing")))
            hit = _cache_get(ckey)
            if hit is None:
                cav = covered_voids(g_layers)
                extra = []
                room = cav
                if gcfg["sla_cavity_outside"]:
                    room = under_model(g_layers)
                    extra = _overhang_points(room, cav, gcfg["sla_cavity_diameter"] / 2, gcfg["sla_cavity_spacing"])
                segs: list = []
                out_layers = _internal_pillars(g_layers, room, gcfg, from_plate=True, extra_points=extra,
                                               radius=gcfg["sla_cavity_diameter"] / 2, spacing=gcfg["sla_cavity_spacing"],
                                               segments=segs)
                hit = (out_layers, segs)
                _cache_put(ckey, hit)
            g_layers = list(hit[0])
            pillar_segments += hit[1]
        layers = g_layers if len(groups) == 1 else _per_layer(_union_chunk, list(zip(layers, g_layers)))
    lifted = lift if cfg["support_enable"] and not jetting else 0.0
    hole_tubes = []
    for i, group in enumerate(holes or []):
        flat = jetting or (cfg["support_enable"] and direct and i < len(direct) and direct[i])
        hole_tubes += hole_meshes([group], cfg, 0.0 if flat else lifted)
    cutter_meshes = hole_tubes + [c.translated([0, 0, lifted]) for c in (cutters or [])]
    # labels: (embossed, engraved) text per model, raised with its model
    raised_text = []
    for i, pair in enumerate(labels or []):
        if not pair:
            continue
        flat = jetting or (cfg["support_enable"] and direct and i < len(direct) and direct[i])
        up, cut = pair
        if cut is not None:
            cutter_meshes.append(cut.translated([0, 0, 0.0 if flat else lifted]))
        if up is not None:
            raised_text.append(up.translated([0, 0, 0.0 if flat else lifted]))
    if cutter_meshes:
        cut_layers = _cached_slice(cutter_meshes, planes, cfg["resolution"])
        layers = [as_multipolygon(g.difference(c)) if not c.is_empty and not g.is_empty else g
                  for g, c in zip(layers, cut_layers)]
    if raised_text:
        text_layers = _cached_slice([Mesh.merge(raised_text)], planes, cfg["resolution"])
        layers = [as_multipolygon(g.union(t)) if not t.is_empty else g for g, t in zip(layers, text_layers)]
    # one pass on all cores: thicken thin parts (the model, never the honeycomb walls inside it), the shell
    # without that infill (its walls are thin on purpose), the shell's thin parts and its enclosed cells
    post_key = None
    if not cutter_meshes and not raised_text:
        post_key = ("post", tuple(tuple(mesh_key(models[i]) for i in idx) for idx in groups.values()),
                    tuple(round(p, 6) for p in planes), cfg["resolution"], cfg["xy_compensation"], lh,
                    tuple(bool(filled and i < len(filled) and filled[i]) for i in range(len(models))),
                    tuple(tuple(c[k] for k in HOLLOW_KEYS) + (c["support_enable"], c["sla_pillar_diameter"]) for c in cfgs),
                    jetting,                                   # PolyJet is never hollowed: not the LCD run's layers
                    bool(cfg["sla_thicken_thin"]))
    post = _cache_get(post_key) if post_key else None
    base_key = post_key[:-1] + (False,) if post_key and post_key[-1] else None
    redo = None
    if post is None:
        thicken = bool(cfg["sla_thicken_thin"])
        base = _cache_get(base_key) if base_key else None
        if base is None:
            base = _post_process(layers, infill if hollowing else None, False)
            if base_key or (post_key and not thicken):
                _cache_put(base_key or post_key, base)
        if thicken:
            # grow only what the thin check flags (thin through 0.5 mm of height), with the layers around it;
            # the brief thin slivers of almost every layer of a scanned model need nothing
            k = max(1, int(round(0.5 / lh / 2)))
            flagged = _lasting_thin([x[2] for x in base], layer_height=lh)
            changed = sorted({j for i in flagged for j in range(max(0, i - k), min(len(base), i + k + 1))})
            post, redo = _post_thicken_incremental(base, layers, infill if hollowing else None, changed)
        else:
            post = base
        if post_key:
            _cache_put(post_key, post)
    layers = [x[0] for x in post]
    shell = [x[1] for x in post]
    thin = _lasting_thin([x[2] for x in post], layer_height=lh)
    shell_cells = [x[3] for x in post]
    if support_job is not None:
        supports, points = support_job.result()
        support_meshes = [supports] if supports is not None and len(supports.faces) else []
    model_areas = [g.area for g in layers]
    if support_meshes:
        sup_layers = _slice_supports(support_meshes[0] if len(support_meshes) == 1 else Mesh.merge(support_meshes),
                                     planes, cfg["resolution"],
                                     progress=lambda f: report("slice", split + f * (1 - split)), cancelled=cancelled)
        layers = _per_layer(_union_chunk, list(zip(layers, sup_layers)))
    pillars, left = 0, None
    if cfg["sla_island_pillars"] and not jetting:
        layers, pillars, left = island_pillars(layers)
    foot = cfg["bottom_xy_compensation"]
    if foot > 0:
        for i in range(min(int(cfg["bottom_layers"]), len(layers))):
            if not layers[i].is_empty:
                layers[i] = as_multipolygon(layers[i].buffer(-foot, join_style="mitre", mitre_limit=3))
    elif cfg["bottom_xy_auto"] and not jetting:
        layers = _auto_bottom_compensation(layers, models, planes, cfg,
                                           [i for i in range(len(models)) if not cfg["support_enable"]
                                            or (direct and i < len(direct) and direct[i])])
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
    if pillar_segments:
        job.pillars = _pillar_mesh(pillar_segments, [z for z, _ in stack], [h for _, h in stack])
    job.volume_ml = sum(g.area * h for g, h in zip(layers, job.layer_thickness)) / 1000.0
    job.model_ml = min(job.volume_ml, sum(a * h for a, h in zip(model_areas, job.layer_thickness)) / 1000.0)
    if support_meshes and cfg["sla_raft_type"] != "none":
        raft_top = cfg["sla_raft_thickness"] + 1e-6
        job.raft_ml = sum(max(0.0, g.area - a) * h for g, a, z, h in
                          zip(layers, model_areas, job.layer_z, job.layer_thickness) if z <= raft_top) / 1000.0
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
    # with pillars, the islands left are what their last check found (every other layer was checked before)
    job.islands = left if left is not None else find_islands(layers)
    # suction cups are cavities of the model itself: look at the shell (no supports, no honeycomb infill,
    # whose hundreds of cells per layer made this the slowest step)
    cup_layers = shell if len(shell) == len(layers) else layers
    job.cups_checked = bool(cfg["sla_hole_advice"])
    if not job.cups_checked:
        # holes are off (the default): no suction-cup analysis, nothing that would lead to drilling the model
        cup_layers = []
    seal_key = ("sealed",) + post_key[1:] if post_key and cup_layers is shell else None
    sealed = _cache_get(seal_key) if seal_key else None
    if sealed is None:
        prev = _cache_get(("sealed",) + base_key[1:]) if redo is not None and seal_key else None
        sealed = _sealed_layers(cup_layers, shell_cells if len(shell_cells) == len(cup_layers) else None,
                                previous=prev, changed=redo if prev is not None else None)
        if seal_key:
            _cache_put(seal_key, sealed)
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
    box = _layer_canvas(geom, cfg)
    if box is not None:
        canvas, x0, y0 = box
        image.paste(canvas, (x0, y0))
    if cfg["mirror_x"]:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    if cfg["mirror_y"]:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    return image


def _layer_canvas(geom: MultiPolygon, cfg: Config):
    """The layer drawn in the box round its parts at the printer's resolution: (picture, x0, y0), or None."""
    rx, ry = int(cfg["resolution_x"]), int(cfg["resolution_y"])
    aa = int(cfg["antialiasing"])
    # outermost first, by the area its outline encloses (not its own area): a thin ring (a shell's wall)
    # painted after a solid part sitting in its hole cleared that part with its hole
    polys = sorted(polygons_of(geom), key=lambda p: -shapely.Polygon(p.exterior).area)
    if not polys:
        return None
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
        return None
    canvas = Image.new("L", ((x1 - x0) * aa, (y1 - y0) * aa), 0)
    draw = ImageDraw.Draw(canvas)

    def ring(coords):
        px, py = to_px(coords)
        return list(zip(((px - x0) * aa).tolist(), ((py - y0) * aa).tolist()))

    for p in polys:  # outermost first, so islands inside holes are painted after the hole
        draw.polygon(ring(p.exterior.coords), fill=255)
        for hole in p.interiors:
            draw.polygon(ring(hole.coords), fill=0)
    if aa > 1:
        canvas = canvas.resize((x1 - x0, y1 - y0), Image.BOX)
    if blur > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    return canvas, x0, y0


def render_layer_array(geom: MultiPolygon, cfg: Config) -> np.ndarray:
    """``render_layer`` as a numpy array (the same pixels): only the parts' box is drawn and converted - turning
    the whole 8K / 12K picture into an array and clearing it took most of a print file's export."""
    rx, ry = int(cfg["resolution_x"]), int(cfg["resolution_y"])
    out = np.zeros((ry, rx), dtype=np.uint8)
    box = _layer_canvas(geom, cfg)
    if box is not None:
        canvas, x0, y0 = box
        a = np.asarray(canvas, dtype=np.uint8)
        out[y0:y0 + a.shape[0], x0:x0 + a.shape[1]] = a
    if cfg["mirror_x"]:
        out = out[:, ::-1]
    if cfg["mirror_y"]:
        out = out[::-1, :]
    return np.ascontiguousarray(out)


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
    return [encode_layer(render_layer_array(g, cfg)) for g in layers]


def _render_chunk(layers: list, values: dict) -> list[bytes]:
    """PNG data for a run of layers (process-pool entry point)."""
    cfg = Config(dict(values))
    return [_png_bytes(render_layer(g, cfg)) for g in layers]


PRINT_FLIPPED = ("prz",)     # formats whose printers need the layers drawn mirrored (found by test prints)


def export(job: SlaJob, cfg: Config, path: str, job_name: str = "print",
           progress: Callable[[float], None] | None = None, cancelled: Callable[[], bool] | None = None) -> str:
    """Write the print file. It is written beside ``path`` first and put in place only when complete, so a
    cancel or an error never leaves a half-written file - nor destroys the good one already there."""
    import os
    import threading
    part = f"{path}.{os.getpid()}-{threading.get_ident()}.part"     # unique: two windows may export at once
    try:
        _export_file(job, cfg, part, job_name, progress, cancelled)
        os.replace(part, path)
        from .project import drop_stale_parts
        drop_stale_parts(path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return path


def _export_file(job: SlaJob, cfg: Config, path: str, job_name: str = "print",
                 progress: Callable[[float], None] | None = None, cancelled: Callable[[], bool] | None = None) -> str:
    fmt = cfg["sla_format"]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_name) or "print"
    n = job.layer_count
    bottom = min(int(cfg["bottom_layers"]), n)
    from . import parallel

    if fmt in ("slc", "cli", "3mf"):
        return _export_vector(job, cfg, path, fmt, progress)
    ranges = parallel.chunk_ranges(n, 8, parallel.workers() * 4)
    values = dict(cfg.values)
    if fmt in PRINT_FLIPPED:
        # Phrozen's printers show PRZ layers the other way round to how they are drawn here: prints came
        # out mirror images of the models. Drawn flipped, they print as designed (the header keeps the
        # printer's own mirror settings).
        values["mirror_y"] = not values.get("mirror_y", False)
    args = [(job.layers[a:b], values) for a, b in ranges]
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
                "layer_exposure_s": [round(job.exposures[i] if i < len(job.exposures) else layer_exposure(i, cfg), 3)
                                     for i in range(n)],
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
