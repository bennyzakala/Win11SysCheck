"""Dental shades for multi-material (PolyJet / TrueDent) dentures, crowns and bridges.

Tooth shades follow the VITA classical scale with the "T" prefix GrabCAD Print uses for TrueDent
(A1 -> TA1), including the darker shades added in 2025 (TA3.5-TD4). Gingiva (denture base) shades are
the usual clinical range from light pink to dark ethnic tones plus a light brown, each in four
translucency levels. The display colours are sRGB approximations for the screen only; what prints is
the shade name, which GrabCAD Print maps to its own formulations. Names live in this one table so they
can be matched exactly to a lab's GrabCAD library later.

A denture that arrives as one file usually holds the base and every tooth as separate shells:
:func:`split_denture` separates them, keeps the base as gingiva and numbers the teeth along the arch."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .mesh import Mesh


@dataclass(frozen=True)
class Shade:
    code: str              # the name written to the 3MF material (what GrabCAD Print sees)
    kind: str              # tooth | gingiva
    rgb: tuple             # display colour (0-255)
    he: str = ""
    en: str = ""


def _t(code, rgb, group):
    return Shade(code, "tooth", rgb, f"{code} ({group})", f"{code} ({group})")


TOOTH_SHADES = [
    # VITA classical: A reddish-brown, B reddish-yellow, C grey, D reddish-grey; plus bleach shades
    _t("TBL1", (247, 244, 236), "Bleach"), _t("TBL2", (245, 240, 228), "Bleach"),
    _t("TA1", (240, 230, 208), "A"), _t("TA2", (235, 220, 192), "A"), _t("TA3", (229, 208, 174), "A"),
    _t("TA3.5", (222, 197, 158), "A"), _t("TA4", (210, 182, 140), "A"),
    _t("TB1", (243, 235, 215), "B"), _t("TB2", (237, 225, 196), "B"), _t("TB3", (229, 209, 168), "B"),
    _t("TB4", (223, 199, 152), "B"),
    _t("TC1", (228, 220, 200), "C"), _t("TC2", (219, 207, 181), "C"), _t("TC3", (207, 192, 164), "C"),
    _t("TC4", (192, 172, 142), "C"),
    _t("TD2", (228, 216, 194), "D"), _t("TD3", (218, 202, 176), "D"), _t("TD4", (214, 198, 166), "D"),
]

GINGIVA_BASE = [
    ("Light Pink", "ורוד בהיר", (242, 168, 176)),
    ("Pink", "ורוד", (232, 142, 152)),
    ("Medium Pink", "ורוד בינוני", (218, 118, 130)),
    ("Dark Pink", "ורוד כהה", (198, 92, 106)),
    ("Red Pink", "ורוד-אדום", (185, 71, 90)),
    ("Veined Pink", "ורוד עם ורידים", (217, 128, 138)),
    ("Clear Pink", "ורוד שקוף", (240, 194, 198)),
    ("Light Ethnic", "אתני בהיר", (185, 122, 110)),
    ("Medium Ethnic", "אתני בינוני", (156, 93, 83)),
    ("Dark Ethnic", "אתני כהה", (122, 68, 64)),
    ("Light Brown", "חום בהיר", (168, 122, 92)),
]
TRANSLUCENCY = (1, 2, 3, 4)          # 1 = most opaque ... 4 = most translucent


def gingiva_code(name: str, level: int = 2) -> str:
    return f"G {name} T{int(level)}"


GINGIVA_SHADES = [Shade(gingiva_code(n, t), "gingiva", rgb, f"{he} T{t}", f"{n} T{t}")
                  for n, he, rgb in GINGIVA_BASE for t in TRANSLUCENCY]
ALL = {s.code: s for s in TOOTH_SHADES + GINGIVA_SHADES}
INCISAL = ("off", "low", "medium", "high")      # incisal translucency (TrueVoxel, GrabCAD Print 1.115+)

DEFAULT_TOOTH = "TA2"
DEFAULT_GINGIVA = gingiva_code("Pink", 2)


def find(code: str) -> Shade | None:
    """The shade for a material name, accepting the usual spellings: 'TA2', 'A2', 'VITA A2', 'a2'."""
    if not code:
        return None
    if code in ALL:
        return ALL[code]
    key = code.strip().upper().replace("VITA", "").replace(" ", "").replace("-", "")
    for s in TOOTH_SHADES:
        bare = s.code[1:]                     # 'TA2' -> 'A2'
        if key in (s.code.upper(), bare.upper(), "T" + bare.upper()):
            return s
    low = code.strip().lower()
    for s in GINGIVA_SHADES:
        if low == s.code.lower() or low == s.en.lower():
            return s
    return None


def display_rgb(code: str) -> tuple | None:
    s = find(code)
    if s is None:
        return None
    r, g, b = s.rgb
    if s.kind == "gingiva":             # more translucent: a little lighter on screen
        level = int(s.code.rsplit("T", 1)[-1]) if s.code[-1].isdigit() else 2
        mix = (level - 1) * 0.07
        r, g, b = (int(c + (255 - c) * mix) for c in (r, g, b))
    return (r / 255.0, g / 255.0, b / 255.0)


def hex_color(code: str) -> str:
    s = find(code)
    if s is None:
        return "#C8C8C8FF"
    r, g, b = (int(round(c * 255)) for c in display_rgb(code))
    return f"#{r:02X}{g:02X}{b:02X}FF"


# ---------------------------------------------------------------- denture regions
def denture_regions(mesh: Mesh) -> list[dict]:
    """Regions of a denture saved as one mesh with separate shells: the base (gingiva) and each tooth.

    The part stays one model (it moves, rotates and is laid out as a whole); each region is a set of its
    faces with its own shade. The base is the shell with the largest footprint; shells smaller than 2 % of
    the median tooth stay with the base (scan crumbs). Teeth are numbered along the arch.
    Returns [] when the mesh is a single shell."""
    from .mesh_tools import shell_labels
    labels = shell_labels(mesh)
    n = int(labels.max()) + 1 if len(labels) else 0
    if n < 2:
        return []
    tris = mesh.triangles
    shells = []
    for k in range(n):
        idx = np.flatnonzero(labels == k)
        pts = tris[idx].reshape(-1, 3)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        shells.append({"faces": idx, "footprint": (hi[0] - lo[0]) * (hi[1] - lo[1]), "centre": pts.mean(axis=0)})
    shells.sort(key=lambda r: -r["footprint"])
    base, rest = shells[0], shells[1:]
    med = float(np.median([len(r["faces"]) for r in rest]))
    teeth = [r for r in rest if len(r["faces"]) >= 0.02 * med]
    crumbs = [r for r in rest if len(r["faces"]) < 0.02 * med]
    base_faces = np.sort(np.concatenate([base["faces"]] + [r["faces"] for r in crumbs]))
    c = base["centre"]
    ang = np.array([math.atan2(r["centre"][1] - c[1], r["centre"][0] - c[0]) for r in teeth])
    order = np.argsort(ang)
    a = ang[order]
    gaps = np.diff(np.r_[a, a[0] + 2 * math.pi])         # the open side of the arch is the biggest gap
    start = (int(np.argmax(gaps)) + 1) % len(a)
    order = np.r_[order[start:], order[:start]]
    regions = [{"name": "Gingiva", "faces": base_faces, "shade": DEFAULT_GINGIVA}]
    regions += [{"name": f"Tooth {k + 1}", "faces": np.sort(teeth[i]["faces"]), "shade": DEFAULT_TOOTH}
                for k, i in enumerate(order)]
    return regions


def face_shades(n_faces: int, material: str, regions: list) -> list:
    """Per face, the shade / material name (regions override the part's own material)."""
    out = [material] * n_faces
    for r in regions or []:
        for f in r["faces"]:
            out[int(f)] = r["shade"]
    return out
