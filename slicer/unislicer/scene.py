"""Objects placed on the build plate."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .mesh import Mesh, euler_matrix

_ids = itertools.count(1)


@dataclass
class SceneObject:
    mesh: Mesh                                  # source mesh, centred on its own origin
    name: str = ""
    x: float = 0.0                              # centre position on the bed (mm)
    y: float = 0.0
    rotation: np.ndarray = field(default_factory=lambda: np.zeros(3))   # degrees, XYZ
    scale: np.ndarray = field(default_factory=lambda: np.ones(3))
    base_rotation: np.ndarray = field(default_factory=lambda: np.eye(3))  # from "lay flat"
    mirror: np.ndarray = field(default_factory=lambda: np.ones(3))         # -1 flips that axis
    # resin support contact points in the mesh's own coordinates; None = place automatically
    support_points: np.ndarray | None = None
    # manual drain holes: (N, 6) surface point and outward normal, in the mesh's own coordinates
    drain_holes: np.ndarray = field(default_factory=lambda: np.zeros((0, 6)))
    role: str = "model"                          # model | cutter (subtracted from the other models)
    z: float = 0.0                               # height of the model's lowest point above the plate
    direct: bool = False                         # resin: printed straight on the plate, no supports or elevation
    material: str = ""                           # PolyJet: material assigned to this part ("" = the default)
    uid: int = field(default_factory=lambda: next(_ids))

    @classmethod
    def from_mesh(cls, mesh: Mesh, name: str = "") -> "SceneObject":
        b = mesh.bounds
        centred = mesh.translated(-(b[0] + b[1]) / 2)
        return cls(centred, name or mesh.name)

    def matrix(self) -> np.ndarray:
        """Rotation and scale only (no placement)."""
        m = np.eye(4)
        m[:3, :3] = euler_matrix(*self.rotation) @ self.base_rotation @ np.diag(self.scale * self.mirror)
        return m

    def _linear_bounds(self) -> np.ndarray:
        """Bounds of the rotated/scaled mesh, cached until rotation or scale change."""
        lin = self.matrix()[:3, :3]
        key = lin.tobytes()
        cache = self.__dict__.get("_bounds_cache")
        if cache is None or cache[0] != key:
            v = self.mesh.vertices @ lin.T
            cache = (key, np.array([v.min(axis=0), v.max(axis=0)]))
            self.__dict__["_bounds_cache"] = cache
        return cache[1]

    def world_matrix(self) -> np.ndarray:
        """Full transform to bed coordinates: resting on z = 0 and centred at (x, y)."""
        m = self.matrix()
        b = self._linear_bounds()
        m[:3, 3] = [self.x - (b[0, 0] + b[1, 0]) / 2, self.y - (b[0, 1] + b[1, 1]) / 2, -b[0, 2] + self.z]
        return m

    def world_bounds(self) -> np.ndarray:
        b = self._linear_bounds()
        return b + self.world_matrix()[:3, 3]

    def world_size(self) -> np.ndarray:
        b = self._linear_bounds()
        return b[1] - b[0]

    def world_mesh(self) -> Mesh:
        """Mesh in bed coordinates (a new copy; prefer world_bounds/world_matrix for cheap queries)."""
        out = self.mesh.transform(self.world_matrix())
        out.name = self.name
        return out

    @property
    def volume(self) -> float:
        return self.mesh.volume() * abs(float(np.linalg.det(self.matrix()[:3, :3])))

    def lay_flat(self) -> None:
        current = self.mesh.transform(self.matrix())
        rot = current.lay_flat_rotation()
        self.base_rotation = euler_matrix(*self.rotation).T @ rot @ euler_matrix(*self.rotation) @ self.base_rotation

    def world_support_points(self, lift: float = 0.0) -> np.ndarray | None:
        """Stored support points in bed coordinates (optionally raised by ``lift``)."""
        if self.support_points is None:
            return None
        m = self.world_matrix()
        return self.support_points @ m[:3, :3].T + m[:3, 3] + [0, 0, lift]

    def set_world_support_points(self, points: np.ndarray | None, lift: float = 0.0) -> None:
        if points is None:
            self.support_points = None
            return
        m = self.world_matrix()
        inv = np.linalg.inv(m[:3, :3])
        self.support_points = (np.asarray(points, dtype=np.float64).reshape(-1, 3) - [0, 0, lift] - m[:3, 3]) @ inv.T

    def world_drain_holes(self, lift: float = 0.0) -> np.ndarray:
        """Drain holes in bed coordinates as (N, 6) point + unit normal."""
        if len(self.drain_holes) == 0:
            return np.zeros((0, 6))
        m = self.world_matrix()
        pts = self.drain_holes[:, :3] @ m[:3, :3].T + m[:3, 3] + [0, 0, lift]
        n = self.drain_holes[:, 3:] @ np.linalg.inv(m[:3, :3])        # normals use the inverse transpose
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        return np.hstack([pts, n])

    def add_world_drain_hole(self, point, normal, lift: float = 0.0) -> None:
        m = self.world_matrix()
        inv = np.linalg.inv(m[:3, :3])
        p = (np.asarray(point, dtype=float) - [0, 0, lift] - m[:3, 3]) @ inv.T
        n = np.asarray(normal, dtype=float) @ m[:3, :3]
        n /= np.linalg.norm(n) or 1.0
        self.drain_holes = np.vstack([self.drain_holes, np.concatenate([p, n])[None, :]])

    def state(self) -> tuple:
        """Snapshot of everything the user can change (for undo)."""
        sp = None if self.support_points is None else self.support_points.copy()
        return (self.x, self.y, self.rotation.copy(), self.scale.copy(), self.base_rotation.copy(), self.mirror.copy(), sp,
                self.drain_holes.copy(), self.role, self.z, self.direct, self.material)

    def restore(self, state: tuple) -> None:
        self.x, self.y, rot, scale, base, mirror, sp, holes, self.role, self.z, self.direct, self.material = state
        self.drain_holes = holes.copy()
        self.rotation, self.scale, self.base_rotation, self.mirror = rot.copy(), scale.copy(), base.copy(), mirror.copy()
        self.support_points = None if sp is None else sp.copy()

    def apply_world_rotation(self, rot: np.ndarray) -> None:
        """Turn the object by ``rot`` (in bed coordinates), keeping its other settings."""
        e = euler_matrix(*self.rotation)
        self.base_rotation = e.T @ rot @ e @ self.base_rotation

    def auto_orient(self, technology: str, support_angle: float = 45.0, weights=None) -> None:
        from .orient import best_rotation
        current = self.mesh.transform(self.matrix())
        self.apply_world_rotation(best_rotation(current, technology, support_angle, weights=weights))

    def duplicate(self) -> "SceneObject":
        sp = None if self.support_points is None else self.support_points.copy()
        return SceneObject(self.mesh, self.name, self.x, self.y, self.rotation.copy(), self.scale.copy(),
                           self.base_rotation.copy(), self.mirror.copy(), sp, self.drain_holes.copy(), self.role, self.z,
                           self.direct, self.material)


def plate_rect(cfg) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the usable build plate: the chosen plate, centred (plus offset) on the bed."""
    bx, by = float(cfg["bed_x"]), float(cfg["bed_y"])
    w = float(cfg.get("plate_width", 0) or 0) or bx
    d = float(cfg.get("plate_depth", 0) or 0) or by
    w, d = min(w, bx), min(d, by)
    cx = min(max(bx / 2 + float(cfg.get("plate_offset_x", 0) or 0), w / 2), bx - w / 2)
    cy = min(max(by / 2 + float(cfg.get("plate_offset_y", 0) or 0), d / 2), by - d / 2)
    return cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2


