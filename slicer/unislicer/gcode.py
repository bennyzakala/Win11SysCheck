"""G-code generation for FDM plans, with firmware-specific start/end code."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import __version__
from .fdm import FdmPlan
from .settings import Config

CURA_TYPES = {
    "outer_wall": "WALL-OUTER", "inner_wall": "WALL-INNER", "skin": "SKIN", "infill": "FILL",
    "support": "SUPPORT", "interface": "SUPPORT-INTERFACE", "skirt": "SKIRT",
}
SPEED_KEYS = {
    "outer_wall": "outer_wall_speed", "inner_wall": "wall_speed", "skin": "top_bottom_speed",
    "infill": "print_speed", "support": "support_speed", "interface": "support_speed", "skirt": "first_layer_speed",
}
FLAVOR_NAMES = {"marlin": "Marlin", "klipper": "Klipper", "reprap": "RepRap", "prusa": "Marlin", "smoothie": "Smoothie"}


@dataclass
class GcodeStats:
    time_s: float = 0.0
    filament_mm: float = 0.0
    filament_g: float = 0.0
    cost: float = 0.0
    layer_count: int = 0
    layer_times: list = field(default_factory=list)


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h or d else []) + [f"{m}m", f"{s}s"]
    return " ".join(parts)


def render_template(text: str, values: dict) -> str:
    """Replace {key} placeholders with setting values; unknown placeholders are left untouched."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def default_start_gcode(cfg: Config) -> str:
    lines = ["; ---- start ----"]
    flavor = cfg["gcode_flavor"]
    if cfg["heated_bed"]:
        lines.append("M140 S{first_layer_bed_temp} ; bed temperature")
    lines.append("M104 S{first_layer_nozzle_temp} ; nozzle temperature")
    lines.append("G28 ; home all axes")
    if flavor == "prusa":
        lines.append("; add G29 (MK4/XL) or G80 (MK3) here for mesh bed levelling")
    if cfg["heated_bed"]:
        lines.append("M190 S{first_layer_bed_temp} ; wait for bed")
    lines.append("M109 S{first_layer_nozzle_temp} ; wait for nozzle")
    lines.append("G92 E0")
    lines.append("G1 Z2.0 F3000")
    if not cfg["origin_center"]:
        y_end = min(cfg["bed_y"] - 20, 200)
        lines += [
            "; prime line",
            "G1 X3 Y20 Z0.28 F5000",
            f"G1 X3 Y{y_end:.0f} Z0.28 F1500 E{(y_end - 20) * 0.075:.2f}",
            f"G1 X3.4 Y{y_end:.0f} Z0.28 F5000",
            f"G1 X3.4 Y20 Z0.28 F1500 E{(y_end - 20) * 0.15:.2f}",
            "G92 E0",
            "G1 Z2.0 F3000",
        ]
    return "\n".join(lines)


def default_end_gcode(cfg: Config) -> str:
    lines = [
        "; ---- end ----",
        "M104 S0 ; nozzle off",
    ]
    if cfg["heated_bed"]:
        lines.append("M140 S0 ; bed off")
    lines += ["M107 ; fan off", "G91", "G1 E-2 F2400", "G1 Z10 F3000", "G90"]
    if not cfg["origin_center"]:
        lines.append("G1 X5 Y{bed_y} F6000 ; present the print")
    lines.append("M84 ; motors off")
    return "\n".join(lines)


class GcodeWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.out: list[str] = []
        self.e = 0.0
        self.retracted = False
        self.x = self.y = None
        self.z = 0.0
        self.f = None
        self.stats = GcodeStats()
        d = cfg["filament_diameter"]
        self.filament_area = math.pi * (d / 2) ** 2
        self.relative = bool(cfg["relative_e"])
        self.ox = -cfg["bed_x"] / 2 if cfg["origin_center"] else 0.0
        self.oy = -cfg["bed_y"] / 2 if cfg["origin_center"] else 0.0

    # ---------------------------------------------------------- primitives
    def _emit(self, line: str):
        self.out.append(line)

    def _feed(self, mm_s: float) -> str:
        f = round(mm_s * 60)
        if f == self.f:
            return ""
        self.f = f
        return f" F{f}"

    def _retract(self):
        cfg = self.cfg
        if self.retracted or cfg["retract_length"] <= 0:
            return
        if cfg["firmware_retraction"]:
            self._emit("G10")
        else:
            self._extrude_e(-cfg["retract_length"], cfg["retract_speed"])
        self.stats.time_s += cfg["retract_length"] / cfg["retract_speed"]
        self.retracted = True

    def _unretract(self):
        cfg = self.cfg
        if not self.retracted:
            return
        if cfg["firmware_retraction"]:
            self._emit("G11")
        else:
            self._extrude_e(cfg["retract_length"], cfg["retract_speed"])
        self.stats.time_s += cfg["retract_length"] / cfg["retract_speed"]
        self.retracted = False

    def _extrude_e(self, amount: float, speed: float):
        if self.relative:
            self._emit(f"G1 E{amount:.5f}{self._feed(speed)}")
        else:
            self.e += amount
            self._emit(f"G1 E{self.e:.5f}{self._feed(speed)}")

    def _travel(self, x: float, y: float, retract: bool):
        cfg = self.cfg
        if self.x is not None:
            dist = math.hypot(x - self.x, y - self.y)
            if dist < 1e-4:
                return
        else:
            dist = 0.0
        hop = retract and cfg["z_hop"] > 0
        if retract:
            self._retract()
        if hop:
            self._emit(f"G1 Z{self.z + cfg['z_hop']:.3f}{self._feed(cfg['travel_speed'] / 3)}")
        self._emit(f"G0 X{x + self.ox:.3f} Y{y + self.oy:.3f}{self._feed(cfg['travel_speed'])}")
        if hop:
            self._emit(f"G1 Z{self.z:.3f}{self._feed(cfg['travel_speed'] / 3)}")
        self.stats.time_s += dist / cfg["travel_speed"]
        self.x, self.y = x, y

    # -------------------------------------------------------------- layers
    def _layer_moves(self, layer, w):
        """Return [(path, speed_mm_s, length)] and the estimated layer time."""
        cfg = self.cfg
        items = []
        t = 0.0
        for path in layer.paths:
            pts = path.points
            if path.closed:
                pts = np.vstack([pts, pts[:1]])
            length = float(np.hypot(*np.diff(pts, axis=0).T).sum())
            speed = cfg[SPEED_KEYS[path.kind]]
            if layer.index == 0:
                speed = min(speed, cfg["first_layer_speed"])
            items.append([path, pts, speed, length])
            t += length / speed
        return items, t

    def write(self, plan: FdmPlan, job_name: str = "print") -> str:
        cfg = self.cfg
        w = plan.line_width
        values = dict(cfg.values)
        values["line_width"] = w
        body: list[str] = []
        self.out = body
        flavor = cfg["gcode_flavor"]
        progress_codes = flavor in ("marlin", "prusa")

        self._emit("G90 ; absolute positioning")
        self._emit("M83 ; relative extrusion" if self.relative else "M82 ; absolute extrusion")
        start = cfg["start_gcode"].strip() or default_start_gcode(cfg)
        self._emit(render_template(start, values))
        self._emit("M83" if self.relative else "M82")
        self._emit("G92 E0")
        self._emit("M107")

        # pass 1: estimate layer times so progress (M73) and slow-down are known up front
        prepared = []
        for layer in plan.layers:
            items, t = self._layer_moves(layer, w)
            if cfg["min_layer_time"] > 0 and 0 < t < cfg["min_layer_time"]:
                factor = t / cfg["min_layer_time"]
                for it in items:
                    it[2] = max(cfg["min_print_speed"], it[2] * factor)
            prepared.append(items)
        total_estimate = sum(sum(it[3] / it[2] for it in items) for items in prepared) or 1.0
        done = 0.0

        fan_layer = max(0, int(cfg["fan_start_layer"]) - 1)
        for layer, items in zip(plan.layers, prepared):
            layer_start_time = self.stats.time_s
            self._emit(";LAYER_CHANGE")
            self._emit(f";Z:{layer.z:.3f}")
            self._emit(f";LAYER:{layer.index}")
            if progress_codes:
                pct = int(100 * done / total_estimate)
                remaining = max(0, int((total_estimate - done) / 60))
                self._emit(f"M73 P{pct} R{remaining}")
            if layer.index == 1:
                self._emit(f"M104 S{cfg['nozzle_temp']}")
                if cfg["heated_bed"]:
                    self._emit(f"M140 S{cfg['bed_temp']}")
            if layer.index == fan_layer and cfg["fan_speed"] > 0:
                self._emit(f"M106 S{round(255 * cfg['fan_speed'] / 100)}")
            if not self.relative:
                self._emit("G92 E0")
                self.e = 0.0
            if items:
                self._retract()
            self.z = layer.z
            self._emit(f"G1 Z{layer.z:.3f}{self._feed(cfg['travel_speed'] / 3)}")
            e_per_mm = self._e_per_mm(w, layer.height)
            current_type = None
            for path, pts, speed, length in items:
                self._travel(pts[0, 0], pts[0, 1], path.retract or self.retracted)
                self._unretract()
                if path.kind != current_type:
                    self._emit(f";TYPE:{CURA_TYPES[path.kind]}")
                    current_type = path.kind
                self._extrude_polyline(pts, speed, e_per_mm)
                done += length / speed
            self.stats.layer_times.append(self.stats.time_s - layer_start_time)
        self._retract()
        self._emit(render_template(cfg["end_gcode"].strip() or default_end_gcode(cfg), values))
        if progress_codes:
            self._emit("M73 P100 R0")

        st = self.stats
        st.layer_count = len(plan.layers)
        st.filament_g = st.filament_mm * self.filament_area / 1000.0 * cfg["filament_density"]
        st.cost = st.filament_g / 1000.0 * cfg["filament_cost"]
        header = [
            f";FLAVOR:{FLAVOR_NAMES.get(flavor, 'Marlin')}",
            f";TIME:{int(st.time_s)}",
            f";Filament used: {st.filament_mm / 1000:.4f}m",
            f";Layer height: {cfg['layer_height']}",
            f";LAYER_COUNT:{st.layer_count}",
            f";Generated with UniSlicer {__version__}",
            f";Job: {job_name}",
            f";Printer bed: {cfg['bed_x']} x {cfg['bed_y']} mm, nozzle {cfg['nozzle_diameter']} mm, material {cfg['material_name']}",
            "",
        ]
        footer = [
            "",
            f"; filament used [mm] = {st.filament_mm:.2f}",
            f"; filament used [g] = {st.filament_g:.2f}",
            f"; estimated printing time (normal mode) = {format_duration(st.time_s)}",
        ]
        return "\n".join(header + body + footer) + "\n"

    def _e_per_mm(self, w: float, h: float) -> float:
        # Slic3r's extrusion model: rectangle with semicircular ends
        area = (w - h) * h + math.pi * (h / 2) ** 2 if w > h else math.pi * (w / 2) ** 2
        return area / self.filament_area * self.cfg["flow"] / 100.0

    def _extrude_polyline(self, pts: np.ndarray, speed: float, e_per_mm: float):
        feed = self._feed(speed)
        for k in range(1, len(pts)):
            x, y = pts[k]
            seg = math.hypot(x - self.x, y - self.y)
            if seg < 1e-4:
                continue
            de = seg * e_per_mm
            self.stats.filament_mm += de
            self.stats.time_s += seg / speed
            if self.relative:
                e_txt = f"{de:.5f}"
            else:
                self.e += de
                e_txt = f"{self.e:.5f}"
            self._emit(f"G1 X{x + self.ox:.3f} Y{y + self.oy:.3f} E{e_txt}{feed}")
            feed = ""
            self.x, self.y = x, y


def generate(plan: FdmPlan, cfg: Config, job_name: str = "print") -> tuple[str, GcodeStats]:
    writer = GcodeWriter(cfg)
    text = writer.write(plan, job_name)
    return text, writer.stats
