"""Reference plate: checks that automatic supports and rafts stay sound, run on a fixed set of parts.

For a part and a configuration it measures what a user would see go wrong:
- ``sticking_out``: supports that show above a top surface of the part with nothing over them (a support
  coming out of the model);
- ``through``: supports that run through a separate solid of the part below their contact point (a pillar
  through a lower tooth) - a foot sinking into the surface it stands on and a tip sinking into its contact
  point do not count;
- ``points`` / ``used``: support points found and points that got a support (dropping many is a fault too);
- ``raft_area``: the raft under the part (none where there should be one is a fault).

``tests/test_reference_plate.py`` runs it on synthetic dental parts in every build;
``tools/reference_plate.py`` runs it on a folder of real files against a stored baseline of these numbers.
"""
from __future__ import annotations

import numpy as np

from . import sla_supports
from .mesh import Mesh
from .settings import Config


def check_part(mesh: Mesh, cfg: Config, label: str = "") -> dict:
    """The numbers above for ``mesh`` (as it lies; it is lifted to the support height first)."""
    lifted = mesh.translated([0.0, 0.0, -float(mesh.bounds[0][2]) + float(cfg["sla_elevation"])])
    pts = sla_supports.auto_support_points(lifted, cfg)
    owners: dict = {}
    sup, used = sla_supports.build_supports(lifted, cfg, pts, owners=owners, label=label)
    caster = sla_supports.caster_for(lifted)
    sticking, through = _faults(sup, owners, pts, caster)
    raft = 0.0 if cfg["sla_raft_type"] == "none" else float(cfg["sla_raft_thickness"])
    raft_area = 0.0
    if raft > 0 and len(sup.faces):
        from .slicing import slice_mesh
        raft_area = float(slice_mesh(sup, [raft * 0.5], 0.05)[0].area)
    return {"points": int(len(pts)), "used": int(len(used)), "sticking_out": len(sticking),
            "through": len(through), "raft_area": round(raft_area, 1)}


def _faults(sup: Mesh, owners: dict, pts: np.ndarray, caster) -> tuple[set, set]:
    faces = owners.get("faces")
    groups = owners.get("groups") or []
    if faces is None or len(sup.faces) == 0:
        return set(), set()
    cent = sup.triangles.mean(axis=1)
    lowest: dict = {}
    for k, g in enumerate(faces):
        if g >= 0:
            lowest[int(g)] = min(lowest.get(int(g), np.inf), float(cent[k, 2]))
    contact_z: dict = {}
    contact: dict = {}
    for g, idx in enumerate(groups):
        if len(idx):
            c = sla_supports.underside_contact(caster, pts[idx[0]])
            if c is not None:
                contact_z[g] = float(c[2])
                contact[g] = c
    sticking, through = set(), set()
    for k, p in enumerate(cent):
        g = int(faces[k])
        if g < 0:
            continue                                        # the raft
        z, fc = caster.hits(p[0], p[1])
        if len(z) == 0:
            continue
        spans = _spans(z)
        inside = [s for s in spans if s[0] < p[2] < s[1]]
        if not inside:
            above = z > p[2] + 0.05
            below = z < p[2] - 0.05
            near_tip = g in contact and float(np.linalg.norm(p - contact[g])) < HEAD_REACH
            if not above.any() and below.any() and not near_tip:
                j = int(np.argmax(np.where(below, z, -np.inf)))
                if caster.normals[fc[j], 2] > 0.3 and p[2] - z[j] > 0.3:
                    sticking.add(g)                         # standing above the part's top, open sky over it
            continue
        lo, hi = inside[0]
        cz = contact_z.get(g)
        foot = lowest.get(g, -np.inf)
        # inside a solid that ends well below the contact and above the support's own foot: run through it
        if cz is not None and hi < cz - 0.5 and hi > foot + FOOT_SINK and hi - p[2] > 0.6:
            through.add(g)
    return sticking, through


HEAD_REACH = 2.0      # mm round the contact point: the support's head, which ends at the contact (at an edge too)
FOOT_SINK = 1.5       # mm a foot may sink into the (sloped) surface it stands on


def _spans(z: np.ndarray) -> np.ndarray:
    """Inside-the-part ranges along a vertical line from its sorted surface crossings (its own copy, so the
    check measures the same way whatever version of the support code it checks)."""
    if len(z) > 1:
        z = z[np.concatenate([[True], np.diff(z) > 1e-4])]
    n = len(z) // 2 * 2
    return z[:n].reshape(-1, 2)


# the configurations every part is checked with: both support shapes, the raft types that differ most
CONFIGS = (("tree", "solid"), ("tree", "tiny"), ("pillar", "mini"), ("pillar", "none"))


def config_for(base: Config, shape: str, raft: str, model_type: str = "") -> Config:
    cfg = base.copy().update({"support_enable": True, "sla_support_type": shape, "sla_raft_type": raft})
    if model_type:
        cfg.update({"sla_model_type": model_type})
    return cfg


def _shell(outer: Mesh, inner: Mesh) -> Mesh:
    """``outer`` with the hollow ``inner`` inside it (inner faces turned to face into the hollow)."""
    return Mesh(np.vstack([outer.vertices, inner.vertices]),
                np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)]))


def _turned(mesh: Mesh, axis: str, deg: float) -> Mesh:
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    r = {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])}[axis]
    return Mesh(np.asarray(mesh.vertices) @ r.T, mesh.faces, mesh.name)


def synthetic_plate() -> list[tuple[str, Mesh, str]]:
    """Dental-like parts that once went wrong, as (name, mesh, model type): a crown and a bridge (hollow,
    upright and tilted), a shelf 0.3 mm over a lower block (pillars ran through the block), a jaw model with
    its base, a step to the gum and teeth (supports climbed to the step), and a die."""
    from . import primitives
    crown = _shell(primitives.sphere(5, 24, 48), primitives.sphere(4, 24, 48))
    bridge = Mesh.merge([crown.translated([k * 8.0, 0, 0]) for k in range(3)])
    block = primitives.box(20, 20, 6)
    shelf = primitives.box(30, 20, 2).translated([5, 0, 6.3])
    base = primitives.box(50, 40, 8)
    gum = primitives.box(52, 42, 6).translated([-1, -1, 8])
    teeth = [primitives.sphere(3.5, 12, 24).translated([6 + k * 9.5, 6, 16]) for k in range(5)]
    jaw = Mesh.merge([base, gum, *teeth])
    die = primitives.cylinder(4, 14)
    return [("crown", crown, "cb"), ("crown_tilted", _turned(crown, "x", 45), "cb"),
            ("bridge_tilted", _turned(bridge, "y", 45), "cb"),
            ("shelf_over_block", Mesh.merge([block, shelf]), ""),
            ("jaw_model", jaw, "model"), ("die", die, "model")]
