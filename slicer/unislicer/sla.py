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
from shapely.geometry import Point

from . import __version__
from .mesh import Mesh
from .settings import Config
from .sla_supports import build_supports
from .slicing import EMPTY, MultiPolygon, as_multipolygon, polygons_of, slice_meshes


@dataclass
class SlaJob:
    layers: list[MultiPolygon]
    layer_height: float
    models: list[Mesh]                 # elevated models
    supports: Mesh | None
    support_points: list[np.ndarray] = field(default_factory=list)
    islands: list[tuple[int, object]] = field(default_factory=list)   # (layer index, polygon)
    volume_ml: float = 0.0
    saved_ml: float = 0.0              # resin saved by hollowing
    time_s: float = 0.0
    weight_g: float = 0.0
    cost: float = 0.0
    warnings: list[str] = field(default_factory=list)

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
    if cfg["tsmc_enable"]:
        lift2 = cfg["lift_height_2"]
        up = lift / lift_speed + lift2 / (cfg["lift_speed_2"] / 60.0)
        slow = min(cfg["retract_height_2"], lift + lift2)
        down = (lift + lift2 - slow) / retract_speed + slow / (cfg["retract_speed_2"] / 60.0)
    else:
        up, down = lift / lift_speed, lift / retract_speed
    return cfg["rest_before_lift"] + up + cfg["rest_after_lift"] + down + cfg["light_off_delay"]


def print_time(n: int, cfg: Config) -> float:
    return sum(layer_exposure(i, cfg) + layer_motion_time(i, cfg) for i in range(n))


# --------------------------------------------------------------- hollowing
def _window_intersection(items: list, k: int) -> list:
    """For each i, the intersection of items[i-k..i+k] (empty near the ends), in O(n) operations
    using the van Herk / Gil-Werman prefix/suffix trick."""
    n = len(items)
    m = 2 * k + 1
    out = [EMPTY] * n
    if n < m:
        return out
    prefix, suffix = [None] * n, [None] * n
    for j in range(n):
        prefix[j] = items[j] if j % m == 0 else _and(prefix[j - 1], items[j])
    for j in range(n - 1, -1, -1):
        suffix[j] = items[j] if (j % m == m - 1 or j == n - 1) else _and(suffix[j + 1], items[j])
    for i in range(k, n - k):
        out[i] = _and(suffix[i - k], prefix[i + k])
    return out


def _and(a, b):
    if a.is_empty or b.is_empty:
        return EMPTY
    return as_multipolygon(a.intersection(b))


def hollow(layers: list[MultiPolygon], lh: float, cfg: Config) -> tuple[list[MultiPolygon], float, list[str]]:
    """Hollow solid layers to ``hollow_wall`` thickness and cut drain holes. Returns (layers, saved ml, notes)."""
    wall = cfg["hollow_wall"]
    k = max(1, int(math.ceil(wall / lh)))
    eroded = [as_multipolygon(g.buffer(-wall)) if not g.is_empty else EMPTY for g in layers]
    cavity = _window_intersection(eroded, k)
    cavity = [as_multipolygon(c.buffer(-0.01).buffer(0.01)) if not c.is_empty else c for c in cavity]
    saved = sum(c.area for c in cavity) * lh / 1000.0
    if saved <= 0:
        return layers, 0.0, ["The model is too thin to hollow with this wall thickness."]
    out = [g.difference(c) if not c.is_empty else g for g, c in zip(layers, cavity)]
    notes = []
    holes = int(cfg["drain_holes"])
    if holes > 0:
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
    else:
        notes.append("Hollowed without drain holes: uncured resin will stay trapped inside.")
    return [as_multipolygon(g) for g in out], saved, notes


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
def find_islands(layers: list[MultiPolygon], min_area: float = 0.01) -> list[tuple[int, object]]:
    """Regions that start in mid-air: not connected to anything on the layer below."""
    out = []
    for i in range(1, len(layers)):
        prev = layers[i - 1]
        if layers[i].is_empty:
            continue
        shapely.prepare(prev)
        for poly in polygons_of(layers[i]):
            if poly.area >= min_area and not prev.intersects(poly):
                out.append((i, poly))
    return out


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


