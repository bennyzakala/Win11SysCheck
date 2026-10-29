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
    uid: int = field(default_factory=lambda: next(_ids))

    @classmethod
    def from_mesh(cls, mesh: Mesh, name: str = "") -> "SceneObject":
        b = mesh.bounds
        centred = mesh.translated(-(b[0] + b[1]) / 2)
        return cls(centred, name or mesh.name)

    def matrix(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3] = euler_matrix(*self.rotation) @ self.base_rotation @ np.diag(self.scale)
        return m

    def world_mesh(self) -> Mesh:
        """Mesh in bed coordinates: transformed, resting on z = 0 and centred at (x, y)."""
        m = self.mesh.transform(self.matrix())
        b = m.bounds
        offset = np.array([self.x - (b[0, 0] + b[1, 0]) / 2, self.y - (b[0, 1] + b[1, 1]) / 2, -b[0, 2]])
        out = m.translated(offset)
        out.name = self.name
        return out

    def lay_flat(self) -> None:
        current = self.mesh.transform(self.matrix())
        rot = current.lay_flat_rotation()
        self.base_rotation = euler_matrix(*self.rotation).T @ rot @ euler_matrix(*self.rotation) @ self.base_rotation

    def duplicate(self) -> "SceneObject":
        return SceneObject(self.mesh, self.name, self.x, self.y, self.rotation.copy(), self.scale.copy(),
                           self.base_rotation.copy())


def arrange(objects: list[SceneObject], bed_x: float, bed_y: float, spacing: float = 6.0) -> bool:
    """Shelf-pack object footprints around the bed centre. Returns False if they don't fit."""
    if not objects:
        return True
    sizes = [o.world_mesh().size for o in objects]
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
            objects[i].x = x + w / 2
            objects[i].y = y - row_h / 2
            x += w
        y -= row_h
    return total_h <= bed_y and all(r[1] <= bed_x + spacing for r in rows)


def out_of_bounds(objects: list[SceneObject], bed_x: float, bed_y: float, max_z: float) -> list[SceneObject]:
    bad = []
    for o in objects:
        b = o.world_mesh().bounds
        if b[0, 0] < -1e-6 or b[0, 1] < -1e-6 or b[1, 0] > bed_x + 1e-6 or b[1, 1] > bed_y + 1e-6 or b[1, 2] > max_z + 1e-6:
            bad.append(o)
    return bad
