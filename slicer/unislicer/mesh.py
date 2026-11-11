"""Triangle meshes: loading (STL / OBJ / 3MF), welding, transforms and export."""
from __future__ import annotations

import math
import os
import struct
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

WELD_TOLERANCE = 1e-5


def _group_rows(keys: np.ndarray):
    """(group index of every row, size of every group) for equal rows of an (N, 3) integer array - what
    np.unique(axis=0, return_inverse=True, return_counts=True) gives, by a lexsort (several times faster)."""
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
    k = keys[order]
    new = np.ones(len(k), dtype=bool)
    new[1:] = np.any(k[1:] != k[:-1], axis=1)
    group = np.cumsum(new) - 1
    inverse = np.empty(len(k), dtype=np.int64)
    inverse[order] = group
    counts = np.diff(np.append(np.flatnonzero(new), len(k)))
    return inverse, counts


class Mesh:
    """Indexed triangle mesh. ``vertices`` is (V, 3) float64, ``faces`` is (F, 3) int64."""

    def __init__(self, vertices, faces, name: str = ""):
        self.vertices = np.ascontiguousarray(vertices, dtype=np.float64).reshape(-1, 3)
        self.faces = np.ascontiguousarray(faces, dtype=np.int64).reshape(-1, 3)
        self.name = name
        self._watertight: bool | None = None   # cached: meshes are treated as immutable
        self._volume: float | None = None

    # ------------------------------------------------------------------ build
    @classmethod
    def from_triangles(cls, triangles, name: str = "", tolerance: float = WELD_TOLERANCE) -> "Mesh":
        """Build an indexed mesh from a (F, 3, 3) triangle soup, welding coincident vertices."""
        tris = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        finite = np.isfinite(tris).all(axis=(1, 2))
        if not finite.all():                  # a damaged file: a NaN corner made every bound and slice NaN
            tris = tris[finite]
        if len(tris) == 0:
            return cls(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), name)
        points = tris.reshape(-1, 3)
        keys = np.round(points / tolerance).astype(np.int64)
        # rows sorted by (x, y, z) with lexsort and split where they change: the same result as
        # np.unique(axis=0) at a fraction of the time (that one sorts whole rows as opaque bytes)
        order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
        k = keys[order]
        new = np.ones(len(k), dtype=bool)
        new[1:] = np.any(k[1:] != k[:-1], axis=1)
        group = np.cumsum(new) - 1
        first = order[new]
        inverse = np.empty(len(k), dtype=np.int64)
        inverse[order] = group
        faces = inverse.reshape(-1, 3)
        mesh = cls(points[first], faces, name)
        mesh.remove_degenerate()
        return mesh

    @staticmethod
    def merge(meshes, name: str = "") -> "Mesh":
        verts, faces, offset = [], [], 0
        for m in meshes:
            if len(m.faces) == 0:
                continue
            verts.append(m.vertices)
            faces.append(m.faces + offset)
            offset += len(m.vertices)
        if not verts:
            return Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), name)
        return Mesh(np.vstack(verts), np.vstack(faces), name)

    def copy(self) -> "Mesh":
        return Mesh(self.vertices.copy(), self.faces.copy(), self.name)

    def remove_degenerate(self) -> None:
        f = self.faces
        ok = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
        self.faces = f[ok]
        self._watertight = self._volume = None

    # ------------------------------------------------------------ properties
    @property
    def triangles(self) -> np.ndarray:
        # meshes are treated as immutable once built; the cache is checked against the arrays it came from
        c = self.__dict__.get("_tris")
        if c is not None and c[0] is self.vertices and c[1] is self.faces:
            return c[2]
        t = self.vertices[self.faces]
        self._tris = (self.vertices, self.faces, t)
        return t

    @property
    def bounds(self) -> np.ndarray:
        if len(self.vertices) == 0:
            return np.zeros((2, 3))
        # meshes built here only keep referenced vertices, so all of them count
        return np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])

    @property
    def size(self) -> np.ndarray:
        b = self.bounds
        return b[1] - b[0]

    def drop_caches(self) -> None:
        """Forget everything worked out from the mesh (triangles, normals, light copies, picking and outline
        data...): about five times the mesh itself. For a mesh kept only for undo; it is all rebuilt when
        needed again."""
        keep = ("vertices", "faces", "name", "_watertight", "_volume")       # small, and always there
        for k in [k for k in self.__dict__ if k not in keep]:
            del self.__dict__[k]

    def face_normals(self) -> np.ndarray:
        c = self.__dict__.get("_normals")
        if c is not None and c[0] is self.vertices and c[1] is self.faces:
            return c[2]
        t = self.triangles
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        length[length == 0] = 1.0
        n = n / length
        self._normals = (self.vertices, self.faces, n)
        return n

    def face_areas(self) -> np.ndarray:
        cached = self.__dict__.get("_areas")
        if cached is None or len(cached) != len(self.faces):
            t = self.triangles
            cached = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
            self._areas = cached
        return cached

    def decimated(self, target_faces: int = 60000) -> "Mesh":
        """A light look-alike for drawing while the view moves: vertices merged on a grid sized so that
        about ``target_faces`` triangles remain (vertex clustering). Returns self if already small.
        Kept on the mesh (meshes are not edited in place), so it is made once."""
        if len(self.faces) <= target_faces:
            return self
        cache = self.__dict__.setdefault("_decimated", {})
        hit = cache.get(target_faces)
        if hit is not None and hit[0] is self.vertices and hit[1] is self.faces:
            return hit[2]
        area = float(self.face_areas().sum()) or 1.0
        cell = (2.0 * area / target_faces) ** 0.5
        keys = np.floor((self.vertices - self.vertices.min(axis=0)) / cell).astype(np.int64)
        cluster, counts = _group_rows(keys)
        verts = np.zeros((len(counts), 3))
        np.add.at(verts, cluster, self.vertices)
        verts /= counts[:, None]
        f = cluster[self.faces]
        ok = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
        f = f[ok]
        sf = np.sort(f, axis=1)
        order = np.lexsort((sf[:, 2], sf[:, 1], sf[:, 0]))
        keep = np.ones(len(order), dtype=bool)
        keep[1:] = np.any(sf[order][1:] != sf[order][:-1], axis=1)
        out = Mesh(verts, f[np.sort(order[keep])], self.name)
        cache[target_faces] = (self.vertices, self.faces, out)
        return out

    def volume(self) -> float:
        if self._volume is None:
            t = self.triangles
            self._volume = float(abs(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6.0)
        return self._volume

    def is_watertight(self) -> bool:
        if self._watertight is None:
            if len(self.faces) == 0:
                self._watertight = False
            else:
                # every edge shared by exactly two faces; edges packed into one int64 for a fast unique
                e = np.sort(self.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
                keys = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
                _, counts = np.unique(keys, return_counts=True)
                self._watertight = bool(np.all(counts == 2))
        return self._watertight

    # ------------------------------------------------------------ transforms
    def transform(self, matrix: np.ndarray) -> "Mesh":
        """Return a copy transformed by a 4x4 matrix (faces flipped on mirroring)."""
        m = np.asarray(matrix, dtype=np.float64)
        v = self.vertices @ m[:3, :3].T + m[:3, 3]
        faces = self.faces
        det = np.linalg.det(m[:3, :3])
        if det < 0:
            faces = faces[:, ::-1]
        out = Mesh(v, faces.copy(), self.name)
        out._watertight = self._watertight
        if self._volume is not None:
            out._volume = self._volume * abs(det)
        return out

    # results kept on a mesh (dental_cad's part type, a crown's opening, a mask's flat side, the arch test,
    # the repair fingerprint) that moving it does not change: a moved copy keeps them (loading centres every
    # model, and the crowns' openings were worked out a second time - 1.7 s for 14 crowns)
    MOVE_SAFE = ("_opening", "_flat_side", "_horseshoe", "_kind", "_repair_fp")

    def translated(self, offset) -> "Mesh":
        out = Mesh(self.vertices + np.asarray(offset, dtype=np.float64), self.faces, self.name)
        out._watertight, out._volume = self._watertight, self._volume
        for key in self.MOVE_SAFE:
            hit = self.__dict__.get(key)
            if hit is not None and hit[0] is self.vertices:
                out.__dict__[key] = (out.vertices, hit[1])
        return out

    def lay_flat_rotation(self) -> np.ndarray:
        """3x3 rotation that puts the largest flat face (by summed area) onto the bed."""
        normals = self.face_normals()
        areas = self.face_areas()
        q = np.round(normals * 50).astype(np.int64) + 64          # 0..128 per axis
        keys = (q[:, 0] * 129 + q[:, 1]) * 129 + q[:, 2]
        uniq, inverse = np.unique(keys, return_inverse=True)
        sums = np.bincount(inverse.ravel(), weights=areas, minlength=len(uniq))
        best = np.argmax(sums)
        n = normals[inverse.ravel() == best]
        n = (n * areas[inverse.ravel() == best][:, None]).sum(axis=0)
        n /= np.linalg.norm(n) or 1.0
        return rotation_between(n, np.array([0.0, 0.0, -1.0]))


# ----------------------------------------------------------------- helpers
def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix taking unit vector a onto unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        # 180 degrees: rotate about any axis perpendicular to a
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def euler_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Rotation matrix from XYZ Euler angles in degrees (applied X, then Y, then Z)."""
    ax, ay, az = np.radians([rx, ry, rz])
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def euler_angles(m: np.ndarray) -> np.ndarray:
    """XYZ Euler angles in degrees with ``euler_matrix(*angles) == m`` for a rotation ``m`` - of the two
    answers, the one with the smaller angles (a part turned 180 around Y reads 0, 180, 0, not 180, 0, 180)."""
    m = np.asarray(m, dtype=float)
    sy = -float(np.clip(m[2, 0], -1.0, 1.0))
    ry = math.asin(sy)
    if math.cos(ry) > 1e-7:
        rx, rz = math.atan2(m[2, 1], m[2, 2]), math.atan2(m[1, 0], m[0, 0])
        options = [(rx, ry, rz), (rx + math.pi, math.pi - ry, rz + math.pi)]
    else:                                          # straight up or down: X and Z turn about the same line
        options = [(math.atan2(-m[1, 2], m[1, 1]), ry, 0.0)]

    def wrap(a):
        d = (math.degrees(a) + 180.0) % 360.0 - 180.0
        d = 180.0 if abs(d + 180.0) < 1e-9 else d
        return 0.0 if abs(d) < 1e-9 else d
    best = min(([wrap(a) for a in o] for o in options), key=lambda o: sum(abs(a) for a in o))
    return np.array(best)


# ------------------------------------------------------------------ loading
SUPPORTED_EXTENSIONS = (".stl", ".obj", ".3mf", ".ply", ".amf", ".zip")


def load_mesh(path: str) -> list[Mesh]:
    """Load a model file. Returns one mesh per object (3MF may contain several)."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0]
    if ext == ".stl":
        meshes = [Mesh.from_triangles(_read_stl(path), name)]
    elif ext == ".obj":
        meshes = [_read_obj(path, name)]
    elif ext == ".3mf":
        meshes = _read_3mf(path, name)
    elif ext in (".ply", ".amf", ".zip"):
        from . import model_rescue
        with open(path, "rb") as fh:
            data = fh.read()
        meshes = model_rescue._read_any(data, name, [])
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    meshes = [_finite_only(m) for m in meshes]
    meshes = [m for m in meshes if len(m.faces)]
    if not meshes:
        raise ValueError("The file contains no triangles")
    return meshes


def _finite_only(mesh: "Mesh") -> "Mesh":
    """The mesh without triangles that touch a NaN / infinite corner (a damaged file): one such corner made
    every bound, layout and slice of the plate NaN."""
    bad = ~np.isfinite(mesh.vertices).all(axis=1)
    if not bad.any():
        return mesh
    keep = ~bad[mesh.faces].any(axis=1)
    out = Mesh.from_triangles(mesh.vertices[mesh.faces[keep]], mesh.name) if keep.any() else \
        Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), mesh.name)
    if "material" in mesh.__dict__:
        out.material = mesh.material
    return out


