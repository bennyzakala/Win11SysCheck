"""Vector slice formats and mesh packages for printers that do their own rasterising or scanning.

* SLC (3D Systems contour format): imported by Asiga Composer and many industrial SLA/DLP systems.
* CLI (Common Layer Interface, ASCII): contours and hatches for laser SLA machines.
* 3MF: the prepared plate (models and supports as separate objects, with material names) for
  GrabCAD Print (PolyJet), PreForm and other vendor software.
"""
from __future__ import annotations

import struct
import zipfile
from typing import Callable

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon

from . import __version__
from .infill import line_fill
from .mesh import Mesh
from .slicing import polygons_of

SLC_HEADER_MAX = 2048
SLC_END = 0xFFFFFFFF


def _rings(poly: Polygon):
    """Outer ring counter-clockwise, holes clockwise (seen from above), each closed (first == last)."""
    poly = shapely.geometry.polygon.orient(poly, 1.0)
    yield np.asarray(poly.exterior.coords)[:, :2], True
    for hole in poly.interiors:
        yield np.asarray(hole.coords)[:, :2], False


# ----------------------------------------------------------------------- SLC
def write_slc(path: str, layers: list[MultiPolygon], layer_z: list[float], layer_thickness: list[float],
              extents: tuple, kind: str = "PART", progress: Callable[[float], None] | None = None) -> str:
    """SLC 2.0: ASCII header (CR LF Ctrl-Z, at most 2048 bytes), 256 reserved bytes, sampling table,
    then per layer: min Z, boundary count, and per boundary vertex count, gap count and (x, y) floats.
    Little-endian; ends with the last Z and 0xFFFFFFFF."""
    (x0, y0, z0), (x1, y1, z1) = extents
    header = (f"-SLCVER 2.0 -UNIT MM -TYPE {kind} -PACKAGE UniSlicer {__version__} "
              f"-EXTENTS {x0:.4f},{x1:.4f} {y0:.4f},{y1:.4f} {z0:.4f},{z1:.4f}").encode("ascii") + b"\r\n\x1a"
    if len(header) > SLC_HEADER_MAX:
        raise ValueError("SLC header too long")
    # one sampling-table entry per run of equal layer thickness (max 256 entries)
    table, last = [], None
    for z, h in zip(layer_z, layer_thickness):
        if last is None or abs(h - last) > 1e-6:
            table.append((round(z - h, 6), h))
            last = h
    if len(table) > 255:
        table = [(0.0, float(np.median(layer_thickness)))]
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(bytes(256))
        fh.write(struct.pack("<B", len(table)))
        for zmin, h in table:
            fh.write(struct.pack("<4f", zmin, h, 0.0, 0.0))     # min Z, thickness, line width comp., reserved
        n = len(layers)
        for i, (geom, z, h) in enumerate(zip(layers, layer_z, layer_thickness)):
            rings = [r for poly in polygons_of(geom) for r, _ in _rings(poly) if len(r) >= 4]
            fh.write(struct.pack("<fI", z - h, len(rings)))
            for r in rings:
                fh.write(struct.pack("<II", len(r), 0))
                fh.write(r.astype("<f4").tobytes())
            if progress and i % 50 == 0:
                progress(i / max(1, n))
        fh.write(struct.pack("<fI", layer_z[-1] if layer_z else 0.0, SLC_END))
    return path


def read_slc(path: str) -> dict:
    """Parse an SLC file written by :func:`write_slc` (used by the tests)."""
    data = open(path, "rb").read()
    end = data.index(b"\r\n\x1a") + 3
    header = data[:end - 3].decode("ascii")
    pos = end + 256
    count = data[pos]
    pos += 1
    table = [struct.unpack_from("<4f", data, pos + 16 * k) for k in range(count)]
    pos += 16 * count
    layers = []
    while True:
        z, nb = struct.unpack_from("<fI", data, pos)
        pos += 8
        if nb == SLC_END:
            break
        rings = []
        for _ in range(nb):
            nv, _gaps = struct.unpack_from("<II", data, pos)
            pos += 8
            rings.append(np.frombuffer(data, "<f4", nv * 2, pos).reshape(-1, 2))
            pos += nv * 8
        layers.append((z, rings))
    return {"header": header, "table": table, "layers": layers}


# ----------------------------------------------------------------------- CLI
def hatch(geom, spacing: float, angle: float) -> list[np.ndarray]:
    """Straight scan vectors filling ``geom`` (each an array of two points)."""
    out = []
    for line in line_fill(geom, spacing, angle):
        for a, b in zip(line[:-1], line[1:]):
            out.append(np.array([a, b]))
    return out


def scan_geometry(geom, cfg) -> tuple[MultiPolygon, list[np.ndarray]]:
    """Laser contours offset by half the spot (beam compensation) and the hatch vectors inside them."""
    spot = cfg["laser_spot"]
    contour = geom.buffer(-spot / 2, join_style="mitre", mitre_limit=3) if spot > 0 else geom
    inner = contour.buffer(-spot * max(cfg["laser_contours"] - 1, 0)) if cfg["laser_contours"] > 1 else contour
    return contour, inner


