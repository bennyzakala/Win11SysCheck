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
    # gingiva mask (3Shape: "..._Tissue_17"), before denture: "gingiva mask" must not read as a denture's gingiva
    ("gingiva", ("tissue", "soft tissue", "gingiva mask", "gingivamask", "gingival mask", "gum mask", "gummask",
                 "gingiva shell")),
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
LABEL = {"model": ("מודל", "Model"), "die": ("מודל גדם (Die)", "Die"), "gingiva": ("מסכת חניכיים", "Gingiva mask"), "crown": ("כתר / גשר", "Crown / bridge"),
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
    # a lab order name with a tooth range and a unit count and nothing else said ("...-044-P-14-24-8-A3-",
    # teeth 14 to 24, 8 units): the restoration itself - a crown or bridge, however long the bridge
    if _TOOTH_RANGE.search(os.path.basename(path)):
        return "crown"
    return "unknown"


_FDI = r"(?:[1-4][1-8]|[5-8][1-5])"
_TOOTH_RANGE = re.compile(rf"-{_FDI}-{_FDI}-\d{{1,2}}(?=[-_.]|$)")


def order_text_for(path: str) -> str:
    """Indication-related text of the 3Shape / exocad order file that belongs to this design (patient data
    left out): the only order file in the design's folder (a case folder), or, in a folder that collects
    several cases, the one named like the design - its name starts the design's name, or it carries the
    design's case code - so one case's order (say, a crown) is not lent to another case's model."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith((".xml", ".dentalproject", ".constructioninfo")))
    except OSError:
        return ""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    case = re.split(r"[_\-\s.]", stem)[0]

    def belongs(n: str) -> bool:
        order = os.path.splitext(n)[0].lower()
        if len(names) == 1:
            return True                          # a case folder with its one order file (as 3Shape exports)
        return bool(order) and (stem.startswith(order) or (len(case) >= 4 and case in order))
    keep = []
    for n in [n for n in names if belongs(n)][:5]:
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


def case_code(name: str) -> str:
    """The short case code a lab writes on a part, from the SOD order number in the file name: the last five
    digits of the SOD number and the two groups after it - "SMG-SOD260045198-N183-E-16-16-1-A35_Antag" ->
    "45198-N183-E", "DSY-SOD260046671-044-P-46-46-1-A35" -> "46671-044-P". "" when there is no SOD number."""
    m = re.search(r"SOD\d*?(\d{5})-([A-Za-z0-9]+)(?:-([A-Za-z0-9]+))?", os.path.basename(name or ""), re.IGNORECASE)
    if not m:
        return ""
    return "-".join(g for g in (m.group(1), m.group(2), m.group(3)) if g)


_KIND_CACHE: dict = {}


OPENING_ERRORS: list = []      # the last few failures of opening_direction (shown by the tests)


def opening_direction(mesh):
    """Which way the opening (fitting surface) of a crown or bridge faces, as a unit axis (+-X, +-Y, +-Z),
    or None when there is no clear hollow. Designs come from the CAD along their insertion axis, so the
    answer is the axis the cavity runs along:

    - cross-sections across that axis are rings: the part's outline with the cavity as a hole in it; across
      the other two axes they are not;
    - along it the hole is widest at the opening and narrows to the inside of the biting surface, so the
      side where it is wider is the opening;
    - when that is not clear (a shallow cavity, a hole in one or two cuts only), rays from deep inside the
      hollow say which way they get out. Cached per mesh."""
    key = ("open", id(mesh))
    hit = _KIND_CACHE.get(key)
    if hit is None or hit[0] is not mesh:
        try:
            d = _opening_direction(mesh.decimated(6000))
        except Exception as exc:                 # no answer: the part is left as it is
            OPENING_ERRORS[:] = (OPENING_ERRORS + [repr(exc)])[-5:]
            d = None
        hit = _KIND_CACHE[key] = (mesh, d)
    return None if hit[1] is None else hit[1].copy()


def opening_directions(meshes) -> list:
    """opening_direction for several parts at once, on several threads (the cuts run in compiled code)."""
    meshes = list(meshes)
    if len(meshes) < 2:
        return [opening_direction(m) for m in meshes]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, len(meshes), os.cpu_count() or 1)) as ex:
        return list(ex.map(opening_direction, meshes))


def _hole_profile(mesh, axis: int, n: int = 20):
    """For each of ``n`` cross-sections across ``axis`` (low end first): the share of the outline that is hole,
    and the hole's area (mm2)."""
    import numpy as np
    from shapely.geometry import Polygon
    from .mesh import Mesh
    from .slicing import slice_mesh
    perm = [(axis + 1) % 3, (axis + 2) % 3, axis]               # that axis becomes the height
    v = np.asarray(mesh.vertices)[:, perm]
    m = Mesh(v, mesh.faces)
    lo, hi = float(v[:, 2].min()), float(v[:, 2].max())
    zs = lo + (hi - lo) * (np.arange(n) + 0.5) / n
    vz = np.unique(np.round(v[:, 2], 6))
    near = np.abs(vz[np.clip(np.searchsorted(vz, zs), 0, len(vz) - 1)] - zs) < 1e-4
    zs[near] += 3e-4 * (hi - lo) + 1e-3                         # not exactly through a row of vertices
    share, area = np.zeros(n), np.zeros(n)
    for i, g in enumerate(slice_mesh(m, zs)):
        try:
            polys = [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]
            filled = sum(Polygon(p.exterior).area for p in polys)
            if filled > 0:
                area[i] = max(0.0, filled - sum(p.area for p in polys))
                share[i] = area[i] / filled
        except Exception:                                        # an odd cut: leave it out
            continue
    return share, area


def _opening_direction(mesh):
    import numpy as np
    # CAD designs mostly stand along Z: holes in the cuts across Z settle it (a crown lying on its side cut
    # across Z gives open C shapes, no hole); only otherwise are X and Y cut too
    z = _hole_profile(mesh, 2)
    if float(z[0].sum()) >= 1.0 and np.count_nonzero(z[0] > 0.03) >= 3:
        axis, (prof, area) = 2, z
    else:
        profiles = [_hole_profile(mesh, 0), _hole_profile(mesh, 1), z]
        totals = [float(p[0].sum()) for p in profiles]
        axis = int(np.argmax(totals))
        prof, area = profiles[axis]
        if totals[axis] < 0.5:
            return None                                          # no hollow: a solid part, a die
    run = np.nonzero(prof > 0.03)[0]
    if len(run) == 0:
        return None
    sign = 0.0
    if len(run) >= 3:
        # the hole is widest at the opening and narrows toward the biting side: measured both as its share of
        # the outline and as its own area (the share also grows where the outside rounds off, the area also
        # where a bridge's units join) - when the two agree, that settles it
        x = (run - run.mean()) / max(1.0, float(run.max() - run.min()))
        slopes = [float(np.polyfit(x, v[run] / max(float(v[run].max()), 1e-9), 1)[0]) for v in (prof, area)]
        if all(abs(k) > 0.1 for k in slopes) and slopes[0] * slopes[1] > 0:
            sign = 1.0 if slopes[0] > 0 else -1.0               # wider toward the high end: it opens there
    if sign == 0.0:
        up, down = _escape_votes(mesh, axis)
        if max(up, down) < 0.15 or abs(up - down) < 0.15:
            return None
        sign = 1.0 if up > down else -1.0
    d = np.zeros(3)
    d[axis] = sign
    return d


def _escape_votes(mesh, axis: int, voxel: float = 0.3, samples: int = 700, reach: float = 14.0):
    """Shares of the ways out of the hollow that point along +axis and -axis: from spots deep inside the part
    (blocked in most directions), which open directions are left (rays against a voxel copy of the surface)."""
    import math
    import numpy as np
    from .orient import _directions
    t = mesh.triangles
    v = np.asarray(mesh.vertices)
    pts = np.vstack([v, t.mean(axis=1), (t[:, 0] + t[:, 1]) / 2, (t[:, 1] + t[:, 2]) / 2, (t[:, 2] + t[:, 0]) / 2])
    lo = pts.min(axis=0) - 2 * voxel
    idx = np.floor((pts - lo) / voxel).astype(np.int64)
    shape = idx.max(axis=0) + 3
    g = np.zeros(shape, dtype=bool)
    g[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    grid = g.copy()
    for ax in range(3):                                          # close the gaps between the samples
        for s in (1, -1):
            grid |= np.roll(g, s, axis=ax)
    n, a, c = mesh.face_normals(), mesh.face_areas(), t.mean(axis=1)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(a), min(samples, len(a)), replace=False, p=a / a.sum())
    dirs = _directions(200)
    steps = np.arange(1.2, reach, voxel * 0.8)
    e_axis = np.eye(3)[axis]
    cone = math.cos(math.radians(45))
    up = down = 0.0
    count = 0
    for f in pick:
        hemi = dirs[dirs @ n[f] > 0.05]
        q = np.floor((c[f][None, None, :] + hemi[:, None, :] * steps[None, :, None] - lo) / voxel).astype(np.int64)
        inside = np.all((q >= 0) & (q < shape), axis=2)
        q = np.clip(q, 0, shape - 1)
        blocked = (grid[q[..., 0], q[..., 1], q[..., 2]] & inside).any(axis=1)
        if blocked.mean() <= 0.6 or blocked.all():
            continue
        out = hemi[~blocked] @ e_axis
        up += float((out > cone).mean())
        down += float((out < -cone).mean())
        count += 1
    if count < 0.05 * len(pick):
        return 0.0, 0.0
    return up / count, down / count


def model_type_of(name: str, mesh) -> str:
    """Supports > Model type for a part: crown / bridge, guide, model or horseshoe model, splint, denture -
    from its file name (3Shape / exocad words, a lab code's tooth range); "" when the name says nothing, so
    any other part keeps the support settings as they are."""
    from .settings import KIND_MODEL_TYPE
    kind = detect_indication(name or "")       # the name only: a part that says nothing keeps the plate's supports
    t = KIND_MODEL_TYPE.get(kind, "")
    if t == "model" and kind == "model" and mesh is not None and _horseshoe(mesh):
        return "model_u"
    return t


def _horseshoe(mesh) -> bool:
    """A U-shaped arch with no palate or base plate between its ends: its outline seen from above fills
    well under two thirds of its own convex hull (cached per mesh)."""
    key = ("u", id(mesh))
    hit = _KIND_CACHE.get(key)
    if hit is None or hit[0] is not mesh:
        try:
            from .layout import _raster_outline
            outline = _raster_outline(mesh)
            u = bool(outline.area < 0.62 * outline.convex_hull.area)
        except Exception:
            u = False
        hit = _KIND_CACHE[key] = (mesh, u)
    return hit[1]


def kind_of(name: str, mesh) -> str:
    """Indication of a part: from its name, else from its shape (cached per mesh)."""
    kind = detect_indication(name or "")
    if kind != "unknown":
        return kind
    key = id(mesh)
    hit = _KIND_CACHE.get(key)
    if hit is None or hit[0] is not mesh:
        if len(_KIND_CACHE) > 256:
            _KIND_CACHE.clear()
        hit = _KIND_CACHE[key] = (mesh, indication_from_shape(mesh))
    return hit[1]
