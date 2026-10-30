"""Model rescue: when a model file does not load, find out why and recover it without losing quality.

Every recovery keeps the valid triangles exactly as they are in the file (full precision, no
simplification, no remeshing). Only what is invalid is dropped (triangles with NaN coordinates, faces
that point at vertices that do not exist, a cut-off last record) and only what is missing is added
(small holes are closed, flipped faces are turned outward). The report says exactly what was done.

Recovery strategies (tried in the order the assistant learned works best for this kind of file):

- ``sniff``          the content is another format than the extension says (e.g. an OBJ named .stl,
                     a 3MF renamed to .zip) - read it as what it really is;
- ``stl_binary``     binary STL with a wrong triangle count in the header, or cut off at the end;
- ``stl_ascii``      text STL with decimal commas, UTF-16 text, odd spacing or an unfinished last facet;
- ``obj_tolerant``   OBJ with faces pointing at missing vertices, line continuations, UTF-16, decimal commas;
- ``3mf_production`` 3MF that keeps its objects in separate files (Bambu Studio, Orca, PrusaSlicer,
                     production extension), XML with invalid characters, broken triangle indices;
- ``archive``        a ZIP or GZIP holding model files;
- ``ply`` / ``amf``  PLY (text or binary) and AMF files.

After loading: coordinates in metres are scaled to millimetres, and a lossless clean-up removes exact
duplicate faces, turns inverted faces outward and closes small holes (large openings, such as the open
base of a scan, are left as they are)."""
from __future__ import annotations

import gzip
import io
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .mesh import Mesh

PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
MODEL_EXTS = (".stl", ".obj", ".3mf", ".ply", ".amf")


REASONS = ("empty", "not_model", "truncated", "encrypted", "unreadable", "unknown")


class RescueFailed(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason            # empty | not_model | truncated | encrypted | unknown
        self.detail = detail


@dataclass
class RescueResult:
    meshes: list
    strategy: str                       # the recovery that worked
    signature: str                      # kind of file + kind of failure (what the assistant learns from)
    notes: list = field(default_factory=list)     # (key, args) - what was wrong and what was done
    dropped: int = 0                    # invalid triangles left out
    kept: int = 0                       # triangles loaded


# ---------------------------------------------------------------- helpers
def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="ignore")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if len(data) > 4 and data[1:2] == b"\x00" and data[3:4] == b"\x00":
        return data.decode("utf-16-le", errors="ignore")
    return data.decode("utf-8", errors="ignore")


def _numbers(text: str) -> list[float]:
    """Numbers on a line, accepting decimal commas ("1,5 2,5 3") and comma separators ("1.5,2.5,3")."""
    text = text.strip()
    if "," in text:
        if re.search(r"\d,\d", text) and "." not in text and len(re.split(r"[\s;]+", text)) >= 3:
            text = text.replace(",", ".")              # decimal commas (European locale)
        else:
            text = text.replace(",", " ")
    out = []
    for tok in re.split(r"[\s;]+", text):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _finite_triangles(tris: np.ndarray) -> tuple[np.ndarray, int]:
    tris = np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)
    ok = np.isfinite(tris).all(axis=(1, 2)) & (np.abs(tris) < 1e7).all(axis=(1, 2))
    return tris[ok], int((~ok).sum())


def sniff(data: bytes) -> str:
    """What the bytes really are: stl_binary | stl_ascii | obj | 3mf | zip | gzip | ply | amf | unknown."""
    head = data[:2048]
    if head.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "zip"
        if any(n.lower().endswith(".model") for n in names):
            return "3mf"
        if any(n.lower().endswith(".amf") or n.lower() == "amf" for n in names) and len(names) == 1:
            return "amf"
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"ply"):
        return "ply"
    text = _decode(head).lstrip().lower()
    if text.startswith("<?xml") or text.startswith("<amf") or text.startswith("<model"):
        return "amf" if "<amf" in text else "3mf_xml"
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data) or (not text.startswith("solid") and (len(data) - 84) % 50 == 0):
            return "stl_binary"
    if text.startswith("solid") and "facet" in _decode(data[:20000]).lower():
        return "stl_ascii"
    if re.search(r"(?m)^\s*v\s+[-\d.]", text) or re.search(r"(?m)^\s*f\s+\d", text):
        return "obj"
    if text.startswith("solid") or len(data) >= 134:
        return "stl_binary"       # many binary STLs start their header with "solid"
    return "unknown"


