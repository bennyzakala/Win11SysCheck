"""Project files (.unislicer): models, their placement, support points and settings in one zip."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np

from . import __version__
from .mesh import Mesh
from .scene import SceneObject

EXTENSION = ".unislicer"
FORMAT_VERSION = 1


def save(path: str, objects: list[SceneObject], settings_state: dict) -> None:
    meshes: dict[int, int] = {}          # id(mesh) -> index, so duplicates share one copy
    entries = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for o in objects:
            key = id(o.mesh)
            if key not in meshes:
                meshes[key] = len(meshes)
                buf = io.BytesIO()
                np.savez_compressed(buf, vertices=o.mesh.vertices, faces=o.mesh.faces)
                zf.writestr(f"meshes/{meshes[key]}.npz", buf.getvalue())
            entries.append({
                "name": o.name, "mesh": meshes[key], "x": o.x, "y": o.y,
                "rotation": o.rotation.tolist(), "scale": o.scale.tolist(), "mirror": o.mirror.tolist(),
                "base_rotation": o.base_rotation.tolist(),
                "support_points": None if o.support_points is None else o.support_points.tolist(),
            })
        doc = {"format": FORMAT_VERSION, "generator": f"UniSlicer {__version__}",
               "settings": settings_state, "objects": entries}
        zf.writestr("project.json", json.dumps(doc, indent=1, ensure_ascii=False))


def load(path: str) -> tuple[list[SceneObject], dict]:
    with zipfile.ZipFile(path) as zf:
        doc = json.loads(zf.read("project.json"))
        if doc.get("format", 0) > FORMAT_VERSION:
            raise ValueError("This project was saved by a newer version of UniSlicer")
        cache: dict[int, Mesh] = {}
        objects = []
        for e in doc["objects"]:
            idx = int(e["mesh"])
            if idx not in cache:
                data = np.load(io.BytesIO(zf.read(f"meshes/{idx}.npz")))
                cache[idx] = Mesh(data["vertices"], data["faces"], e.get("name", ""))
            sp = e.get("support_points")
            objects.append(SceneObject(
                cache[idx], e.get("name", ""), float(e["x"]), float(e["y"]),
                np.array(e["rotation"], dtype=float), np.array(e["scale"], dtype=float),
                np.array(e["base_rotation"], dtype=float), np.array(e.get("mirror", [1, 1, 1]), dtype=float),
                None if sp is None else np.array(sp, dtype=float).reshape(-1, 3)))
    return objects, doc.get("settings", {})