def write_cli(path: str, layers: list[MultiPolygon], layer_z: list[float], cfg, extents: tuple,
              progress: Callable[[float], None] | None = None) -> str:
    """CLI (ASCII, units 1 mm): polylines (dir 1 = outer/CCW, 0 = inner/CW) and hatches per layer.
    The hatch angle turns by the set amount every layer."""
    (x0, y0, z0), (x1, y1, z1) = extents
    lines = ["$$HEADERSTART", "$$ASCII", "$$UNITS/1.000000", "$$VERSION/200",
             f"$$LABEL/1,UniSlicer {__version__}",
             f"$$DIMENSION/{x0:.4f},{y0:.4f},{z0:.4f},{x1:.4f},{y1:.4f},{z1:.4f}",
             f"$$LAYERS/{len(layers)}", "$$HEADEREND", "$$GEOMETRYSTART"]
    spacing = cfg["laser_hatch_spacing"]
    for i, (geom, z) in enumerate(zip(layers, layer_z)):
        lines.append(f"$$LAYER/{z:.4f}")
        if geom.is_empty:
            continue
        contour, inner = scan_geometry(geom, cfg)
        for k in range(max(int(cfg["laser_contours"]), 1)):
            ring_geom = contour if k == 0 else contour.buffer(-cfg["laser_spot"] * k)
            for poly in polygons_of(ring_geom):
                for ring, outer in _rings(poly):
                    pts = ",".join(f"{x:.4f},{y:.4f}" for x, y in ring)
                    lines.append(f"$$POLYLINE/1,{1 if outer else 0},{len(ring)},{pts}")
        if spacing > 0 and not inner.is_empty:
            vecs = hatch(inner, spacing, cfg["laser_hatch_angle"] + i * cfg["laser_hatch_rotation"])
            if vecs:
                pts = ",".join(f"{a[0]:.4f},{a[1]:.4f},{b[0]:.4f},{b[1]:.4f}" for a, b in vecs)
                lines.append(f"$$HATCHES/1,{len(vecs)},{pts}")
        if progress and i % 50 == 0:
            progress(i / max(1, len(layers)))
    lines.append("$$GEOMETRYEND")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def scan_length(geom, cfg, layer_index: int = 0) -> float:
    """Approximate laser path length of one layer (contours plus hatching), for time estimates."""
    if geom.is_empty:
        return 0.0
    contour = geom.buffer(-cfg["laser_spot"] / 2) if cfg["laser_spot"] > 0 else geom
    hatch_len = contour.area / cfg["laser_hatch_spacing"] if cfg["laser_hatch_spacing"] > 0 else 0.0
    return contour.length * max(int(cfg["laser_contours"]), 1) + hatch_len


# ----------------------------------------------------------------------- 3MF
def write_3mf(path: str, parts: list[tuple[str, Mesh, str]], colors: dict[str, str] | None = None,
              metadata: dict[str, str] | None = None) -> str:
    """3MF package with one object per part. ``parts`` = [(name, mesh in plate mm, material name)];
    parts with the same material share a base material so vendor software can map it."""
    materials = []
    for _, _, mat in parts:                       # a name, or one name per face (shaded regions)
        for m in (mat if isinstance(mat, list) else [mat]):
            if m and m not in materials:
                materials.append(m)
    colors = colors or {}
    esc = lambda t: (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))  # noqa: E731
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
           f'<metadata name="Application">UniSlicer {__version__}</metadata>']
    out += [f'<metadata name="{esc(k)}">{esc(str(v))}</metadata>' for k, v in (metadata or {}).items()]
    out.append("<resources>")
    if materials:
        out.append('<basematerials id="1">')
        for m in materials:
            out.append(f'<base name="{esc(m)}" displaycolor="{colors.get(m, "#C8C8C8FF")}"/>')
        out.append("</basematerials>")
    ids = []
    for k, (name, mesh, mat) in enumerate(parts):
        oid = k + 2
        ids.append(oid)
        per_face = isinstance(mat, list)
        main = max(set(mat), key=mat.count) if per_face and mat else mat
        attrs = f' pid="1" pindex="{materials.index(main)}"' if main else ""
        out.append(f'<object id="{oid}" name="{esc(name)}" type="model"{attrs}><mesh><vertices>')
        out.extend(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in mesh.vertices)
        out.append("</vertices><triangles>")
        if per_face:                               # material per triangle, as 3Shape writes denture shades
            idx = {m: materials.index(m) for m in set(mat) if m}
            out.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{idx[m]}"/>' if m and m != main
                       else f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for (a, b, c), m in zip(mesh.faces, mat))
        else:
            out.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
        out.append("</triangles></mesh></object>")
    out.append("</resources><build>")
    out.extend(f'<item objectid="{oid}"/>' for oid in ids)
    out.append("</build></model>")
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", "\n".join(out))
    return path
