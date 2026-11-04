"""Dental CAD integration (3Shape, exocad, Medit): files arrive from the CAD and are prepared by indication.

How Phrozen DS Slicer works with 3Shape: the printer is a production target in 3Shape (Produce / Unite), the
designs are handed to the slicer, and the slicer orients and supports each part by its dental indication with
validated resin settings. UniSlicer does the same with what every CAD can do without a plug-in:

- a watched folder: 3Shape / exocad / Medit export (or "send to") into it, and every new model is imported;
- "Open with UniSlicer" / "Send to": files handed to UniSlicer while it is running join the open plate;
- the indication (model, die, crown/bridge, splint, surgical guide, denture...) is read from the file name and,
  when present, from the 3Shape order file (*.xml) next to it, and decides orientation, supports and whether
  the part sits flat on the plate.

Only the indication words are read from an order file; patient details are never read or stored."""
from __future__ import annotations

import os
import re

MODEL_EXTS = (".stl", ".3mf", ".obj", ".ply", ".amf")

# indication -> words that name it in file names and CAD order files (3Shape, exocad, Medit, common lab use)
INDICATIONS = (
    ("denture", ("denture", "full denture", "partial denture", "gingiva", "denturebase", "denture base",
                 "dentureteeth", "monolithic")),
    ("guide", ("surgical guide", "surgicalguide", "implant guide", "drill guide", "guide")),
    ("splint", ("splint", "nightguard", "night guard", "occlusal", "bite", "bitesplint", "mouthguard",
                "retainer", "tray", "custom tray", "aligner")),
    ("crown", ("crown", "coping", "bridge", "pontic", "inlay", "onlay", "veneer", "provisional", "temporary",
               "temp", "anatomic", "telescope", "abutment", "framework")),
    # 3Shape's model builder names the removable die of a prepared tooth "..._Tooth_14"
    ("die", ("die", "ditched", "stump", "tooth")),
    ("model", ("model", "upperjaw", "lowerjaw", "upper jaw", "lower jaw", "maxilla", "mandible", "antagonist", "antag",
               "preparation", "raw", "scan", "arch", "study", "ortho", "base")),
)
LABEL = {"model": ("מודל", "Model"), "die": ("מודל גדם (Die)", "Die"), "crown": ("כתר / גשר", "Crown / bridge"),
         "splint": ("סד / שומר לילה", "Splint / night guard"), "guide": ("מדריך כירורגי", "Surgical guide"),
         "denture": ("תותבת", "Denture"), "unknown": ("לא זוהה", "Not recognised")}


def _words(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)            # UpperJaw -> Upper Jaw
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def detect_indication(path: str, order_text: str = "") -> str:
    """The dental indication of a design file: from its name first, then the CAD order file."""
    for source in (os.path.splitext(os.path.basename(path))[0], order_text):
        w = _words(source)
        squashed = w.replace(" ", "")
        for kind, keys in INDICATIONS:
            for k in keys:
                kw = " " + k + " "
                if kw in w or (len(k) > 5 and " " not in k and k in squashed):
                    return kind
    return "unknown"


def order_text_for(path: str) -> str:
    """Indication-related text of a 3Shape / exocad order file beside the design (patient data left out)."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith((".xml", ".dentalproject", ".constructioninfo"))]
    except OSError:
        return ""
    keep = []
    for n in names[:5]:
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8", errors="ignore") as fh:
                raw = fh.read(400_000)
        except OSError:
            continue
        # only element/attribute values that describe the work, never names, dates or IDs
        for m in re.finditer(r"<(\w*(?:Type|Indication|Material|Class|Restoration|Product|Item)\w*)[^>]*>([^<]{1,80})</",
                             raw, re.I):
            keep.append(m.group(2))
        for m in re.finditer(r'(?:type|indication|material|class|restorationtype)="([^"]{1,80})"', raw, re.I):
            keep.append(m.group(1))
    return " ".join(keep)


def is_model_file(path: str) -> bool:
    return path.lower().endswith(MODEL_EXTS) and not os.path.basename(path).startswith(("~", "."))


class FolderTracker:
    """Which model files in a folder are new and finished being written (same size on two looks)."""

    def __init__(self, seen=None):
        self.seen = set(seen or [])
        self._sizes: dict[str, int] = {}

    def poll(self, folder: str) -> list[str]:
        ready = []
        try:
            entries = [os.path.join(folder, n) for n in os.listdir(folder)]
        except OSError:
            return []
        for p in sorted(entries):
            if not os.path.isfile(p) or not is_model_file(p):
                continue
            key = f"{p}|{os.path.getmtime(p):.0f}"
            if key in self.seen:
                continue
            size = os.path.getsize(p)
            if size > 0 and self._sizes.get(p) == size:
                ready.append(p)
                self.seen.add(key)
                self._sizes.pop(p, None)
            else:
                self._sizes[p] = size
        return ready

    def mark_existing(self, folder: str) -> None:
        """Files already there when watching starts are not imported (only new arrivals are)."""
        try:
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                if os.path.isfile(p) and is_model_file(p):
                    self.seen.add(f"{p}|{os.path.getmtime(p):.0f}")
        except OSError:
            pass


def indication_from_shape(mesh) -> str:
    """When the file name says nothing (e.g. a lab code like FYQ-SOD260046892-044-P-16-16-1-00): guess from
    the shape. Longer than 40 mm: a model (arch / antagonist); a single tooth that is solid through most of
    its height: a die; anything else tooth-sized: a crown or bridge (thin walls over a hollow fitting
    surface)."""
    import numpy as np
    lo, hi = mesh.bounds
    size = np.sort(hi - lo)[::-1]
    if size[0] > 40.0:
        return "model"
    if size[0] < 20.0 and _solid_fraction(mesh) > 0.6:
        return "die"
    return "crown"


def _solid_fraction(mesh) -> float:
    """Median share of the height that is solid along vertical rays through the middle of the part, taken
    along the part's tallest axis (a crown is ~0.1-0.3: thin walls over its cavity; a die ~0.8)."""
    import numpy as np
    from .mesh import Mesh
    from .sla_supports import _Caster
    lo, hi = mesh.bounds
    axis = int(np.argmax(hi - lo)) if (hi - lo)[2] < 0.5 * float(np.max(hi - lo)) else 2
    perm = [(axis + 1) % 3, (axis + 2) % 3, axis]
    m = Mesh(np.asarray(mesh.vertices)[:, perm], mesh.faces)
    lo, hi = m.bounds
    height = float(hi[2] - lo[2]) or 1.0
    caster = _Caster(m)
    ratios = []
    for x in np.linspace(lo[0], hi[0], 12)[2:-2]:
        for y in np.linspace(lo[1], hi[1], 12)[2:-2]:
            z, _ = caster.hits(x, y)
            if len(z) >= 2 and len(z) % 2 == 0:
                ratios.append(float(np.sum(z[1::2] - z[0::2])) / height)
    return float(np.median(ratios)) if ratios else 0.0
