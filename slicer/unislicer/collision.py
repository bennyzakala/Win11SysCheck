"""Collision check between parts on the plate.

Each part becomes a coarse "column map": for every XY cell under it, the lowest and highest point of the
part there. Two parts collide (or risk touching) when they share a cell whose height ranges overlap, with a
clearance. This follows the real shape, so U-shaped dental arches nested into each other are not flagged
the way bounding boxes or convex hulls would be."""
from __future__ import annotations

import numpy as np

from .mesh import Mesh


class ColumnMap:
    def __init__(self, mesh: Mesh, cell: float = 0.5):
        tris = mesh.triangles
        # sample each triangle at its corners, edge midpoints and centre (dense enough for a 0.5 mm grid
        # once large triangles are split)
        for _ in range(10):
            edges = np.linalg.norm(tris[:, [1, 2, 0], :2] - tris[:, :, :2], axis=2).max(axis=1)
            big = edges > cell * 1.5
            if not big.any():
                break
            t = tris[big]
            m01, m12, m20 = (t[:, 0] + t[:, 1]) / 2, (t[:, 1] + t[:, 2]) / 2, (t[:, 2] + t[:, 0]) / 2
            tris = np.concatenate([tris[~big], np.stack([t[:, 0], m01, m20], 1), np.stack([m01, t[:, 1], m12], 1),
                                   np.stack([m20, m12, t[:, 2]], 1), np.stack([m01, m12, m20], 1)])
        pts = np.concatenate([tris.reshape(-1, 3), tris.mean(axis=1)]).astype(np.float32)
        self.cell = cell
        self.origin = pts[:, :2].min(axis=0)
        ij = np.floor((pts[:, :2] - self.origin) / cell).astype(np.int64)
        self.shape = (int(ij[:, 0].max()) + 1, int(ij[:, 1].max()) + 1)
        flat = ij[:, 0] * self.shape[1] + ij[:, 1]
        n = self.shape[0] * self.shape[1]
        lo = np.full(n, np.inf)
        hi = np.full(n, -np.inf)
        order = np.argsort(flat, kind="stable")       # grouped reductions: much faster than ufunc.at
        f, z = flat[order], pts[order, 2]
        starts = np.flatnonzero(np.r_[True, f[1:] != f[:-1]])
        lo[f[starts]] = np.minimum.reduceat(z, starts)
        hi[f[starts]] = np.maximum.reduceat(z, starts)
        self.lo = lo.reshape(self.shape)
        self.hi = hi.reshape(self.shape)

    def shifted(self, dx: float, dy: float, dz: float = 0.0) -> "ColumnMap":
        out = object.__new__(ColumnMap)
        out.__dict__.update(self.__dict__)
        out.origin = self.origin + [dx, dy]
        if dz:
            out.lo, out.hi = self.lo + dz, self.hi + dz
        return out


def _dilate(lo, hi, r: int):
    """Grow occupied cells by ``r`` cells (clearance), keeping the widest height range."""
    if r <= 0:
        return lo, hi
    plo = np.pad(lo, r, constant_values=np.inf)
    phi = np.pad(hi, r, constant_values=-np.inf)
    out_lo, out_hi = lo.copy(), hi.copy()
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy > r * r:
                continue
            out_lo = np.minimum(out_lo, plo[r + dx:r + dx + lo.shape[0], r + dy:r + dy + lo.shape[1]])
            out_hi = np.maximum(out_hi, phi[r + dx:r + dx + lo.shape[0], r + dy:r + dy + lo.shape[1]])
    return out_lo, out_hi


def touching(a: ColumnMap, b: ColumnMap, clearance: float = 0.5) -> bool:
    """True if the parts overlap or come closer than ``clearance`` (mm) anywhere."""
    cell = a.cell
    # overlap of the two grids, in a's cells
    off = np.round((b.origin - a.origin) / cell).astype(int)
    x0, y0 = max(0, off[0]), max(0, off[1])
    x1, y1 = min(a.shape[0], off[0] + b.shape[0]), min(a.shape[1], off[1] + b.shape[1])
    r = int(np.ceil(clearance / cell))
    x0, y0, x1, y1 = x0 - r, y0 - r, x1 + r, y1 + r
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(a.shape[0], x1), min(a.shape[1], y1)
    if x1 <= x0 or y1 <= y0:
        return False
    alo, ahi = a.lo[x0:x1, y0:y1], a.hi[x0:x1, y0:y1]
    # b sampled on the same cells, grown by the clearance
    blo = np.full(alo.shape, np.inf)
    bhi = np.full(alo.shape, -np.inf)
    bx0, by0 = x0 - off[0], y0 - off[1]
    sx0, sy0 = max(0, bx0), max(0, by0)
    sx1, sy1 = min(b.shape[0], bx0 + alo.shape[0]), min(b.shape[1], by0 + alo.shape[1])
    if sx1 > sx0 and sy1 > sy0:
        blo[sx0 - bx0:sx1 - bx0, sy0 - by0:sy1 - by0] = b.lo[sx0:sx1, sy0:sy1]
        bhi[sx0 - bx0:sx1 - bx0, sy0 - by0:sy1 - by0] = b.hi[sx0:sx1, sy0:sy1]
    blo, bhi = _dilate(blo, bhi, r)
    both = np.isfinite(alo) & np.isfinite(blo)
    if not both.any():
        return False
    return bool(np.any((alo[both] <= bhi[both] + clearance) & (blo[both] <= ahi[both] + clearance)))


def colliding_pairs(maps: dict, clearance: float = 0.5) -> list[tuple]:
    """Pairs of keys whose parts touch. ``maps`` = {key: ColumnMap}; bounding boxes are checked first."""
    keys = list(maps)
    out = []
    for i in range(len(keys)):
        a = maps[keys[i]]
        a_hi = a.origin + np.array(a.shape) * a.cell
        for j in range(i + 1, len(keys)):
            b = maps[keys[j]]
            b_hi = b.origin + np.array(b.shape) * b.cell
            if np.any(a.origin > b_hi + clearance) or np.any(b.origin > a_hi + clearance):
                continue
            if touching(a, b, clearance):
                out.append((keys[i], keys[j]))
    return out
