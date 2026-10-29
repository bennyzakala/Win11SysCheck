"""Automatic orientation: try many 'down' directions and score each one."""
from __future__ import annotations

import math

import numpy as np

from .mesh import Mesh, rotation_between


def _directions(n: int) -> np.ndarray:
    """``n`` roughly uniform unit vectors (Fibonacci sphere)."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


def scores(mesh: Mesh, technology: str, support_angle: float, directions: np.ndarray,
           elevation: float = 5.0, max_faces: int = 60000) -> np.ndarray:
    """Lower is better. For each candidate 'down' direction d (the model is turned so d points to -Z)."""
    normals = mesh.face_normals()
    areas = mesh.face_areas()
    centroids = mesh.triangles.mean(axis=1)
    verts = mesh.vertices
    if len(areas) > max_faces:  # area-weighted sample keeps big models fast
        rng = np.random.default_rng(0)
        pick = rng.choice(len(areas), max_faces, replace=False, p=areas / areas.sum())
        scale = areas.sum() / max_faces
        normals, centroids, areas = normals[pick], centroids[pick], np.full(max_faces, scale)
    size = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
    sin_t = math.sin(math.radians(support_angle))
    out = np.empty(len(directions))
    for a in range(0, len(directions), 25):
        d = directions[a:a + 25]
        nz = -(normals @ d.T)                       # face normal z after turning d to -Z
        cz = -(centroids @ d.T)
        z = cz - (-(verts @ d.T)).min(axis=0)       # height above the bed
        over = nz < -sin_t
        if technology == "sla":
            support = (areas[:, None] * -nz * (z + elevation) * over).sum(axis=0)
            footprint = 0.5 * (areas[:, None] * np.abs(nz)).sum(axis=0)     # ~ largest peel area
            flat = (areas[:, None] * (np.abs(nz) > 0.985)).sum(axis=0)      # big faces parallel to the screen
            height = z.max(axis=0)
            out[a:a + 25] = support + size * (0.5 * footprint + 1.5 * flat) + 0.05 * height * footprint
        else:
            lifted = over & (z > 0.2)
            support = (areas[:, None] * -nz * z * lifted).sum(axis=0)
            bed = (areas[:, None] * ((nz < -0.999) & (z <= 0.2))).sum(axis=0)
            out[a:a + 25] = support - size * 0.5 * bed + 0.01 * z.max(axis=0) * size
    return out


def _flat_face_normals(mesh: Mesh, count: int = 16) -> np.ndarray:
    """Normals of the largest groups of coplanar-ish faces (exact 'lay flat' candidates)."""
    normals = mesh.face_normals()
    areas = mesh.face_areas()
    q = np.round(normals * 40).astype(np.int64) + 64
    keys = (q[:, 0] * 129 + q[:, 1]) * 129 + q[:, 2]
    uniq, inverse = np.unique(keys, return_inverse=True)
    sums = np.bincount(inverse, weights=areas, minlength=len(uniq))
    out = []
    for k in np.argsort(-sums)[:count]:
        sel = inverse == k
        n = (normals[sel] * areas[sel, None]).sum(axis=0)
        if np.linalg.norm(n) > 0:
            out.append(n / np.linalg.norm(n))
    return np.array(out).reshape(-1, 3)


def best_rotation(mesh: Mesh, technology: str, support_angle: float = 45.0, elevation: float = 5.0,
                  samples: int = 240) -> np.ndarray:
    """3x3 rotation that puts the mesh in its best orientation."""
    axes = np.vstack([np.eye(3), -np.eye(3)])
    dirs = np.vstack([_directions(samples), axes, _flat_face_normals(mesh)])
    if technology == "sla":
        # resin parts print best tilted; also try every direction tilted 30 degrees off a principal axis
        tilt = math.radians(30)
        extra = [np.array([math.sin(tilt) * math.cos(a), math.sin(tilt) * math.sin(a), s * math.cos(tilt)])
                 for a in np.linspace(0, 2 * math.pi, 12, endpoint=False) for s in (-1, 1)]
        dirs = np.vstack([dirs, extra])
    s = scores(mesh, technology, support_angle, dirs, elevation)
    return rotation_between(dirs[int(np.argmin(s))], np.array([0.0, 0.0, -1.0]))
