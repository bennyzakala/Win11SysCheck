"""G-code generation for FDM plans, with firmware-specific start/end code."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import __version__
from . import filaments as fil
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
    tool_mm: dict = field(default_factory=dict)        # filament (T number) -> mm used, purges included
    toolchanges: int = 0
    purge_mm: float = 0.0


# Creality filament data used by the CFS change sequence (Creality Print's filament presets)
TEMP_HIGH = {"PLA": 230, "PETG": 260, "ABS": 270, "ASA": 270, "TPU": 240, "PA": 290, "PC": 290, "PVA": 220, "HIPS": 250}
MAX_FLOW = {"PLA": 20.0, "PETG": 12.0, "ABS": 18.0, "ASA": 18.0, "TPU": 3.6, "PA": 12.0, "PC": 12.0, "PVA": 8.0}
CFS_CUT_RETRACT = 28.0           # retraction_distances_when_cut
CFS_TOOLCHANGE_RETRACT = 2.0     # retract_length_toolchange
CFS_CHANGE_TIME = 60.0           # unload + load (machine_unload/load_filament_time 30 s each)


def creality_cfs_start(cfg: Config, tool: int, first_temp: int) -> str:
    """SPARKX i7 start sequence (Creality Print 7.3 profile) for the first filament ``tool``."""
    prime_f = max(60, round(MAX_FLOW.get("PLA", 15.0) / 0.3 * 60 * 0.1))
    slow = max(60, round(cfg["outer_wall_speed"] * 0.15 / (0.3 * 0.5) / 4 * 60 * 0.5))
    fast = max(60, slow * 4)
    lines = [
        "M140 S0", "M104 S0",
        f"START_PRINT EXTRUDER_TEMP={first_temp} BED_TEMP={cfg['first_layer_bed_temp']}",
        "M83", f"M8200 P S{tool}", "M220 S100", "",
        "SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000", "G0 X267 F18000", "G0 X278 F2400",
        f"M104 S{first_temp}", "M106 S0", f"T{tool}", "", "M8200 O", "M204 S2000", "G1 Z3 F600", "M83",
        "G1 Y0 F12000", "G1 X150 F30000", "G1 Z0.2 F600", f"G1 X120 Y0 E15 F{prime_f}",
        f"G1 X115 E.3742 F{slow}", f"G1 X110 E.3742 F{fast}", f"G1 X105 E.3742 F{slow}",
        f"G1 X100 E.3742 F{fast}", f"G1 X95 E.3742 F{slow}", "G91", "G1 X-1 Z-0.3", "G1 X-4", "G92 E0", "G90",
    ]
    return "\n".join(lines)


def creality_cfs_end(cfg: Config, z: float) -> str:
    return "\n".join(["G1 E-0.8 F2400 ; retract", f"G1 Z{min(cfg['max_z'], z + 3):.2f} F600", "G92 E0 ; zero the extruder",
                      "G1 X0 Y180 F8000", "END_PRINT"])


def creality_cfs_change(prev: fil.Filament, new: fil.Filament, z: float, count: int, first_layer: bool,
                        purges: list[float], filament_diameter: float) -> list[str]:
    """The SPARKX i7 CFS filament change (Creality Print 7.3 change_filament_gcode), with its values filled in:
    cut and unload the old filament, load the new one from its CFS slot, purge into the chute, wipe."""
    area = math.pi / 4 * filament_diameter ** 2
    old_feed = int(60.0 * MAX_FLOW.get(prev.type, 15.0) / area) or 100
    new_feed = int(60.0 * MAX_FLOW.get(new.type, 15.0) / area) or 100
    new_temp = new.first_temp if first_layer else new.temp
    hi_prev = TEMP_HIGH.get(prev.type, prev.temp + 20)
    hi_new = TEMP_HIGH.get(new.type, new.temp + 20)
    cut = prev.type != "PETG"              # Creality skips the long cut retraction for PETG
    r = CFS_TOOLCHANGE_RETRACT
    L = list(purges) + [0.0] * (5 - len(purges))
    wipe = ["; WIPE", "SET_VELOCITY_LIMIT ACCEL=10000 ACCEL_TO_DECEL=10000",
            "G0 X267 F24000", "G0 X278 F2400", "G0 X267 F24000", "G0 X278 F2400"]
    out = [f"; CP TOOLCHANGE T{prev.index} -> T{new.index}"]
    if count > 1:
        out += ["G17", f"G2 Z{z + 0.4:.3f} I0.86 J0.86 P1 F10000"]
    out += ["G1 X0 F18000", "M106 S120", f"M8200 P S{new.index}", "M220 S100"]
    if cut:
        out.append(f"M8200 R E-{CFS_CUT_RETRACT:g}")
    out += ["", "M400", "G1 X5 F10000", "SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=500", "G0 F500", "",
            "M8200 C S0", "SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000", "G0 Y255 F18000", "G0 X267 F18000",
            "G0 Y130 F18000", "G0 X278 F2400", "M8200 R", "M106 S30", f"M104 S{hi_prev}", f"M8200 L I{new.index}"]
    if cut:
        out.append(f"G1 E{CFS_CUT_RETRACT:g} F{old_feed}")
    out += [f"T{new.index}", ""]
    if L[0] > 1:
        f1 = L[0]
        out += [f";flush_length_1={f1:.3f}", "; FLUSH_START", "M106 S70",
                f"M104 S{hi_new}" if L[1] > 1 else f"M104 S{new_temp}"]
        if f1 > 32:
            k = f1 - 32
            out += ["G1 F60", "M400", f"G1 E32 F{old_feed}",
                    f"G1 E{k * 0.02:.4f} F60", f"G1 E{k * 0.23:.4f} F{old_feed}"]
            for _ in range(3):
                out += [f"G1 E{k * 0.02:.4f} F60", f"G1 E{k * 0.23:.4f} F{new_feed}"]
        else:
            out.append(f"G1 E{f1:.4f} F{old_feed}")
        out += ["", "M400", "M106 S255", "G4 P2500", "M106 S30", f"G1 E-{r:g} F1800", "; FLUSH_END", ""] + wipe + [""]
    else:
        out.append(f"M104 S{new_temp}")
    for i in range(1, 5):
        fl = L[i]
        if fl <= 1:
            continue
        nxt = L[i + 1] if i + 1 < 5 else 0.0
        out += [f";flush_length_{i + 1}={fl:.3f}", "; FLUSH_START"]
        if i == 4 or nxt < 1:
            out.append(f"M104 S{new_temp}")
        out += ["SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000", "M106 S70", "G1 F60", "M400",
                f"G1 E{fl * 0.18 + r:.4f} F{new_feed}", f"G1 E{fl * 0.02:.4f} F60"]
        for _ in range(4):
            out += [f"G1 E{fl * 0.18:.4f} F{new_feed}", f"G1 E{fl * 0.02:.4f} F60"]
        out += ["", "M400", "M106 S255", "G4 P2500", "M106 S30", f"G1 E-{r:g} F1800", "; FLUSH_END", ""] + wipe + [""]
    out += ["SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000", "G0 X267 F2400", "M8200 O"]
    return out


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
        self.cfs = cfg["toolchange_mode"] == "creality_cfs"
        self.relative = bool(cfg["relative_e"]) or self.cfs      # the CFS sequence is written for relative E
        self.ox = -cfg["bed_x"] / 2 if cfg["origin_center"] else 0.0
        self.oy = -cfg["bed_y"] / 2 if cfg["origin_center"] else 0.0
        self.filaments = fil.filament_list(cfg)
        self.tool = 0
        self.need_z = False             # after a filament change the nozzle is parked: lower to the layer after travelling
        self.pending_prime = 0.0        # filament to push back after a change (mm)

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
        if hop or self.need_z:
            self._emit(f"G1 Z{self.z:.3f}{self._feed(cfg['travel_speed'] / 3)}")
            self.need_z = False
        if self.pending_prime > 0:
            self._extrude_e(self.pending_prime, 30.0)
            self.pending_prime = 0.0
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

    def _change_tool(self, new_tool: int, layer) -> None:
        """Swap to filament ``new_tool`` (CFS sequence, or T<n> plus the user's change G-code)."""
        cfg = self.cfg
        prev = self.filaments[self.tool] if self.tool < len(self.filaments) else self.filaments[0]
        new = self.filaments[new_tool] if new_tool < len(self.filaments) else fil.Filament(new_tool, "#FFFFFF", "PLA",
                                                                                              int(cfg["nozzle_temp"]),
                                                                                              int(cfg["first_layer_nozzle_temp"]))
        first_layer = layer.index == 0
        self.stats.toolchanges += 1
        self._retract()
        if self.cfs:
            volume = fil.purge_volume(prev.colour, new.colour, cfg["flush_multiplier"])
            purges = fil.purge_cycles(volume, cfg["filament_diameter"])
            for line in creality_cfs_change(prev, new, self.z, self.stats.toolchanges, first_layer, purges,
                                            cfg["filament_diameter"]):
                self._emit(line)
            purge_mm = sum(purges)
            self.stats.purge_mm += purge_mm
            self.stats.tool_mm[new.index] = self.stats.tool_mm.get(new.index, 0.0) + purge_mm
            self.stats.time_s += CFS_CHANGE_TIME + 2.5 * len(purges) + purge_mm / 4.0
            self.x, self.y = 267.0 - self.ox, 130.0 - self.oy      # parked at the chute
            self.retracted = False
            self.pending_prime = CFS_TOOLCHANGE_RETRACT
            self.need_z = True
        else:
            temp = new.first_temp if first_layer else new.temp
            self._emit(f"T{new.index}")
            if temp != (prev.first_temp if first_layer else prev.temp):
                self._emit(f"M109 S{temp}")
            extra = cfg["toolchange_gcode"].strip()
            if extra:
                self._emit(render_template(extra, {**cfg.values, "previous_tool": prev.index, "next_tool": new.index,
                                                    "next_temp": temp, "layer_z": f"{self.z:.3f}"}))
                self.x = self.y = None
                self.need_z = True
            self.stats.time_s += 30.0
        self.tool = new.index
        self.f = None

    def write(self, plan: FdmPlan, job_name: str = "print", thumbnails: str = "") -> str:
        cfg = self.cfg
        w = plan.line_width
        values = dict(cfg.values)
        values["line_width"] = w
        body: list[str] = []
        self.out = body
        flavor = cfg["gcode_flavor"]
        progress_codes = flavor in ("marlin", "prusa")
        first_tool = next((p.tool for layer in plan.layers for p in layer.paths), plan.tools[0] if plan.tools else 0)
        self.tool = first_tool
        ff = self.filaments[first_tool] if first_tool < len(self.filaments) else self.filaments[0]
        values["initial_tool"] = first_tool
        if plan.multi_material or first_tool:
            values["first_layer_nozzle_temp"], values["nozzle_temp"] = ff.first_temp, ff.temp

        self._emit("G90 ; absolute positioning")
        self._emit("M83 ; relative extrusion" if self.relative else "M82 ; absolute extrusion")
        if self.cfs and not cfg["start_gcode"].strip():
            start = creality_cfs_start(cfg, first_tool, ff.first_temp)
        else:
            start = cfg["start_gcode"].strip() or default_start_gcode(cfg)
            if plan.multi_material and not self.cfs and not re.search(r"^\s*T\d", start, re.M):
                start += f"\nT{first_tool}"
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
                cur = self.filaments[self.tool] if self.tool < len(self.filaments) else ff
                self._emit(f"M104 S{cur.temp if (plan.multi_material or self.tool) else cfg['nozzle_temp']}")
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
                if path.tool != self.tool:
                    self._change_tool(path.tool, layer)
                    current_type = None
                    if self.cfs and cfg["fan_speed"] > 0 and layer.index >= fan_layer:
                        self._emit(f"M106 S{round(255 * cfg['fan_speed'] / 100)}")
                self._travel(pts[0, 0], pts[0, 1], path.retract or self.retracted)
                self._unretract()
                if path.kind != current_type:
                    self._emit(f";TYPE:{CURA_TYPES[path.kind]}")
                    current_type = path.kind
                before = self.stats.filament_mm
                self._extrude_polyline(pts, speed, e_per_mm)
                self.stats.tool_mm[path.tool] = self.stats.tool_mm.get(path.tool, 0.0) + self.stats.filament_mm - before
                done += length / speed
            self.stats.layer_times.append(self.stats.time_s - layer_start_time)
        self._retract()
        if self.cfs and not cfg["end_gcode"].strip():
            self._emit(creality_cfs_end(cfg, self.z))
        else:
            self._emit(render_template(cfg["end_gcode"].strip() or default_end_gcode(cfg), values))
        if progress_codes:
            self._emit("M73 P100 R0")

        st = self.stats
        st.layer_count = len(plan.layers)
        st.filament_mm += st.purge_mm
        st.filament_g = st.filament_mm * self.filament_area / 1000.0 * cfg["filament_density"]
        st.cost = st.filament_g / 1000.0 * cfg["filament_cost"]
        if cfg["gcode_header"] == "creality":
            return self._creality_file(plan, body, job_name, thumbnails)
        tools = sorted(st.tool_mm) or [first_tool]
        header = [
            f";FLAVOR:{FLAVOR_NAMES.get(flavor, 'Marlin')}",
            f";TIME:{int(st.time_s)}",
            f";Filament used: {st.filament_mm / 1000:.4f}m",
            f";Layer height: {cfg['layer_height']}",
            f";LAYER_COUNT:{st.layer_count}",
            f";Generated with UniSlicer {__version__}",
            f";Job: {job_name}",
            f";Printer bed: {cfg['bed_x']} x {cfg['bed_y']} mm, nozzle {cfg['nozzle_diameter']} mm, material {cfg['material_name']}",
        ]
        if plan.multi_material:
            header.append(";Filaments: " + ", ".join(f"T{f.index} {f.type} {f.colour}" for f in self.filaments
                                                    if f.index in tools))
        header.append("")
        if thumbnails:
            header = [thumbnails] + header
        footer = [
            "",
            f"; filament used [mm] = {', '.join(f'{st.tool_mm.get(t, 0.0):.2f}' for t in tools) if plan.multi_material else f'{st.filament_mm:.2f}'}",
            f"; filament used [g] = {st.filament_g:.2f}",
            f"; estimated printing time (normal mode) = {format_duration(st.time_s)}",
        ]
        if plan.multi_material:
            footer.append(f"; filament_colour = {';'.join(f.colour for f in self.filaments)}")
            footer.append(f"; filament_type = {';'.join(f.type for f in self.filaments)}")
        return "\n".join(header + body + footer) + "\n"

    def _creality_file(self, plan: FdmPlan, body: list[str], job_name: str, thumbnails: str) -> str:
        """Creality Print's file layout (K series, SPARKX): header block, bounds, thumbnails, the commands, then the
        per-filament totals and a config block with each filament's colour and type for the printer's CFS mapping."""
        import time as _time
        cfg, st = self.cfg, self.stats
        n_fil = len(self.filaments)
        area = self.filament_area
        mm = [st.tool_mm.get(i, 0.0) for i in range(n_fil)]
        cm3 = [v * area / 1000.0 for v in mm]
        grams = [v * cfg["filament_density"] for v in cm3]
        cost = [g / 1000.0 * cfg["filament_cost"] for g in grams]
        pts = [p.points for layer in plan.layers for p in layer.paths if len(p.points)]
        if pts:
            allp = np.vstack(pts)
            (minx, miny), (maxx, maxy) = allp.min(axis=0) + (self.ox, self.oy), allp.max(axis=0) + (self.ox, self.oy)
        else:
            minx = miny = maxx = maxy = 0.0
        max_z = plan.layers[-1].z if plan.layers else 0.0
        stamp = _time.strftime("%Y-%m-%d at %H:%M:%S")
        dens = ",".join(f"{cfg['filament_density']:g}" for _ in range(n_fil))
        diam = ",".join(f"{cfg['filament_diameter']:g}" for _ in range(n_fil))
        head = [
            "; HEADER_BLOCK_START",
            f"; generated by UniSlicer {__version__} on {stamp}",
            f"; total layer number: {st.layer_count}",
            f"; filament_density: {dens}",
            f"; filament_diameter: {diam}",
            f"; max_z_height: {max_z:.2f}",
            "; HEADER_BLOCK_END",
            "",
            f"; MINX = {minx:.2f}", f"; MINY = {miny:.2f}", "; MINZ = 0.00",
            f"; MAXX = {maxx:.2f}", f"; MAXY = {maxy:.2f}", f"; MAXZ = {max_z:.2f}",
            f"; OBJECTS_BOUNDING = {minx:.2f},{miny:.2f},0.00,{maxx:.2f},{maxy:.2f},{max_z:.2f}",
            "; WIPE_TOWER_BOUNDING = 0.00,0.00,0.00,0.00,0.00,0.00",
            "",
        ]
        if self.cfs:
            head += ["; multicolor_method = 1 ", ""]
        if thumbnails:
            head += [thumbnails]
        head += ["; EXECUTABLE_BLOCK_START", f";TIME:{int(st.time_s)}", f";Job: {job_name}"]
        temps = ",".join(str(f.temp) for f in self.filaments)
        first_temps = ",".join(str(f.first_temp) for f in self.filaments)
        tail = [
            "; EXECUTABLE_BLOCK_END",
            "",
            "; filament used [mm] = " + ", ".join(f"{v:.2f}" for v in mm),
            "; filament used [cm3] = " + ", ".join(f"{v:.2f}" for v in cm3),
            "; filament used [g] = " + ", ".join(f"{v:.2f}" for v in grams),
            "; filament cost = " + ", ".join(f"{v:.2f}" for v in cost),
            f"; total filament used [g] = {sum(grams):.2f}",
            f"; total filament cost = {sum(cost):.2f}",
            f"; total filament change = {st.toolchanges}",
            f"; total layers count = {st.layer_count}",
            f"; estimated printing time (normal mode) = {format_duration(st.time_s)}",
            "",
            "; CONFIG_BLOCK_START",
            f"; printer_model = {cfg['printer_model'] or cfg.get('printer_name', '')}",
            f"; filament_colour = {';'.join(f.colour for f in self.filaments)}",
            f"; filament_type = {';'.join(f.type for f in self.filaments)}",
            "; filament_settings_id = " + ";".join(f'"Generic {f.type}"' for f in self.filaments),
            f"; filament_diameter = {diam}",
            f"; filament_density = {dens}",
            f"; nozzle_temperature = {temps}",
            f"; nozzle_temperature_initial_layer = {first_temps}",
            f"; bed_temperature = {cfg['bed_temp']}",
            f"; bed_temperature_initial_layer = {cfg['first_layer_bed_temp']}",
            f"; layer_height = {cfg['layer_height']}",
            f"; initial_layer_print_height = {cfg['first_layer_height']}",
            f"; nozzle_diameter = {cfg['nozzle_diameter']}",
            f"; single_extruder_multi_material = {1 if n_fil > 1 or self.cfs else 0}",
            "; enable_prime_tower = 0",
            f"; multicolor_method = {1 if self.cfs else 0}",
            f"; thumbnails = {cfg['thumbnails']}",
            "; CONFIG_BLOCK_END",
        ]
        return "\n".join(head + body + tail) + "\n"

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


def generate(plan: FdmPlan, cfg: Config, job_name: str = "print", meshes: list | None = None,
             tools: list[int] | None = None) -> tuple[str, GcodeStats]:
    """G-code for ``plan``. With ``meshes`` (and their filament ``tools``) and a ``thumbnails`` setting,
    preview images for the printer's screen are embedded, drawn in the filament colours."""
    thumbs = ""
    if meshes and cfg["thumbnails"].strip():
        from . import thumbnail
        flist = fil.filament_list(cfg)
        tools = tools or [0] * len(meshes)
        colours = [fil.rgb(flist[t].colour if t < len(flist) else "#CCCCCC") for t in tools]
        try:
            thumbs = thumbnail.gcode_blocks(meshes, colours, cfg["thumbnails"])
        except Exception:          # a picture must never stop the print file
            thumbs = ""
    writer = GcodeWriter(cfg)
    text = writer.write(plan, job_name, thumbs)
    return text, writer.stats