def exporter_of(data: bytes) -> str:
    """The program that wrote the file, when it says so (STL header, 3MF metadata, OBJ comment)."""
    head = data[:80] if data[:5].lower() != b"solid" else data[:200].split(b"\n")[0]
    text = _decode(head)
    m = re.search(r"(solidworks|blender|3shape|exocad|meshmixer|fusion|autodesk|rhino|zbrush|sketchup|"
                  r"freecad|openscad|tinkercad|prusaslicer|bambu|orca|cura|chitubox|lychee|medit|carestream|"
                  r"dentsply|itero|netfabb|materialise|magics|onshape|creo|catia|inventor|nomad|shapr3d)",
                  text, re.I)
    return m.group(1).lower() if m else ""


# ---------------------------------------------------------------- readers
def read_stl_binary_tolerant(data: bytes, notes: list) -> np.ndarray:
    if len(data) < 84 + 50:
        raise RescueFailed("truncated", "shorter than one binary STL triangle")
    sample = data[84:84 + 4096]
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in sample) / max(1, len(sample))
    if printable > 0.97:                    # text, not binary float records
        raise RescueFailed("not_model", "text data, not a binary STL")
    count = struct.unpack_from("<I", data, 80)[0]
    n = (len(data) - 84) // 50
    if n == 0:
        raise RescueFailed("empty")
    if count != n:
        notes.append(("rs_stl_count", (count, n)))
    if (len(data) - 84) % 50:
        notes.append(("rs_stl_cut", ((len(data) - 84) % 50,)))
    record = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    arr = np.frombuffer(data, dtype=record, count=n, offset=84)
    with np.errstate(invalid="ignore", over="ignore"):
        tris = arr["v"].astype(np.float64)
    finite = np.isfinite(tris).all(axis=(1, 2))
    with np.errstate(invalid="ignore", over="ignore"):
        sane = finite & (np.abs(np.nan_to_num(tris)) < 1e6).all(axis=(1, 2))
    if sane.mean() < 0.9:                   # not really float triangles: not a binary STL
        raise RescueFailed("not_model", "binary STL data is not valid numbers")
    return tris


def read_stl_ascii_tolerant(data: bytes, notes: list) -> np.ndarray:
    text = _decode(data)
    if re.search(r"vertex\s+-?\d+,\d", text, re.I):
        notes.append(("rs_decimal_comma", ()))
    verts = [_numbers(m.group(1)) for m in re.finditer(r"(?im)^\s*vertex\s+(.+?)\s*$", text)]
    verts = [v[:3] for v in verts if len(v) >= 3]
    if not verts:
        raise RescueFailed("not_model", "no vertex lines")
    extra = len(verts) % 3
    if extra:
        notes.append(("rs_stl_unfinished", (extra,)))
        verts = verts[:len(verts) - extra]
    if not verts:
        raise RescueFailed("empty")
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


def read_obj_tolerant(data: bytes, notes: list) -> np.ndarray:
    text = _decode(data).replace("\\\r\n", " ").replace("\\\n", " ")
    verts, faces = [], []
    bad = 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("v "):
            nums = _numbers(s[2:])
            if len(nums) >= 3:
                verts.append(nums[:3])
            else:
                verts.append([np.nan] * 3)        # keep numbering; faces using it are dropped later
        elif s.startswith("f "):
            idx = []
            for tok in s.split()[1:]:
                head = tok.split("/")[0]
                try:
                    i = int(head)
                except ValueError:
                    idx = None
                    break
                idx.append(i - 1 if i > 0 else len(verts) + i)
            if not idx or len(idx) < 3:
                bad += 1
                continue
            for k in range(1, len(idx) - 1):
                faces.append([idx[0], idx[k], idx[k + 1]])
    if not verts or not faces:
        raise RescueFailed("empty" if verts else "not_model", "no faces" if verts else "no vertices")
    v = np.array(verts, dtype=np.float64)
    f = np.array(faces, dtype=np.int64)
    ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
    if (~ok).sum() or bad:
        notes.append(("rs_obj_bad_faces", (int((~ok).sum()) + bad,)))
    return v[f[ok]]


