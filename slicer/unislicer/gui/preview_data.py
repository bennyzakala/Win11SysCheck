"""Turn an FDM plan into GPU line segments for the preview."""
from __future__ import annotations

import numpy as np

from ..fdm import FdmPlan
from .theme import FEATURE_ORDER

TRAVEL = FEATURE_ORDER.index("travel")
KIND_INDEX = {k: i for i, k in enumerate(FEATURE_ORDER)}


def toolpath_vertices(plan: FdmPlan) -> np.ndarray:
    """(2 * segments, 5) float32 array: x, y, z, kind, layer."""
    chunks = []
    prev_end = None
    for layer in plan.layers:
        z = layer.z - layer.height * 0.5
        for path in layer.paths:
            pts = path.points
            if path.closed:
                pts = np.vstack([pts, pts[:1]])
            if prev_end is not None:
                travel = np.array([prev_end, pts[0]])
                chunks.append((travel, TRAVEL, layer.index, z))
            chunks.append((pts, KIND_INDEX[path.kind], layer.index, z))
            prev_end = pts[-1]
    total = sum(len(c[0]) - 1 for c in chunks)
    out = np.empty((total * 2, 5), dtype=np.float32)
    i = 0
    for pts, kind, layer, z in chunks:
        n = len(pts) - 1
        if n <= 0:
            continue
        seg = out[i:i + 2 * n]
        seg[0::2, 0:2] = pts[:-1]
        seg[1::2, 0:2] = pts[1:]
        seg[:, 2] = z
        seg[:, 3] = kind
        seg[:, 4] = layer
        i += 2 * n
    return out[:i]
