"""Procedural meshes: used for demo models, tests and SLA support geometry."""
from __future__ import annotations

import numpy as np

from .mesh import Mesh


def box(sx: float, sy: float, sz: float, name: str = "Cube") -> Mesh:
    v = np.array([[x, y, z] for z in (0, sz) for y in (0, sy) for x in (0, sx)], dtype=np.float64)
    f = np.array([
        [0, 2, 1], [1, 2, 3],  # bottom
        [4, 5, 6], [5, 7, 6],  # top
        [0, 1, 4], [1, 5, 4],  # front
        [2, 6, 3], [3, 6, 7],  # back
        [0, 4, 2], [2, 4, 6],  # left
        [1, 3, 5], [3, 7, 5],  # right
    ])
    return Mesh(v, f, name)


def frustum(r_bottom: float, r_top: float, height: float, segments: int = 32,
            z0: float = 0.0, center=(0.0, 0.0), name: str = "Cylinder") -> Mesh:
    """Closed truncated cone (a cylinder when both radii are equal)."""
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    cx, cy = center
    ring = np.stack([np.cos(a), np.sin(a)], axis=1)
    bottom = np.column_stack([cx + ring[:, 0] * r_bottom, cy + ring[:, 1] * r_bottom, np.full(segments, z0)])
    top = np.column_stack([cx + ring[:, 0] * r_top, cy + ring[:, 1] * r_top, np.full(segments, z0 + height)])
    v = np.vstack([bottom, top, [[cx, cy, z0], [cx, cy, z0 + height]]])
    i = np.arange(segments)
    j = (i + 1) % segments
    cb, ct = 2 * segments, 2 * segments + 1
    sides = np.vstack([np.column_stack([i, j, i + segments]), np.column_stack([j, j + segments, i + segments])])
    caps = np.vstack([np.column_stack([np.full(segments, cb), j, i]),
                      np.column_stack([np.full(segments, ct), i + segments, j + segments])])
    return Mesh(v, np.vstack([sides, caps]), name)


def cylinder(radius: float, height: float, segments: int = 48, name: str = "Cylinder") -> Mesh:
    return frustum(radius, radius, height, segments, name=name)


def sphere(radius: float, rings: int = 24, segments: int = 48, name: str = "Sphere") -> Mesh:
    verts = [[0, 0, -radius]]
    for r in range(1, rings):
        phi = -np.pi / 2 + np.pi * r / rings
        for s in range(segments):
            th = 2 * np.pi * s / segments
            verts.append([radius * np.cos(phi) * np.cos(th), radius * np.cos(phi) * np.sin(th), radius * np.sin(phi)])
    verts.append([0, 0, radius])
    faces = []
    for s in range(segments):
        faces.append([0, 1 + (s + 1) % segments, 1 + s])
    for r in range(rings - 2):
        base = 1 + r * segments
        for s in range(segments):
            a, b = base + s, base + (s + 1) % segments
            faces.append([a, b, a + segments])
            faces.append([b, b + segments, a + segments])
    top = len(verts) - 1
    base = 1 + (rings - 2) * segments
    for s in range(segments):
        faces.append([top, base + s, base + (s + 1) % segments])
    m = Mesh(np.array(verts), np.array(faces), name)
    return m.translated([0, 0, radius])


def crown_cap(width: float = 9.0, height: float = 8.0, wall: float = 0.8, taper: float = 0.35,
              segments: int = 48, name: str = "Crown") -> Mesh:
    """A closed shell like a crown seen in profile: a rounded cap open at the bottom, its cavity narrowing
    toward the top (as over a prepared tooth), the walls meeting at the margin ring at z = 0. Made by turning
    a profile round the Z axis."""
    r_out, r_in = width / 2, width / 2 - wall
    top_in = height - 1.5 * wall
    prof = [(0.0, height)]                                      # outer surface, top to margin
    for k in range(1, 7):
        a = k / 6 * np.pi / 2
        prof.append((r_out * np.sin(a), height - (height * 0.35) * (1 - np.cos(a))))
    prof.append((r_out, 0.0))
    prof.append((r_in, 0.0))                                    # the margin
    for k in range(1, 7):                                       # inner surface, margin up to the top
        f = k / 6
        prof.append((r_in * (1 - taper * f), top_in * f))
    prof.append((0.0, top_in))
    verts, rings = [], []
    for r, z in prof:
        if r == 0.0:
            rings.append([len(verts)])
            verts.append([0.0, 0.0, z])
        else:
            rings.append(list(range(len(verts), len(verts) + segments)))
            verts += [[r * np.cos(2 * np.pi * s / segments), r * np.sin(2 * np.pi * s / segments), z]
                      for s in range(segments)]
    faces = []
    for a, b in zip(rings[:-1], rings[1:]):
        for s in range(segments):
            s1 = (s + 1) % segments
            if len(a) == 1:
                faces.append([a[0], b[s], b[s1]])
            elif len(b) == 1:
                faces.append([a[s], b[0], a[s1]])
            else:
                faces.append([a[s], b[s], b[s1]])
                faces.append([a[s], b[s1], a[s1]])
    return Mesh(np.array(verts, dtype=float), np.array(faces), name)


def prism(polygon_xy: np.ndarray, z0: float, z1: float, name: str = "Prism") -> Mesh:
    """Extrude a convex, counter-clockwise polygon between z0 and z1."""
    p = np.asarray(polygon_xy, dtype=np.float64)
    n = len(p)
    v = np.vstack([np.column_stack([p, np.full(n, z0)]), np.column_stack([p, np.full(n, z1)])])
    i = np.arange(n)
    j = (i + 1) % n
    sides = np.vstack([np.column_stack([i, j, i + n]), np.column_stack([j, j + n, i + n])])
    k = np.arange(1, n - 1)
    bottom = np.column_stack([np.zeros(n - 2, dtype=int), k + 1, k])
    top = np.column_stack([np.full(n - 2, n), k + n, k + 1 + n])
    return Mesh(v, np.vstack([sides, bottom, top]), name)


def demo_bridge(name: str = "Bridge") -> Mesh:
    """A small arch with an overhang: handy for testing supports."""
    parts = [box(8, 10, 20).translated([0, 0, 0]),
             box(8, 10, 20).translated([32, 0, 0]),
             box(40, 10, 4).translated([0, 0, 20])]
    return Mesh.merge(parts, name)


def tube(p, q, r1: float, r2: float, segments: int = 8) -> Mesh:
    """Closed truncated cone from point p (radius r1) to point q (radius r2), in any direction."""
    p, q = np.asarray(p, dtype=np.float64), np.asarray(q, dtype=np.float64)
    axis = q - p
    length = np.linalg.norm(axis)
    if length < 1e-9:
        return Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))
    axis /= length
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = np.outer(np.cos(a), u) + np.outer(np.sin(a), v)
    verts = np.vstack([p + ring * r1, q + ring * r2, p, q])
    i = np.arange(segments)
    j = (i + 1) % segments
    cp, cq = 2 * segments, 2 * segments + 1
    sides = np.vstack([np.column_stack([i, j, i + segments]), np.column_stack([j, j + segments, i + segments])])
    caps = np.vstack([np.column_stack([np.full(segments, cp), j, i]),
                      np.column_stack([np.full(segments, cq), i + segments, j + segments])])
    return Mesh(verts, np.vstack([sides, caps]), "Tube")


def ball(center, radius: float, rings: int = 4, segments: int = 8) -> Mesh:
    """Low-poly sphere around ``center`` (used for the joints of tree supports)."""
    return sphere(radius, rings, segments).translated(np.asarray(center, dtype=np.float64) - [0, 0, radius])
