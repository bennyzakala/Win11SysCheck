"""3D surface textures (as GrabCAD Print Pro): a pattern pressed into the model's surface - knurl,
dots, ribs, grain, honeycomb - for grip, fit or looks. The surface is refined until its triangles are
small against the pattern, then every vertex moves along its normal by the pattern's height there, so
the model stays closed (same topology) and prints like any other part. The flat base is left alone."""
from __future__ import annotations

import math

import numpy as np

from .mesh import Mesh

PATTERNS = ("knurl", "dots", "ribs", "grain", "hexagon")
AREAS = ("all", "sides", "top")
MAX_FACES = 1_500_000


def subdivide(mesh: Mesh, max_edge: float, max_faces: int = MAX_FACES) -> Mesh:
    """Split every triangle in four (shared edge midpoints, no cracks) until the longest edge is at most
    ``max_edge`` or the face budget is reached."""
    v, f = mesh.vertices.copy(), mesh.faces.copy()
    while len(f) and len(f) * 4 <= max_faces:
        t = v[f]
        longest = float(np.max(np.linalg.norm(t[:, [1, 2, 0]] - t, axis=2)))
        if longest <= max_edge:
            break
        e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        uniq, inv = np.unique(e, axis=0, return_inverse=True)
        mids = (v[uniq[:, 0]] + v[uniq[:, 1]]) / 2
        base = len(v)
        v = np.concatenate([v, mids])
        n = len(f)
        m01, m12, m20 = (inv[:n] + base, inv[n:2 * n] + base, inv[2 * n:] + base)
        a, b, c = f[:, 0], f[:, 1], f[:, 2]
        f = np.concatenate([np.stack([a, m01, m20], 1), np.stack([m01, b, m12], 1),
                            np.stack([m20, m12, c], 1), np.stack([m01, m12, m20], 1)])
    out = Mesh(v, f, mesh.name)
    return out


def vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])      # area-weighted
    vn = np.zeros_like(v)
    for k in range(3):
        np.add.at(vn, f[:, k], fn)
    length = np.linalg.norm(vn, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return vn / length


def _hash(ix, iy):
    h = (ix * 374761393 + iy * 668265263) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return (h & 0xFFFF) / 65535.0


def _value_noise(u, v):
    iu, iv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fu, fv = u - iu, v - iv
    fu, fv = fu * fu * (3 - 2 * fu), fv * fv * (3 - 2 * fv)
    a, b = _hash(iu, iv), _hash(iu + 1, iv)
    c, d = _hash(iu, iv + 1), _hash(iu + 1, iv + 1)
    return (a * (1 - fu) + b * fu) * (1 - fv) + (c * (1 - fu) + d * fu) * fv


def pattern_height(kind: str, u: np.ndarray, v: np.ndarray, spacing: float) -> np.ndarray:
    """Height 0..1 of the pattern at surface coordinates (u, v) in mm."""
    s = max(spacing, 0.1)
    if kind == "ribs":
        return 0.5 + 0.5 * np.sin(2 * math.pi * u / s)
    if kind == "knurl":                       # crossed diagonal grooves: raised diamonds
        a = np.abs(np.sin(math.pi * (u + v) / s))
        b = np.abs(np.sin(math.pi * (u - v) / s))
        return np.minimum(a, b) ** 0.6
    if kind == "dots":
        cu, cv = np.round(u / s) * s, np.round(v / s) * s
        d = np.hypot(u - cu, v - cv) / (0.35 * s)
        return np.clip(1 - d * d, 0, 1)
    if kind == "hexagon":                     # honeycomb: raised walls between hexagonal cells
        x, y = u / s, v / s
        r3 = math.sqrt(3)
        p = np.stack([x, y], -1)
        c1 = np.stack([np.round(x), np.round(y / r3) * r3], -1)
        c2 = np.stack([np.round(x - 0.5) + 0.5, np.round((y - r3 / 2) / r3) * r3 + r3 / 2], -1)
        d = np.minimum(np.linalg.norm(p - c1, axis=-1), np.linalg.norm(p - c2, axis=-1))
        return np.clip((d - 0.38) / 0.12, 0, 1)
    if kind == "grain":
        n = (_value_noise(u / s * 2, v / s * 2) * 0.6 + _value_noise(u / s * 5, v / s * 5) * 0.4)
        return np.clip(n, 0, 1)
    raise ValueError(kind)


def area_weights(v: np.ndarray, n: np.ndarray, area: str, base_band: float = 0.6) -> np.ndarray:
    """0..1 per vertex: where the texture goes. 'sides' = walls, 'top' = surfaces facing up, 'all' = all
    but the flat base (the bottom band always stays clean so the model sits / adheres flat)."""
    nz = n[:, 2]
    if area == "sides":
        w = np.clip((0.85 - np.abs(nz)) / 0.25, 0, 1)
    elif area == "top":
        w = np.clip((nz - 0.35) / 0.25, 0, 1)
    else:
        w = np.ones(len(v))
    zmin = v[:, 2].min() if len(v) else 0.0
    w *= np.clip((v[:, 2] - zmin - base_band) / max(base_band, 1e-6), 0, 1)
    return w


def apply(mesh: Mesh, kind: str = "knurl", spacing: float = 1.5, depth: float = 0.3, area: str = "all",
          max_faces: int = MAX_FACES) -> Mesh:
    """The textured copy of ``mesh`` (in the same coordinates). ``depth`` > 0 raises the pattern out of the
    surface, < 0 presses it in."""
    if kind not in PATTERNS:
        raise ValueError(kind)
    fine = subdivide(mesh, max(spacing / 4.0, 0.05), max_faces)
    v, f = fine.vertices, fine.faces
    n = vertex_normals(v, f)
    # triplanar coordinates: project on the plane facing each vertex's normal most
    ax = np.argmax(np.abs(n), axis=1)
    u = np.where(ax == 0, v[:, 1], v[:, 0])
    w_ = np.where(ax == 2, v[:, 1], v[:, 2])
    h = pattern_height(kind, u, w_, spacing)
    wts = area_weights(v, n, area)
    d = n.copy()
    if area == "sides":       # walls move sideways only: the top and the base keep their height
        d[:, 2] = 0.0
    elif area == "top":       # top surfaces move up / down only: the outline keeps its size
        d[:, :2] = 0.0
    length = np.linalg.norm(d, axis=1, keepdims=True)
    d = np.where(length > 1e-9, d / np.where(length > 1e-9, length, 1.0), 0.0)
    moved = v + d * (depth * h * wts)[:, None]          # depth < 0 presses the pattern into the surface
    return Mesh(moved, f, mesh.name)
