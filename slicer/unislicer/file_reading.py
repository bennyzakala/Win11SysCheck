"""Reading model files for the window, in the worker pool: parsing a file and working out what loading asks
of each mesh (closed or not, the part type, a crown's opening, a mask's flat side, the repair fingerprint) is
Python work. On threads in the window's process it held the window up to ~0.9 s at a time while a plate of
files loaded; in worker processes it does not, and the files are read on every core at once."""
from __future__ import annotations


def read_for_window(path: str, source: str, crowns_on: bool, tissue_on: bool, learned: bool):
    """(meshes, source), or (the exception, "") when the file cannot be read. What is worked out is kept on
    each mesh (its ``__dict__``), which travels back with it."""
    from . import dental_cad
    from .mesh import load_mesh
    try:
        meshes = load_mesh(path)
    except Exception as exc:
        return exc, ""
    for m in meshes:
        try:
            closed = m.is_watertight()
            kind = dental_cad.kind_of(m.name, m)
            if kind == "crown" and crowns_on:
                dental_cad.opening_direction(m)        # kept on the mesh for the turn after loading
            elif kind == "gingiva" and tissue_on:
                dental_cad.flat_side(m)
            # also for every open file: it is closed right after loading, and a repair the user then runs must
            # be learned for what was wrong with the file, not for the closed copy
            if (learned or not closed) and not (closed and len(m.faces) > 150_000):
                from . import mesh_tools
                from .assistant import repair_fingerprint
                m.__dict__["_repair_fp"] = (m.vertices, repair_fingerprint(mesh_tools.analyze(m), source))
        except Exception:
            pass                                         # worked out again later, as before
    return meshes, source