def _sanitize_xml(raw: bytes) -> bytes:
    raw = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
    return raw.lstrip(b"\xef\xbb\xbf")


def read_3mf_tolerant(data: bytes, notes: list, name: str) -> list[Mesh]:
    """3MF with objects spread over several .model files (production extension), invalid XML characters
    or broken triangle indices."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RescueFailed("truncated", "the 3MF (zip) archive is damaged") from e
    models = {}
    for n in zf.namelist():
        if not n.lower().endswith(".model"):
            continue
        try:
            raw = zf.read(n)
        except (zipfile.BadZipFile, RuntimeError) as e:
            if "encrypted" in str(e).lower() or "password" in str(e).lower():
                raise RescueFailed("encrypted") from e
            notes.append(("rs_3mf_bad_part", (n,)))
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            try:
                root = ET.fromstring(_sanitize_xml(raw))
                notes.append(("rs_xml_cleaned", (n,)))
            except ET.ParseError:
                notes.append(("rs_3mf_bad_part", (n,)))
                continue
        models["/" + n.lstrip("/")] = root
    if not models:
        raise RescueFailed("not_model", "no readable .model part")
    if len(models) > 1:
        notes.append(("rs_3mf_parts", (len(models),)))

    def ns_of(root):
        return root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""

    objects = {}
    for path, root in models.items():
        ns = ns_of(root)
        q = (lambda t, ns=ns: f"{{{ns}}}{t}" if ns else t)
        for obj in root.iter(q("object")):
            objects[(path, obj.get("id"))] = (obj, q)
    dropped_idx = [0]

    def tris_of(path, oid, matrix, depth=0):
        hit = objects.get((path, oid))
        if hit is None or depth > 24:
            return []
        obj, q = hit
        out = []
        mesh_el = obj.find(q("mesh"))
        if mesh_el is not None and mesh_el.find(q("vertices")) is not None and mesh_el.find(q("triangles")) is not None:
            vs = []
            for v in mesh_el.find(q("vertices")).iter(q("vertex")):
                try:
                    vs.append([float(v.get("x")), float(v.get("y")), float(v.get("z"))])
                except (TypeError, ValueError):
                    vs.append([np.nan] * 3)
            fs = []
            for t in mesh_el.find(q("triangles")).iter(q("triangle")):
                try:
                    fs.append([int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))])
                except (TypeError, ValueError):
                    dropped_idx[0] += 1
            if vs and fs:
                vs, fs = np.array(vs), np.array(fs, dtype=np.int64)
                ok = (fs >= 0).all(axis=1) & (fs < len(vs)).all(axis=1)
                dropped_idx[0] += int((~ok).sum())
                vs = vs @ matrix[:3, :3].T + matrix[:3, 3]
                out.append(vs[fs[ok]])
        comps = obj.find(q("components"))
        if comps is not None:
            for c in comps.iter(q("component")):
                target = c.get(f"{{{PRODUCTION_NS}}}path") or path
                out += tris_of(target, c.get("objectid"), matrix @ _transform(c.get("transform")), depth + 1)
        return out

    meshes = []
    for path, root in models.items():
        ns = ns_of(root)
        q = (lambda t, ns=ns: f"{{{ns}}}{t}" if ns else t)
        build = root.find(q("build"))
        if build is None:
            continue
        for i, item in enumerate(build.iter(q("item"))):
            target = item.get(f"{{{PRODUCTION_NS}}}path") or path
            tris = tris_of(target, item.get("objectid"), _transform(item.get("transform")))
            if tris:
                obj = objects.get((target, item.get("objectid")), (None,))[0]
                label = (obj.get("name") if obj is not None else None) or f"{name}_{len(meshes) + 1}"
                meshes.append((label, np.vstack(tris)))
    if not meshes:          # no build section: every object that has a mesh
        for (path, oid), (obj, q) in objects.items():
            tris = tris_of(path, oid, np.eye(4))
            if tris:
                meshes.append((obj.get("name") or f"{name}_{len(meshes) + 1}", np.vstack(tris)))
    if dropped_idx[0]:
        notes.append(("rs_3mf_bad_triangles", (dropped_idx[0],)))
    if not meshes:
        raise RescueFailed("empty")
    return [Mesh.from_triangles(t, label) for label, t in meshes]


def _transform(text):
    m = np.eye(4)
    if text:
        try:
            v = [float(x) for x in text.split()]
        except ValueError:
            return m
        if len(v) == 12:
            m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
            m[:3, 3] = v[9:12]
    return m


_PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
              "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
              "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def read_ply(data: bytes, notes: list) -> np.ndarray:
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        raise RescueFailed("not_model", "not a PLY file")
    header = data[:end].decode("ascii", errors="ignore").splitlines()
    body_start = data.index(b"\n", end) + 1
    fmt, elements = "ascii", []
    for line in header:
        p = line.split()
        if not p:
            continue
        if p[0] == "format":
            fmt = p[1]
        elif p[0] == "element":
            elements.append([p[1], int(p[2]), []])
        elif p[0] == "property" and elements:
            elements[-1][2].append(p[1:])
    verts, faces = None, []
    if fmt == "ascii":
        lines = data[body_start:].decode("ascii", errors="ignore").split("\n")
        li = 0
        for name, count, props in elements:
            rows = lines[li:li + count]
            li += count
            if name == "vertex":
                names = [pp[-1] for pp in props]
                ix = [names.index(c) for c in ("x", "y", "z")]
                verts = np.array([[float(r.split()[i]) for i in ix] for r in rows if r.strip()])
            elif name == "face":
                for r in rows:
                    vals = r.split()
                    if not vals:
                        continue
                    n = int(vals[0])
                    idx = [int(x) for x in vals[1:1 + n]]
                    faces += [[idx[0], idx[k], idx[k + 1]] for k in range(1, n - 1)]
    else:
        bo = "<" if fmt == "binary_little_endian" else ">"
        off = body_start
        for name, count, props in elements:
            if all(pp[0] != "list" for pp in props):
                dt = np.dtype([(pp[1], bo + _PLY_TYPES[pp[0]]) for pp in props])
                arr = np.frombuffer(data, dtype=dt, count=count, offset=off)
                off += dt.itemsize * count
                if name == "vertex":
                    verts = np.column_stack([arr["x"], arr["y"], arr["z"]]).astype(np.float64)
            else:
                # faces: a list property (count type, index type), possibly with other properties
                for _ in range(count):
                    for pp in props:
                        if pp[0] == "list":
                            ct, it = np.dtype(bo + _PLY_TYPES[pp[1]]), np.dtype(bo + _PLY_TYPES[pp[2]])
                            n = int(np.frombuffer(data, ct, 1, off)[0])
                            off += ct.itemsize
                            idx = np.frombuffer(data, it, n, off).astype(np.int64)
                            off += it.itemsize * n
                            if name == "face" and n >= 3:
                                faces += [[idx[0], idx[k], idx[k + 1]] for k in range(1, n - 1)]
                        else:
                            off += np.dtype(_PLY_TYPES[pp[0]]).itemsize
    if verts is None or not faces:
        raise RescueFailed("empty")
    f = np.array(faces, dtype=np.int64)
    ok = (f >= 0).all(axis=1) & (f < len(verts)).all(axis=1)
    if (~ok).sum():
        notes.append(("rs_obj_bad_faces", (int((~ok).sum()),)))
    return verts[f[ok]]


def read_amf(data: bytes, notes: list, name: str) -> list[Mesh]:
    if data.startswith(b"PK"):
        zf = zipfile.ZipFile(io.BytesIO(data))
        data = zf.read(zf.namelist()[0])
    root = ET.fromstring(_sanitize_xml(data))
    out = []
    for oi, obj in enumerate(root.iter("object")):
        mesh = obj.find("mesh")
        if mesh is None:
            continue
        vs = np.array([[float(c.find(a).text) for a in ("x", "y", "z")]
                       for c in mesh.iter("coordinates")], dtype=np.float64)
        tris = []
        for vol in mesh.iter("volume"):
            for t in vol.iter("triangle"):
                tris.append([int(t.find(k).text) for k in ("v1", "v2", "v3")])
        if len(vs) and tris:
            f = np.array(tris, dtype=np.int64)
            ok = (f >= 0).all(axis=1) & (f < len(vs)).all(axis=1)
            out.append(Mesh.from_triangles(vs[f[ok]], obj.get("id") and f"{name}_{oi + 1}" or name))
    if not out:
        raise RescueFailed("empty")
    return out


def read_archive(data: bytes, notes: list, name: str, depth: int = 0) -> list[Mesh]:
    if depth > 2:
        raise RescueFailed("not_model")
    if data.startswith(b"\x1f\x8b"):
        inner = gzip.decompress(data)
        notes.append(("rs_archive", ("gzip",)))
        return _read_any(inner, name, notes, depth + 1)
    zf = zipfile.ZipFile(io.BytesIO(data))
    inside = [n for n in zf.namelist() if os.path.splitext(n)[1].lower() in MODEL_EXTS]
    if not inside:
        raise RescueFailed("not_model", "no model files in the archive")
    notes.append(("rs_archive", (", ".join(os.path.basename(n) for n in inside[:5]),)))
    out = []
    for n in inside:
        out += _read_any(zf.read(n), os.path.splitext(os.path.basename(n))[0], notes, depth + 1)
    return out


def _read_any(data: bytes, name: str, notes: list, depth: int = 0) -> list[Mesh]:
    kind = sniff(data)
    if kind == "stl_binary":
        return [Mesh.from_triangles(_clean(read_stl_binary_tolerant(data, notes), notes), name)]
    if kind == "stl_ascii":
        return [Mesh.from_triangles(_clean(read_stl_ascii_tolerant(data, notes), notes), name)]
    if kind == "obj":
        return [Mesh.from_triangles(_clean(read_obj_tolerant(data, notes), notes), name)]
    if kind == "3mf":
        return read_3mf_tolerant(data, notes, name)
    if kind == "ply":
        return [Mesh.from_triangles(_clean(read_ply(data, notes), notes), name)]
    if kind == "amf":
        return read_amf(data, notes, name)
    if kind in ("zip", "gzip"):
        return read_archive(data, notes, name, depth)
    raise RescueFailed("not_model", f"unrecognised content ({kind})")


def _clean(tris: np.ndarray, notes: list) -> np.ndarray:
    tris, bad = _finite_triangles(tris)
    if bad:
        notes.append(("rs_nan", (bad,)))
    if not len(tris):
        raise RescueFailed("empty")
    return tris


# ---------------------------------------------------------------- strategies
def _strategy_sniff(data, ext, name, notes):
    kind = sniff(data)
    expected = {".stl": ("stl_binary", "stl_ascii"), ".obj": ("obj",), ".3mf": ("3mf",), ".ply": ("ply",),
                ".amf": ("amf",)}.get(ext, ())
    if kind in expected:
        raise RescueFailed("unknown", "content matches the extension")
    notes.append(("rs_wrong_type", (ext or "?", kind)))
    return _read_any(data, name, notes)


def _strategy_stl_binary(data, ext, name, notes):
    return [Mesh.from_triangles(_clean(read_stl_binary_tolerant(data, notes), notes), name)]


def _strategy_stl_ascii(data, ext, name, notes):
    return [Mesh.from_triangles(_clean(read_stl_ascii_tolerant(data, notes), notes), name)]


def _strategy_obj(data, ext, name, notes):
    return [Mesh.from_triangles(_clean(read_obj_tolerant(data, notes), notes), name)]


def _strategy_3mf(data, ext, name, notes):
    return read_3mf_tolerant(data, notes, name)


def _strategy_archive(data, ext, name, notes):
    if not data.startswith((b"PK", b"\x1f\x8b")):
        raise RescueFailed("unknown", "not an archive")
    return read_archive(data, notes, name)


def _strategy_ply(data, ext, name, notes):
    return [Mesh.from_triangles(_clean(read_ply(data, notes), notes), name)]


def _strategy_amf(data, ext, name, notes):
    return read_amf(data, notes, name)


STRATEGIES = {
    "sniff": _strategy_sniff, "stl_binary": _strategy_stl_binary, "stl_ascii": _strategy_stl_ascii,
    "obj_tolerant": _strategy_obj, "3mf_production": _strategy_3mf, "archive": _strategy_archive,
    "ply": _strategy_ply, "amf": _strategy_amf,
}
DEFAULT_ORDER = {
    ".stl": ["stl_binary", "stl_ascii", "sniff", "archive", "obj_tolerant"],
    ".obj": ["obj_tolerant", "sniff", "archive"],
    ".3mf": ["3mf_production", "sniff", "archive"],
    ".ply": ["ply", "sniff"],
    ".amf": ["amf", "sniff"],
}


def signature(path: str, data: bytes, error: Exception | None) -> str:
    ext = os.path.splitext(path)[1].lower() or "?"
    err = type(error).__name__ if error is not None else "-"
    return f"{ext}|{sniff(data)}|{err}|{exporter_of(data) or '-'}"


# ---------------------------------------------------------------- lossless finishing
def finish(meshes: list[Mesh], notes: list) -> list[Mesh]:
    """Units and a clean-up that never lowers quality: every original triangle keeps its exact position."""
    from .mesh_tools import analyze, repair_advanced
    out = []
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    size = float((hi - lo).max())
    scale = 1000.0 if 0 < size < 0.5 else 1.0       # metres (a whole model under half a millimetre)
    if scale != 1.0:
        notes.append(("rs_units_m", (round(size * scale, 1),)))
    for m in meshes:
        if scale != 1.0:
            m = Mesh(m.vertices * scale, m.faces, m.name)
        before = analyze(m)
        if not before["watertight"] or before["duplicates"] or before["degenerate"]:
            edges = max(1, 3 * before["faces"] // 2)
            small_holes = before["open_edges"] <= max(60, edges // 200)   # a few small gaps, not an open base
            fixed, rep = repair_advanced(m, weld=1e-6, remove_shells_below=0.0, fix_nonmanifold=False,
                                         fill_holes=small_holes)
            if len(fixed.faces) >= len(m.faces) - before["duplicates"] - before["degenerate"]:
                if rep.get("holes_filled"):
                    notes.append(("rs_holes", (rep["holes_filled"],)))
                if before["duplicates"]:
                    notes.append(("rs_duplicates", (before["duplicates"],)))
                if rep.get("flipped"):
                    notes.append(("rs_flipped", (rep["flipped"],)))
                if not small_holes and before["open_edges"]:
                    notes.append(("rs_open_kept", ()))
                fixed.name = m.name
                m = fixed
        out.append(m)
    return out


def rescue(path: str, error: Exception | None = None, order_hint: list | None = None) -> RescueResult:
    """Try every recovery (learned order first). Raises RescueFailed with the most telling reason."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        raise RescueFailed("unreadable", str(e)) from e
    if not data.strip():
        raise RescueFailed("empty", "the file is empty (0 bytes)")
    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0]
    sig = signature(path, data, error)
    order = list(dict.fromkeys([s for s in (order_hint or []) if s in STRATEGIES]
                               + DEFAULT_ORDER.get(ext, ["sniff", "archive"]) + list(STRATEGIES)))
    reasons = []
    for strat in order:
        notes: list = []
        try:
            meshes = STRATEGIES[strat](data, ext, name, notes)
        except RescueFailed as e:
            reasons.append(e)
            continue
        except Exception as e:                       # a strategy that does not fit this file
            reasons.append(RescueFailed("unknown", f"{type(e).__name__}: {e}"))
            continue
        meshes = [m for m in meshes if len(m.faces)]
        if not meshes:
            reasons.append(RescueFailed("empty"))
            continue
        meshes = finish(meshes, notes)
        kept = sum(len(m.faces) for m in meshes)
        dropped = sum(a[0] for k, a in notes if k in ("rs_nan", "rs_obj_bad_faces", "rs_3mf_bad_triangles"))
        return RescueResult(meshes, strat, sig, notes, dropped, kept)
    rank = ["encrypted", "truncated", "empty", "not_model", "unknown"]
    best = min(reasons, key=lambda e: rank.index(e.reason) if e.reason in rank else 9) if reasons else \
        RescueFailed("unknown")
    best.signature = sig
    raise best