def _read_stl(path: str) -> np.ndarray:
    """Triangles of a binary or ASCII STL. Binary files whose triangle count does not match their length
    (a few bytes of padding at the end, or a count of 0 written by some exporters) are read too."""
    with open(path, "rb") as fh:
        data = fh.read()
    record = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    count = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else -1
    if count >= 0 and 84 + count * 50 == len(data):
        return np.frombuffer(data, dtype=record, count=count, offset=84)["v"].astype(np.float64)
    head = data[:512].lstrip().lower()
    if not head.startswith(b"solid") or b"facet" not in data[:4096].lower():
        # two harmless kinds of binary file only: a few bytes of padding after the triangles the count says,
        # or a count of 0 (all whole triangles are read). Any other mismatch may hide lost triangles and is
        # left to the repair, which says what it found.
        fits = (len(data) - 84) // 50 if len(data) >= 134 else 0
        n = count if 0 < count == fits else fits if count == 0 else 0
        if n > 0:
            v = np.frombuffer(data, dtype=record, count=n, offset=84)["v"].astype(np.float64)
            if np.isfinite(v).all():
                return v
    text = data.decode("utf-8", errors="ignore")
    verts = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
    if not verts or len(verts) % 3:
        raise ValueError("Could not parse STL file")
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


def _read_obj(path: str, name: str) -> Mesh:
    verts, faces = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            # a damaged line is skipped, not the whole file (it failed with "index 8 is out of bounds")
            try:
                if line.startswith("v "):
                    p = line.split()
                    verts.append([float(p[1]), float(p[2]), float(p[3])])
                elif line.startswith("f "):
                    idx = []
                    for token in line.split()[1:]:
                        i = int(token.split("/")[0])
                        idx.append(i - 1 if i > 0 else len(verts) + i)
                    for k in range(1, len(idx) - 1):  # fan triangulation
                        faces.append([idx[0], idx[k], idx[k + 1]])
            except (ValueError, IndexError):
                continue
    f = np.array(faces, dtype=np.int64).reshape(-1, 3)
    f = f[np.all((f >= 0) & (f < len(verts)), axis=1)]      # faces naming a vertex the file does not have
    if not len(f):
        raise ValueError("The file contains no triangles")
    mesh = Mesh(np.array(verts, dtype=float).reshape(-1, 3), f, name)
    return Mesh.from_triangles(mesh.triangles, name)