# ----------------------------------------------------------------- prepare
def prepare(meshes: list[Mesh], cfg: Config, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] | None = None,
            support_points: list[np.ndarray | None] | None = None) -> SlaJob:
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
    models = [_shrink_compensate(m, cfg) for m in meshes]
    supports = None
    points = []
    if cfg["support_enable"]:
        lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
        models = [m.translated([0, 0, lift]) for m in models]
        parts = []
        for i, m in enumerate(models):
            report("support", i / len(models))
            manual = support_points[i] if support_points and i < len(support_points) else None
            if manual is not None:
                manual = np.asarray(manual, dtype=np.float64).reshape(-1, 3) + [0, 0, lift]
            s, p = build_supports(m, cfg, manual)
            parts.append(s)
            points += list(p)
        supports = Mesh.merge(parts, "Supports")
    lh = cfg["sla_layer_height"]
    support_meshes = [supports] if supports is not None and len(supports.faces) else []
    top = max(float(m.bounds[1, 2]) for m in models + support_meshes)
    n = max(1, int(np.ceil(top / lh - 1e-9)))
    planes = [(k + 0.5) * lh for k in range(n)]
    split = 0.85 if support_meshes else 1.0
    layers = slice_meshes(models, planes, cfg["resolution"],
                          progress=lambda f: report("slice", f * split), cancelled=cancelled)
    if cfg["xy_compensation"]:
        layers = [as_multipolygon(g.buffer(cfg["xy_compensation"], join_style="mitre", mitre_limit=3))
                  if not g.is_empty else g for g in layers]
    saved = 0.0
    if cfg["hollow_enable"]:
        report("hollow", 0.0)
        layers, saved, notes = hollow(layers, lh, cfg)
        warnings += notes
    if support_meshes:
        sup_layers = slice_meshes(support_meshes, planes, cfg["resolution"],
                                  progress=lambda f: report("slice", split + f * (1 - split)), cancelled=cancelled)
        layers = [as_multipolygon(shapely.union_all([a, b])) if not b.is_empty else a for a, b in zip(layers, sup_layers)]
    foot = cfg["bottom_xy_compensation"]
    if foot > 0:
        for i in range(min(int(cfg["bottom_layers"]), len(layers))):
            if not layers[i].is_empty:
                layers[i] = as_multipolygon(layers[i].buffer(-foot, join_style="mitre", mitre_limit=3))
    job = SlaJob(layers, lh, models, supports, points, warnings=warnings)
    job.islands = find_islands(layers)
    if job.islands:
        first = ", ".join(str(i + 1) for i in job.island_layers[:8])
        job.warnings.append(f"{len(job.islands)} unsupported island(s) start in mid-air (layers {first}"
                            f"{'…' if len(job.island_layers) > 8 else ''}). Add supports there.")
    area = sum(g.area for g in layers)
    job.volume_ml = area * lh / 1000.0
    job.saved_ml = saved
    job.weight_g = job.volume_ml * cfg["resin_density"]
    job.cost = job.volume_ml / 1000.0 * cfg["resin_cost"]
    job.time_s = print_time(n, cfg)
    return job


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

    ranges = parallel.chunk_ranges(n, 8, parallel.workers() * 4)
    args = [(job.layers[a:b], cfg.values) for a, b in ranges]
    if fmt in ("ctb", "goo", "prz"):
        from . import ctb, goo
        chunks = parallel.run_chunks(_rle_chunk, [a + ("ctb" if fmt == "ctb" else "goo",) for a in args],
                                     lambda f: progress(f * 0.9) if progress else None,
                                     cancelled, [b - a for a, b in ranges])
        rle = ((i, data) for i, data in enumerate(d for chunk in chunks for d in chunk))
        common = dict(layer_height=job.layer_height, exposures=[layer_exposure(i, cfg) for i in range(n)],
                      volume_ml=job.volume_ml, weight_g=job.weight_g, cost=job.cost, print_time_s=job.time_s,
                      progress=lambda f: progress(0.9 + 0.1 * f) if progress else None)
        if fmt == "ctb":
            ctb.write(path, rle, n, cfg, preview_layers=job.layers, **common)
        else:
            goo.write(path, rle, n, cfg, preview=ctb.make_preview(job.layers, cfg, (290, 290)), **common)
        return path
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        # layer images are drawn and PNG-encoded on all cores; the zip is written here in order
        def write_chunk(k, pngs):
            a = ranges[k][0]
            for j, data in enumerate(pngs):
                name = f"{safe}{a + j:05d}.png" if fmt == "sl1" else f"layers/layer_{a + j:05d}.png"
                zf.writestr(name, data)

        chunks = parallel.run_chunks(_render_chunk, args, progress, cancelled, [b - a for a, b in ranges])
        for k, pngs in enumerate(chunks):
            write_chunk(k, pngs)
        if fmt == "sl1":
            zf.writestr("config.ini", _sl1_config(job, cfg, safe, bottom))
            zf.writestr("prusaslicer.ini", _sl1_printer_ini(job, cfg))
        else:
            keys = ("exposure_time", "bottom_exposure_time", "bottom_layers", "transition_layers", "light_pwm",
                    "lift_height", "lift_speed", "bottom_lift_height", "bottom_lift_speed", "retract_speed",
                    "bottom_retract_speed", "rest_before_lift", "rest_after_lift", "light_off_delay", "tsmc_enable",
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
