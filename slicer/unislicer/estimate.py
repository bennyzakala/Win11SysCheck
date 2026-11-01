"""Quick estimates per model and for the whole plate (time, material, weight, cost), without slicing - as
GrabCAD Print's "Estimate". A sliced result, when there is one, calibrates the totals so the per-model
split adds up to what the printer will really use."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .settings import Config


@dataclass
class Row:
    name: str
    model_ml: float            # resin ml, or filament cm³ for FDM
    support_ml: float
    weight_g: float
    cost: float
    height: float              # mm, as printed (with elevation)

    @property
    def total_ml(self) -> float:
        return self.model_ml + self.support_ml


@dataclass
class PlateEstimate:
    technology: str
    rows: list = field(default_factory=list)
    time_s: float = 0.0
    layers: int = 0
    calibrated: bool = False   # totals taken from a sliced result

    @property
    def total_ml(self) -> float:
        return sum(r.total_ml for r in self.rows)

    @property
    def weight_g(self) -> float:
        return sum(r.weight_g for r in self.rows)

    @property
    def cost(self) -> float:
        return sum(r.cost for r in self.rows)

    def csv(self) -> str:
        unit = "ml" if self.technology == "sla" else "cm3"
        out = [f"model,model_{unit},support_{unit},total_{unit},weight_g,cost,height_mm"]
        for r in self.rows:
            out.append(f"\"{r.name}\",{r.model_ml:.2f},{r.support_ml:.2f},{r.total_ml:.2f},{r.weight_g:.1f},{r.cost:.2f},{r.height:.1f}")
        out.append(f"\"TOTAL\",,,{self.total_ml:.2f},{self.weight_g:.1f},{self.cost:.2f},")
        out.append(f"\"print time (h:mm)\",{int(self.time_s // 3600)}:{int(self.time_s % 3600 // 60):02d},layers,{self.layers}")
        return "\n".join(out)


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def _hollowed_ml(volume_mm3: float, area_mm2: float, cfg: Config) -> float:
    if not (cfg["hollow_enable"] or cfg["hollow_infill"] != "none"):
        return volume_mm3 / 1000.0
    shell = min(volume_mm3, area_mm2 * cfg["hollow_wall"])
    infill = 0.0
    if cfg["hollow_infill"] != "none":           # honeycomb / grid walls fill about thickness / spacing
        infill = (volume_mm3 - shell) * min(1.0, 2 * cfg["hollow_infill_thickness"] / max(cfg["hollow_infill_spacing"], 0.5))
    return (shell + infill) / 1000.0


def plate(objects: list, cfg: Config, supports: dict | None = None, lift: float = 0.0, result=None) -> PlateEstimate:
    """Per-model estimate. ``supports`` maps an object's uid to its support mesh (resin), if any;
    ``result`` is the sliced result tuple from the main window, used to calibrate the totals."""
    tech = cfg["technology"]
    est = PlateEstimate(tech)
    parts = [o for o in objects if o.role != "cutter"]
    if not parts:
        return est
    top = 0.0
    for o in parts:
        m = o.world_mesh()
        vol = float(m.volume())
        area = float(m.face_areas().sum())
        h = float(m.bounds[1][2]) + (0.0 if getattr(o, "direct", False) else lift)
        top = max(top, h)
        if tech == "sla":
            model_ml = _hollowed_ml(vol, area, o.config(cfg) if hasattr(o, "config") else cfg)
            s = (supports or {}).get(o.uid)
            sup_ml = float(s.volume()) / 1000.0 if s is not None and len(s.faces) else 0.0
            ml = model_ml + sup_ml
            est.rows.append(Row(o.name or "model", model_ml, sup_ml, ml * cfg["resin_density"],
                                ml / 1000.0 * cfg["resin_cost"], h))
        else:
            lw = cfg["line_width"] or cfg["nozzle_diameter"] * 1.125
            shell = min(vol, area * cfg["wall_count"] * lw * 0.5 +
                        area * 0.5 * (cfg["top_layers"] + cfg["bottom_layers"]) * cfg["layer_height"] * 0.5)
            cm3 = (shell + (vol - shell) * cfg["infill_density"] / 100.0) / 1000.0
            g = cm3 * cfg["filament_density"]
            est.rows.append(Row(o.name or "model", cm3, 0.0, g, g / 1000.0 * cfg["filament_cost"], h))
    if tech == "sla":
        lh = cfg["sla_layer_height"]
        est.layers = int(math.ceil(top / lh - 1e-9)) if lh > 0 else 0
        from .sla import print_time
        est.time_s = print_time(est.layers, cfg)
    else:
        lh = cfg["layer_height"]
        est.layers = int(math.ceil(top / lh - 1e-9)) if lh > 0 else 0
        lw = cfg["line_width"] or cfg["nozzle_diameter"] * 1.125
        flow = max(cfg["print_speed"] * 0.7 * lw * lh, 0.5)                 # mm³/s, travel and walls slower
        est.time_s = sum(r.model_ml for r in est.rows) * 1000.0 / flow + est.layers * 2.0
    if result is not None:
        _calibrate(est, result)
    return est


def _calibrate(est: PlateEstimate, result) -> None:
    """Scale the per-model rows so they add up to the sliced totals."""
    try:
        if result[0] == "sla" and est.technology == "sla":
            job = result[1]
            total, time_s, layers = job.volume_ml, job.time_s, job.layer_count
        elif result[0] == "fdm" and est.technology == "fdm":
            stats = result[3]
            total = stats.filament_g / max(result[5]["filament_density"], 0.1)
            time_s, layers = stats.time_s, stats.layer_count
        else:
            return
    except (AttributeError, IndexError, TypeError, KeyError):
        return
    have = est.total_ml
    if have > 0 and total > 0:
        k = total / have
        for r in est.rows:
            r.model_ml *= k
            r.support_ml *= k
            r.weight_g *= k
            r.cost *= k
    if time_s:
        est.time_s = float(time_s)
    if layers:
        est.layers = int(layers)
    est.calibrated = True