def _parse_3mf_transform(text: str | None) -> np.ndarray:
    m = np.eye(4)
    if text:
        v = [float(x) for x in text.split()]
        if len(v) == 12:
            m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
            m[:3, 3] = v[9:12]
    return m


def _read_3mf(path: str, name: str) -> list[Mesh]:
    with zipfile.ZipFile(path) as zf:
        models = [n for n in zf.namelist() if n.lower().endswith(".model")]
        if not models:
            raise ValueError("3MF archive has no model")
        if len(models) > 1:
            # objects spread over several .model files (production extension): Creality Print, Bambu Studio,
            # OrcaSlicer and PrusaSlicer projects. Read them all, components across files included.
            from .model_rescue import read_3mf_tolerant
            with open(path, "rb") as fh:
                return read_3mf_tolerant(fh.read(), [], name)
        root = ET.fromstring(zf.read(models[0]))
    ns = {"m": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {"m": ""}
    q = (lambda tag: f"{{{ns['m']}}}{tag}") if ns["m"] else (lambda tag: tag)

    objects: dict[str, ET.Element] = {}
    for obj in root.iter(q("object")):
        objects[obj.get("id")] = obj
    # materials (3Shape / exocad write tooth and gingiva shades as base materials)
    materials: dict[str, list[str]] = {}
    for group in root.iter(q("basematerials")):
        materials[group.get("id")] = [b.get("name") or "" for b in group.iter(q("base"))]

    def material_name(pid, pindex) -> str:
        names = materials.get(pid or "")
        try:
            return names[int(pindex)] if names is not None else ""
        except (TypeError, ValueError, IndexError):
            return ""

    def object_triangles(oid: str, matrix: np.ndarray, depth: int = 0) -> list[tuple[str, np.ndarray]]:
        """(material name, triangles) pieces of an object; triangles with their own material are split off."""
        obj = objects.get(oid)
        if obj is None or depth > 16:
            return []
        out = []
        mesh_el = obj.find(q("mesh"))
        if mesh_el is not None:
            vs = np.array([[float(v.get("x")), float(v.get("y")), float(v.get("z"))]
                           for v in mesh_el.find(q("vertices")).iter(q("vertex"))])
            tri_els = list(mesh_el.find(q("triangles")).iter(q("triangle")))
            fs = np.array([[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))] for t in tri_els], dtype=np.int64)
            if len(vs) and len(fs):
                vs = vs @ matrix[:3, :3].T + matrix[:3, 3]
                default = material_name(obj.get("pid"), obj.get("pindex"))
                if materials:
                    names = [material_name(t.get("pid") or obj.get("pid"), t.get("p1")) if t.get("p1") is not None
                             else default for t in tri_els]
                else:
                    names = [default] * len(tri_els)
                for name in dict.fromkeys(names):
                    sel = np.array([n == name for n in names])
                    out.append((name, vs[fs[sel]]))
        comps = obj.find(q("components"))
        if comps is not None:
            for c in comps.iter(q("component")):
                out += object_triangles(c.get("objectid"), matrix @ _parse_3mf_transform(c.get("transform")), depth + 1)
        return out

    meshes = []
    build = root.find(q("build"))
    items = list(build.iter(q("item"))) if build is not None else []
    if not items:
        items = [ET.Element("item", objectid=k) for k in objects]
    for i, item in enumerate(items):
        pieces = object_triangles(item.get("objectid"), _parse_3mf_transform(item.get("transform")))
        if not pieces:
            continue
        label = objects[item.get("objectid")].get("name") or f"{name}_{i + 1}"
        by_material: dict[str, list] = {}
        for mat, tris in pieces:
            by_material.setdefault(mat, []).append(tris)
        if len(by_material) == 1:
            mat, tris = next(iter(by_material.items()))
            m = Mesh.from_triangles(np.vstack(tris), label)
            m.material = mat
            meshes.append(m)
            continue
        # several materials in one part (e.g. a denture's gingiva and teeth from 3Shape): one model whose
        # regions keep their material, so it still moves and prints as one piece
        pieces_m = [(mat, Mesh.from_triangles(np.vstack(tris), mat)) for mat, tris in by_material.items()]
        m = Mesh.merge([pm for _, pm in pieces_m], label)
        regions, start = [], 0
        for mat, pm in pieces_m:
            regions.append({"name": mat or "part", "faces": np.arange(start, start + len(pm.faces)), "shade": mat})
            start += len(pm.faces)
        m.material = max(by_material, key=lambda k: sum(len(t) for t in by_material[k]))
        m.regions = regions
        meshes.append(m)
    return meshes


# ------------------------------------------------------------------- saving
def save_stl(mesh: Mesh, path: str) -> None:
    tris = mesh.triangles.astype(np.float32)
    normals = mesh.face_normals().astype(np.float32)
    record = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    arr = np.zeros(len(tris), dtype=record)
    arr["n"] = normals
    arr["v"] = tris
    with open(path, "wb") as fh:
        header = f"UniSlicer export: {mesh.name}".encode()[:80]
        fh.write(header.ljust(80, b" "))
        fh.write(struct.pack("<I", len(tris)))
        fh.write(arr.tobytes())
