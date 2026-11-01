"""Insert / void bodies (as GrabCAD Print Pro): a ready-made empty space cut into a model - a hole for a
die pin or an implant analog, a pocket for a magnet or a threaded insert, a vent. It is a cutter part
(subtracted from the models it overlaps), so it can be moved, copied and deleted like any part."""
from __future__ import annotations

from dataclasses import dataclass

from . import primitives
from .mesh import Mesh

SIDES = ("bottom", "top", "through")


@dataclass
class VoidSpec:
    shape: str = "cylinder"       # cylinder | box
    diameter: float = 2.0         # cylinder Ø, or box width (mm)
    length: float = 2.0           # box length (mm)
    depth: float = 10.0           # how deep into the model (mm)
    clearance: float = 0.1        # added to the size so the insert fits (mm)
    side: str = "bottom"          # bottom | top | through


# dental-lab presets: name -> spec
PRESETS = {
    "pin_hole": VoidSpec("cylinder", 2.0, 2.0, 10.0, 0.1, "bottom"),
    "magnet_6x3": VoidSpec("cylinder", 6.0, 6.0, 3.0, 0.15, "bottom"),
    "magnet_5x2": VoidSpec("cylinder", 5.0, 5.0, 2.0, 0.15, "bottom"),
    "analog_hole": VoidSpec("cylinder", 4.2, 4.2, 12.0, 0.05, "top"),
    "insert_m3": VoidSpec("cylinder", 4.0, 4.0, 5.0, 0.0, "bottom"),
    "vent": VoidSpec("cylinder", 1.5, 1.5, 0.0, 0.0, "through"),
    "slot": VoidSpec("box", 3.0, 10.0, 4.0, 0.1, "bottom"),
}


def body(spec: VoidSpec, model_height: float = 0.0) -> Mesh:
    """The cutter mesh. Through-holes run the whole model height (plus a margin); top pockets stick out
    0.5 mm above the surface so the cut is clean."""
    depth = model_height + 1.0 if spec.side == "through" else spec.depth + (0.5 if spec.side == "top" else 0.0)
    depth = max(depth, 0.2)
    if spec.shape == "box":
        w, l = spec.diameter + 2 * spec.clearance, spec.length + 2 * spec.clearance
        return primitives.box(w, l, depth, "Void")
    r = spec.diameter / 2 + spec.clearance
    return primitives.cylinder(max(r, 0.1), depth, 48, "Void")


def place(cutter, target_bounds, spec: VoidSpec, at_xy=None) -> None:
    """Put the cutter part at the target's centre (or ``at_xy``): from its underside up, from its top down,
    or right through it. ``target_bounds`` = world bounds of the model ((x0, y0, z0), (x1, y1, z1))."""
    (x0, y0, z0), (x1, y1, z1) = target_bounds
    cutter.x, cutter.y = at_xy if at_xy is not None else ((x0 + x1) / 2, (y0 + y1) / 2)
    if spec.side == "top":
        cutter.z = max(0.0, z1 - spec.depth)
    elif spec.side == "through":
        cutter.z = max(0.0, z0 - 0.5)
    else:
        cutter.z = max(0.0, z0)
    cutter.role = "cutter"