def arrange(objects: list[SceneObject], bed_x: float, bed_y: float, spacing: float = 6.0,
            rect: tuple | None = None) -> bool:
    """Shelf-pack object footprints around the plate centre. Returns False if they don't fit."""
    objects = [o for o in objects if o.role != "cutter"]   # cutters stay where the user put them
    if not objects:
        return True
    if rect is not None:
        ox, oy = rect[0], rect[1]
        bed_x, bed_y = rect[2] - rect[0], rect[3] - rect[1]
    else:
        ox = oy = 0.0
    sizes = [o.world_size() for o in objects]
    order = sorted(range(len(objects)), key=lambda i: -sizes[i][1])
    rows, row, row_w, row_h = [], [], 0.0, 0.0
    for i in order:
        w, h = sizes[i][0] + spacing, sizes[i][1] + spacing
        if row and row_w + w > bed_x:
            rows.append((row, row_w, row_h))
            row, row_w, row_h = [], 0.0, 0.0
        row.append((i, w))
        row_w += w
        row_h = max(row_h, h)
    rows.append((row, row_w, row_h))
    total_h = sum(r[2] for r in rows)
    y = bed_y / 2 + total_h / 2
    for row, row_w, row_h in rows:
        x = bed_x / 2 - row_w / 2
        for i, w in row:
            objects[i].x = ox + x + w / 2
            objects[i].y = oy + y - row_h / 2
            x += w
        y -= row_h
    return total_h <= bed_y and all(r[1] <= bed_x + spacing for r in rows)


def out_of_bounds(objects: list[SceneObject], bed_x: float, bed_y: float, max_z: float,
                  rect: tuple | None = None) -> list[SceneObject]:
    x0, y0, x1, y1 = rect if rect is not None else (0.0, 0.0, bed_x, bed_y)
    bad = []
    for o in objects:
        if o.role == "cutter":
            continue
        b = o.world_bounds()
        if b[0, 0] < x0 - 1e-6 or b[0, 1] < y0 - 1e-6 or b[1, 0] > x1 + 1e-6 or b[1, 1] > y1 + 1e-6 or b[1, 2] > max_z + 1e-6:
            bad.append(o)
    return bad
