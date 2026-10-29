"""Resin (SLA / MSLA) slicing: supports, layer masks and packaged output."""
from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

from . import __version__
from .mesh import Mesh
from .settings import Config
from .sla_supports import build_supports
from .slicing import MultiPolygon, polygons_of, slice_meshes


@dataclass
class SlaJob:
    layers: list[MultiPolygon]
    layer_height: float
    models: list[Mesh]                 # elevated models
    supports: Mesh | None
    support_points: list[np.ndarray] = field(default_factory=list)
    volume_ml: float = 0.0
    time_s: float = 0.0
    weight_g: float = 0.0
    cost: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def layer_count(self) -> int:
        return len(self.layers)


def prepare(meshes: list[Mesh], cfg: Config, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> SlaJob:
    def report(stage, f):
        if cancelled and cancelled():
            raise InterruptedError
        if progress:
            progress(stage, f)

    if not meshes:
        raise ValueError("Nothing to slice")
    warnings = [f"'{m.name}' is not watertight; check the layer preview." for m in meshes if not m.is_watertight()]
    models = list(meshes)
    supports = None
    points = []
    if cfg["support_enable"]:
        lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
        models = [m.translated([0, 0, lift]) for m in meshes]
        parts = []
        for i, m in enumerate(models):
            report("support", i / len(models))
            s, p = build_supports(m, cfg)
            parts.append(s)
            points += p
        supports = Mesh.merge(parts, "Supports")
    lh = cfg["sla_layer_height"]
    all_meshes = models + ([supports] if supports is not None and len(supports.faces) else [])
    top = max(float(m.bounds[1, 2]) for m in all_meshes)
    n = max(1, int(np.ceil(top / lh - 1e-9)))
    planes = [(k + 0.5) * lh for k in range(n)]
    layers = slice_meshes(all_meshes, planes, cfg["resolution"],
                          progress=lambda f: report("slice", f), cancelled=cancelled)
    job = SlaJob(layers, lh, models, supports, points, warnings=warnings)
    area = sum(g.area for g in layers)
    job.volume_ml = area * lh / 1000.0
    job.weight_g = job.volume_ml * cfg["resin_density"]
    job.cost = job.volume_ml / 1000.0 * cfg["resin_cost"]
    bottom = min(int(cfg["bottom_layers"]), n)
    move = 2 * cfg["lift_height"] / cfg["lift_speed"] * 60.0
    job.time_s = (bottom * cfg["bottom_exposure_time"] + (n - bottom) * cfg["exposure_time"]
                  + n * (move + cfg["light_off_delay"]))
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
    x0, y0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
    x1, y1 = min(rx, int(max(xs)) + 3), min(ry, int(max(ys)) + 3)
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
            meta = {
                "generator": f"UniSlicer {__version__}", "job": job_name, "layer_count": n,
                "layer_height_mm": job.layer_height, "resolution": [cfg["resolution_x"], cfg["resolution_y"]],
                "display_mm": [cfg["bed_x"], cfg["bed_y"]], "exposure_s": cfg["exposure_time"],
                "bottom_exposure_s": cfg["bottom_exposure_time"], "bottom_layers": bottom,
                "lift_height_mm": cfg["lift_height"], "lift_speed_mm_min": cfg["lift_speed"],
                "light_off_delay_s": cfg["light_off_delay"], "volume_ml": round(job.volume_ml, 3),
                "estimated_time_s": round(job.time_s), "material": cfg["material_name"],
                "mirror_x": cfg["mirror_x"], "mirror_y": cfg["mirror_y"],
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
        "numFade": str(max(0, min(10, job.layer_count - bottom))),
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
