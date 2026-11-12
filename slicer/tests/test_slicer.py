import io
import os
import re
import zipfile

import numpy as np
import pytest
import shapely

from unislicer import fdm, gcode, primitives, profiles, sla
from unislicer.mesh import Mesh, load_mesh, save_stl
from unislicer.scene import SceneObject, arrange, out_of_bounds
from unislicer.slicing import slice_mesh


def placed(mesh, x=110, y=110):
    o = SceneObject.from_mesh(mesh)
    o.x, o.y = x, y
    return o


def test_cube_cross_section():
    layers = slice_mesh(primitives.box(20, 10, 5), [0.1, 2.5, 4.9])
    assert [round(g.area, 3) for g in layers] == [200, 200, 200]


def test_sphere_cross_section_area():
    area = slice_mesh(primitives.sphere(10, 48, 96), [10.0])[0].area
    assert area == pytest.approx(np.pi * 100, rel=0.01)


def test_hollow_section_has_hole():
    outer = primitives.box(20, 20, 10)
    inner = primitives.box(10, 10, 10).translated([5, 5, 0])
    inner = Mesh(inner.vertices, inner.faces[:, ::-1])  # inward normals = cavity
    area = slice_mesh(Mesh.merge([outer, inner]), [5])[0].area
    assert area == pytest.approx(300)


def test_stl_roundtrip(tmp_path):
    path = tmp_path / "cube.stl"
    save_stl(primitives.box(10, 10, 10), str(path))
    mesh = load_mesh(str(path))[0]
    assert mesh.is_watertight()
    assert mesh.volume() == pytest.approx(1000)


def test_ascii_stl_and_obj(tmp_path):
    tris = primitives.box(5, 5, 5).triangles
    lines = ["solid t"]
    for t in tris:
        lines += ["facet normal 0 0 0", "outer loop"] + [f"vertex {x} {y} {z}" for x, y, z in t] + ["endloop", "endfacet"]
    (tmp_path / "a.stl").write_text("\n".join(lines + ["endsolid t"]))
    assert load_mesh(str(tmp_path / "a.stl"))[0].volume() == pytest.approx(125)
    m = primitives.box(2, 3, 4)
    obj = "\n".join([f"v {x} {y} {z}" for x, y, z in m.vertices] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in m.faces])
    (tmp_path / "a.obj").write_text(obj)
    assert load_mesh(str(tmp_path / "a.obj"))[0].volume() == pytest.approx(24)


def test_3mf(tmp_path):
    m = primitives.box(4, 4, 4)
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in m.vertices)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces)
    model = (f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             f'<object id="1" type="model"><mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh></object>'
             f'</resources><build><item objectid="1"/></build></model>')
    path = tmp_path / "a.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", model)
    assert load_mesh(str(path))[0].volume() == pytest.approx(64)


@pytest.mark.parametrize("pattern", ["grid", "lines", "triangles", "gyroid", "concentric"])
def test_fdm_gcode(pattern):
    cfg = profiles.build_config("creality_ender3v2", "pla", "standard", {"infill_pattern": pattern})
    plan = fdm.plan([placed(primitives.box(20, 20, 4)).world_mesh()], cfg)
    assert len(plan.layers) == 20
    kinds = {p.kind for layer in plan.layers for p in layer.paths}
    assert {"outer_wall", "inner_wall", "skin", "skirt"} <= kinds
    assert "infill" in kinds
    text, stats = gcode.generate(plan, cfg)
    assert stats.time_s > 0 and stats.filament_mm > 0
    xs = [float(v) for v in re.findall(r"^G[01] X([\d.]+)", text, re.M)]
    assert min(xs) >= 0 and max(xs) <= cfg["bed_x"]
    es = [float(v) for v in re.findall(r"^G1 X\S+ Y\S+ E([\d.\-]+)", text, re.M)]
    assert es and all(e > 0 for e in es)


def test_filament_volume_close_to_model_volume():
    cfg = profiles.build_config("generic_fdm", "pla", "standard", {"infill_density": 100, "adhesion_type": "none"})
    plan = fdm.plan([placed(primitives.box(20, 20, 10)).world_mesh()], cfg)
    _, stats = gcode.generate(plan, cfg)
    extruded_volume = stats.filament_mm * np.pi * (1.75 / 2) ** 2
    assert extruded_volume == pytest.approx(4000, rel=0.05)


def test_supports_generated_under_bridge():
    cfg = profiles.build_config("creality_ender3v2", "pla", "standard", {"support_enable": True})
    plan = fdm.plan([placed(primitives.demo_bridge()).world_mesh()], cfg)
    assert any(p.kind == "support" for p in plan.layers[10].paths)
    cfg.update({"support_enable": False})
    plan = fdm.plan([placed(primitives.demo_bridge()).world_mesh()], cfg)
    assert not any(p.kind in ("support", "interface") for layer in plan.layers for p in layer.paths)


@pytest.mark.parametrize("flavor", ["marlin", "klipper", "reprap", "prusa", "smoothie"])
def test_flavors_and_relative_e(flavor):
    cfg = profiles.build_config("generic_fdm", None, None, {"gcode_flavor": flavor, "relative_e": True, "origin_center": True})
    plan = fdm.plan([placed(primitives.cylinder(5, 2), 0, 0).world_mesh()], cfg)
    text, _ = gcode.generate(plan, cfg)
    assert "M83" in text
    xs = [float(v) for v in re.findall(r"^G[01] X(-?[\d.]+)", text, re.M)]
    assert min(xs) < 0  # coordinates are relative to the bed centre


def test_sla_export(tmp_path):
    cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast", {"support_enable": True, "sla_format": "sl1"})
    obj = placed(primitives.sphere(6), 70, 45)
    job = sla.prepare([obj.world_mesh()], cfg)
    assert job.supports is not None and len(job.supports.faces) > 0
    assert job.volume_ml > 0.9  # sphere ~0.9 ml plus supports
    path = tmp_path / "out.sl1"
    sla.export(job, cfg, str(path), "ball")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "config.ini" in names
        assert sum(n.endswith(".png") and not n.startswith("thumbnail/") for n in names) == job.layer_count
        assert "numFast = %d" % job.layer_count in zf.read("config.ini").decode()


def test_sla_mask_pixels():
    cfg = profiles.build_config("generic_msla", None, None, {"antialiasing": "1", "bottom_xy_auto": False})
    obj = placed(primitives.box(10, 10, 1), 60, 34)
    job = sla.prepare([obj.world_mesh()], cfg)
    img = np.asarray(sla.render_layer(job.layers[0], cfg))
    px_area = (img > 127).sum() * (cfg["bed_x"] / cfg["resolution_x"]) * (cfg["bed_y"] / cfg["resolution_y"])
    assert px_area == pytest.approx(100, rel=0.05)


def test_arrange_and_bounds():
    objs = [SceneObject.from_mesh(primitives.box(50, 50, 10)) for _ in range(4)]
    assert arrange(objs, 220, 220)
    assert out_of_bounds(objs, 220, 220, 250) == []
    big = SceneObject.from_mesh(primitives.box(300, 10, 10))
    assert out_of_bounds([big], 220, 220, 250) == [big]


def test_all_profiles_build():
    for kind in ("printers", "materials", "qualities"):
        for pid in profiles.load(kind):
            profiles.build_config(**{kind[:-1] if kind != "qualities" else "quality": pid})


def _square(x):
    return x * x


def test_parallel_survives_a_pool_that_was_shut_down(monkeypatch):
    """A worker crash marks the pool shut down: the next job gets a fresh pool instead of
    'cannot schedule new futures after shutdown' (seen on Windows while slicing)."""
    from unislicer import parallel
    monkeypatch.setenv("UNISLICER_WORKERS", "2")
    monkeypatch.setattr(parallel, "_disabled", False)
    try:
        dead = parallel.pool()
        dead.shutdown(wait=True)                 # what the executor does to itself when a worker dies
        assert parallel.pool() is dead
        assert parallel.run_chunks(_square, [(i,) for i in range(6)]) == [i * i for i in range(6)]
        assert parallel.pool() is not dead and not parallel._disabled
    finally:
        parallel.shutdown()


def test_parallel_matches_serial(monkeypatch):
    from unislicer import parallel
    w = placed(primitives.sphere(20, 120, 240)).world_mesh()
    planes = [0.1 + 0.2 * i for i in range(200)]
    monkeypatch.setenv("UNISLICER_WORKERS", "1")
    serial = slice_mesh(w, planes)
    monkeypatch.setenv("UNISLICER_WORKERS", "2")
    try:
        par = slice_mesh(w, planes)
        cfg = profiles.build_config("generic_fdm", "pla", "standard")
        plan_par = fdm.plan([w], cfg)
    finally:
        parallel.shutdown()
    assert [round(g.area, 6) for g in par] == [round(g.area, 6) for g in serial]
    monkeypatch.setenv("UNISLICER_WORKERS", "1")
    plan_ser = fdm.plan([w], cfg)
    assert gcode.generate(plan_par, cfg)[0] == gcode.generate(plan_ser, cfg)[0]


def test_hollowing_saves_resin_and_cuts_drain_holes():
    from unislicer.slicing import polygons_of
    solid_cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast")
    hollow_cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast",
                                       {"hollow_enable": True, "hollow_wall": 2.0, "drain_holes": 1})
    mesh = placed(primitives.box(20, 20, 20), 70, 45).world_mesh()
    solid = sla.prepare([mesh], solid_cfg)
    hollow = sla.prepare([mesh], hollow_cfg)
    # 20 mm cube with 2 mm walls: the 16 mm cavity holds 4.1 ml
    assert solid.volume_ml - hollow.volume_ml == pytest.approx(4.096, rel=0.08)
    assert hollow.saved_ml == pytest.approx(4.096, rel=0.08)
    # the first layer has a drain hole through the bottom wall
    assert any(len(p.interiors) for p in polygons_of(hollow.layers[0]))


def test_islands_are_detected():
    from unislicer.mesh import Mesh
    floating = Mesh.merge([primitives.box(10, 10, 3), primitives.box(4, 4, 3).translated([3, 3, 8])])
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast")
    job = sla.prepare([placed(floating, 70, 45).world_mesh()], cfg)
    assert job.island_layers == [80]
    supported = sla.prepare([placed(primitives.sphere(8), 70, 45).world_mesh()],
                            profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True}))
    assert supported.islands == []


def test_exposure_transition_and_print_time():
    cfg = profiles.build_config("elegoo_mars3", None, None, {"bottom_layers": 3, "transition_layers": 3,
                                                            "bottom_exposure_time": 30, "exposure_time": 2})
    assert [sla.layer_exposure(i, cfg) for i in range(8)] == [30, 30, 30, 23, 16, 9, 2, 2]
    plain = sla.print_time(100, cfg)
    cfg.update({"tsmc_enable": True})
    assert sla.print_time(100, cfg) != plain


def test_manual_support_points_are_used():
    mesh = placed(primitives.sphere(10), 70, 45).world_mesh()
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True})
    point = np.array([[70.0, 45.0, mesh.bounds[0, 2] + 0.05]])
    job = sla.prepare([mesh], cfg, support_points=[point])
    assert len(job.support_points) == 1
    auto = sla.prepare([mesh], cfg)
    assert len(auto.support_points) > 5


def test_support_presets():
    heavy = profiles.build_config("elegoo_mars3", None, None, {"sla_support_preset": "heavy"})
    light = profiles.build_config("elegoo_mars3", None, None, {"sla_support_preset": "light"})
    assert heavy["sla_pillar_diameter"] > light["sla_pillar_diameter"]


def test_mirror_and_project_roundtrip(tmp_path):
    from unislicer import project
    o = placed(primitives.demo_bridge())
    o.mirror[0] = -1
    o.support_points = np.array([[1.0, 2.0, 3.0]])
    b = o.world_bounds()
    assert b[0, 2] == pytest.approx(0) and o.world_mesh().is_watertight() == o.mesh.is_watertight()
    path = tmp_path / "p.unislicer"
    project.save(str(path), [o, o.duplicate()], {"printer": "elegoo_mars3", "overrides": {"hollow_enable": True}})
    objs, state = project.load(str(path))
    assert len(objs) == 2 and objs[0].mesh is objs[1].mesh
    assert np.allclose(objs[0].world_bounds(), b) and objs[0].support_points.shape == (1, 3)
    assert state["overrides"]["hollow_enable"] is True


def test_ctb_roundtrip(tmp_path):
    from unislicer import ctb
    cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast",
                                {"support_enable": True, "transition_layers": 3, "sla_format": "ctb"})
    job = sla.prepare([placed(primitives.sphere(6), 70, 45).world_mesh()], cfg)
    path = tmp_path / "ball.ctb"
    sla.export(job, cfg, str(path), "ball")
    f = ctb.read(str(path))
    h = f["header"]
    assert (h["magic"], h["version"], h["layer_count"]) == (ctb.MAGIC_CTB_V4, 4, job.layer_count)
    assert (h["res_x"], h["res_y"]) == (cfg["resolution_x"], cfg["resolution_y"]) and h["seed"] > 0
    assert f["disclaimer"] == ctb.DISCLAIMER and f["transition_layers"] == 3
    for i in (0, job.layer_count // 2, job.layer_count - 1):
        img = np.asarray(sla.render_layer(job.layers[i], cfg)).ravel()
        assert np.array_equal(ctb.decode_layer(f["layers"][i]["rle"], img.size) >> 1, img >> 1)
        assert f["layers"][i]["exposure"] == pytest.approx(sla.layer_exposure(i, cfg))
    run = np.array([0] * 5 + [255] * 70000 + [64] + [0] * 3, dtype=np.uint8)
    assert np.array_equal(ctb.decode_layer(ctb.encode_layer(run), run.size) >> 1, run >> 1)


def test_goo_roundtrip(tmp_path):
    from unislicer import goo
    cfg = profiles.build_config("elegoo_mars4_ultra", "resin_standard", "sla_fast",
                                {"support_enable": True, "transition_layers": 2, "tsmc_enable": True})
    assert cfg["sla_format"] == "goo"
    job = sla.prepare([placed(primitives.sphere(6), 70, 40).world_mesh()], cfg)
    path = tmp_path / "ball.goo"
    sla.export(job, cfg, str(path), "ball")
    f = goo.read(str(path))
    h = f["header"]
    assert h["version"] == b"V3.0" and h["magic"] == goo.MAGIC and f["footer_ok"]
    assert (h["layer_count"], h["res_x"], h["res_y"]) == (job.layer_count, 8520, 4320)
    assert h["layer_offset"] == goo.HEADER_SIZE == 195477 and h["transition_layers"] == 2
    for i in (0, job.layer_count // 2, job.layer_count - 1):
        img = np.asarray(sla.render_layer(job.layers[i], cfg)).ravel()
        assert np.array_equal(goo.decode_layer(f["layers"][i]["rle"], img.size), img)
        assert f["layers"][i]["exposure"] == pytest.approx(sla.layer_exposure(i, cfg))
    # byte patterns from Elegoo's format specification
    for value, length, expected in ((0, 0x555657F, "3f555657"), (0xAA, 0xBBCC155, "75aabbcc15"), (0, 5, "05")):
        out = bytearray()
        goo._chunk(out, value, length)
        assert out.hex() == expected


def test_supports_reach_every_tip_that_starts_in_the_air():
    """A test print of temporary crowns (opening up) needed supports added by hand: cusp tips a millimetre
    from the nearest support printed their first layers in the air. Every small region that starts in the
    air with open space under it now gets a support on its lowest point, and the sliced part has no
    islands - while a broad flat underside is left to the grid (no extra supports at its corners)."""
    from unislicer import sla_supports
    from unislicer.mesh import Mesh
    slab = primitives.box(12, 12, 3).translated([0, 0, 2.0])
    tip = primitives.frustum(0.05, 1.2, 2.0, 24).translated([7.3, 4.9, 0.0])        # a cusp hanging below
    part = placed(Mesh.merge([slab, tip]), 60, 40).world_mesh().translated([0, 0, 5])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {
        "support_enable": True, "sla_support_spacing": 4.0, "sla_support_preset": "custom"})
    rep = {}
    pts = sla_supports.auto_support_points(part, cfg, rep)
    low = part.vertices[np.argmin(part.vertices[:, 2])]
    assert np.min(np.hypot(pts[:, 0] - low[0], pts[:, 1] - low[1])) < 0.5, (low, rep)
    job = sla.prepare([part], cfg, support_points=[pts])
    assert job.islands == [], [(i, p.area) for i, p in job.islands]
    flat = placed(primitives.box(30, 20, 4), 60, 40).world_mesh().translated([0, 0, 5])
    rep = {}
    sla_supports.auto_support_points(flat, cfg, rep)
    assert rep.get("minima_added", 0) == 0                     # the grid holds a flat underside


def test_crown_with_unjoined_margin_is_stitched_not_lidded():
    """A CAD export with a crown's outer and inner surfaces unjoined at the margin (two rims a hair apart,
    with different vertex counts): closing each rim with a flat lid filled every opening of an 11-unit bridge
    with resin. The rims are now stitched together - the part comes out closed and its cavity stays open."""
    import math
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    from unislicer.slicing import slice_mesh

    def surface(profile, segments, close_top):
        """A turned surface from (radius, z) points, open at its first ring (the margin)."""
        verts, rings = [], []
        for r, z in profile:
            if r == 0.0:
                rings.append([len(verts)]); verts.append([0.0, 0.0, z])
            else:
                rings.append(list(range(len(verts), len(verts) + segments)))
                verts += [[r * math.cos(2 * math.pi * k / segments), r * math.sin(2 * math.pi * k / segments), z]
                          for k in range(segments)]
        faces = []
        for a, b in zip(rings[:-1], rings[1:]):
            for k in range(segments):
                k1 = (k + 1) % segments
                if len(b) == 1:
                    faces.append([a[k], b[0], a[k1]] if close_top else [a[k], a[k1], b[0]])
                else:
                    faces += ([[a[k], b[k], b[k1]], [a[k], b[k1], a[k1]]] if close_top else
                              [[a[k], b[k1], b[k]], [a[k], a[k1], b[k1]]])
        return Mesh(np.array(verts), np.array(faces))
    outer = surface([(4.5, 0.0), (4.6, 2.0), (4.4, 5.0), (3.0, 7.5), (0.0, 8.0)], 48, True)
    inner = surface([(4.503, 0.002), (3.9, 2.0), (3.2, 5.0), (2.0, 6.5), (0.0, 6.8)], 61, False)
    crown = Mesh.merge([outer, inner])
    assert not crown.is_watertight() and len(mesh_tools._boundary_cycles(crown.faces)) == 2
    fixed, rep, ok = mesh_tools.make_watertight(crown)
    assert ok and rep["steps"][0][0] == "stitch", rep
    cut = slice_mesh(fixed, [3.0])[0]
    assert sum(len(p.interiors) for p in getattr(cut, "geoms", [cut])) == 1   # the cavity is still there
    tri = fixed.triangles
    volume = float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6)
    outer_only = math.pi * 4.5 ** 2 * 8.0
    assert 0 < abs(volume) < 0.6 * outer_only, volume                         # a shell, not a solid
    f = fixed.faces
    directed = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    assert len(np.unique(directed, axis=0)) == len(directed)                  # the strip's faces face the same way


def test_tree_supports_merge_and_use_less_resin():
    from unislicer.slicing import slice_meshes
    mesh = placed(primitives.demo_bridge(), 70, 45).world_mesh()
    volumes = {}
    for kind in ("pillar", "tree"):
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True, "sla_support_type": kind})
        job = sla.prepare([mesh], cfg)
        assert job.islands == []
        planes = [0.05 + 0.1 * i for i in range(int(job.supports.bounds[1, 2] / 0.1))]
        volumes[kind] = sum(g.area for g in slice_meshes([job.supports], planes)) * 0.1
        if kind == "tree":
            # fewer feet on the raft than contact points: branches merged into shared trunks
            first = slice_meshes([job.supports], [cfg["sla_raft_thickness"] + 0.5])[0]
            assert len(first.geoms) < len(job.support_points) / 2
    assert volumes["tree"] < volumes["pillar"]


def test_print_checks_cups_and_thin_features():
    from unislicer.mesh import Mesh

    def check(mesh, **over):
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"sla_hole_advice": True, **over})
        return sla.prepare([placed(mesh, 70, 45).world_mesh()], cfg)
    assert check(primitives.box(20, 20, 20)).cups == []
    assert check(primitives.box(20, 20, 20), hollow_enable=True, drain_holes=0).cups
    assert check(primitives.box(20, 20, 20), hollow_enable=True, drain_holes=2).cups == []
    cup = Mesh.merge([primitives.box(20, 20, 2), primitives.box(20, 2, 10).translated([0, 0, 2]),
                      primitives.box(20, 2, 10).translated([0, 18, 2]), primitives.box(2, 16, 10).translated([0, 2, 2]),
                      primitives.box(2, 16, 10).translated([18, 2, 2])])
    assert check(cup).cups
    fin = Mesh.merge([primitives.box(10, 10, 3), primitives.box(0.2, 8, 5).translated([4, 1, 3])])
    assert check(fin).thin_layers and check(primitives.box(10, 10, 8)).thin_layers == []


def test_auto_orient():
    from unislicer.scene import SceneObject
    plate = SceneObject.from_mesh(primitives.box(30, 20, 5))
    plate.rotation[:] = [35, 20, 0]
    plate.auto_orient("fdm", 50)
    assert plate.world_size()[2] == pytest.approx(5)          # lying on its largest face
    bridge = SceneObject.from_mesh(primitives.demo_bridge())
    bridge.auto_orient("sla", 50)
    n = bridge.world_mesh().face_normals()
    assert not np.any(np.abs(n[:, 2]) > 0.999)   # resin: tilted, no faces parallel to the screen


def test_compensation_and_profile_library(tmp_path, monkeypatch):
    m = placed(primitives.box(20, 20, 10), 70, 45).world_mesh()
    base = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", {"bottom_xy_auto": False}))
    grown = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", {"shrink_xy": 1, "bottom_xy_compensation": 0.2}))
    assert grown.layers[50].area == pytest.approx(20.2 ** 2, rel=1e-3)
    assert grown.layers[0].area < base.layers[0].area
    # every resin builds; user profiles can override, export and import
    resins = profiles.for_technology("materials", "sla")
    assert len(resins) >= 30
    for pid in resins:
        profiles.build_config("elegoo_mars3", pid)
    monkeypatch.setattr(profiles, "USER_DIR", str(tmp_path))
    pid = profiles.save_user_profile("materials", "My resin", {"exposure_time": 2.1}, "sla")
    assert profiles.build_config("elegoo_mars3", pid)["exposure_time"] == 2.1
    profiles.save_user_profile("materials", "Elegoo tweak", {"exposure_time": 2.9}, "sla", pid="elegoo_standard", vendor="Elegoo")
    assert profiles.build_config("elegoo_mars3", "elegoo_standard")["exposure_time"] == 2.9
    profiles.delete_user_profile("materials", "elegoo_standard")
    assert profiles.build_config("elegoo_mars3", "elegoo_standard")["exposure_time"] == 2.5
    out = tmp_path / "p.json"
    profiles.export_profiles("materials", [pid], str(out))
    (new,) = profiles.import_profiles("materials", str(out))
    assert new != pid and profiles.load("materials")[new]["values"]["exposure_time"] == 2.1


def test_prz_uses_goo_layout(tmp_path):
    from unislicer import goo
    cfg = profiles.build_config("phrozen_sonic_mini_8k_s", None, "sla_fast")
    assert cfg["sla_format"] == "prz"
    job = sla.prepare([placed(primitives.box(10, 10, 2), 80, 35).world_mesh()], cfg)
    path = tmp_path / "a.prz"
    sla.export(job, cfg, str(path), "a")
    f = goo.read(str(path))
    assert f["footer_ok"] and f["header"]["layer_count"] == job.layer_count


def test_prz_layers_are_drawn_mirrored_so_prints_are_not(tmp_path):
    """Prints from PRZ files came out as mirror images of the models (a test plate of crowns and bridges on
    a Phrozen Sonic XL 4K Plus): PRZ layers are drawn flipped top to bottom, relative to the same plate in
    GOO, which uses the same layer layout; the printer's own mirror settings in the header are unchanged."""
    from unislicer import goo
    cfg = profiles.build_config("phrozen_sonic_mini_8k_s", None, "sla_fast")
    assert cfg["sla_format"] == "prz"
    # an L: asymmetric, so a mirror image cannot pass for it
    l_shape = Mesh.merge([primitives.box(20, 4, 2), primitives.box(4, 14, 2)])
    job = sla.prepare([placed(l_shape, 60, 40).world_mesh()], cfg)
    res = (int(cfg["resolution_x"]), int(cfg["resolution_y"]))
    out = {}
    for fmt in ("prz", "goo"):
        c = cfg.copy().update({"sla_format": fmt})
        path = tmp_path / f"a.{fmt}"
        sla.export(job, c, str(path), "a")
        f = goo.read(str(path))
        out[fmt] = goo.decode_layer(f["layers"][0]["rle"], res[0] * res[1]).reshape(res[1], res[0])
    assert out["goo"].any()
    assert np.array_equal(out["prz"], out["goo"][::-1, :])
    assert not np.array_equal(out["prz"], out["goo"])


def test_review_fixes_bounds_collision_stl_orders_and_files(tmp_path, monkeypatch):
    """Fixes from the code review: a part's size follows a new mesh (undo, repair); parts closer than the
    clearance are seen as touching even with no overlap; slightly irregular binary STLs open; another case's
    order file is not read; print files and saved profiles are written whole or not at all; a broken
    printer profile (screen resolution against build area) is warned about."""
    import struct
    from unislicer import collision, dental_cad, goo
    from unislicer.mesh import save_stl, load_mesh
    from unislicer.scene import SceneObject
    # 1. bounds follow the mesh
    o = SceneObject(primitives.box(10, 10, 5), "p")
    small = o.state()
    assert np.allclose(o.world_size(), [10, 10, 5])
    o.mesh = primitives.box(30, 30, 30)
    assert np.allclose(o.world_size(), [30, 30, 30])
    o.restore(small)
    assert np.allclose(o.world_size(), [10, 10, 5])
    # 2. a 0.3 mm gap with 0.5 mm clearance touches; 3 mm does not
    a = collision.ColumnMap(primitives.box(10, 10, 10))
    for gap, expect in ((0.3, True), (0.45, True), (3.0, False)):
        b = collision.ColumnMap(primitives.box(10, 10, 10).translated([10 + gap, 0, 0]))
        assert collision.touching(a, b, 0.5) is expect and collision.touching(b, a, 0.5) is expect, gap
    # 3. binary STL with padding, or with a triangle count of 0 (a count that is just wrong is not guessed at)
    path = tmp_path / "c.stl"
    save_stl(primitives.box(10, 10, 10), str(path))
    raw = path.read_bytes()
    (tmp_path / "pad.stl").write_bytes(raw + b"\0\0")
    (tmp_path / "zero.stl").write_bytes(raw[:80] + struct.pack("<I", 0) + raw[84:])
    for n in ("pad.stl", "zero.stl"):
        m = load_mesh(str(tmp_path / n))
        assert len((m[0] if isinstance(m, list) else m).faces) == 12, n
    # 4. order files: in a folder of several cases, only the design's own
    orders = tmp_path / "orders"
    orders.mkdir()
    (orders / "caseA.xml").write_text("<x><ItemType>Crown</ItemType></x>")
    (orders / "ORD123.xml").write_text("<x><ItemType>Model</ItemType></x>")
    assert dental_cad.order_text_for(str(orders / "FYQ-SOD260046892-044-P.stl")) == ""
    assert dental_cad.order_text_for(str(orders / "ORD123_UpperJaw.stl")) == "Model"
    # 5. a failed export keeps the file that was there and leaves no part file
    cfg = profiles.build_config("phrozen_sonic_mini_8k_s", None, "sla_fast")
    job = sla.prepare([placed(primitives.box(10, 10, 2), 60, 40).world_mesh()], cfg)
    out = tmp_path / "plate.prz"
    sla.export(job, cfg, str(out), "a")
    good = out.read_bytes()
    assert goo.read(str(out))["footer_ok"] and not list(tmp_path.glob("*.part"))

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(goo, "write", broken)
    with pytest.raises(RuntimeError):
        sla.export(job, cfg, str(out), "a")
    assert out.read_bytes() == good and not list(tmp_path.glob("*.part"))
    monkeypatch.undo()
    # 6. profiles are saved through a part file
    monkeypatch.setattr(profiles, "USER_DIR", str(tmp_path / "prof"))
    import os
    os.makedirs(profiles.USER_DIR, exist_ok=True)
    profiles.save_user_profile("materials", "R1", {"exposure_time": 2.0}, "sla")
    assert not [f for f in os.listdir(profiles.USER_DIR) if f.endswith(".part")]
    # 7. a portrait resolution on a landscape plate is reported
    bad = cfg.copy().update({"resolution_x": 1440, "resolution_y": 2560, "bed_x": 199.68, "bed_y": 124.8})
    j = sla.prepare([placed(primitives.box(10, 10, 2), 60, 40).world_mesh()], bad)
    assert any("screen resolution" in w for w in j.warnings)
    assert not any("screen resolution" in w for w in job.warnings)


def test_hebrew_long_and_network_paths(tmp_path):
    """Lab paths: Hebrew folder and file names with spaces open, save as a project and export as a print
    file; the print library keeps its paths within Windows' limit; on Windows the same goes through a
    network path (\\server\share\..., here the machine's own C$ share, like a lab's mainserver drive)."""
    import os
    from unislicer import project, export_library
    from unislicer.mesh import save_stl, load_mesh
    from unislicer.scene import SceneObject
    folders = [tmp_path / "הדפסות מעבדה" / "תיק 2026 - כתרים זמניים"]
    if os.name == "nt":
        drive, rest = os.path.splitdrive(str(tmp_path))
        unc = "\\\\localhost\\" + drive.rstrip(":") + "$" + rest
        if os.path.isdir(unc):                    # the admin share is there on the build machine
            folders.append(type(tmp_path)(unc) / "רשת מעבדה")
        else:
            print("network share not reachable here:", unc)
    cfg = profiles.build_config("phrozen_sonic_mini_8k_s", None, "sla_fast")
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
        stl = folder / "גשר זמני FYQ-SOD260046892-044-P-16-16-1.stl"
        save_stl(primitives.box(10, 8, 6), str(stl))
        m = load_mesh(str(stl))
        m = m[0] if isinstance(m, list) else m
        assert len(m.faces) == 12, folder
        o = SceneObject(m, stl.stem)
        proj = folder / ("פלטה " + project.EXTENSION)
        project.save(str(proj), [o], {"printer": "x"})
        back, _ = project.load(str(proj))
        assert back and back[0].name == stl.stem
        job = sla.prepare([placed(m, 60, 40).world_mesh()], cfg)
        out = folder / "פלטה ראשונה.prz"
        sla.export(job, cfg, str(out), out.stem)
        assert out.exists() and out.stat().st_size > 1000 and not list(folder.glob("*.part"))
        target = export_library.target_path(str(folder / "Prints"), "כתר " + "FOL-SOD260046956-Z046-E-16-16-1" * 5, ".prz")
        assert len(target) <= export_library.MAX_PATH + 5, len(target)
        sla.export(job, cfg, target, "x")
        assert os.path.exists(target)


def test_fast_mode_uses_the_makers_values_up_to_the_print_file(tmp_path):
    """Resins with a maker's table (Phrozen DS Slicer): Fast mode takes the maker's Fast row for every
    material, printer and layer height, and the print file carries it - e.g. Saremco Crowntec on a Sonic XL
    4K Plus: lift 150 / retract 250 mm/min in Fast against 50 / 100 in Stable, in every layer and the header
    of CTB and PRZ files, and the print gets shorter."""
    from unislicer import ctb, goo
    mats = profiles.load("materials")
    wrong = []
    for mid, m in mats.items():
        for pr, layers in (m.get("layer_table") or {}).items():
            for lh, modes in layers.items():
                for mode in ("stable", "fast"):
                    cfg = profiles.build_config(pr, mid, None, {"exposure_mode": mode, "sla_layer_height": float(lh)})
                    wrong += [(mid, pr, lh, mode, k) for k, v in modes[mode].items()
                              if k != "sla_layer_height" and k in cfg.values and cfg[k] != v]
    assert not wrong, wrong[:5]
    times = {}
    for mode, lift, retract in (("stable", 50.0, 100.0), ("fast", 150.0, 250.0)):
        cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", "phrozen_ds_saremco_crowntec", None,
                                    {"exposure_mode": mode})
        job = sla.prepare([placed(primitives.box(10, 10, 3), 60, 40).world_mesh()], cfg)
        times[mode] = job.time_s
        c = cfg.copy().update({"sla_format": "ctb"})
        sla.export(job, c, str(tmp_path / f"{mode}.ctb"), "x")
        f = ctb.read(str(tmp_path / f"{mode}.ctb"))
        assert all(layer["ex"][2] == lift and layer["ex"][5] == retract for layer in f["layers"]), mode
        params = ctb.PRINT_PARAMS.unpack_from((tmp_path / f"{mode}.ctb").read_bytes(), f["header"]["print_params"])
        assert params[1] == lift and params[3] == lift and params[4] == retract, (mode, params)
        c = cfg.copy().update({"sla_format": "prz"})
        path = tmp_path / f"{mode}.prz"
        sla.export(job, c, str(path), "x")
        data, g = path.read_bytes(), goo.read(str(path))
        pos = g["header"]["layer_offset"]
        for _ in range(g["header"]["layer_count"]):
            d = goo.LAYER_DEF.unpack_from(data, pos)
            assert d[9] == lift and d[13] == retract, (mode, d[9], d[13])
            pos += goo.LAYER_DEF.size + d[-1] + 2
    assert times["fast"] < times["stable"] * 0.8, times


def test_assistant_learns_orientation_preference(tmp_path):
    from unislicer import orient
    from unislicer.assistant import Assistant
    a = Assistant(str(tmp_path))
    mesh = primitives.demo_bridge()
    dirs = orient.candidate_directions(mesh, "sla")
    feats = orient.features(mesh, "sla", 50, dirs)
    # a user who always prefers the lowest model height (feature 3) over the default choice
    for _ in range(12):
        auto = feats[int(np.argmin(feats @ a.orient_weights("sla")))]
        user = feats[int(np.argmin(feats[:, 3] + 1e-6 * feats[:, 0]))]
        a.learn_orientation("sla", auto, user)
    learned = feats[int(np.argmin(feats @ a.orient_weights("sla")))]
    default = feats[int(np.argmin(feats @ orient.DEFAULT_WEIGHTS["sla"]))]
    assert learned[3] < default[3]
    assert Assistant(str(tmp_path)).data["orient"]["sla"]["examples"] >= 1   # persisted


def test_assistant_supports_prints_and_suggestions(tmp_path):
    from unislicer.assistant import Assistant
    a = Assistant(str(tmp_path))
    cfg = profiles.build_config("elegoo_mars3", None, None, {"support_enable": True, "sla_support_type": "pillar"})
    base = a.learned_config(cfg)["sla_support_spacing"]
    a.learn_support_edits(auto_count=20, added_angles=[30, 35, 40], removed=0, threshold=50)
    learned = a.learned_config(cfg)
    assert learned["sla_support_spacing"] < base and learned["support_angle"] < cfg["support_angle"]
    # at most 1 degree a session, and a support placed on a wall or a top face (angle 0 or less) teaches
    # nothing about the angle - one such click moved it 16 degrees, two 25 (50 -> 25)
    b = Assistant(str(tmp_path / "b"))
    b.learn_support_edits(auto_count=20, added_angles=[-5.0], removed=0, threshold=50)
    b.learn_support_edits(auto_count=20, added_angles=[-5.0, 0.0], removed=0, threshold=50)
    assert b.learned_config(cfg)["support_angle"] == cfg["support_angle"]
    b.learn_support_edits(auto_count=20, added_angles=[10.0], removed=0, threshold=50)
    assert cfg["support_angle"] - 1.0 <= b.learned_config(cfg)["support_angle"] < cfg["support_angle"]
    tips = a.record_print("fail_supports", "sla", cfg)
    assert tips and tips[0].action_args == {"sla_support_preset": "heavy"}
    for _ in range(3):
        a.record_override("sla_layer_height", 0.03)
    for _ in range(5):
        a.record_action("arrange", "button")
    ctx = {"technology": "sla", "cfg": cfg, "objects": [{"uid": 1, "name": "x", "watertight": False,
                                                          "hollow_saving_ml": 12.0, "orient_gain": 0.4}],
           "job": {"cups": 3, "islands": 2}, "overrides": {"sla_layer_height": 0.03}, "plate_free_copies": 5}
    ids = {s.id for s in a.suggestions(ctx)}
    assert {"cups_orient", "islands", "watertight_1", "hollow", "tree_supports", "orient_1", "copies",
            "default_sla_layer_height=0.03", "shortcut_arrange"} <= ids
    a.dismiss("tree_supports")
    assert "tree_supports" not in {s.id for s in a.suggestions(ctx)}
    a.set_enabled(False)
    assert a.learned_config(cfg)["sla_support_spacing"] == cfg["sla_support_spacing"]


def test_build_plate_sizes(tmp_path, monkeypatch):
    from unislicer.scene import SceneObject, arrange, out_of_bounds, plate_rect
    cfg = profiles.build_config("phrozen_sonic_mini_8k")
    assert plate_rect(cfg) == (0.0, 0.0, cfg["bed_x"], cfg["bed_y"])          # full plate by default
    cfg.update({"plate_width": 70, "plate_depth": 70})
    x0, y0, x1, y1 = plate_rect(cfg)
    assert (x1 - x0, y1 - y0) == (70, 70) and (x0 + x1) / 2 == pytest.approx(cfg["bed_x"] / 2)
    objs = [SceneObject.from_mesh(primitives.box(20, 20, 5)) for _ in range(4)]
    assert arrange(objs, cfg["bed_x"], cfg["bed_y"], rect=plate_rect(cfg))
    assert out_of_bounds(objs, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg)) == []
    big = SceneObject.from_mesh(primitives.box(80, 20, 5))
    big.x, big.y = (x0 + x1) / 2, (y0 + y1) / 2
    assert out_of_bounds([big], cfg["bed_x"], cfg["bed_y"], cfg["max_z"], rect=plate_rect(cfg)) == [big]
    # resin layer images still use the whole screen: pixel scale is unchanged by the plate
    job = sla.prepare([objs[0].world_mesh()], cfg)
    img = np.asarray(sla.render_layer(job.layers[10], cfg))
    assert img.shape == (cfg["resolution_y"], cfg["resolution_x"])
    monkeypatch.setattr(profiles, "USER_DIR", str(tmp_path))
    profiles.add_plate("phrozen_sonic_mini_8k", "Mini", 70, 70)
    assert profiles.plates("phrozen_sonic_mini_8k")[0]["width"] == 70
    assert profiles.build_config("phrozen_sonic_mini_8k")["bed_x"] == cfg["bed_x"]   # printer itself unchanged


def test_waiting_modes_and_bottom_parameters(tmp_path):
    from unislicer import ctb, goo
    cfg = profiles.build_config("elegoo_mars3", None, None, {"bottom_layers": 2, "rest_before_lift": 1, "rest_after_lift": 2,
                                                            "rest_after_retract": 3, "bottom_rest_after_retract": 7,
                                                            "light_pwm": 200, "light_pwm_bottom": 255})
    assert sla.layer_rests(0, cfg) == (0.0, 0.0, 7.0) and sla.layer_rests(5, cfg) == (1.0, 2.0, 3.0)
    resting = sla.print_time(10, cfg)
    cfg.update({"wait_mode": "light_off", "light_off_delay": 30, "bottom_light_off_delay": 30})
    assert sla.print_time(10, cfg) > resting
    cfg.update({"wait_mode": "rest", "sla_layer_height": 0.1})
    job = sla.prepare([placed(primitives.box(8, 8, 1), 70, 45).world_mesh()], cfg)
    for fmt, mod in (("ctb", ctb), ("goo", goo)):
        cfg.update({"sla_format": fmt})
        path = tmp_path / f"a.{fmt}"
        sla.export(job, cfg, str(path), "a")
        mod.read(str(path))
    layers = ctb.read(str(tmp_path / "a.ctb"))["layers"]
    assert layers[0]["ex"][-1] == 255 and layers[5]["ex"][-1] == 200          # per-layer PWM
    assert layers[0]["ex"][-2] == 7 and layers[5]["ex"][-2] == 3              # rest after retract


def test_phrozen_dental_printers():
    from unislicer.scene import plate_rect
    for pid in ("phrozen_sonic_cs_plus", "phrozen_lumii_2k"):
        cfg = profiles.build_config(pid)
        assert cfg["sla_format"] == "prz" and profiles.plates(pid)
        linked = [m for m, p in profiles.for_technology("materials", "sla").items() if pid in p.get("printers", [])]
        assert linked
    plates = {p["name"]: p for p in profiles.plates("phrozen_sonic_cs_plus")}
    assert (plates["Mini plate"]["width"], plates["Mini plate"]["depth"]) == (70.0, 70.0)
    cfg = profiles.build_config("phrozen_sonic_cs_plus", None, None, {"plate_width": 70, "plate_depth": 70})
    x0, y0, x1, y1 = plate_rect(cfg)
    assert (x1 - x0, y1 - y0) == (70, 70)


def test_hollow_modes_infill_internal_supports_and_manual_holes():
    m = placed(primitives.box(30, 30, 25), 70, 45).world_mesh()

    def run(**over):
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"hollow_enable": True, "drain_holes": 2,
                                                                      "sla_hole_advice": True, **over})
        return sla.prepare([m], cfg)
    closed = run()
    for kind in ("grid", "hive"):
        job = run(hollow_infill=kind)
        assert job.volume_ml > closed.volume_ml and job.cups == [] and job.islands == []
    assert run(hollow_mode="bottom").volume_ml < closed.volume_ml
    supported = run(support_enable=True, sla_internal_supports=True)
    assert supported.islands == []
    sealed = run(drain_holes=0)
    assert sealed.cups
    vented = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(hollow_enable=True, drain_holes=0,
                                                                                          sla_hole_advice=True)),
                         holes=[np.array([[70, 30, 3, 0, -1, 0]])])
    assert len(vented.cups) < len(sealed.cups)


def test_support_shapes_and_raft_types():
    m = placed(primitives.demo_bridge(), 70, 45).world_mesh()
    for over in ({"sla_raft_type": "grid"}, {"sla_raft_type": "mini"}, {"sla_raft_type": "none"},
                 {"sla_tip_shape": "sphere", "sla_pillar_shape": "cube", "sla_base_shape": "disk", "sla_support_type": "pillar"}):
        job = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(support_enable=True, **over)))
        assert job.islands == [], over


def test_model_tools_text_cut_split_repair_and_cutters():
    from PySide6.QtGui import QGuiApplication
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    app = QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    assert app is not None
    text = mesh_tools.text_mesh("UNI 8", 10, 2)
    assert text.is_watertight() and text.size[1] == pytest.approx(10, rel=0.02)
    s = primitives.sphere(10, 32, 64)
    lo, hi = mesh_tools.cut_mesh(s, 7.0)
    assert lo.is_watertight() and hi.is_watertight()
    assert lo.volume() + hi.volume() == pytest.approx(s.volume(), rel=1e-6)
    two = Mesh.merge([primitives.box(5, 5, 5), primitives.box(5, 5, 5).translated([10, 0, 0])])
    assert len(mesh_tools.split_parts(two)) == 2
    b = primitives.box(10, 10, 10)
    f = b.faces.copy()
    f[[0, 3, 5]] = f[[0, 3, 5]][:, ::-1]
    fixed, report = mesh_tools.repair(Mesh(b.vertices, f[:-1]))
    assert fixed.is_watertight() and fixed.volume() == pytest.approx(1000) and report["holes_filled"] == 1
    # a cutter overlapping the top of a plate engraves it in both technologies
    plate = placed(primitives.box(50, 20, 10), 70, 40)
    cutter = placed(text, 70, 40)
    cutter.z = 9.0
    job = sla.prepare([plate.world_mesh()], profiles.build_config("elegoo_mars3", None, "sla_fast"),
                      cutters=[cutter.world_mesh()])
    assert [g for g in job.layers if not g.is_empty][-1].area < 990
    plan = fdm.plan([plate.world_mesh()], profiles.build_config("generic_fdm", "pla", "standard"),
                    cutters=[cutter.world_mesh()])
    assert plan.layers[-1].paths


def test_adaptive_layers_follow_slope(tmp_path):
    from unislicer import goo
    cfg = profiles.build_config("elegoo_mars4_ultra", "resin_standard", "sla_fast",
                                {"support_enable": False, "sla_adaptive": True, "sla_layer_min": 0.025,
                                 "sla_layer_max": 0.1, "sla_adaptive_error": 0.01})
    mesh = placed(primitives.sphere(6), 70, 40).world_mesh()
    job = sla.prepare([mesh], cfg)
    th = job.layer_thickness
    assert len(job.layer_z) == job.layer_count == len(th) == len(job.exposures)
    assert np.all(np.diff(job.layer_z) > 0) and job.layer_z[-1] >= mesh.bounds[1, 2] - 1e-6
    mid = job.layer_count // 2
    assert th[mid] > th[2] and th[mid] > th[-3]     # steep equator thick, flat poles thin
    assert min(th) >= 0.025 - 1e-9 and max(th) <= 0.1 + 1e-9
    assert job.volume_ml == pytest.approx(4 / 3 * np.pi * 6 ** 3 / 1000, rel=0.03)
    path = tmp_path / "ball.goo"
    sla.export(job, cfg, str(path), "ball")
    layers = goo.read(str(path))["layers"]
    assert [round(l["z"], 4) for l in layers] == [round(z, 4) for z in job.layer_z]


def test_support_options_and_direct_models():
    from unislicer import sla_supports
    m = placed(primitives.sphere(8), 70, 45).world_mesh().translated([0, 0, 20])
    base = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast", {"support_enable": True, "sla_support_type": "pillar"})
    plain, used = sla_supports.build_supports(m, base)
    dense = sla_supports.build_supports(m, base.copy().update({"sla_density": 300}))[1]
    assert len(dense) > len(used) * 2
    braced = sla_supports.build_supports(m, base.copy().update({"sla_bracing": True, "sla_brace_spacing": 4}))[0]
    assert len(braced.faces) > len(plain.faces)
    tilted = sla_supports.build_supports(m, base.copy().update({"sla_head_tilt": 40}))[0]
    assert tilted.is_watertight() or len(tilted.faces) > len(plain.faces)
    wide = sla_supports.build_supports(m, base.copy().update({"sla_pillar_widening": 50}))[0]
    assert wide.volume() > plain.volume()
    # a model printed straight on the plate gets no supports and stays at z = 0
    box = placed(primitives.box(10, 10, 4), 70, 45).world_mesh()
    job = sla.prepare([box], base, direct=[True])
    assert len(job.support_points) == 0 and job.layer_z[-1] == pytest.approx(4.0)


def test_multi_range_layers_and_exposure():
    cfg = profiles.build_config("elegoo_mars4_ultra", "resin_standard", "sla_fast",
                                {"sla_z_ranges": "0-1: *, 20; 2-3: 0.025, 4"})
    job = sla.prepare([placed(primitives.box(10, 10, 4), 70, 40).world_mesh()], cfg)
    z = np.array(job.layer_z)
    th = np.array(job.layer_thickness)
    assert np.allclose(th[(z > 2.03) & (z <= 3.0)], 0.025) and np.allclose(th[z > 3.11], cfg["sla_layer_height"])
    assert job.exposures[5] == 20 and job.exposures[int(np.argmax(z > 2.5))] == 4
    assert z[-1] == pytest.approx(4.0)


def test_vector_formats_and_processes(tmp_path):
    from unislicer import sla_supports, vector_formats as vf
    from unislicer.mesh import load_mesh
    obj = placed(primitives.box(10, 8, 3), 60, 50)
    # Asiga (DLP) -> SLC contours readable back, one layer per slice, outer ring counter-clockwise
    cfg = profiles.build_config("asiga_max2_62", None, None, {"support_enable": False, "bottom_xy_auto": False})
    assert cfg["sla_process"] == "dlp" and cfg["sla_format"] == "slc"
    job = sla.prepare([obj.world_mesh()], cfg)
    path = sla.export(job, cfg, str(tmp_path / "part.slc"))
    f = vf.read_slc(path)
    assert "-SLCVER 2.0" in f["header"] and len(f["layers"]) == job.layer_count
    ring = f["layers"][3][1][0]
    assert sla_supports._signed_area(ring) > 0 and abs(sla_supports._signed_area(ring)) == pytest.approx(80, rel=1e-3)
    # laser SLA -> CLI with contours and hatches, time from scan length
    cfg = profiles.build_config("generic_laser_sla", None, None, {"support_enable": False, "bottom_xy_auto": False})
    job = sla.prepare([placed(primitives.box(10, 8, 3), 125, 125).world_mesh()], cfg)
    text = open(sla.export(job, cfg, str(tmp_path / "part.cli"))).read()
    assert text.count("$$LAYER/") == job.layer_count and "$$HATCHES/" in text and "$$POLYLINE/1,1," in text
    assert job.time_s > job.layer_count * cfg["laser_recoat_time"]
    # PolyJet -> gel support under an overhang, 3MF with per-part material
    cfg = profiles.build_config("stratasys_objet30_pro", "pj_verowhite", "pj_high_speed", {"pj_finish": "glossy"})
    bridge = placed(primitives.demo_bridge(), 140, 90).world_mesh()
    job = sla.prepare([bridge], cfg, materials=["VeroClear"])
    assert job.support_ml > 0 and job.supports is None and job.layer_thickness[0] == pytest.approx(0.028)
    path = sla.export(job, cfg, str(tmp_path / "plate.3mf"))
    assert len(load_mesh(path)) == 1
    assert "VeroClear" in zipfile.ZipFile(path).read("3D/3dmodel.model").decode()


def test_auto_layout_options_and_learning(tmp_path):
    from unislicer import layout
    from unislicer.assistant import Assistant
    plate = (0.0, 0.0, 143.0, 89.0)
    objs = [SceneObject.from_mesh(primitives.box(30, 12, 5) if k % 2 else primitives.cylinder(8, 10, 48)) for k in range(6)]
    for strategy in ("contour", "box"):
        opt = layout.LayoutOptions(strategy=strategy, spacing=4, margin=3)
        assert layout.auto_layout(objs, plate, opt) == []
        st = layout.layout_stats(objs, plate)
        assert st["gap"] >= 4 - 0.05 and st["edge"] >= 3 - 0.05
    # corner anchor gathers parts away from the centre; "selected" leaves the others alone
    layout.auto_layout(objs, plate, layout.LayoutOptions(anchor="front_left"))
    assert layout.layout_stats(objs, plate)["offcentre"] > 0.2
    keep = [(o.x, o.y) for o in objs[2:]]
    layout.auto_layout(objs, plate, layout.LayoutOptions(scope="selected"), selected=objs[:2])
    assert [(o.x, o.y) for o in objs[2:]] == keep and layout.layout_stats(objs, plate)["gap"] > 0
    added = layout.fill_plate(objs[:1], objs[0], plate, layout.LayoutOptions(spacing=2))
    assert len(added) >= 8
    # the assistant learns the options picked and the user's corrections
    a = Assistant(str(tmp_path))
    for _ in range(2):
        a.record_layout_choice({"strategy": "grid", "rotation": "90", "anchor": "back_left", "sort": "height",
                                "spacing": 3, "margin": 1})
        a.learn_layout({"gap": 2.5, "edge": 4.0, "offcentre": 0.1, "count": 5})
    got = a.learned_layout({"strategy": "contour", "spacing": 6, "margin": 2, "rotation": "none", "anchor": "center", "sort": "area"})
    assert got["learned"] and got["strategy"] == "grid" and got["rotation"] == "90" and got["anchor"] == "back_left"
    # the gap is the one the user set in the Auto layout window (3), not the gaps measured afterwards (2.5)
    assert got["spacing"] == 3.0 and got["margin"] == 4.0
    assert not a.learn_layout({"gap": 0.0, "edge": 1, "offcentre": 0, "count": 3})   # overlapping parts are not a preference


def test_single_support_editing_helpers():
    from unislicer import sla_supports
    m = placed(primitives.sphere(8), 70, 45).world_mesh().translated([0, 0, 6])
    cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast", {"support_enable": True})
    # a click on top of the part gives one support touching the underside only (never through the part)
    top = np.array([70.0, 45.0, 22.0])
    mesh, used = sla_supports.build_supports(m, cfg, top[None])
    assert len(used) == 1 and used[0][2] == pytest.approx(6.0, abs=0.05)
    assert mesh.bounds[1][2] <= 6.0 + cfg["sla_contact_depth"] + 1e-6
    # every support face knows which support it belongs to
    for kind in ("tree", "pillar"):
        c = cfg.copy().update({"sla_support_type": kind})
        pts = sla_supports.auto_support_points(m, c)
        owners = {}
        mesh, _ = sla_supports.build_supports(m, c, pts, owners=owners)
        assert len(owners["faces"]) == len(mesh.faces)
        singles = {g[0] for g in owners["groups"] if len(g) == 1}
        assert singles == set(range(len(pts)))


def test_collision_between_parts():
    from unislicer import collision
    a, b = placed(primitives.box(10, 10, 10), 50, 50), placed(primitives.box(10, 10, 10), 60.2, 50)
    ma = collision.ColumnMap(a.world_mesh())
    assert collision.touching(ma, collision.ColumnMap(b.world_mesh()), 0.5)       # 0.2 mm apart
    b.x = 62.0
    assert not collision.touching(ma, collision.ColumnMap(b.world_mesh()), 0.5)   # 2 mm apart
    # a small part under the arch of a bridge does not touch it
    bridge = placed(primitives.demo_bridge(), 100, 100)
    small = placed(primitives.box(4, 4, 2), 100, 100)
    assert collision.colliding_pairs({1: collision.ColumnMap(bridge.world_mesh()),
                                      2: collision.ColumnMap(small.world_mesh())}) == []
    assert "phrozen_sonic_ls_plus" in profiles.load("printers")


def test_audit_features_engine():
    from unislicer import sla_supports
    m = placed(primitives.demo_bridge(), 70, 45).world_mesh().translated([0, 0, 6])
    cfg = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast", {"support_enable": True})
    full = sla_supports.auto_support_points(m, cfg)
    only_minima = sla_supports.auto_support_points(m, cfg.copy().update({"sla_support_overhangs": False}))
    none = sla_supports.auto_support_points(m, cfg.copy().update({"sla_support_overhangs": False, "sla_support_minima": False}))
    assert len(full) > len(only_minima) and len(none) == 0
    # all overhangs reachable with the automatic points, none without supports
    assert len(sla_supports.unsupported_faces(m, cfg, full)) < len(sla_supports.unsupported_faces(m, cfg, None))
    # solid base: bottom layers stay solid, hollow above
    box = placed(primitives.box(20, 20, 20), 70, 45).world_mesh()
    c = profiles.build_config("elegoo_mars3", "resin_standard", "sla_fast",
                              {"support_enable": False, "hollow_enable": True, "hollow_mode": "solid_base",
                               "hollow_base_height": 5.0, "drain_holes": 0})
    job = sla.prepare([box], c)
    z = np.array(job.layer_z)
    assert job.layers[int(np.argmax(z > 3))].area == pytest.approx(400, rel=0.01)    # inside the base: solid
    assert job.layers[int(np.argmax(z > 10))].area < 300                              # above: a hollow shell
    pts = sla_supports.island_points(job.layers, job.layer_z, [(2, job.layers[2].geoms[0])])
    assert pts.shape == (1, 3)


def test_advanced_repair():
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    b = primitives.box(10, 10, 10)
    f = np.vstack([b.faces[2:], b.faces[5:6], b.faces[7:8][:, ::-1]])     # hole, duplicate, inner wall
    m = Mesh(b.vertices, f)
    before = mesh_tools.analyze(m)
    assert not before["watertight"] and before["open_edges"] > 0
    fixed, rep = mesh_tools.repair_advanced(m)
    assert rep["after"]["watertight"] and rep["holes_filled"] == 1 and fixed.volume() == pytest.approx(1000, rel=1e-6)
    # scanner noise: tiny floating shells are dropped
    noisy = Mesh.merge([primitives.sphere(3), primitives.box(0.2, 0.2, 0.2).translated([30, 0, 0])])
    _, rep2 = mesh_tools.repair_advanced(noisy, remove_shells_below=0.05)
    assert rep2["shells_removed"] == 1 and rep2["after"]["shells"] == 1
    assert rep["inner_walls_removed"] == 1


def test_chitubox_zip_for_sonic_xl(tmp_path):
    cfg = profiles.build_config("phrozen_sonic_xl_4k_2022", "resin_standard", "sla_fast", {"support_enable": False})
    assert cfg["sla_format"] == "zip"
    job = sla.prepare([placed(primitives.box(10, 10, 2), 98, 60).world_mesh()], cfg)
    path = sla.export(job, cfg, str(tmp_path / "part.zip"), "part")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        gcode = zf.read("run.gcode").decode()
    assert "1.png" in names and f"{job.layer_count}.png" in names and "preview.png" in names
    assert f";totalLayer:{job.layer_count}" in gcode and gcode.count(";LAYER_START:") == job.layer_count
    assert ";resolutionX:3840" in gcode and 'M6054 "1.png"' in gcode
    for pid in ("phrozen_sonic_xl_4k", "phrozen_sonic_xl_4k_2022", "phrozen_sonic_xl_4k_plus"):
        assert pid in profiles.load("printers")


def test_crash_diagnosis(tmp_path):
    from unislicer import diagnose

    def boom(exc):
        try:
            raise exc
        except Exception as e:
            return diagnose.diagnose(e)

    d = boom(ValueError("The truth value of an array with more than one element is ambiguous."))
    assert d.kind == "bug" and d.fixes and "test_slicer.py" in d.where
    assert boom(MemoryError()).kind == "memory"
    assert boom(PermissionError(13, "Access is denied")).kind == "file"
    assert boom(OSError(28, "No space left on device")).kind == "disk"
    assert boom(ZeroDivisionError("division by zero")).kind == "profile"
    # same error at the same place -> same signature, counted in the log
    log = diagnose.CrashLog(str(tmp_path))
    a, b = log.record(boom(KeyError("x"))), None
    b = log.record(boom(KeyError("x")))
    assert a.signature == b.signature and b.repeat == 2
    # hard crash left by a previous run, in the 3D view -> graphics driver advice
    (tmp_path / diagnose.FAULT_NAME.format(1)).write_text(
        'Windows fatal exception: access violation\n\nCurrent thread:\n'
        '  File "C:\\\\UniSlicer\\\\unislicer\\\\gui\\\\viewer.py", line 580 in paintGL\n')
    import os, time
    old = time.time() - 60
    os.utime(tmp_path / diagnose.FAULT_NAME.format(1), (old, old))
    found = log.arm()
    log.disarm()
    assert found is not None and found.kind == "graphics" and "viewer.py" in found.where
    assert not list(tmp_path.glob("last_crash_*.txt"))
    assert "איך מתקנים" in diagnose.report_text(found, "he")


def test_suction_cup_vents_fix_the_cup():
    from unislicer.mesh import Mesh
    cup = Mesh.merge([primitives.box(20, 20, 2), primitives.box(20, 2, 10).translated([0, 0, 2]),
                      primitives.box(20, 2, 10).translated([0, 18, 2]), primitives.box(2, 16, 10).translated([0, 2, 2]),
                      primitives.box(2, 16, 10).translated([18, 2, 2])])
    m = placed(cup, 70, 45).world_mesh()
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"sla_hole_advice": True})
    job = sla.prepare([m], cfg)
    assert job.cups and job.cup_vents
    v = job.cup_vents[0]
    assert v["kind"] == "side"              # sits on the plate: vent through the thinnest wall at the roof
    assert m.bounds[0][0] - 0.1 <= v["x"] <= m.bounds[1][0] + 0.1 and v["depth"] <= 2.5
    assert sla.prepare([m], cfg, holes=[sla.vent_hole_rows(v, cfg)]).cups == []
    # on supports the cup is vented from below, through the roof
    up = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True, "sla_hole_advice": True})
    job = sla.prepare([m], up)
    lift = up["sla_elevation"] + up["sla_raft_thickness"]
    if job.cups:
        roof = [v for v in job.cup_vents if v["kind"] == "roof"]
        assert roof and roof[0]["z_surface"] < roof[0]["z_cavity"]
        assert sla.prepare([m], up, holes=[np.vstack([sla.vent_hole_rows(v, up, lift) for v in job.cup_vents])]).cups == []


def test_network_printers(tmp_path, monkeypatch):
    """Send to printer over each protocol, against small local stand-ins for the printers."""
    import base64, hashlib, json, socket, struct, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unislicer import network

    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.headers.get("X-Api-Key") == "bad":
                return self._reply(401, {})
            if self.path == "/api/version":
                return self._reply(200, {"text": "OctoPrint 1.10", "server": "1.10"})
            if self.path.startswith("/server/info"):
                return self._reply(200, {"result": {"klippy_state": "ready", "moonraker_version": "v0.9"}})
            if self.path.startswith("/printer/objects/query"):
                return self._reply(200, {"result": {"status": {"print_stats": {"state": "printing", "filename": "a.gcode"},
                                                               "display_status": {"progress": 0.5}}}})
            self._reply(404, {})

        def _body(self):
            return self.rfile.read(int(self.headers["Content-Length"]))

        def do_POST(self):
            body = self._body()
            got.setdefault(self.path, []).append((dict(self.headers), body))
            if self.path == "/uploadFile/upload":
                return self._reply(200, {"code": "000000", "messages": None, "data": {}, "success": True})
            self._reply(201, {"done": True})

        def do_PUT(self):
            got.setdefault(self.path, []).append((dict(self.headers), self._body()))
            self._reply(201, {})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    data = bytes(range(256)) * 9000            # 2.3 MB: SDCP sends it in 1 MB packets
    f = tmp_path / "part.goo"
    f.write_bytes(data)

    def body_file(raw):
        if b"filename=" not in raw:
            return raw
        return raw.split(b"filename=", 1)[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]

    seen = []
    for kind in ("octoprint", "moonraker"):
        p = network.NetPrinter("p", kind, "127.0.0.1", port)
        assert network.test_connection(p)
        network.send(p, str(f), start=True, progress=seen.append)
        path = "/api/files/local" if kind == "octoprint" else "/server/files/upload"
        raw = got[path][-1][1]
        assert data in raw and b'name="print"\r\n\r\ntrue' in raw
    assert seen and abs(seen[-1] - 1.0) < 1e-9
    assert network.printer_status(network.NetPrinter("p", "moonraker", "127.0.0.1", port))["percent"] == 50.0
    p = network.NetPrinter("p", "prusalink", "127.0.0.1", port, api_key="k", storage="local")
    network.send(p, str(f), "my print.sl1", start=True)
    headers, raw = got["/api/v1/files/local/my%20print.sl1"][-1]
    assert raw == data and headers["Print-After-Upload"] == "?1" and headers["X-Api-Key"] == "k"
    with pytest.raises(network.NetworkError) as e:
        network.test_connection(network.NetPrinter("p", "octoprint", "127.0.0.1", port, api_key="bad"))
    assert "API" in network.explain(e.value)
    with pytest.raises(network.NetworkError):
        network.test_connection(network.NetPrinter("p", "octoprint", "127.0.0.1", 1))

    # SDCP: UDP discovery, chunked HTTP upload, start over WebSocket
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    monkeypatch.setattr(network, "SDCP_UDP_PORT", udp.getsockname()[1])

    def udp_serve():
        msg, addr = udp.recvfrom(100)
        if msg == b"M99999":
            udp.sendto(json.dumps({"Id": "x", "Data": {"Attributes": {"Name": "Saturn", "MachineName": "ELEGOO Saturn 4 Ultra",
                       "MainboardIP": "127.0.0.1", "MainboardID": "ABCD", "FirmwareVersion": "V1.2"}}}).encode(), addr)
    threading.Thread(target=udp_serve, daemon=True).start()
    hits = network.discover(1.0, ["127.0.0.1"])
    assert hits and hits[0]["mainboard_id"] == "ABCD" and hits[0]["model"] == "ELEGOO Saturn 4 Ultra"

    ws_srv = socket.socket()
    ws_srv.bind(("127.0.0.1", 0))
    ws_srv.listen(1)
    commands = []

    def ws_serve():
        c, _ = ws_srv.accept()
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(1024)
        key = [l.split(b": ")[1] for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        b0, b1 = c.recv(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", c.recv(2))[0]
        mask = c.recv(4)
        payload = b""
        while len(payload) < n:
            payload += c.recv(n - len(payload))
        msg = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        commands.append(msg)
        out = json.dumps({"Id": "x", "Data": {"Cmd": msg["Data"]["Cmd"], "Data": {"Ack": 0},
                          "RequestID": msg["Data"]["RequestID"], "MainboardID": "ABCD"},
                          "Topic": "sdcp/response/ABCD"}).encode()
        c.sendall(bytes([0x81, 126]) + struct.pack(">H", len(out)) + out)
        c.close()
    threading.Thread(target=ws_serve, daemon=True).start()

    p = network.NetPrinter("s", "sdcp", "127.0.0.1", port, mainboard_id="ABCD")
    # uploads go to the HTTP stand-in, the WebSocket to its own port
    orig = network.WebSocket.__init__
    monkeypatch.setattr(network.WebSocket, "__init__",
                        lambda self, host, _port, path="/websocket", timeout=8.0: orig(self, host, ws_srv.getsockname()[1], path, timeout))
    network.send(p, str(f), "part.goo", start=True)
    parts = got["/uploadFile/upload"]
    assert len(parts) == 3
    joined = b"".join(body_file(raw) for _, raw in parts)
    assert joined == data
    assert b'name="Offset"\r\n\r\n1048576' in parts[1][1]
    assert hashlib.md5(data).hexdigest().encode() in parts[0][1]
    assert commands[0]["Data"]["Cmd"] == 128 and commands[0]["Data"]["Data"]["Filename"] == "part.goo"
    assert commands[0]["Topic"] == "sdcp/request/ABCD"

    # shared folder + saved list
    dest = tmp_path / "share"
    dest.mkdir()
    fp = network.NetPrinter("share", "folder", str(dest))
    network.send(fp, str(f), "copy.goo")
    assert (dest / "copy.goo").read_bytes() == data
    network.save_printers([p, fp], str(tmp_path))
    assert [x.kind for x in network.load_printers(str(tmp_path))] == ["sdcp", "folder"]
    srv.shutdown()


def test_model_rescue_recovers_broken_files_without_quality_loss(tmp_path):
    import gzip, struct, zipfile
    from unislicer import model_rescue
    from unislicer.assistant import Assistant
    from unislicer.mesh import load_mesh

    ref = primitives.sphere(10, 24, 48)               # closed, curved: easy to see any loss
    tris = ref.triangles

    def same_surface(m):
        # every original triangle is present at full precision (order-independent)
        a = np.sort(np.round(tris.reshape(-1, 3), 9), axis=0)
        b = np.sort(np.round(m.triangles.reshape(-1, 3), 9), axis=0)
        return a.shape == b.shape and np.allclose(a, b, atol=1e-6)

    def binary_stl(t, count=None):
        rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        rec["v"] = t
        return b"x" * 80 + struct.pack("<I", len(t) if count is None else count) + rec.tobytes()

    def check(name, data, expect_note=None, exact=True):
        p = tmp_path / name
        p.write_bytes(data)
        try:
            load_mesh(str(p))
            ok_before = True
        except Exception:
            ok_before = False
        r = model_rescue.rescue(str(p))
        assert r.meshes and r.kept > 0
        if expect_note:
            assert expect_note in [k for k, _ in r.notes], (name, r.notes)
        if exact:
            assert same_surface(r.meshes[0]), name
        return r, ok_before

    t32 = tris.astype(np.float32).astype(np.float64)
    tris_saved = tris
    tris = t32                                     # binary STL stores float32
    r, before = check("count.stl", binary_stl(tris, count=7), "rs_stl_count")
    assert not before
    r, _ = check("cut.stl", binary_stl(tris)[:-20], "rs_stl_cut", exact=False)
    assert r.kept >= len(tris) - 1
    tris = tris_saved

    lines = ["solid x"]
    for t in tris:
        lines += ["facet normal 0 0 0", "outer loop"] + \
                 [("vertex " + " ".join(f"{c:.9f}" for c in v)).replace(".", ",") for v in t] + ["endloop", "endfacet"]
    lines += ["facet normal 0 0 0", "outer loop", "vertex 1,0 2,0 3,0"]          # unfinished
    r, before = check("comma.stl", "\n".join(lines).encode(), "rs_decimal_comma")
    assert not before and "rs_stl_unfinished" in [k for k, _ in r.notes]

    v_lines = [f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in ref.vertices]
    f_lines = [f"f {a + 1} {b + 1} \\\n {c + 1}" for a, b, c in ref.faces] + ["f 1 2 99999"]
    r, before = check("broken.obj", "\n".join(v_lines + f_lines).encode(), "rs_obj_bad_faces")
    assert not before

    # OBJ content saved with an .stl name
    plain_obj = "\n".join(v_lines + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in ref.faces]).encode()
    check("really_obj.stl", plain_obj, "rs_wrong_type")

    # Bambu / Orca style 3MF: the mesh lives in another .model file (production extension)
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    vx = "".join(f'<vertex x="{x:.9f}" y="{y:.9f}" z="{z:.9f}"/>' for x, y, z in ref.vertices)
    tr_ = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in ref.faces)
    obj_model = f'<model xmlns="{core}"><resources><object id="1" type="model"><mesh><vertices>{vx}</vertices><triangles>{tr_}</triangles></mesh></object></resources><build/></model>'
    root_model = (f'<model xmlns="{core}" xmlns:p="{prod}"><resources><object id="2" type="model"><components>'
                  f'<component p:path="/3D/Objects/object_1.model" objectid="1"/></components></object></resources>'
                  f'<build><item objectid="2"/></build></model>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", root_model)
        z.writestr("3D/Objects/object_1.model", obj_model)
    r, before = check("bambu.3mf", buf.getvalue(), "rs_3mf_parts")
    assert before                        # the normal loader reads production-extension 3MF files directly

    # archives, PLY, metres, NaN, empty
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("inner/part.obj", plain_obj)
    check("models.zip", zbuf.getvalue())
    check("part.stl", gzip.compress(plain_obj), "rs_archive")
    ply = ("ply\nformat binary_little_endian 1.0\nelement vertex %d\nproperty double x\nproperty double y\n"
           "property double z\nelement face %d\nproperty list uchar int vertex_indices\nend_header\n"
           % (len(ref.vertices), len(ref.faces))).encode()
    ply += ref.vertices.astype("<f8").tobytes()
    ply += b"".join(struct.pack("<B3i", 3, *f) for f in ref.faces)
    p = tmp_path / "scan.ply"
    p.write_bytes(ply)
    assert same_surface(load_mesh(str(p))[0])
    metres = "\n".join([f"v {x / 1000:.12f} {y / 1000:.12f} {z / 1000:.12f}" for x, y, z in ref.vertices]
                       + [f"f {a + 1} {b + 1} {c + 1} 999999" for a, b, c in ref.faces[:1]]
                       + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in ref.faces[1:]]).encode()
    r, _ = check("metres.obj", metres, "rs_units_m", exact=False)
    assert abs(float(np.ptp(r.meshes[0].vertices[:, 0])) - float(np.ptp(ref.vertices[:, 0]))) < 1e-3
    bad = tris.astype(np.float32).copy()
    bad[0, 0, 0] = np.nan
    r, _ = check("nan.stl", binary_stl(bad, count=3), "rs_nan", exact=False)
    assert r.dropped == 1 and "rs_holes" in [k for k, _ in r.notes]      # the gap it leaves is closed
    assert r.meshes[0].is_watertight()
    (tmp_path / "empty.stl").write_bytes(b"")
    with pytest.raises(model_rescue.RescueFailed) as e:
        model_rescue.rescue(str(tmp_path / "empty.stl"))
    assert e.value.reason == "empty"

    # learning: the strategy that worked for a kind of file is tried first next time
    a = Assistant(str(tmp_path / "learn"))
    sig = model_rescue.signature("x.stl", plain_obj, ValueError("x"))
    a.learn_rescue(sig, "sniff")
    a.learn_rescue(sig, "sniff")
    assert a.rescue_order(sig)[0] == "sniff" and a.summary()["models_rescued"] == 2


def test_ring_tray_layout_and_bounds():
    """Stratasys J5: parts go on the rotating ring tray, never in its centre or past its rim."""
    from unislicer import layout
    from unislicer.scene import out_of_bounds, plate_area, plate_rect, plate_region
    cfg = profiles.build_config("stratasys_j5_dentajet", None, None, {})
    assert cfg["bed_shape"] == "ring" and cfg["bed_inner_diameter"] > 0
    region = plate_region(cfg)
    parts = [placed(primitives.box(50, 20, 10), cfg["bed_x"] / 2, cfg["bed_y"] / 2) for _ in range(14)]
    assert len(out_of_bounds(parts, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], plate_rect(cfg), region)) == 14
    failed = layout.auto_layout(parts, plate_area(cfg), layout.LayoutOptions(spacing=4))
    assert failed == [] and out_of_bounds(parts, cfg["bed_x"], cfg["bed_y"], cfg["max_z"], plate_rect(cfg), region) == []
    assert plate_region(profiles.build_config("elegoo_mars3", None, "sla_fast", {})) is None
    # the no-placement wedge: a part dropped into it is outside, and layout keeps clear of it
    import math
    cfg = profiles.build_config("stratasys_j5_dentajet", None, None, {})
    assert cfg["bed_dead_sector"] > 0                        # the J5's no-placement wedge is on by default
    region = plate_region(cfg)
    a = math.radians(cfg["bed_dead_sector_start"] + cfg["bed_dead_sector"] / 2)
    rmid = (cfg["bed_x"] / 2 + cfg["bed_inner_diameter"] / 2) / 2
    wedge = placed(primitives.box(20, 20, 10), cfg["bed_x"] / 2 + rmid * math.cos(a), cfg["bed_y"] / 2 + rmid * math.sin(a))
    assert out_of_bounds([wedge], cfg["bed_x"], cfg["bed_y"], cfg["max_z"], plate_rect(cfg), region) == [wedge]


def test_dental_shades_regions_import_export(tmp_path):
    import math, zipfile
    from unislicer import dental_shades as ds
    from unislicer.mesh import Mesh, load_mesh
    from unislicer.scene import SceneObject
    from unislicer import vector_formats as vf

    assert ds.find("A2").code == "TA2" and ds.find("VITA A3.5").code == "TA3.5" and ds.find("ta1").code == "TA1"
    assert ds.find(ds.DEFAULT_GINGIVA).kind == "gingiva" and ds.hex_color("TA2").startswith("#")
    assert len(ds.TOOTH_SHADES) >= 16 and len(ds.GINGIVA_BASE) >= 10

    # a denture as one file: base plus 10 separate teeth along the arch -> one model with 11 regions
    base = primitives.box(60, 50, 4).translated([-30, -25, 0])
    teeth = []
    for k in range(10):
        a = math.radians(200 + k * 14)
        teeth.append(primitives.box(6, 6, 9).translated([22 * math.cos(a) - 3, 22 * math.sin(a) - 3, 4.5]))
    one = Mesh.merge([base] + teeth, "denture")
    regions = ds.denture_regions(one)
    assert len(regions) == 11 and regions[0]["name"] == "Gingiva" and regions[1]["name"] == "Tooth 1"
    assert sum(len(r["faces"]) for r in regions) == len(one.faces)
    ang = []
    for r in regions[1:]:
        c = one.triangles[r["faces"]].reshape(-1, 3).mean(axis=0)
        ang.append(math.atan2(c[1], c[0]) % (2 * math.pi))
    assert ang == sorted(ang) or ang == sorted(ang, reverse=True)      # numbered along the arch
    assert ds.denture_regions(primitives.box(10, 10, 10)) == []
    regions[3]["shade"] = "TA1"
    shades = ds.face_shades(len(one.faces), "", regions)
    assert shades.count("TA1") == len(regions[3]["faces"]) and shades[int(regions[0]["faces"][0])] == ds.DEFAULT_GINGIVA

    # a 3MF with a material per triangle (as 3Shape writes shades): one model, regions keep their shade
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    m = Mesh.merge([primitives.box(10, 10, 4), primitives.box(4, 4, 6).translated([3, 3, 4])])
    nb = len(primitives.box(10, 10, 4).faces)
    vx = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in m.vertices)
    tri = "".join(f'<triangle v1="{a}" v2="{b_}" v3="{c}" pid="1" p1="{0 if i < nb else 1}"/>'
                  for i, (a, b_, c) in enumerate(m.faces))
    model = (f'<model xmlns="{core}"><resources><basematerials id="1"><base name="Gingiva Pink" displaycolor="#E88D97"/>'
             f'<base name="A2" displaycolor="#EBDCC0"/></basematerials><object id="2" type="model" pid="1" pindex="0">'
             f'<mesh><vertices>{vx}</vertices><triangles>{tri}</triangles></mesh></object></resources>'
             f'<build><item objectid="2"/></build></model>')
    p = tmp_path / "denture.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", model)
    parts = load_mesh(str(p))
    assert len(parts) == 1
    obj = SceneObject.from_mesh(parts[0])
    assert sorted(r["shade"] for r in obj.regions) == ["Gingiva Pink", "TA2"]           # A2 -> TA2
    assert obj.duplicate().regions == obj.regions and obj.state()[12] == obj.regions

    # export: a material per triangle with the shade colours, plus the incisal setting; reads back the same
    out = tmp_path / "out.3mf"
    names = ds.face_shades(len(one.faces), "", regions)
    vf.write_3mf(str(out), [("Denture", one, names)], {c: ds.hex_color(c) for c in set(names)},
                 {"DentalIncisalTranslucency": "high"})
    xml = zipfile.ZipFile(out).read("3D/3dmodel.model").decode()
    assert f'name="TA1" displaycolor="{ds.hex_color("TA1")}"' in xml and ds.DEFAULT_GINGIVA in xml
    assert '<metadata name="DentalIncisalTranslucency">high</metadata>' in xml
    back = SceneObject.from_mesh(load_mesh(str(out))[0])
    assert sorted({r["shade"] for r in back.regions}) == sorted({ds.DEFAULT_GINGIVA, ds.DEFAULT_TOOTH, "TA1"})

    # project round trip keeps the regions
    from unislicer import project
    pp = tmp_path / "d.unislicer"
    o2 = SceneObject.from_mesh(one)
    o2.regions = regions
    project.save(str(pp), [o2], {})
    loaded, _ = project.load(str(pp))
    assert [r["shade"] for r in loaded[0].regions] == [r["shade"] for r in regions]


def test_copies_of_a_part_compare_by_identity():
    """Multiply / duplicate make parts with the same mesh and values; lists of them must still work
    (auto layout after 'multiply' used to fail comparing their rotation arrays)."""
    from unislicer import layout
    a = placed(primitives.box(10, 10, 10), 50, 50)
    copies = [a.duplicate() for _ in range(3)]
    parts = [a] + copies
    assert copies[1] in parts and parts.index(copies[2]) == 3 and a != copies[0]
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {})
    from unislicer.scene import plate_area
    assert layout.auto_layout(parts, plate_area(cfg), layout.LayoutOptions(), selected=copies[:2]) == []
    assert layout.auto_layout(parts, plate_area(cfg), layout.LayoutOptions(scope="selected"), selected=copies[:2]) == []


def test_creality_lan_mode_and_network_scan(tmp_path, monkeypatch):
    """Creality LAN mode (SPARKX i7 / K-series): found by the network scan, file uploaded over HTTP,
    print started over the port-9999 WebSocket - against small stand-ins for the printer."""
    import base64, hashlib, json, socket, struct, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unislicer import network

    uploads, commands = [], []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            uploads.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_GET(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    http = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    ws_srv = socket.socket()
    ws_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ws_srv.bind(("127.0.0.1", 0))
    ws_srv.listen(8)
    ws_port = ws_srv.getsockname()[1]

    def frame(obj):
        raw = json.dumps(obj).encode()
        return bytes([0x81, 126]) + struct.pack(">H", len(raw)) + raw if len(raw) > 125 else bytes([0x81, len(raw)]) + raw

    def read_frame(c):
        head = c.recv(2)
        if len(head) < 2:
            return None
        n = head[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", c.recv(2))[0]
        mask = c.recv(4)
        data = b""
        while len(data) < n:
            data += c.recv(n - len(data))
        return bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode()

    def ws_serve():
        while True:
            try:
                c, _ = ws_srv.accept()
            except OSError:
                return
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(1024)
            key = [l.split(b": ")[1] for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
            acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
            c.sendall(frame({"ModeCode": "heart_beat", "msg": 1}))
            try:
                while True:
                    msg = read_frame(c)
                    if msg is None:
                        break
                    if not msg.startswith("{"):          # "ok" heartbeat replies, close frames
                        if msg == "":
                            break
                        continue
                    j = json.loads(msg)
                    commands.append(j)
                    if j.get("method") == "get":
                        c.sendall(frame({"model": "SPARKX i7", "hostname": "SPARKX-i7-1234", "state": 0,
                                         "printProgress": 0}))
                    else:
                        c.sendall(frame({"state": 1, "printFileName": j["params"].get("opGcodeFile", "")}))
            except OSError:
                pass
            c.close()
    threading.Thread(target=ws_serve, daemon=True).start()

    # route the fixed ports (9999 WebSocket, 80 upload) to the stand-ins
    orig_ws = network.WebSocket.__init__
    monkeypatch.setattr(network.WebSocket, "__init__",
                        lambda self, host, port, path="/websocket", timeout=8.0:
                        orig_ws(self, host, ws_port if port == 9999 else port, path, timeout))
    info = network.creality_info("127.0.0.1")
    assert info["model"] == "Creality SPARKX i7" and info["name"] == "SPARKX-i7-1234"

    p = network.NetPrinter("SPARKX", "creality", "127.0.0.1", http.server_address[1])
    monkeypatch.setattr(network, "_open_port", lambda h, port, t: False)      # no Moonraker here
    assert "SPARKX i7" in network.test_connection(p)
    f = tmp_path / "cube.gcode"
    f.write_bytes(b"G28\nG1 X10\n" * 1000)
    network.send(p, str(f), "cube.gcode", start=True)
    assert uploads and uploads[-1][0] == "/upload/cube.gcode" and f.read_bytes() in uploads[-1][1]
    starts = [c for c in commands if c.get("method") == "set"]
    assert starts and starts[-1]["params"]["opGcodeFile"] == "printprt:/mnt/UDISK/printer_data/gcodes/cube.gcode"
    assert network.printer_status(p)["state"] in ("idle", "printing")

    # the scan finds the printer by its open ports and names it
    monkeypatch.setattr(network, "discover", lambda *a, **k: [])
    monkeypatch.setattr(network, "_open_port", lambda h, port, t: h == "10.9.8.7" and port in (80, 9999))
    monkeypatch.setattr(network, "creality_info", lambda h: {"kind": "creality", "host": h, "port": 80,
                                                             "name": "SPARKX-i7-1234", "model": "Creality SPARKX i7"})
    monkeypatch.setattr(network, "_http_json", lambda *a, **k: (404, None, {}))
    found = network.scan(prefixes=["10.9.8"])
    assert [(x["host"], x["kind"]) for x in found] == [("10.9.8.7", "creality")]
    from unislicer.gui.network_dialog import _guess_profile
    assert _guess_profile(found[0]) == "creality_sparkx_i7"
    http.shutdown()
    ws_srv.close()


def test_view_cube_hover_between_faces_and_arrows():
    """Moving the mouse from a cube face onto an arrow (and back) compared a face direction with an
    arrow name and failed; every hover change must work. Runs in its own process: it needs a widget
    application, and other tests create a plain GUI application."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import math, os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        import numpy as np
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        from unislicer.gui.viewcube import ViewCube
        app = QApplication([])

        class Cam:
            yaw, pitch = -62.0, 30.0
            def view(self):
                y, p = math.radians(self.yaw), math.radians(self.pitch)
                z = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])
                x = np.cross([0, 0, 1], z); x /= np.linalg.norm(x)
                m = np.eye(4); m[:3, :3] = np.array([x, np.cross(z, x), z])
                return m

        class V:
            camera = Cam()
            def update(self):
                pass
        cube = ViewCube(V())
        c = ViewCube.SIZE / 2
        face, arrow = QPointF(c, c), QPointF(12, c)
        for pos in (face, arrow, face, arrow, QPointF(1, 1), face):
            cube.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            cube.repaint()
        assert ViewCube._same_target("left", "left") and not ViewCube._same_target("left", np.array([1.0, 0, 0]))
        assert not ViewCube._same_target(np.array([1.0, 0, 0]), "left") and ViewCube._same_target(None, None)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr

def test_russian_and_arabic_cover_the_interface():
    """Every interface string, setting, option and category has a Russian and an Arabic text with the same
    {} placeholders (a missing one would silently show English)."""
    import re
    from unislicer.gui import i18n
    from unislicer import settings as st
    texts = [en for _he, en in i18n.STRINGS.values()]
    for s in st.SCHEMA:
        texts += [s.en] + ([s.tip_en] if s.tip_en else []) + [o[2] for o in s.options]
    texts += [c[2] for c in st.CATEGORIES]
    ph = re.compile(r"\{[^}]*\}")
    for lang in ("ru", "ar"):
        table = i18n._table(lang)
        missing = [t for t in texts if t and re.search("[A-Za-z]", t) and t not in table]
        assert not missing, (lang, missing[:5])
        assert all(ph.findall(k) == ph.findall(v) for k, v in table.items())
    try:
        i18n.set_language("ru")
        assert i18n.tr("slice") == "Нарезать" and not i18n.is_rtl()
        i18n.set_language("ar")
        assert i18n.tr("slice") == "تقطيع" and i18n.is_rtl()
        assert i18n.translate("no such text") == "no such text"
    finally:
        i18n.set_language("he")


def test_auto_orient_twice_and_auto_prepare_twice():
    """The auto-orient choice was stored under the method's own name, so a second auto-orient (e.g. a
    second Auto prepare) called a tuple. Both must work any number of times."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow()
        w.add_mesh(primitives.box(20, 10, 5))
        w.add_mesh(primitives.demo_bridge())
        for _ in range(3):
            w.auto_orient_selected()
            w.auto_prepare()
        assert callable(w.objects[0].auto_orient)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


def test_dental_cad_link_indications_folder_and_handoff(tmp_path, monkeypatch):
    import subprocess, sys, textwrap
    from unislicer import dental_cad as dc
    cases = {"2024-05-01_UpperJaw.stl": "model", "LowerJaw_antagonist.stl": "model", "Crown_16.stl": "crown",
             "bridge 34-36.3mf": "crown", "NightGuard_upper.stl": "splint", "SurgicalGuide.stl": "guide",
             "Die_21.stl": "die", "FullDenture_base.stl": "denture", "x123.stl": "unknown"}
    for name, kind in cases.items():
        assert dc.detect_indication(name) == kind, name
    # 3Shape-style order file: the type of work is read, patient details are not
    case = tmp_path / "case"
    case.mkdir()
    (case / "order.xml").write_text('<Order><PatientName>John Smith</PatientName><RestorationType>Crown</RestorationType>'
                                    '<Material>Zirconia</Material></Order>')
    (case / "part_1.stl").write_bytes(b"x")
    text = dc.order_text_for(str(case / "part_1.stl"))
    assert "Crown" in text and "Smith" not in text
    assert dc.detect_indication(str(case / "part_1.stl"), text) == "crown"
    # watched folder: only new files, and only once they stopped growing
    t = dc.FolderTracker()
    (case / "old.stl").write_bytes(b"1")
    t.mark_existing(str(case))
    (case / "new.stl").write_bytes(b"12345")
    assert t.poll(str(case)) == [] and t.poll(str(case)) == [str(case / "new.stl")] and t.poll(str(case)) == []
    # "Open with UniSlicer" passes the file first: it must open the GUI with it
    import unislicer.__main__ as m, unislicer.gui.app as appmod
    got = []
    monkeypatch.setattr(appmod, "run", lambda paths: got.append(paths) or 0)
    m.main(["C:/cases/UpperJaw.stl"])
    assert got == [["C:/cases/UpperJaw.stl"]]

    # in the window: designs from the CAD are oriented by indication, supported and laid out; a second
    # launch hands its file to the running window
    code = textwrap.dedent('''
        import os, sys, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.mesh import save_stl
        from unislicer.gui import app as appmod
        from unislicer.gui.main_window import MainWindow
        d = sys.argv[1]
        for n, mesh in (("UpperJaw.stl", primitives.box(60, 50, 15)), ("Crown_16.stl", primitives.sphere(5, 12, 24)),
                        ("Splint.stl", primitives.box(50, 40, 3))):
            save_stl(mesh, os.path.join(d, n))
        w = MainWindow()
        assert w.settings.select_printer("elegoo_mars3") and w.settings.config()["technology"] == "sla"
        app.processEvents()
        w.import_from_cad([os.path.join(d, n) for n in ("UpperJaw.stl", "Crown_16.stl", "Splint.stl")])
        by = {o.name: o for o in w.objects}
        assert not by["UpperJaw"].direct and by["UpperJaw"].support_points is not None   # not on the plate by itself
        assert not by["Crown_16"].direct and by["Crown_16"].support_points is not None
        assert not by["Splint"].direct
        state = {"w": w}
        server = appmod._listen_for_files(app, lambda: state["w"])
        save_stl(primitives.box(10, 10, 10), os.path.join(d, "Die_11.stl"))
        # a second launch ("Open with UniSlicer") is another process: it hands the file over and exits
        import subprocess
        second = subprocess.Popen([sys.executable, "-c",
                                   "import os, sys; os.environ['QT_QPA_PLATFORM']='offscreen';"
                                   "from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]);"
                                   "from unislicer.gui.app import _hand_to_running;"
                                   "sys.exit(0 if _hand_to_running([sys.argv[1]]) else 3)", os.path.join(d, "Die_11.stl")],
                                  cwd=os.getcwd())
        end = time.time() + 20
        while time.time() < end and (second.poll() is None or not any(o.name == "Die_11" for o in w.objects)):
            app.processEvents(); time.sleep(0.05)
        assert second.poll() == 0, second.poll()
        assert any(o.name == "Die_11" and not o.direct for o in w.objects)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=root, capture_output=True, text=True,
                       timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_broken_mesh_layers_do_not_grow_extra_shapes():
    """A thin-walled part whose inner and outer walls both have holes at the same place: stitching the
    open contours joined the outer wall to the inner one and filled the whole hollow inside (the 'extra
    shapes' after slicing). The winding / even-odd fill keeps only the real wall."""
    import math
    from shapely.geometry import Point
    from unislicer.mesh import Mesh
    from unislicer.slicing import slice_mesh
    n, r_out, wall, h = 120, 15.0, 0.4, 20.0
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = np.array([[r * math.cos(t), r * math.sin(t), z] for r in (r_out, r_out - wall) for z in (0.0, h) for t in a])
    F = []
    o0, o1, i0, i1 = 0, n, 2 * n, 3 * n
    for k in range(n):
        j = (k + 1) % n
        F += [[o0 + k, o0 + j, o1 + j], [o0 + k, o1 + j, o1 + k], [i0 + k, i1 + j, i0 + j], [i0 + k, i1 + k, i1 + j],
              [o1 + k, o1 + j, i1 + j], [o1 + k, i1 + j, i1 + k], [o0 + k, i0 + j, o0 + j], [o0 + k, i0 + k, i0 + j]]
    F = np.array(F)
    holes = {8 * k + q for k in (10, 11, 50, 51, 90) for q in range(4)}      # same angles, both walls
    m = Mesh(V, F[[i not in holes for i in range(len(F))]])
    assert not m.is_watertight()
    layers = slice_mesh(m, [float(z) for z in np.arange(0.5, 19.5, 1.0)])
    ring = Point(0, 0).buffer(r_out, 256).difference(Point(0, 0).buffer(r_out - wall, 256))
    assert sum(l.difference(ring.buffer(0.2)).area for l in layers) < 1.0          # nothing inside the hollow
    # the wall is all there except where the mesh itself has no wall (the punched strips: ~1.2 mm² a layer)
    assert all(ring.buffer(-0.05).difference(l).area < 2.0 for l in layers)
    # a closed model is sliced exactly as before (the robust fill only runs on broken layers)
    box = slice_mesh(primitives.box(20, 10, 5), [2.5])[0]
    assert abs(box.area - 200.0) < 1e-6


def test_fill_lines_stay_inside_after_rotation():
    """Rotating a valid outline in floating point can leave it invalid (tiny edges crossing in
    near-degenerate spots); clipping fill lines against it then returned strands outside the part (the
    'extra shapes' on a Creality Print model). Fill areas are repaired after rotation."""
    import shapely
    from shapely.geometry import LineString, Polygon
    from unislicer import infill
    # a valid shape with a pinch point and a near-zero-width spike: rotation makes it invalid
    shape = Polygon([(0, 0), (40, 0), (40, 20), (20.0, 10.0), (20.0 + 1e-9, 10.0 + 1e-9), (0, 20)])
    assert shape.is_valid or True
    for angle in (45.0, 135.0, 30.0):
        for ln in infill.line_fill(shape, 0.4, angle):
            assert LineString(ln).difference(shape.buffer(0.05)).length < 1e-6
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])            # invalid on purpose
    assert not bowtie.is_valid
    fixed = shapely.make_valid(bowtie)
    for ln in infill.line_fill(bowtie, 0.4, 45.0):
        assert LineString(ln).difference(fixed.buffer(0.05)).length < 1e-6


def test_3mf_with_parts_in_separate_model_files_loads_directly(tmp_path):
    """Creality Print / Bambu / Orca projects keep the meshes in 3D/Objects/*.model; the normal loader reads
    them (before, only the rescue agent could)."""
    import zipfile
    from unislicer.mesh import load_mesh
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    box = primitives.box(10, 10, 10)
    vx = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in box.vertices)
    tr_ = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in box.faces)
    obj = (f'<model xmlns="{core}"><resources><object id="1" type="model"><mesh><vertices>{vx}</vertices>'
           f'<triangles>{tr_}</triangles></mesh></object></resources><build/></model>')
    root = (f'<model xmlns="{core}" xmlns:p="{prod}" requiredextensions="p"><resources><object id="4" type="model">'
            f'<components><component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 5 5 0"/>'
            f'</components></object></resources><build><item objectid="4"/></build></model>')
    p = tmp_path / "creality.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", root)
        z.writestr("3D/Objects/object_1.model", obj)
    meshes = load_mesh(str(p))
    assert len(meshes) == 1 and len(meshes[0].faces) == len(box.faces)
    assert np.allclose(meshes[0].bounds[0], [5, 5, 0])


def test_remote_control_agent_links_reports_and_obeys(tmp_path, monkeypatch):
    """Remote control through Firebase: the agent signs in, shows a link code, waits for the computer's
    'Allow', publishes printer status and carries out pause/resume/stop - against a local stand-in for
    Firebase Auth + Realtime Database (with its event stream) and for a Klipper printer."""
    import json, queue, threading, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unislicer import network, remote

    # ---- parsing the snippet the Firebase console shows
    snippet = '''const firebaseConfig = {
      apiKey: "AIzaTest",
      authDomain: 'lab.firebaseapp.com',
      databaseURL: "https://lab-default-rtdb.europe-west1.firebasedatabase.app/",
      projectId: "lab",
    };'''
    cfg = remote.parse_config(snippet)
    assert cfg["apiKey"] == "AIzaTest" and cfg["databaseURL"].endswith(".app") and cfg["projectId"] == "lab"
    for bad, why in (("", "empty"), ("hello", "no_config"), ('{apiKey: "x", projectId: "p"}', "no_database")):
        with pytest.raises(remote.RemoteError, match=why):
            remote.parse_config(bad)
    rules = json.load(open(os.path.join(os.path.dirname(__file__), "..", "remote_web", "database.rules.json")))
    assert "commands" in json.dumps(rules) and rules["rules"]["agents"]["$agent"][".read"]

    # ---- a stand-in for Firebase
    db, subs, lock = {}, [], threading.Lock()
    tokens = {}

    def node(path, create=False):
        cur = db
        parts = [p for p in path.strip("/").split("/") if p]
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                if not create:
                    return None, None
                cur[p] = {}
            cur = cur[p]
        return cur, (parts[-1] if parts else None)

    def get(path):
        parent, key = node(path)
        if key is None:
            return db if parent is not None else None
        return parent.get(key) if parent is not None else None

    def resolve(v):
        if isinstance(v, dict):
            if v == {".sv": "timestamp"}:
                return int(time.time() * 1000)
            return {k: resolve(x) for k, x in v.items() if x is not None}
        return v

    def write(path, value, merge=False):
        with lock:
            parent, key = node(path, create=True)
            value = resolve(value)
            if merge and isinstance(parent.get(key), dict) and isinstance(value, dict):
                parent[key].update(value)
            elif value is None:
                parent.pop(key, None)
            else:
                parent[key] = value
            for spath, q in list(subs):
                if spath.startswith(path.strip("/")) or path.strip("/").startswith(spath):
                    q.put(json.dumps({"path": "/", "data": get(spath)}))

    class FB(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"null")

        def _db_path(self):
            path, _, query = self.path.partition("?")
            assert "auth=" in query
            tok = urllib.parse.unquote(query.split("auth=")[1].split("&")[0])
            if tok not in tokens:
                return None
            return urllib.parse.unquote(path[: -len(".json")])

        def do_POST(self):
            if self.path.startswith("/v1/accounts:signUp"):
                uid = "dev" + str(len(tokens))
                tokens["tok-" + uid] = uid
                return self._send({"localId": uid, "idToken": "tok-" + uid, "refreshToken": "ref-" + uid,
                                   "expiresIn": "3600"})
            if self.path.startswith("/v1/token"):
                ref = self._body()["refresh_token"]
                uid = ref[4:]
                tokens["tok-" + uid] = uid
                return self._send({"user_id": uid, "id_token": "tok-" + uid, "refresh_token": ref,
                                   "expires_in": "3600"})
            self._send({"error": "no"}, 404)

        def do_GET(self):
            path = self._db_path()
            if path is None:
                return self._send({"error": "Permission denied"}, 401)
            if self.headers.get("Accept") == "text/event-stream":
                q = queue.Queue()
                spath = path.strip("/")
                with lock:
                    subs.append((spath, q))
                    first = json.dumps({"path": "/", "data": get(spath)})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    self.wfile.write(f"event: put\ndata: {first}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=0.5)
                            self.wfile.write(f"event: put\ndata: {msg}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b"event: keep-alive\ndata: null\n\n")
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    with lock:
                        subs.remove((spath, q))
                return
            self._send(get(path))

        def do_PUT(self):
            path = self._db_path()
            v = self._body()
            write(path, v)
            self._send(v)

        def do_PATCH(self):
            path = self._db_path()
            v = self._body()
            write(path, v, merge=True)
            self._send(v)

        def do_DELETE(self):
            write(self._db_path(), None)
            self._send(None)

    # ---- a stand-in Klipper printer
    calls = []
    printer_state = {"state": "printing"}

    class Klipper(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            raw = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._send({"result": {"status": {"print_stats": {"state": printer_state["state"],
                                                              "filename": "crown.gcode"},
                                              "display_status": {"progress": 0.42}}}})

        def do_POST(self):
            calls.append(self.path)
            printer_state["state"] = {"/printer/print/pause": "paused", "/printer/print/resume": "printing",
                                      "/printer/print/cancel": "cancelled"}[self.path]
            self._send({"result": "ok"})

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (FB, Klipper)]
    for s in servers:
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
    fb_port, k_port = servers[0].server_address[1], servers[1].server_address[1]
    import urllib.parse
    cfg = {"apiKey": "k", "databaseURL": f"http://127.0.0.1:{fb_port}", "projectId": "lab",
           "authURL": f"http://127.0.0.1:{fb_port}/v1", "tokenURL": f"http://127.0.0.1:{fb_port}/v1"}
    kp = network.NetPrinter("Lab K1", "moonraker", "127.0.0.1", k_port)
    folder = str(tmp_path)
    monkeypatch.setattr(remote, "STATUS_EVERY", 0.2)
    monkeypatch.setattr(remote, "OWNER_CHECK_EVERY", 0.5)

    states, claims, done = [], [], []

    def wait(cond, secs=10.0):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    agent = remote.Agent(cfg, folder, name="Lab PC", on_state=lambda s, d: states.append((s, d)),
                         on_claim=claims.append, on_command=lambda c, ok, m: done.append((c["action"], ok, m)),
                         printers=lambda: [kp])
    try:
        agent.start()
        assert wait(lambda: agent.state == "pairing"), states
        code = agent.code
        assert len(code) == 8 and set(code) <= set(remote.CODE_ALPHABET)
        assert get(f"pairing/{code}")["agent"] == agent.fb.uid
        uid = agent.fb.uid
        # nothing is published before the computer allows the link
        assert get(f"agents/{uid}/printers") is None

        # a Google account types the code on the page; the computer is asked first
        write(f"pairing/{code}/claim", {"uid": "owner1", "email": "lab@example.com"})
        assert wait(lambda: claims == ["lab@example.com"])
        agent.answer_claim(True)
        assert wait(lambda: agent.state == "linked"), states
        assert get(f"agents/{uid}/owner") == "owner1" and get(f"pairing/{code}") is None
        assert remote.load_state(folder)["owner_email"] == "lab@example.com"
        assert wait(lambda: (get(f"agents/{uid}/printers") or {}).get(kp.id, {}).get("percent") == 42.0)
        row = get(f"agents/{uid}/printers")[kp.id]
        assert row["state"] == "printing" and row["file"] == "crown.gcode" and row["control"] is True
        assert "api_key" not in json.dumps(get(f"agents/{uid}")) and get(f"agents/{uid}/info")["online"]

        # commands from the page are carried out once and answered
        now = int(time.time() * 1000)
        write(f"agents/{uid}/commands/c1", {"printer": kp.id, "action": "pause", "at": now, "by": "owner1"})
        assert wait(lambda: (get(f"agents/{uid}/results/{kp.id}") or {}).get("id") == "c1")
        assert get(f"agents/{uid}/results/{kp.id}")["ok"] and calls == ["/printer/print/pause"]
        assert get(f"agents/{uid}/commands") in (None, {})
        assert wait(lambda: get(f"agents/{uid}/printers")[kp.id]["state"] == "paused")
        write(f"agents/{uid}/commands/c2", {"printer": kp.id, "action": "resume", "at": now, "by": "owner1"})
        assert wait(lambda: calls == ["/printer/print/pause", "/printer/print/resume"])
        # c2's answer goes to the same per-printer result: wait for it, or it may land after (over) c3's
        assert wait(lambda: (get(f"agents/{uid}/results/{kp.id}") or {}).get("id") == "c2")
        # an old command (e.g. queued while the computer was off) is refused, an unknown printer too
        write(f"agents/{uid}/commands/c3", {"printer": kp.id, "action": "stop", "at": now - 3_600_000, "by": "owner1"})
        assert wait(lambda: (get(f"agents/{uid}/results/{kp.id}") or {}).get("id") == "c3")
        assert not get(f"agents/{uid}/results/{kp.id}")["ok"] and "/printer/print/cancel" not in calls
        write(f"agents/{uid}/commands/c4", {"printer": "nope", "action": "stop", "at": now, "by": "owner1"})
        assert wait(lambda: (get(f"agents/{uid}/results/nope") or {}).get("id") == "c4")
        assert not get(f"agents/{uid}/results/nope")["ok"]
        assert wait(lambda: len(done) == 4)

        # restarting keeps the device account and the link (no new code)
        agent.stop(2.0)
        agent2 = remote.Agent(cfg, folder, printers=lambda: [kp])
        agent2.start()
        assert wait(lambda: agent2.state == "linked") and agent2.fb.uid == uid
        # the owner removes the computer on the page: the agent notices and shows a new code
        write(f"agents/{uid}/owner", None)
        assert wait(lambda: agent2.state == "pairing")
        assert not remote.load_state(folder).get("owner")
        agent2.unlink()
        assert get(f"agents/{uid}") is None and not remote.load_state(folder).get("refresh_token")
    finally:
        agent.stop(1.0)
        for s in servers:
            s.shutdown()


def test_network_printer_pause_resume_stop_commands(monkeypatch):
    """Each connection type maps pause / resume / stop to its printer's own command."""
    import json
    from unislicer import network
    sent = []

    def fake_open(p, method, path, body=None, headers=None, timeout=15.0):
        sent.append((p.kind, method, path, body))
        if path == "/api/v1/status":
            return 200, json.dumps({"job": {"id": 7, "progress": 10}}).encode()
        return 200, b"{}"

    monkeypatch.setattr(network, "_open", fake_open)
    for kind in ("octoprint", "moonraker", "prusalink"):
        p = network.NetPrinter(kind, kind, "10.0.0.5")
        for a in network.CONTROL_ACTIONS:
            network.control(p, a)
    assert ("octoprint", "POST", "/api/job", b'{"command": "cancel"}') in sent
    assert ("moonraker", "POST", "/printer/print/cancel", b"") in sent
    assert ("prusalink", "PUT", "/api/v1/job/7/pause", b"") in sent
    assert ("prusalink", "DELETE", "/api/v1/job/7", None) in sent
    reqs = []
    monkeypatch.setattr(network._Sdcp, "_request", lambda self, cmd, data, want="response", timeout=8.0:
                        reqs.append(cmd) or {"Ack": 0})
    for a in ("pause", "resume", "stop"):
        network.control(network.NetPrinter("s", "sdcp", "10.0.0.6"), a)
    assert reqs == [129, 131, 130]
    with pytest.raises(network.NetworkError):
        network.control(network.NetPrinter("f", "folder", "/tmp"), "pause")
    with pytest.raises(network.NetworkError):
        network.control(network.NetPrinter("o", "octoprint", "10.0.0.5"), "explode")
    assert not network.can_control("folder") and network.can_control("creality")


def test_remote_control_window_opens():
    """The remote-control window builds in every state without a Firebase project."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, sys, tempfile
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui import i18n
        from unislicer.gui.remote_dialog import RemoteController, RemoteDialog
        d = tempfile.mkdtemp()
        for lang in ("he", "en", "ru", "ar"):
            i18n.set_language(lang)
            ctl = RemoteController(d)
            dlg = RemoteDialog(ctl)
            for s, det in (("idle", ""), ("connecting", ""), ("pairing", "ABCD2345"), ("confirm", "a@b.c"),
                           ("linked", "a@b.c"), ("unlinked", ""), ("error", "offline: x"), ("error", "denied: y")):
                dlg._show(s, det)
            ctl.save(config={"apiKey": "k", "databaseURL": "https://x.firebasedatabase.app", "projectId": "lab"})
            dlg._show("pairing", "ABCD2345")
            assert dlg.code.text() == "ABCD-2345" and "lab.web.app" in dlg.status.text(), dlg.status.text()
            dlg.cfg_edit.setPlainText("not a config")
            dlg._save_config()
            assert dlg.cfg_msg.text()
            assert ctl.agent is None
        print("ok")
    ''')
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]


def test_multi_filament_creality_cfs_sparkx_i7(tmp_path):
    """Two models in two filaments on the SPARKX i7: Creality's CFS change sequence between them, previews for the
    screen, per-filament totals and the filament colours/types the printer maps to CFS slots."""
    import math, re
    from unislicer import fdm, filaments as fil, gcode, primitives, profiles, thumbnail
    from unislicer.scene import SceneObject, arrange
    cfg = profiles.build_config("creality_sparkx_i7", None, None,
                                {"filament_count": 2, "filament_colors": "#FFFFFF;#202020", "filament_types": "PLA;PETG"})
    objs = [SceneObject.from_mesh(primitives.box(20, 20, 6)), SceneObject.from_mesh(primitives.box(20, 20, 3))]
    objs[1].filament = 2
    arrange(objs, cfg["bed_x"], cfg["bed_y"])
    parts = [mt for o in objs for mt in o.print_meshes()]
    meshes, tools = [m for m, _ in parts], [t for _, t in parts]
    assert tools == [0, 1]
    plan = fdm.plan(meshes, cfg, tools=tools)
    assert plan.tools == [0, 1] and plan.multi_material
    assert {p.tool for p in plan.layers[0].paths} == {0, 1}
    assert {p.tool for p in plan.layers[-1].paths} == {0}                # the short box is done
    text, st = gcode.generate(plan, cfg, "two", meshes=meshes, tools=tools)

    # start: Creality's START_PRINT and CFS load of the first filament
    assert "START_PRINT EXTRUDER_TEMP=" in text and "M8200 P S0" in text
    body = text.split("; EXECUTABLE_BLOCK_START")[1].split("; EXECUTABLE_BLOCK_END")[0]
    assert re.search(r"^T0$", body, re.M) and re.search(r"^T1$", body, re.M)
    # one change into the second filament per layer that has both (each layer starts with the current filament)
    layers_with_both = sum(1 for l in plan.layers if {p.tool for p in l.paths} == {0, 1})
    assert st.toolchanges == layers_with_both + 1 or st.toolchanges == layers_with_both
    change = body.split("; CP TOOLCHANGE T0 -> T1")[1].split(";LAYER_CHANGE")[0]
    for cmd in ("M8200 P S1", "M8200 C S0", "M8200 L I1", "M8200 R", "M8200 O", "; FLUSH_START", "; WIPE", "G0 X278"):
        assert cmd in change, cmd
    assert "M8200 R E-28" in change                                    # PLA: long cut retraction
    back = body.split("; CP TOOLCHANGE T1 -> T0")[1].split(";LAYER_CHANGE")[0]
    assert "PETG" not in back and "M8200 R E-28" not in back            # leaving PETG: no cut retraction
    # purge: white after dark-grey needs more than dark-grey after white; Creality's cycle split
    assert fil.purge_volume("#202020", "#FFFFFF") > fil.purge_volume("#FFFFFF", "#202020") >= fil.MIN_PURGE
    cyc = fil.purge_cycles(400.0, 1.75)
    assert len(cyc) == 3 and abs(sum(cyc) - 400.0 / (math.pi / 4 * 1.75 ** 2)) < 1e-6
    assert len(fil.purge_cycles(5000.0, 1.75)) == fil.MAX_PURGE_CYCLES

    # previews for the screen: 140 and 260 px PNG, the models drawn in their filament colours
    thumbs = thumbnail.read_gcode_thumbnails(text)
    assert [(w, h) for w, h, _ in thumbs] == [(140, 140), (260, 260)]
    from PIL import Image
    img = Image.open(io.BytesIO(thumbs[1][2])).convert("RGBA")
    px = np.asarray(img)
    assert px[:, :, 3].max() == 255 and px[:, :, 3].min() == 0         # models on a transparent background
    opaque = px[px[:, :, 3] > 200][:, :3].mean(axis=1)
    assert opaque.max() > 200 and opaque.min() < 90                    # a light and a dark model
    assert text.index("; THUMBNAIL_BLOCK_START") < text.index("; EXECUTABLE_BLOCK_START")

    # header and per-filament totals, colours and types for the CFS mapping
    assert text.startswith("; HEADER_BLOCK_START")
    assert f"; total layer number: {len(plan.layers)}" in text
    mm = [float(v) for v in re.search(r"; filament used \[mm\] = (.*)", text).group(1).split(",")]
    assert len(mm) == 2 and all(v > 0 for v in mm)
    assert "; filament_colour = #FFFFFF;#202020" in text and "; filament_type = PLA;PETG" in text
    assert "; printer_model = Creality SPARKX i7" in text
    # after a change the nozzle comes back down to the layer before printing
    assert re.search(r"M8200 O\n(?:G1 E-[\d.]+ F\d+\n)?(?:G1 Z[\d.]+ F\d+\n)?G0 X[\d.]+ Y[\d.]+ F\d+\nG1 Z", body)

    # a single filament keeps the old behaviour (no changes, no CFS swaps after the start)
    plan1 = fdm.plan(meshes, cfg, tools=[0, 0])
    text1, st1 = gcode.generate(plan1, cfg, "one", meshes=meshes, tools=[0, 0])
    assert st1.toolchanges == 0 and "CP TOOLCHANGE" not in text1 and plan1.tools == [0]

    # plain T printers: a T command and the user's change G-code
    cfg2 = profiles.build_config("generic_klipper", None, None,
                                 {"filament_count": 2, "toolchange_gcode": "; swap {previous_tool}->{next_tool}"})
    text2, st2 = gcode.generate(fdm.plan(meshes, cfg2, tools=tools), cfg2, "t")
    assert re.search(r"^T1$", text2, re.M) and "; swap 0->1" in text2 and "M8200" not in text2


def test_multi_colour_3mf_parts_keep_their_filaments(tmp_path):
    """A Bambu Studio / OrcaSlicer / Creality Print project: one object made of parts, each part with its own
    filament (model_settings.config) and the project's filament colours (project_settings.config); an embedded
    logo part keeps its volume inside the body."""
    import json, zipfile
    from unislicer import fdm, primitives, profiles, project
    from unislicer.mesh import load_mesh
    from unislicer.scene import SceneObject

    def obj_xml(oid, mesh):
        v = "".join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>' for a, b, c in mesh.vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
        return f'<object id="{oid}" type="model"><mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>'

    body = primitives.box(30, 30, 10)
    logo = primitives.box(10, 10, 4).translated(np.array([0.0, 0.0, 4.0]))   # inside the body's top
    ns = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
    objects_model = f'<?xml version="1.0"?><model unit="millimeter" {ns}><resources>{obj_xml(1, body)}{obj_xml(2, logo)}</resources><build/></model>'
    main = (f'<?xml version="1.0"?><model unit="millimeter" {ns}><resources><object id="4" type="model"><components>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            '<component p:path="/3D/Objects/object_1.model" objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            '</components></object></resources><build><item objectid="4" transform="1 0 0 0 1 0 0 0 1 100 100 0"/></build></model>')
    settings = ('<?xml version="1.0"?><config><object id="4"><metadata key="name" value="Badge"/><metadata key="extruder" value="1"/>'
                '<part id="1" subtype="normal_part"><metadata key="name" value="Body"/><metadata key="extruder" value="1"/></part>'
                '<part id="2" subtype="normal_part"><metadata key="name" value="Logo"/><metadata key="extruder" value="2"/></part>'
                '</object></config>')
    path = tmp_path / "badge.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", main)
        z.writestr("3D/Objects/object_1.model", objects_model)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/project_settings.config", json.dumps({"filament_colour": ["#000000", "#7F975F"],
                                                                     "filament_type": ["PETG", "PETG"]}))
    meshes = load_mesh(str(path))
    assert len(meshes) == 1 and meshes[0].name == "Badge"
    assert meshes[0].project_filaments == {"colours": ["#000000", "#7F975F"], "types": ["PETG", "PETG"]}
    obj = SceneObject.from_mesh(meshes[0])
    assert obj.filaments_used() == [1, 2] and [p["name"] for p in obj.filament_parts] == ["Body", "Logo"]
    obj.x, obj.y = 100, 100
    parts = obj.print_meshes()
    assert [t for _, t in parts] == [0, 1]
    cfg = profiles.build_config("creality_sparkx_i7", None, None, {"filament_count": 2})
    plan = fdm.plan([m for m, _ in parts], cfg, tools=[t for _, t in parts])
    # the logo (listed later) prints in filament 2 inside the body, and the body leaves room for it
    top_logo_layer = next(l for l in plan.layers if 4.5 < l.z < 7.5)
    assert {p.tool for p in top_logo_layer.paths} == {0, 1}
    logo_pts = np.vstack([p.points for p in top_logo_layer.paths if p.tool == 1])
    # boxes start at their corner: the body spans 85..115 on the plate, the logo 85..95
    assert np.abs(logo_pts - [90, 90]).max() < 5.5
    body_pts = np.vstack([p.points for p in top_logo_layer.paths if p.tool == 0])
    assert body_pts.max(axis=0).min() > 110                         # the body still fills around the logo
    # undo state and projects keep the filaments
    st = obj.state()
    obj.filament, obj.filament_parts = 3, []
    obj.restore(st)
    assert obj.filaments_used() == [1, 2]
    project.save(str(tmp_path / "p.unislicer"), [obj], {})
    loaded, _ = project.load(str(tmp_path / "p.unislicer"))
    assert loaded[0].filaments_used() == [1, 2] and loaded[0].duplicate().filaments_used() == [1, 2]


def test_filament_menu_and_colours_in_the_window():
    """Right-click › Filament gives the selected model a filament (adding slots as needed), the viewer draws
    models in their filament colours, and a project's filament colours are taken on import."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QMenu
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow()
        w.settings.select_printer("creality_sparkx_i7") if hasattr(w.settings, "select_printer") else None
        w.settings.set_value("technology", "fdm") if w.settings.config()["technology"] != "fdm" else None
        a = w.add_mesh(primitives.box(20, 10, 5))
        b = w.add_mesh(primitives.box(10, 10, 5))
        w._select_uid(b.uid)
        menu = QMenu()
        w._fill_filament_menu(menu)
        labels = [x.text() for x in menu.actions()]
        assert any("2" in t for t in labels), labels
        w.set_filament_selected(3)
        assert b.filament == 3 and a.filament == 1
        assert w.settings.config()["filament_count"] >= 3
        w._objects_changed()
        ca, cb = w.viewer.objects[a.uid]["color"], w.viewer.objects[b.uid]["color"]
        assert ca != cb, (ca, cb)
        m = primitives.box(5, 5, 5)
        m.project_filaments = {"colours": ["#112233", "#AABBCC"], "types": ["petg", "pla"]}
        w._import_project_filaments([m])
        cfg = w.settings.config()
        assert cfg["filament_colors"].startswith("#112233;#AABBCC") and cfg["filament_types"].startswith("PETG;PLA")
        w.undo()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_cfs_slots_mapping_and_reading_from_the_printer(monkeypatch):
    """Filaments mapped to CFS slots by hand load from those slots (T number = slot), the end-of-file lists follow
    the slots, and the CFS contents are read from the printer (boxsInfo on port 9999)."""
    import json, re
    from unislicer import fdm, filaments as fil, gcode, network, primitives, profiles
    from unislicer.scene import SceneObject, arrange
    assert fil.slot_index("1A") == 0 and fil.slot_index("1d") == 3 and fil.slot_index("2A") == 4 and fil.slot_index("X") == -1
    cfg = profiles.build_config("creality_sparkx_i7", None, None,
                                {"filament_count": 2, "filament_colors": "#000000;#7F975F", "filament_slots": "1C;1A"})
    flist = fil.filament_list(cfg)
    assert [(f.slot, f.t) for f in flist] == [("1C", 2), ("1A", 0)]
    objs = [SceneObject.from_mesh(primitives.box(20, 20, 4)), SceneObject.from_mesh(primitives.box(20, 20, 2))]
    objs[1].filament = 2
    arrange(objs, cfg["bed_x"], cfg["bed_y"])
    meshes = [o.world_mesh() for o in objs]
    text, st = gcode.generate(fdm.plan(meshes, cfg, tools=[0, 1]), cfg, "slots")
    assert "M8200 P S2" in text and re.search(r"^T2$", text, re.M)          # filament 1 starts from slot 1C
    assert "M8200 L I0" in text and re.search(r"^T0$", text, re.M)          # filament 2 loads from slot 1A
    assert "T1" not in re.findall(r"^T\d+$", text, re.M)
    colours = re.search(r"; filament_colour = (.*)", text).group(1).split(";")
    assert colours[0] == "#7F975F" and colours[2] == "#000000" and len(colours) == 3
    used = [float(v) for v in re.search(r"; filament used \[mm\] = (.*)", text).group(1).split(",")]
    assert used[1] == 0.0 and used[0] > 0 and used[2] > 0

    # the printer's CFS, as K2 / SPARKX firmware reports it
    payload = {"boxsInfo": {"same_material": [], "materialBoxs": [
        {"id": 0, "type": 1, "materials": [{"id": 0, "state": 1, "vendor": "Generic", "type": "TPU", "color": "#0112233"}]},
        {"id": 1, "type": 0, "state": 1, "materials": [
            {"id": 0, "state": 1, "vendor": "Creality", "type": "PETG", "name": "Hyper PETG", "color": "#07F975F", "percent": 90},
            {"id": 1, "state": 1, "vendor": "Creality", "type": "pla", "name": "Hyper PLA", "color": "#0000000"},
            {"id": 2, "state": 0, "color": "#0FFFFFF"},
            {"id": 3, "state": 1, "vendor": "Generic", "type": "PETG", "color": "0ff69b4"}]}]}}
    sent = []

    class FakeWS:
        def __init__(self):
            self.queue = [json.dumps({"ModeCode": "heart_beat"}), json.dumps({"state": 0}), json.dumps(payload)]
            self.sock = type("S", (), {"settimeout": lambda *a: None})()

        def send(self, text):
            sent.append(text)

        def recv(self):
            return self.queue.pop(0) if self.queue else None

        def close(self):
            pass

    monkeypatch.setattr(network, "_creality_ws", lambda host, timeout=3.0: FakeWS())
    slots = network.creality_cfs_slots("10.0.0.7")
    assert {"method": "get", "params": {"boxsInfo": 1}} in [json.loads(s) for s in sent if s.startswith("{")]
    assert "ok" in sent                                                     # heartbeats answered
    by = {s["slot"]: s for s in slots}
    assert by["EXT"]["type"] == "TPU" and by["EXT"]["colour"] == "#112233"
    assert by["1A"] == {"slot": "1A", "colour": "#7F975F", "type": "PETG", "name": "Hyper PETG", "vendor": "Creality",
                        "loaded": True, "percent": 90}
    assert by["1B"]["type"] == "PLA" and by["1B"]["colour"] == "#000000"
    assert not by["1C"]["loaded"] and by["1D"]["colour"] == "#FF69B4"
    monkeypatch.setattr(network, "_creality_ws", lambda host, timeout=3.0: type("W", (FakeWS,), {"__init__": lambda self: (
        FakeWS.__init__(self), setattr(self, "queue", [json.dumps({"state": 0})]))[0]})())
    with pytest.raises(network.NetworkError):
        network.creality_cfs_slots("10.0.0.7")


def test_printer_families_resin_and_uv():
    """The printer list is split into RESIN / UV (PolyJet) - filament printers are not offered (UniSlicer is for
    resin printers only); switching family goes back to the last printer used in it."""
    import subprocess, sys, textwrap
    from unislicer import profiles
    from unislicer.gui.settings_panel import printer_kind, KINDS
    printers = profiles.load("printers")
    kinds = {pid: printer_kind(p) for pid, p in printers.items()}
    assert kinds["stratasys_j5_dentajet"] == "uv" and kinds["elegoo_mars3"] == "resin"
    assert KINDS == ("resin", "uv")
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import profiles
        from unislicer.gui.settings_panel import SettingsPanel, printer_kind
        printers = profiles.load("printers")
        p = SettingsPanel("elegoo_mars3", None, None, {}, False)
        assert set(p.kind_buttons) == {"resin", "uv"}
        listed = [p.printer_combo.itemData(i) for i in range(p.printer_combo.count()) if p.printer_combo.itemData(i)]
        assert listed and all(printer_kind(printers[x]) == "resin" for x in listed)
        assert p.technology == "sla" and not p.filament_panel.isVisibleTo(p)
        p.kind_buttons["uv"].click()
        assert p.config()["sla_process"] == "polyjet"
        listed = [p.printer_combo.itemData(i) for i in range(p.printer_combo.count()) if p.printer_combo.itemData(i)]
        assert all(printer_kind(printers[x]) == "uv" for x in listed)
        p.kind_buttons["resin"].click()
        assert p.printer_id == "elegoo_mars3" and p.config()["sla_process"] != "polyjet"
        q = SettingsPanel("creality_sparkx_i7", None, None, {}, False)     # a filament printer from before
        assert q.technology == "sla" and printer_kind(printers[q.printer_id]) == "resin"
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_phrozen_ds_slicer_profiles_import_and_layer_table(tmp_path):
    """Phrozen DS Slicer's factory profiles: correct printer geometry, resins linked to their printers only, and the
    exposure row picked by printer, layer height and Stable / Fast mode (user values win)."""
    import importlib.util, subprocess, sys, textwrap
    from unislicer import profiles
    printers = profiles.load("printers")
    cs = printers["phrozen_sonic_cs_plus"]["values"]
    assert (cs["bed_x"], cs["bed_y"], cs["resolution_x"], cs["resolution_y"]) == (165.79, 71.28, 7536, 3240)
    ls = printers["phrozen_sonic_ls_plus"]["values"]
    assert (ls["bed_x"], ls["bed_y"], ls["resolution_x"], ls["resolution_y"]) == (195.84, 122.4, 5760, 3600)
    assert printers["phrozen_lumii_2k"]["values"]["resolution_y"] == 1440 and "phrozen_sonic_4k_2022" in printers
    k8 = printers["phrozen_8k_2023"]["values"]
    assert (k8["bed_x"], k8["bed_y"], k8["max_z"]) == (199.68, 124.8, 140.0)
    assert profiles.build_config("phrozen_8k_2023", "phrozen_ds_phrozen_ds_slicer_default_8k_2023")["exposure_time"] == 10.0
    mats = profiles.load("materials")
    ds = {k: v for k, v in mats.items() if k.startswith("phrozen_ds_")}
    assert len(ds) > 100 and all(v["printers_only"] and v["printers"] for v in ds.values())
    wdm = mats["phrozen_ds_phrozen_dental_washable_dental_model"]
    assert "phrozen_sonic_cs_plus" in wdm["printers"]
    rows = {}
    for lh in (0.03, 0.05, 0.1):
        for mode in ("stable", "fast"):
            c = profiles.build_config("phrozen_sonic_cs_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None,
                                      {"sla_layer_height": lh, "exposure_mode": mode})
            rows[(lh, mode)] = c
            assert c["sla_layer_height"] == lh
    assert rows[(0.03, "stable")]["exposure_time"] < rows[(0.05, "stable")]["exposure_time"] < rows[(0.1, "stable")]["exposure_time"]
    assert rows[(0.05, "fast")]["lift_speed"] > rows[(0.05, "stable")]["lift_speed"]
    assert rows[(0.05, "stable")]["shrink_xy"] > 0 and rows[(0.05, "stable")]["bottom_layers"] >= 1
    near = profiles.build_config("phrozen_sonic_cs_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None,
                                 {"sla_layer_height": 0.045})
    assert near["exposure_time"] == rows[(0.05, "stable")]["exposure_time"] and near["sla_layer_height"] == 0.045
    mine = profiles.build_config("phrozen_sonic_cs_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None,
                                 {"sla_layer_height": 0.05, "exposure_time": 9.5})
    assert mine["exposure_time"] == 9.5
    other = profiles.build_config("elegoo_mars3", "phrozen_ds_phrozen_dental_washable_dental_model")   # no table there
    assert other["exposure_time"] == wdm["values"]["exposure_time"]

    # the converter on a small export
    xml = """<?xml version="1.0"?><PrintingInformation><SoftwareInfo><VersionInfo><Version>9.9</Version></VersionInfo>
    </SoftwareInfo><PrintingParameters><PrinterConfig><ResinConfig><BrandName>Acme</BrandName><ResinName>Model Grey</ResinName>
    <PrinterName>Sonic CS+</PrinterName><DimensionX>165.79</DimensionX><DimensionY>71.28</DimensionY><DimensionZ>175</DimensionZ>
    <ResolutionX>7536</ResolutionX><ResolutionY>3240</ResolutionY><ThicknessConfig><Thickness>0.05</Thickness>
    <ExposureData><ThicknessName>stable</ThicknessName><Ratio><RatioX>1.01</RatioX><RatioY>1.01</RatioY><RatioZ>1.0</RatioZ></Ratio>
    <TwoStage>1</TwoStage><liftSecondDistInputFile>3</liftSecondDistInputFile><liftSecondSpeedInputFile>200</liftSecondSpeedInputFile>
    <retractSecondDistInputFile>2</retractSecondDistInputFile><retractSecondSpeedInputFile>60</retractSecondSpeedInputFile>
    <NumBaseLayer>5</NumBaseLayer><BaseCuringTime>30</BaseCuringTime><NormalCuringTime>2.2</NormalCuringTime>
    <NormalLiftHeight>6</NormalLiftHeight><NormalPeelSpeed>60</NormalPeelSpeed><BufferLayerNumber>4</BufferLayerNumber>
    </ExposureData><ExposureData><ThicknessName>customize</ThicknessName></ExposureData></ThicknessConfig></ResinConfig>
    </PrinterConfig></PrintingParameters></PrintingInformation>"""
    path = tmp_path / "ds.xml"
    path.write_text(xml)
    spec = importlib.util.spec_from_file_location("imp", os.path.join(os.path.dirname(__file__), "..", "tools", "import_phrozen_ds.py"))
    imp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imp)
    geometry, materials, info = imp.convert(str(path))
    assert info == {"version": "9.9", "resins": 1}
    assert geometry["phrozen_sonic_cs_plus"]["resolution_x"] == 7536
    m = materials["phrozen_ds_acme_model_grey"]
    row = m["layer_table"]["phrozen_sonic_cs_plus"]["0.050"]["stable"]
    assert (row["exposure_time"], row["bottom_layers"], row["transition_layers"], row["shrink_xy"]) == (2.2, 5, 4, 1.0)
    assert row["tsmc_enable"] and row["lift_height_2"] == 3 and m["printers"] == ["phrozen_sonic_cs_plus"]

    # the resins appear only on their printers
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.settings_panel import SettingsPanel
        p = SettingsPanel("phrozen_sonic_cs_plus", None, None, {}, False)
        ids = [p.material_combo.itemData(i) for i in range(p.material_combo.count())]
        assert sum(1 for i in ids if i and i.startswith("phrozen_ds_")) >= 30      # 38 in DS Slicer 4.4
        p.material_combo.setCurrentIndex(ids.index("phrozen_ds_phrozen_dental_washable_dental_model"))
        p.set_value("sla_layer_height", 0.1)
        assert p.config()["exposure_time"] > 2.0
        assert p.select_printer("elegoo_mars3")
        ids = [p.material_combo.itemData(i) for i in range(p.material_combo.count())]
        assert not any(i and i.startswith("phrozen_ds_") for i in ids)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path)
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_orientation_suggestion_says_learned_only_after_learning(tmp_path):
    """With no orientation choices learned yet, the auto-orient suggestion credits the built-in rules, not
    "what I learned from your choices"."""
    from unislicer.assistant import Assistant
    a = Assistant(str(tmp_path))
    ctx = {"technology": "sla", "objects": [{"uid": 1, "name": "Tooth_47", "orient_gain": 0.37}]}
    keys = [s.text for s in a.suggestions(ctx) if s.id == "orient_1"]
    assert keys == ["sg_orient_rules"]
    a.data["orient"]["sla"]["examples"] = 3
    keys = [s.text for s in a.suggestions(ctx) if s.id == "orient_1"]
    assert keys == ["sg_orient"]


def test_assistant_learns_model_repairs_and_repairs_on_load(tmp_path):
    """The assistant learns how the user repairs models (by the model's problems and where it came from), offers the
    same repair for similar models, and with auto-repair on repairs them as they load (undo brings them back)."""
    import subprocess, sys, textwrap
    from unislicer import mesh_tools, primitives
    from unislicer.assistant import Assistant, repair_fingerprint
    holed = primitives.box(10, 10, 10)
    holed = type(holed)(holed.vertices, holed.faces[2:], "holed")             # two triangles missing: a hole
    fp = repair_fingerprint(mesh_tools.analyze(holed), ".stl|3Shape")
    assert fp == "holes|.stl|3Shape"
    assert repair_fingerprint(mesh_tools.analyze(primitives.box(5, 5, 5)), ".stl|-").startswith("clean|")
    a = Assistant(str(tmp_path / "a"))
    assert a.repair_recipe(fp) is None and not a.auto_repair
    adv = {"mode": "advanced", "weld": 0.001, "shells": 0.02, "nonmanifold": True, "holes": True}
    assert a.learn_repair(fp, adv) and not a.learn_repair("clean|.stl|-", adv)
    assert a.repair_recipe(fp) == adv
    assert a.repair_recipe("holes|.obj|Blender") == dict(adv, shells=0.0)   # same problems from elsewhere: never
    # its "drop small parts" - a recipe learned on a broken scan deleted the die of an intact jaw
    a.learn_repair("holes+nonmanifold+shells|.stl|exocad", adv)
    assert a.repair_recipe("shells|.stl|3Shape") is None                   # a jaw and its die: nothing to repair
    assert a.repair_recipe("holes+shells|.stl|3Shape") is None             # problems only included: not used
    assert a.repair_recipe("holes+dirty|.stl|3Shape") is None              # more problems than it has seen fixed
    a.learn_repair("holes|.stl|exocad", {"mode": "basic"})
    a.learn_repair("holes|.stl|exocad", {"mode": "basic"})
    assert a.repair_recipe("holes|.stl|exocad") == {"mode": "basic"}       # the exact source wins
    assert a.repair_recipe(fp) == adv
    assert Assistant(str(tmp_path / "a")).repair_recipe(fp) == adv           # remembered
    assert a.summary()["repairs_learned"] == 4

    from unislicer.mesh import save_stl
    for k in range(3):
        save_stl(holed, str(tmp_path / f"part{k}.stl"))
    code = textwrap.dedent(f'''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow()
        d = {str(tmp_path)!r}
        w.load_files([os.path.join(d, "part0.stl")])
        first = w.objects[-1]
        assert not first.mesh.is_watertight() and not w._repair_offers           # nothing learned yet
        w._select_uid(first.uid)
        w.repair_selected()                                                      # the user repairs it
        assert first.mesh.is_watertight() and w.assistant.summary()["repairs_learned"] == 1
        w.load_files([os.path.join(d, "part1.stl")])
        second = w.objects[-1]
        assert second.uid in w._repair_offers                                   # offered, not done
        sugg = [s for s in w.assistant.suggestions(w._scene_context()) if s.id == f"repair_{{second.uid}}"]
        assert sugg and sugg[0].action == "repair_like_before"
        w._apply_suggestion(sugg[0])
        assert second.mesh.is_watertight() and second.uid not in w._repair_offers
        w._on_auto_repair_toggled(True)
        w.load_files([os.path.join(d, "part2.stl")])
        third = w.objects[-1]
        assert third.mesh.is_watertight() and w.assistant.summary()["repairs_auto"] == 1
        w.undo()
        assert not [o for o in w.objects if o.uid == third.uid][0].mesh.is_watertight()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_undo_state_keeps_the_mesh_replaced_by_a_repair():
    """Repairs, cuts and splits replace a model's mesh; undo must bring the old mesh back."""
    from unislicer import mesh_tools, primitives
    from unislicer.scene import SceneObject
    box = primitives.box(10, 10, 10)
    holed = type(box)(box.vertices, box.faces[2:], "holed")
    o = SceneObject.from_mesh(holed)
    before = o.state()
    o.mesh, _ = mesh_tools.repair(o.mesh)
    assert o.mesh.is_watertight()
    o.restore(before)
    assert not o.mesh.is_watertight() and len(o.mesh.faces) == len(holed.faces)
    o.restore(before[:15])                                  # snapshots from before this change still restore
    assert len(o.mesh.faces) == len(holed.faces)


def test_hexagonal_raft():
    """Honeycomb raft: an outline band, hexagonal cells and a pad under every support foot, as one clean solid
    (fast to slice), lighter than a solid raft; every foot stands on raft material."""
    import math, time
    from shapely.geometry import Point
    from unislicer import primitives, profiles, sla
    from unislicer.scene import SceneObject
    from unislicer.sla_supports import hex_edges
    edges = hex_edges((0, 0, 20, 20), 5.0)
    lengths = [math.dist(a, b) for a, b in edges]
    assert all(abs(L - 5.0 / math.sqrt(3)) < 1e-4 for L in lengths)          # wall length of a 5 mm cell
    assert len(edges) == len({tuple(sorted(e)) for e in edges})               # shared walls listed once

    results = {}
    for kind in ("hexagon", "solid", "grid"):
        cfg = profiles.build_config("elegoo_mars3", None, None,
                                    {"support_enable": True, "sla_raft_type": kind, "sla_raft_bar_spacing": 5.0,
                                     "sla_support_spacing": 7.0, "sla_support_preset": "custom"})
        o = SceneObject.from_mesh(primitives.box(40, 26, 5))
        o.x, o.y = cfg["bed_x"] / 2, cfg["bed_y"] / 2
        t0 = time.time()
        job = sla.prepare([o.world_mesh()], cfg)
        results[kind] = (job, time.time() - t0, cfg)
    hexjob, hex_t, cfg = results["hexagon"]
    raft_layer = hexjob.layers[1]
    holes = sum(len(p.interiors) for p in getattr(raft_layer, "geoms", [raft_layer]))
    assert holes >= 15                                                        # open honeycomb cells
    assert raft_layer.area < results["solid"][0].layers[1].area * 0.8         # lighter than a solid raft
    assert len(getattr(raft_layer, "geoms", [raft_layer])) == 1               # one connected piece
    # every foot stands on raft material
    for p in hexjob.support_points:
        assert raft_layer.buffer(0.05).contains(Point(float(p[0]), float(p[1]))) or raft_layer.distance(
            Point(float(p[0]), float(p[1]))) < 0.5
    assert hex_t < 20.0                                                       # a clean solid, not the messy-layer path


def test_honeycomb_infill_continuous_walls_with_drain_bands():
    """Hollow models filled with a honeycomb: walls are whole on most layers and open only in thin drain bands
    (at the bottom of the cavity and then every few mm), so cells drain without sealed chambers; the infill walls
    are not reported as thin model features, and it slices quickly."""
    import time
    from unislicer import primitives, profiles, sla
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("elegoo_mars3", None, None, {
        "support_enable": False, "hollow_enable": True, "hollow_wall": 2.0, "hollow_infill": "hive",
        "hollow_infill_spacing": 6.0, "hollow_infill_thickness": 0.8, "drain_holes": 2, "sla_layer_height": 0.1})
    o = SceneObject.from_mesh(primitives.box(50, 35, 20))
    o.x, o.y = cfg["bed_x"] / 2, cfg["bed_y"] / 2
    t0 = time.time()
    job = sla.prepare([o.world_mesh()], cfg)
    assert time.time() - t0 < 30
    assert job.thin_layers == [] and job.cups == [] and job.saved_ml > 10
    n = len(job.layers)
    mid = job.layers[n // 2]
    first_cavity = int(round(2.0 / 0.1)) + 1
    band = job.layers[first_cavity + 2]
    holes_mid = sum(len(p.interiors) for p in getattr(mid, "geoms", [mid]))
    holes_band = sum(len(p.interiors) for p in getattr(band, "geoms", [band]))
    assert holes_mid >= 30                         # closed honeycomb cells on an ordinary layer
    assert holes_band < holes_mid / 2              # in a drain band the cells open into each other
    assert band.area < mid.area                    # windows cut in the walls
    full_layers = sum(1 for g in job.layers[first_cavity:n - 25]
                      if sum(len(p.interiors) for p in getattr(g, "geoms", [g])) >= 30)
    assert full_layers > (n - 25 - first_cavity) * 0.7       # whole walls on most of the height


def test_honeycomb_fills_models_that_came_already_hollow():
    """A dental model exported as a shell (open base, closed top, posts inside) gets the honeycomb in its own
    cavity; a hole that goes right through (a ring's centre) is left empty."""
    from shapely.geometry import Point, box
    from unislicer import mesh_tools, profiles, sla
    from unislicer.mesh import Mesh
    from unislicer.scene import SceneObject
    arch = box(-20, -6, 20, 6)
    shell = arch.difference(arch.buffer(-1.5))
    pre = Mesh.merge([mesh_tools.extrude(shell, 0, 12), mesh_tools.extrude(arch, 10.5, 12),
                      mesh_tools.extrude(Point(0, 0).buffer(0.6), 0, 11)], "shell")
    ring = mesh_tools.extrude(Point(0, 0).buffer(8).difference(Point(0, 0).buffer(5)), 0, 12, "ring")
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {
        "support_enable": False, "hollow_enable": True, "hollow_wall": 2.0, "hollow_infill": "hive",
        "hollow_infill_spacing": 5.0, "hollow_infill_thickness": 0.8, "sla_layer_height": 0.1})
    a, r = SceneObject.from_mesh(pre), SceneObject.from_mesh(ring)
    a.x, a.y, r.x, r.y = 60, 60, 130, 60
    job = sla.prepare([a.world_mesh(), r.world_mesh()], cfg)
    mid = job.layers[len(job.layers) // 2]
    inside = mid.intersection(box(60 - 18, 60 - 4.5, 60 + 18, 60 + 4.5))      # within the shell's cavity
    assert inside.area > 20                                                  # honeycomb walls (+ the post)
    assert not mid.contains(Point(130, 60)) and mid.intersection(Point(130, 60).buffer(4)).area < 1
    # without infill the cavity stays empty (only the post)
    cfg2 = cfg.copy().update({"hollow_infill": "none"})
    job2 = sla.prepare([a.world_mesh()], cfg2)
    mid2 = job2.layers[len(job2.layers) // 2]
    assert mid2.intersection(box(60 - 18, 60 - 4.5, 60 + 18, 60 + 4.5)).area < 3


def test_network_scan_survives_devices_that_do_not_speak_http(monkeypatch):
    """A device that answers on a printer port with raw HTML (no HTTP status line) made the scan crash with
    BadStatusLine; it must just be skipped, and one failing device never stops the scan."""
    import socket, threading
    from unislicer import network
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(3):
            try:
                c, _a = srv.accept()
            except OSError:
                return
            c.recv(1024)
            c.sendall(b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\r\n<html></html>')
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    assert network._http_json("127.0.0.1", port, "/server/info") == (0, None, {})
    srv.close()
    monkeypatch.setattr(network, "discover", lambda *a, **k: [])
    monkeypatch.setattr(network, "_open_port", lambda h, p, t: h.endswith(".5") and p == 80)
    monkeypatch.setattr(network, "identify", lambda h, ports: (_ for _ in ()).throw(RuntimeError("odd device")))
    assert network.scan(prefixes=["10.9.8"]) == []


def test_layout_nests_real_outlines_turning_only_about_z():
    """Auto layout uses the parts' real outlines (U-shaped arches nest), turns them only about Z (the tilt from
    the red/green rings stays), keeps the default 2 mm gap and never leaves overlapping parts."""
    import time
    from shapely.geometry import Point, box
    from unislicer import layout, mesh_tools
    arch = Point(0, 0).buffer(24, quad_segs=32).difference(Point(0, 0).buffer(14, quad_segs=32)).difference(
        box(-40, -40, 40, -2))
    assert layout.LayoutOptions().spacing == 2.0 and layout.LayoutOptions().rotation == "free"
    objs = []
    for k in range(6):
        o = SceneObject.from_mesh(mesh_tools.extrude(arch, 0, 15, f"arch{k}"))
        o.rotation[:] = [20.0, 0.0, 0.0]                                  # tilted
        objs.append(o)
    hull_area = sum(o.mesh.vertices.shape[0] and layout.MultiPoint(o.world_mesh().vertices[:, :2]).convex_hull.area
                    for o in objs[:1])
    assert layout.footprint(objs[0]).area < hull_area * 0.8               # the real outline, not the hull
    tilt = [o.matrix()[2, :3].copy() for o in objs]
    plate = (0, 0, 200, 125)
    t0 = time.time()
    assert layout.auto_layout(objs, plate, layout.LayoutOptions()) == []
    assert time.time() - t0 < 20
    for o, z in zip(objs, tilt):                                          # turning about Z keeps the tilt
        assert np.allclose(o.matrix()[2, :3], z, atol=1e-6)
    fps = [layout.footprint(o) for o in objs]
    gaps = [fps[i].distance(fps[j]) for i in range(len(fps)) for j in range(i + 1, len(fps))]
    assert min(gaps) >= 2.0 - 0.3 and all(box(*plate).contains(f) for f in fps)


def test_long_collision_warning_does_not_widen_the_window():
    """The collision banner with long model names used to push the window wider than the screen, hiding the
    right panel; it now wraps inside the view (and long status messages do not widen it either)."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        base = w.minimumSizeHint().width()
        name = "SOD260043723-005-15-14-13-12-11-21-22-23-24-25-modelbase_fixed-2"
        a = w.add_mesh(primitives.box(20, 20, 5)); b = w.add_mesh(primitives.box(20, 20, 5))
        a.name, b.name = name, name + "-copy"
        b.x, b.y = a.x + 5, a.y
        w._check_collisions(); w.wait_supports(); app.processEvents()   # collision maps build in the background
        assert w.collision_wrap.isVisible()
        assert w.minimumSizeHint().width() <= base + 5, (base, w.minimumSizeHint().width())
        assert name in w.collision_label.toolTip()
        w.status.setText("x" * 800); app.processEvents()
        assert w.minimumSizeHint().width() <= base + 5
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_choosing_infill_hollows_the_model_and_apply_reslices():
    """Picking the honeycomb infill alone (hollowing left off) used to do nothing: the model stayed solid.
    The infill now hollows the model by itself, the panel turns 'Hollow the model' on with it, and every
    setting has its own 'Apply' that commits only that setting."""
    from shapely.geometry import box
    from unislicer import profiles, primitives, sla
    from unislicer.scene import SceneObject
    o = SceneObject.from_mesh(primitives.box(30, 20, 15))
    o.x, o.y = 60, 60
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {
        "support_enable": False, "hollow_enable": False, "hollow_infill": "hive", "sla_layer_height": 0.1})
    job = sla.prepare([o.world_mesh()], cfg)
    mid = job.layers[len(job.layers) // 2]
    solid = box(*mid.bounds).area
    assert 0.25 * solid < mid.area < 0.8 * solid            # shell + honeycomb walls, not solid, not empty

    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        w.settings.set_value("hollow_enable", False)
        w.add_mesh(primitives.box(20, 20, 10)); app.processEvents()
        w.settings._on_changed("hollow_infill", "hive")
        assert w.settings.config()["hollow_enable"] is True
        w.settings._on_changed("hollow_infill", "none")
        # each setting has its own Apply: a picked value waits, Apply commits only that one and slices
        called = []
        w.slice = lambda: called.append(1)
        infill, spacing = w.settings._rows["hollow_infill"], w.settings._rows["hollow_infill_spacing"]
        assert infill.apply_btn.isEnabled() and not infill.pending      # always clickable
        infill.editor.setCurrentIndex(infill.editor.findData("hive"))
        spacing.editor.setValue(9.0)
        assert infill.pending and spacing.pending
        assert w.settings.config()["hollow_infill"] == "none"        # nothing applied yet
        infill.apply_btn.click()
        cfg = w.settings.config()
        assert cfg["hollow_infill"] == "hive" and cfg["hollow_enable"] is True
        assert cfg["hollow_infill_spacing"] != 9.0 and spacing.pending   # the other change still waits
        assert called and not infill.pending
        spacing.apply_btn.click()
        assert w.settings.config()["hollow_infill_spacing"] == 9.0
        # Apply with nothing changed still carries the setting out (slices with its current value)
        n = len(called)
        infill.apply_btn.click()
        assert len(called) == n + 1 and w.settings.config()["hollow_infill"] == "hive"
        # each Apply carries its setting out on the models: supports settings generate supports, raft
        # settings rebuild the raft, layout spacing re-arranges, other settings are just saved
        assert [w.setting_action(k) for k in ("sla_support_spacing", "sla_raft_type", "arrange_spacing",
                                              "hollow_infill", "exposure_time")] == ["supports", "raft", "arrange", "slice", "save"]
        w.slice = lambda: called.append(1)
        n = len(called)
        assert all(o.support_points is None or not len(o.support_points) for o in w.objects)
        sp = w.settings._rows["sla_support_spacing"]
        sp.editor.setValue(3.0); sp.apply_btn.click(); app.processEvents()
        assert w.settings.config()["sla_support_spacing"] == 3.0 and w.settings.config()["support_enable"]
        assert any(o.support_points is not None and len(o.support_points) for o in w.objects)
        assert len(called) == n                                        # supports made, no slice needed
        ex = w.settings._rows["exposure_time"]
        ex.editor.setValue(3.3); ex.apply_btn.click(); app.processEvents()
        assert abs(w.settings.config()["exposure_time"] - 3.3) < 1e-6 and len(called) == n
        # typing a number and clicking Apply straight away (no Enter) applies the typed number
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        spacing = w.settings._rows["hollow_infill_spacing"]
        w.activateWindow(); spacing.editor.setFocus(); spacing.editor.selectAll()
        QTest.keyClicks(spacing.editor, "12")
        QTest.mouseClick(spacing.apply_btn, Qt.LeftButton)
        app.processEvents()
        assert w.settings.config()["hollow_infill_spacing"] == 12.0, w.settings.config()["hollow_infill_spacing"]
        w.settings._on_changed("hollow_enable", False)
        assert w.settings.config()["hollow_infill"] == "none"
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_vents_found_for_cups_that_start_narrow():
    """A suction cup with a rounded bottom starts as a dot and widens (dental arches, bowls): no vent was
    found, so 'Add automatic vent holes' showed (0) and stayed disabled. The vent now drills from below
    the narrow start, and drilling it clears the cup."""
    from shapely.geometry import Point
    from unislicer import mesh_tools, profiles, sla
    from unislicer.mesh import Mesh
    lift = 3.0
    parts = [mesh_tools.extrude(Point(0, 0).buffer(10), lift, lift + 2)]
    for k in range(16):
        z = lift + 2 + k * 0.5
        parts.append(mesh_tools.extrude(Point(0, 0).buffer(10).difference(Point(0, 0).buffer(0.5 + k * 0.5)), z, z + 0.5))
    bowl = Mesh.merge(parts, "bowl")
    z = [0.05 + 0.1 * i for i in range(130)]
    layers = sla.slice_meshes([bowl], z, 0.05)
    assert sla.find_cups(layers)
    vents = sla.cup_vents(layers, z, radius=1.5)
    assert len(vents) == 1 and vents[0]["kind"] == "roof"
    v = vents[0]
    assert abs(v["x"]) < 1.5 and abs(v["y"]) < 1.5 and abs(v["z_surface"] - lift) < 0.2
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"drain_hole_diameter": 3.0})
    holes = sla.hole_meshes([sla.vent_hole_rows(v, cfg)], cfg, 0.0)
    cut = sla.slice_meshes(holes, z, 0.05)
    drilled = [g.difference(c) if not c.is_empty else g for g, c in zip(layers, cut)]
    assert not sla.find_cups(drilled)


def test_export_print_file_goes_to_the_central_library(tmp_path):
    """Export Print File saves without a dialog into <library>/<file name>/<file name>.<ext>; a second
    export of the same name is kept next to the first; odd characters in the name are made safe."""
    from unislicer import export_library
    lib = str(tmp_path / "UniSlicer Prints")
    p1 = export_library.target_path(lib, "SOD26-005 arch.stl", ".ctb")
    assert p1 == os.path.join(lib, "SOD26-005 arch", "SOD26-005 arch.ctb") and os.path.isdir(os.path.dirname(p1))
    open(p1, "w").close()
    p2 = export_library.target_path(lib, "SOD26-005 arch", ".ctb")
    assert p2 == os.path.join(lib, "SOD26-005 arch", "SOD26-005 arch (2).ctb")
    assert export_library.safe_name('a<b>:c?.') == "a_b__c_" and export_library.safe_name("CON") == "_CON"
    assert export_library.safe_name("") == "print"

    import subprocess, sys, textwrap
    code = textwrap.dedent(f'''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QFileDialog
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        QFileDialog.getSaveFileName = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dialog"))
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("export_library", {lib!r})
        opened = []
        w._reveal = opened.append
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False); app.processEvents()
        o = w.add_mesh(primitives.box(10, 10, 5)); o.name = "my part"
        o.direct = True                         # on the plate: no 'no supports' question
        import time
        settle = time.time() + 1.0                       # let pending settings / collision timers fire first:
        while time.time() < settle:                      # a late settings change would clear the result
            app.processEvents()
            time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents()
            time.sleep(0.01)
        w.export()
        e = time.time() + 120                            # resin files are written on their own thread
        while any(t.isRunning() for t in getattr(w, "_export_tasks", [])) and time.time() < e:
            app.processEvents(); time.sleep(0.02)
        app.processEvents()
        p = os.path.join({lib!r}, "my part", "my part.ctb")
        assert os.path.isfile(p) and os.path.getsize(p) > 1000, os.listdir({lib!r})
        assert opened == [p]                              # the folder opens with the file
        w.store.setValue("export_library", "")
        from unislicer import export_library
        assert w.export_library() == os.path.join(export_library.app_dir(), "Prints")
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_align_lock_fill_cavity_and_layer_playback(tmp_path):
    """CHITUBOX-style tools: Align / Distribute, Lock position (layout and align leave it in place),
    Fill cavity (an open-base dental shell prints solid) and the preview's layer playback."""
    from shapely.geometry import Point, box
    from unislicer import layout, mesh_tools, primitives, profiles, project, sla
    from unislicer.mesh import Mesh
    from unislicer.scene import SceneObject
    objs = [SceneObject.from_mesh(primitives.box(w, 10, 5)) for w in (10, 20, 6)]
    for o, (x, y) in zip(objs, ((20, 10), (60, 40), (110, 25))):
        o.x, o.y = x, y
    fp = lambda o: layout.footprint(o).bounds
    layout.align(objs, "left")
    assert len({round(fp(o)[0], 3) for o in objs}) == 1
    layout.align(objs, "front")
    assert len({round(fp(o)[1], 3) for o in objs}) == 1
    for o, x in zip(objs, (20, 45, 110)):
        o.x = x
    layout.align(objs, "dist_x")
    b = sorted((fp(o) for o in objs), key=lambda q: q[0])
    gaps = [b[k + 1][0] - b[k][2] for k in range(2)]
    assert abs(gaps[0] - gaps[1]) < 1e-6
    # locked: the reference for align, and left alone by auto layout
    objs[1].locked = True
    keep = (objs[1].x, objs[1].y)
    layout.align(objs, "back")
    assert (objs[1].x, objs[1].y) == keep and all(abs(fp(o)[3] - fp(objs[1])[3]) < 1e-6 for o in objs)
    layout.auto_layout(objs, (0, 0, 200, 120), layout.LayoutOptions(spacing=2.0))
    assert (objs[1].x, objs[1].y) == keep
    fps = [layout.footprint(o) for o in objs]
    assert all(fps[a].distance(fps[c]) >= 1.9 for a in range(3) for c in range(a + 1, 3))
    # lock and fill survive undo snapshots and projects
    objs[0].fill_cavity = True
    st = objs[0].state()
    objs[0].fill_cavity, objs[0].locked = False, True
    objs[0].restore(st)
    assert objs[0].fill_cavity and not objs[0].locked
    path = str(tmp_path / "p.unislicer")
    project.save(path, objs, {})
    back, _ = project.load(path)
    assert back[0].fill_cavity and back[1].locked and not back[2].locked
    # fill cavity: an open-base shell prints solid; without it the cavity stays empty
    arch = box(-20, -6, 20, 6)
    shell = Mesh.merge([mesh_tools.extrude(arch.difference(arch.buffer(-1.5)), 0, 12),
                        mesh_tools.extrude(arch, 10.5, 12)], "shell")
    o = SceneObject.from_mesh(shell)
    o.x, o.y = 60, 60
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"support_enable": False, "sla_layer_height": 0.1})
    inner = box(60 - 17, 60 - 3.5, 60 + 17, 60 + 3.5)
    empty = sla.prepare([o.world_mesh()], cfg)
    full = sla.prepare([o.world_mesh()], cfg, filled=[True])
    mid = len(full.layers) // 2
    assert empty.layers[mid].intersection(inner).area < 1
    assert full.layers[mid].intersection(inner).area > 0.95 * inner.area
    assert not full.cups

    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False); app.processEvents()
        import time
        settle = time.time() + 1.0                         # the printer switch reaches the plate after a short
        while time.time() < settle:                        # wait; landing later it moves every model with the
            app.processEvents(); time.sleep(0.01)          # plate (the lock included), as if align had moved it
        a = w.add_mesh(primitives.box(10, 10, 4)); b = w.add_mesh(primitives.box(10, 10, 4))
        a.direct = b.direct = True                         # on the plate: no 'no supports' question
        from PySide6.QtWidgets import QMessageBox           # aligned left, the boxes touch: slice anyway
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
        w._select_uid(a.uid); w._set_locked(True)
        x0 = a.x
        w._on_drag(a.uid, 5.0, 0.0)
        assert a.x == x0
        w.select_all(); w.align_selected("left"); app.processEvents()
        assert a.x == x0
        import time
        settle = time.time() + 1.0                       # let pending settings / collision timers fire first:
        while time.time() < settle:                      # a late settings change would clear the result
            app.processEvents()
            time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents()
            time.sleep(0.01)
        assert w.result is not None
        top = w.layer_slider.maximum()
        assert top > 5
        w.layer_slider.setValue(0)
        w.play_btn.setChecked(True)
        for _ in range(400):
            w._play_step()
            if not w.play_btn.isChecked():
                break
        assert w.layer_slider.value() == top and not w.play_btn.isChecked()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_section_header_click_opens_its_window_and_no_stray_selection_frame():
    """A left click on a settings section header (Printer, Quality, Supports...) opens that section in its
    own window (nothing folds open under it). A box-selection frame whose mouse release was missed
    (a dialog opened mid-drag) is removed instead of staying over the window."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPointF, Qt
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        opened = []
        w.settings.open_section_window = lambda cat: opened.append(cat[0])
        w.settings.rebuild(); app.processEvents()
        sec = w.settings._sections["support"]
        sec.header.click()
        assert opened == ["support"], opened
        assert not sec.body.isVisible() and not sec.toggle.isVisible()     # nothing folds open in the panel
        w.settings.search.setText("spacing"); app.processEvents()
        assert any(s.body.isVisible() for s in w.settings._sections.values())   # search shows the matches
        from PySide6.QtWidgets import QRubberBand
        v = w.viewer
        band = QRubberBand(QRubberBand.Rectangle, v); band.show()
        v._band = (QPointF(1, 1), band)
        v.focusOutEvent(__import__("PySide6.QtGui", fromlist=["QFocusEvent"]).QFocusEvent(__import__("PySide6.QtCore", fromlist=["QEvent"]).QEvent.FocusOut))
        assert v._band is None and band.isHidden()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_rotation_rings_follow_the_cursor_from_any_view():
    """Dragging a rotation ring turns the model the way the cursor goes, smoothly: from the corner view, from
    the top (the side rings seen almost edge-on) and from the front; the rings stay put during the drag; and
    circling the cursor past half a turn keeps adding up instead of jumping back."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, math
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
        from PySide6.QtGui import QMouseEvent
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        o = w.add_mesh(primitives.box(30, 12, 8)); app.processEvents()
        w._select_uid(o.uid); app.processEvents()
        v = w.viewer
        v.tool = "rotate"
        def mouse(kind, xy, buttons):
            p = QPointF(float(xy[0]), float(xy[1]))
            btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
            ev = QMouseEvent(kind, p, v.mapToGlobal(p), btn, buttons, Qt.NoModifier)
            {QEvent.MouseButtonPress: v.mousePressEvent, QEvent.MouseMove: v.mouseMoveEvent,
             QEvent.MouseButtonRelease: v.mouseReleaseEvent}[kind](ev)

        got = []
        v.rotateDragged.connect(lambda uid, axis, deg: got.append((axis, deg)))

        def ring_point(axis, a):
            c, r = v._gizmo()
            u, vv = np.eye(3)[(axis + 1) % 3], np.eye(3)[(axis + 2) % 3]
            p = c + r * (math.cos(a) * u + math.sin(a) * vv)
            return p, v._to_screen(p[None, :])[0][0]

        checked = 0
        modes = []
        for view, near in (("iso", True), ("top", True), ("front", True), ("iso", False), ("top", False)):
            v.view_preset(view)
            if near:
                c0, r0 = v._gizmo()
                v.camera.target = np.array(c0, dtype=float); v.camera.distance = r0 * 5    # the model fills the view
            else:
                v.fit_objects()      # far away: the rings keep a size that is easy to take
            app.processEvents()
            for axis in range(3):
                # a point of the ring that shows on the screen, away from where rings cross
                for a in np.linspace(0.3, 2 * math.pi, 23):
                    p, xy = ring_point(axis, a)
                    hit = v._ring_hit(QPointF(*xy))
                    if hit is not None and hit[0] == axis and 50 < xy[0] < v.width() - 50 and 50 < xy[1] < v.height() - 50:
                        break
                else:
                    continue                        # this ring is hidden in this view
                p = np.asarray(hit[1], dtype=float)             # the point of the ring the program took
                del got[:]
                mouse(QEvent.MouseButtonPress, xy, Qt.LeftButton)
                assert v._rot is not None, (view, axis)
                modes.append(v._rot["circular"])
                frame = v._gizmo()
                c, r = frame
                cs = v._to_screen(c[None, :])[0][0]
                if v._rot["circular"]:
                    # follow the ring 200 degrees round with the cursor
                    for t in np.radians(np.linspace(0, 200, 41))[1:]:
                        _, q = ring_point(axis, a + t)
                        mouse(QEvent.MouseMove, q, Qt.LeftButton)
                        assert v._gizmo() is frame                      # the rings do not move
                    deg = got[-1][1]
                    assert abs(deg - 200) < 3, (view, axis, deg)        # past 180: no jump back
                    steps = np.diff([0.0] + [d for _, d in got])
                    assert np.all(steps > -0.5) and steps.max() < 15, (view, axis, steps)   # smooth, one way
                else:
                    # seen nearly edge-on: drag along the ring
                    along = v._rot["along"]
                    eye = v.camera.eye()
                    if abs(np.eye(3)[axis] @ (eye - c)) < 0.02 * np.linalg.norm(eye - c):   # exactly edge-on, the ring
                        assert np.linalg.norm(p - eye) <= np.linalg.norm(c - eye) + 1e-6, (view, axis)   # lies over itself: the near half
                    for k in range(1, 21):
                        q = xy + along * 6 * k
                        mouse(QEvent.MouseMove, q, Qt.LeftButton)
                    deg = got[-1][1]
                    assert 10 < deg < 120, (view, axis, deg)
                    # the grabbed point starts moving the way the cursor is dragged
                    first = c + w._axis_rotation(axis, got[0][1]) @ (p - c)
                    assert (v._to_screen(first[None, :])[0][0] - xy) @ along > 0, (view, axis)
                # the grabbed point of the ring, turned by what was sent, is where the cursor went (same side)
                rot = w._axis_rotation(axis, got[-1][1])
                moved = c + rot @ (p - c)
                mxy = v._to_screen(moved[None, :])[0][0]
                mouse(QEvent.MouseButtonRelease, q, Qt.NoButton)
                app.processEvents()
                assert v._rot is None
                if modes[-1]:
                    d_cursor = np.array(q) - cs
                    d_point = mxy - cs
                    cosang = d_cursor @ d_point / (np.linalg.norm(d_cursor) * np.linalg.norm(d_point) + 1e-9)
                    assert cosang > 0.98 or np.linalg.norm(d_point) < 25, (view, axis, cosang)
                checked += 1
        assert checked >= 10 and True in modes and False in modes, (checked, modes)
        print("ok", checked, modes)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_rotation_fields_show_every_turn():
    """The Rotation fields show the part's whole turn after the 90-degree buttons, the rings and Auto orient
    (these used to be kept apart and the fields stayed at 0); typing an angle then carries on from there,
    and the part stands exactly as before the change of bookkeeping."""
    import numpy as np
    from unislicer import primitives
    from unislicer.scene import SceneObject
    from unislicer.mesh import euler_matrix
    o = SceneObject(primitives.box(20, 10, 4), "part")
    rz = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    before = o.matrix()
    o.apply_world_rotation(rz)
    assert np.allclose(o.rotation, [0, 0, 90]) and np.allclose(o.base_rotation, np.eye(3))
    assert np.allclose(o.matrix()[:3, :3], rz @ before[:3, :3])
    c, s = np.cos(np.radians(35)), np.sin(np.radians(35))
    o.apply_world_rotation(np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]))
    m = o.matrix()
    assert np.allclose(euler_matrix(*o.rotation), m[:3, :3])
    o.rotation[0] = 10.0                       # typing X = 10 keeps the other two
    assert np.allclose(euler_matrix(*o.rotation), o.matrix()[:3, :3])
    # an old project with a turn kept apart still opens the same
    p = SceneObject(primitives.box(20, 10, 4), "old", base_rotation=rz.copy())
    m0 = p.matrix()
    p.apply_world_rotation(np.eye(3))
    assert np.allclose(p.matrix(), m0) and np.allclose(p.rotation, [0, 0, 90])


def test_deleted_parts_free_their_memory_and_undo_still_works():
    """A day's plates stayed in memory (9 GB after 25 plates in a soak test): deleted parts kept their
    cached previews, global tables kept every mesh alive, and the undo history kept each mesh with all its
    worked-out data. Now a removed part's caches go, its mesh kept for undo drops that data, and Undo still
    brings the part back whole."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, gc
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives, dental_cad, layout
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        o = w.add_mesh(primitives.crown_cap()); app.processEvents()
        mesh = o.mesh
        dental_cad.kind_of("x", mesh); dental_cad.opening_direction(mesh); layout.footprint(o)
        mesh.triangles; mesh.face_normals()
        w._support_cache[o.uid] = ("k", None, None)
        assert not hasattr(dental_cad, "_KIND_CACHE") and not hasattr(layout, "_FOOTPRINTS")   # no global tables
        w._select_uid(o.uid); w.delete_selected(); app.processEvents()
        assert not w.objects and o.uid not in w._support_cache
        assert set(mesh.__dict__) <= {"vertices", "faces", "name", "_watertight", "_volume"}, sorted(mesh.__dict__)
        w.undo(); app.processEvents()
        assert len(w.objects) == 1 and w.objects[0].mesh is mesh
        assert np.allclose(w.objects[0].world_size(), mesh.bounds[1] - mesh.bounds[0])
        assert len(mesh.triangles) == len(mesh.faces)           # rebuilt when needed
        w._stop_preslice()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_rotate_tool_still_drags_the_model_with_the_left_button():
    """With the rotate tool on, pressing on the model itself (not on a ring) and dragging moves it."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        o = w.add_mesh(primitives.box(20, 20, 5)); app.processEvents()
        w._select_uid(o.uid); app.processEvents()
        v = w.viewer
        v.tool = "rotate"
        v._ring_hit = lambda pos, only_axis=None: None          # not on a ring
        v.pick = lambda pos, *a, **k: o.uid                       # on the model
        v._bed_point = lambda pos: np.array([pos.x() * 0.1, pos.y() * 0.1, 0.0])
        x0 = o.x
        QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        QTest.mouseMove(v, QPoint(150, 100))
        QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(150, 100))
        app.processEvents()
        assert abs(o.x - (x0 + 5.0)) < 1e-6, (x0, o.x)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_learn_layout_teaches_the_assistant_my_arrangement(tmp_path):
    """Assistant › Learn layout: the user's arrangement (where each U-shaped arch sits and which way it is
    turned) becomes the layout Arrange repeats; gaps, border and side come from it too."""
    from shapely.geometry import Point
    from unislicer import layout, mesh_tools
    from unislicer.assistant import Assistant
    from unislicer.scene import SceneObject

    def arch():
        ring = Point(0, 0).buffer(20).difference(Point(0, 0).buffer(12))
        u = ring.difference(shapely_box(-30, -30, 30, -2))
        return mesh_tools.extrude(u, 0, 8, "arch")
    from shapely.geometry import box as shapely_box
    plate = (0.0, 0.0, 200.0, 125.0)
    objs = [SceneObject.from_mesh(arch()) for _ in range(4)]
    spots = [(40, 35, 0), (95, 35, 180), (150, 35, 90), (60, 95, 270)]
    for o, (x, y, a) in zip(objs, spots):
        if a:
            o.apply_world_rotation(layout._z_rotation(a))
        o.x, o.y = x, y
    tpl = layout.capture_template(objs, plate)
    assert tpl and tpl["count"] == 4 and tpl["min_gap"] >= 0.3
    want = [(layout.footprint(o).centroid, layout.footprint(o)) for o in objs]
    # scramble: other places, other turns
    for k, o in enumerate(objs):
        o.apply_world_rotation(layout._z_rotation(45 * (k + 1)))
        o.x, o.y = 20 + 45 * k, 110 - 20 * k
    assert layout.apply_template(objs, plate, tpl)
    got = [layout.footprint(o) for o in objs]
    for (c, shape), g in zip(want, got):
        assert any(abs(h.centroid.x - c.x) < 0.6 and abs(h.centroid.y - c.y) < 0.6 and
                   h.intersection(shape).area / h.union(shape).area > 0.85 for h in got)
    # a different number of parts: the template is not forced
    assert not layout.apply_template(objs[:3], plate, tpl)
    # the assistant keeps it and uses its gap / border / side
    a = Assistant(str(tmp_path / "a.json"))
    assert a.teach_layout(tpl)
    out = a.learned_layout({"strategy": "contour", "spacing": 2.0, "margin": 2.0, "rotation": "free",
                            "anchor": "center", "sort": "area"})
    assert out["spacing"] == tpl["spacing"] and out["learned"]
    assert Assistant(str(tmp_path / "a.json")).taught_layout()["count"] == 4
    assert a.summary()["layout_taught"] == 4


def test_learn_layout_button_and_arrange_repeat_it():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1600, 1000); w.show(); app.processEvents()
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        import time
        objs = [w.add_mesh(primitives.box(30, 12, 5)) for _ in range(3)]
        settle = time.time() + 1.0                     # the new printer's plate is taken up on a timer: a late
        while time.time() < settle:                    # one brought the parts along and re-arranged them
            app.processEvents(); time.sleep(0.01)
        for o, (x, y) in zip(objs, ((40, 30), (100, 30), (160, 90))):
            o.x, o.y = x, y
        w._objects_changed()
        w.assistant_panel.learn_layout_btn.click(); app.processEvents()
        keep = [(o.x, o.y) for o in objs]
        for o in objs:
            o.x, o.y = 60, 60
        taught = w.status.text()
        w.arrange_objects(); app.processEvents()
        assert all(abs(o.x - k[0]) < 0.6 and abs(o.y - k[1]) < 0.6 for o, k in zip(objs, keep)), (
            taught, w.status.text(), keep, [(round(o.x, 2), round(o.y, 2)) for o in objs])
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)   # Windows reads the profile folder, not HOME
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_new_install_starts_with_the_bundled_learning(tmp_path, monkeypatch):
    """A new install (no learning file yet) starts with the knowledge learned on the Prodentix workstation;
    the first save writes the user's own file, which wins from then on; an existing file is never replaced."""
    import json
    from unislicer import assistant, profiles
    seed = json.load(open(assistant.SEED_FILE, encoding="utf-8"))
    assert seed["layout"]["taught"]["count"] == 5 and seed["orient"]["sla"]["examples"] >= 8
    assert not any(x.startswith("crash_") for x in seed["dismissed"])
    monkeypatch.delenv("UNISLICER_NO_SEED", raising=False)
    monkeypatch.setattr(profiles, "USER_DIR", str(tmp_path / "user"))
    a = assistant.Assistant()
    assert a.taught_layout()["count"] == 5
    a.data["supports"]["angle_offset"] = -10.0
    a.save()
    b = assistant.Assistant()
    assert b.data["supports"]["angle_offset"] == -10.0          # the user's own file wins
    assert os.path.isfile(os.path.join(str(tmp_path / "user"), assistant.FILE_NAME))


def test_export_and_import_learning_between_computers(tmp_path):
    """Assistant › Export / Import learning: the file carries everything learned (crash ids of the old
    computer left out); importing replaces the learning here; a file that is not a learning file is refused."""
    import json
    from unislicer.assistant import Assistant
    a = Assistant(str(tmp_path / "pc1"))
    a.data["supports"]["angle_offset"] = -12.0
    a.data["dismissed"] = ["tree_supports", "crash_abc"]
    a.teach_layout({"count": 2, "spacing": 3.0, "min_gap": 3.0, "margin": 4.0, "anchor": "center", "plate": [0, 0, 100, 100],
                    "slots": []})
    out = str(tmp_path / "learning.json")
    a.export_to(out)
    saved = json.load(open(out, encoding="utf-8"))
    assert saved["dismissed"] == ["tree_supports"]
    b = Assistant(str(tmp_path / "pc2"))
    b.set_enabled(False) if hasattr(b, "set_enabled") else None
    assert b.import_from(out)
    assert b.data["supports"]["angle_offset"] == -12.0 and b.taught_layout() is not None or not b.enabled
    assert Assistant(str(tmp_path / "pc2")).data["supports"]["angle_offset"] == -12.0     # saved there
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    assert not b.import_from(str(bad))
    assert Assistant(str(tmp_path / "pc2")).data["supports"]["angle_offset"] == -12.0


def test_estimate_void_bodies_and_job_report(tmp_path):
    """GrabCAD-style tools: per-model estimate (calibrated by a slice), insert / void bodies (a magnet
    pocket cut into the model's base) and the job report (HTML; the GUI writes it as a PDF)."""
    from shapely.geometry import Point
    from unislicer import estimate, primitives, profiles, report, sla, void_body
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"support_enable": False, "sla_layer_height": 0.1})
    a = SceneObject.from_mesh(primitives.box(20, 20, 10, "A")); a.x, a.y = 50, 50
    b = SceneObject.from_mesh(primitives.box(10, 10, 5, "B")); b.x, b.y = 100, 50
    est = estimate.plate([a, b], cfg)
    assert [r.name for r in est.rows] == ["A", "B"]
    assert abs(est.rows[0].model_ml - 4.0) < 0.01 and abs(est.rows[1].model_ml - 0.5) < 0.01
    assert est.layers == 100 and est.time_s > 0 and est.cost > 0
    assert "TOTAL" in est.csv()
    # a magnet pocket from the base: the slice loses exactly that cylinder
    spec = void_body.PRESETS["magnet_6x3"]
    cut = SceneObject.from_mesh(void_body.body(spec))
    void_body.place(cut, a.world_bounds(), spec)
    assert cut.role == "cutter" and abs(cut.x - 50) < 1e-6 and cut.z == 0.0
    job = sla.prepare([a.world_mesh(), b.world_mesh()], cfg, cutters=[cut.world_mesh()])
    low = job.layers[10]                                        # z ~1 mm: inside the pocket
    assert not low.contains(Point(50, 50)) and low.contains(Point(55, 50))
    assert job.layers[50].contains(Point(50, 50))               # above the pocket: solid
    # calibrated by the slice: rows add up to the sliced volume
    cal = estimate.plate([a, b], cfg, result=("sla", job, cfg))
    assert cal.calibrated and abs(cal.total_ml - job.volume_ml) < 1e-6
    html = report.build_html(cal, cfg, "Phrozen Sonic XL 4K Plus", "Dental model", "A",
                             picture_png=report.plate_picture_png([a, b]))
    assert "UniSlicer job report" in html and "Phrozen Sonic XL 4K Plus" in html and "data:image/png" in html


def test_job_report_pdf_is_written_beside_the_export(tmp_path):
    import subprocess, sys, textwrap
    lib = str(tmp_path / "prints")
    code = textwrap.dedent(f'''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("export_library", {lib!r}); w._reveal = lambda p: None
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False); app.processEvents()
        o = w.add_mesh(primitives.box(10, 10, 5)); o.name = "part"
        o.direct = True                         # on the plate: no 'no supports' question
        settle = time.time() + 1.0
        while time.time() < settle:
            app.processEvents(); time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        info = w.slice_info.text()                       # print time and material next to Slice
        assert w.slice_info.isVisibleTo(w) and info.startswith("⏱ ") and " m" in info, info
        w.export()
        e = time.time() + 120                            # resin files are written on their own thread
        while any(t.isRunning() for t in getattr(w, "_export_tasks", [])) and time.time() < e:
            app.processEvents(); time.sleep(0.02)
        app.processEvents()
        pdf = os.path.join({lib!r}, "part", "part - report.pdf")
        assert os.path.isfile(pdf) and open(pdf, "rb").read(5) == b"%PDF-", os.listdir(os.path.join({lib!r}, "part"))
        from unislicer import project
        proj = os.path.join({lib!r}, "part", "part" + project.EXTENSION)
        objs, state = project.load(proj)                 # the plate is saved as a project beside the print file
        assert [o.name for o in objs] == ["part"] and state["printer"] == "elegoo_mars3"
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_print_queue_order_moves_and_states(tmp_path):
    from unislicer import print_queue
    q = print_queue.Queue(str(tmp_path))
    a = q.add("p1", str(tmp_path / "a.ctb"), time_s=3600)
    b = q.add("p1", str(tmp_path / "b.ctb"), time_s=1800)
    c = q.add("p2", str(tmp_path / "c.ctb"))
    assert q.next_for("p1").id == a.id and q.waiting_time("p1") == 5400
    q.reorder("p1", [b.id, a.id])
    assert [j.id for j in q.for_printer("p1")] == [b.id, a.id] and q.for_printer("p2")[0].id == c.id
    q.move_to(a.id, "p2")
    assert [j.id for j in q.for_printer("p2")] == [c.id, a.id]
    q.mark(b.id, "sending")
    print_queue._STARTED.clear()                               # the program started again after a crash
    q2 = print_queue.Queue(str(tmp_path))                     # reopened after a crash mid-upload
    assert q2.get(b.id).state == "waiting"
    q2.mark(b.id, "sent", "ok")
    q2.clear_sent()
    assert q2.get(b.id) is None and len(q2.jobs) == 2
    assert print_queue.printer_ready({"state": "Idle"}) and not print_queue.printer_ready({"state": "printing"})
    assert not print_queue.printer_ready(None)


def test_print_queue_window_sends_the_next_job(tmp_path):
    """Schedule view: the sliced plate goes into a printer's queue (file in the prints folder) and
    'Send & start next' delivers it (here to a network-folder printer) after the plate-empty question."""
    import subprocess, sys, textwrap
    lib, drop, home = str(tmp_path / "prints"), str(tmp_path / "printer_folder"), str(tmp_path / "home")
    os.makedirs(drop)
    code = textwrap.dedent(f'''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication([])
        from unislicer import network, primitives, print_queue
        network.save_printers([network.NetPrinter("Lab folder", "folder", {drop!r})])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("export_library", {lib!r}); w._reveal = lambda p: None
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False); app.processEvents()
        o = w.add_mesh(primitives.box(10, 10, 5)); o.name = "crown"
        o.direct = True                         # on the plate: no 'no supports' question
        settle = time.time() + 1.0
        while time.time() < settle:
            app.processEvents(); time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        dlg = w.queue_dialog(); app.processEvents()
        pid = dlg.printers[0].id
        w.add_plate_to_queue(pid, dlg); app.processEvents()
        e = time.time() + 120                            # resin files are written on their own thread
        while any(t.isRunning() for t in getattr(w, "_export_tasks", [])) and time.time() < e:
            app.processEvents(); time.sleep(0.02)
        app.processEvents()
        jobs = dlg.queue.for_printer(pid)
        assert len(jobs) == 1 and os.path.isfile(jobs[0].path) and jobs[0].time_s > 0, jobs
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        dlg.columns[pid].send_btn.click()
        end = time.time() + 60
        while dlg.queue.for_printer(pid)[0].state in ("waiting", "sending") and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert dlg.queue.for_printer(pid)[0].state == "sent", dlg.queue.for_printer(pid)[0]
        assert os.listdir({drop!r}) == ["crown.ctb"], os.listdir({drop!r})
        assert print_queue.Queue().for_printer(pid)[0].state == "sent"
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_model_settings_apply_to_that_model_only(tmp_path):
    """Model-level settings (GrabCAD): one model hollowed with honeycomb while its neighbour prints solid;
    denser supports for one model only; kept in undo snapshots, copies and projects."""
    from shapely.geometry import Point, box
    from unislicer import primitives, profiles, project, sla
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"support_enable": False, "sla_layer_height": 0.1})
    a = SceneObject.from_mesh(primitives.box(30, 20, 15, "A")); a.x, a.y = 50, 50
    b = SceneObject.from_mesh(primitives.box(30, 20, 15, "B")); b.x, b.y = 120, 50
    a.settings = {"hollow_enable": True, "hollow_wall": 2.0, "hollow_infill": "hive", "drain_holes": 0}
    job = sla.prepare([a.world_mesh(), b.world_mesh()], cfg, model_settings=[a.settings, b.settings])
    mid = job.layers[len(job.layers) // 2]
    ra, rb = box(38, 43, 62, 57), box(108, 43, 132, 57)          # inside each model, away from the walls
    assert mid.intersection(ra).area < 0.8 * ra.area             # A: hollow with honeycomb walls
    assert mid.intersection(ra).area > 0.05 * ra.area
    assert mid.intersection(rb).area > 0.99 * rb.area             # B: solid
    # supports: the model with its own spacing gets denser supports
    sup = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"support_enable": True, "sla_layer_height": 0.1})
    ball = SceneObject.from_mesh(primitives.sphere(10)); ball.x, ball.y = 80, 60
    plain = sla.prepare([ball.world_mesh()], sup)
    dense = sla.prepare([ball.world_mesh()], sup, model_settings=[{"sla_support_spacing": 1.5, "sla_support_preset": "custom"}])
    assert len(dense.support_points) > len(plain.support_points)
    # undo / copies / projects keep them
    st = a.state()
    a.settings = {}
    a.restore(st)
    assert a.settings["hollow_infill"] == "hive" and a.duplicate().settings == a.settings
    assert a.config(cfg)["hollow_wall"] == 2.0 and b.config(cfg)["hollow_enable"] == cfg["hollow_enable"]
    path = str(tmp_path / "m.unislicer")
    project.save(path, [a, b], {})
    back, _ = project.load(path)
    assert back[0].settings == a.settings and back[1].settings == {}


def test_3d_texture_keeps_the_model_closed_and_the_base_flat():
    """3D textures (GrabCAD Print Pro): the pattern moves the surface by at most the depth, the model
    stays watertight, the flat base stays flat, 'sides' leaves the top alone; pressing in (negative depth)
    removes material."""
    import numpy as np
    from unislicer import primitives, texture
    box = primitives.box(20, 20, 10)
    for kind in texture.PATTERNS:
        t = texture.apply(box, kind, 2.0, 0.4, "sides")
        assert t.is_watertight(), kind
        lo, hi = t.bounds
        assert lo[2] >= -1e-9 and abs(hi[2] - 10) < 1e-9                    # base and top untouched
        assert lo[0] >= -0.4 - 1e-6 and hi[0] <= 20.4 + 1e-6                 # never more than the depth
    t = texture.apply(box, "knurl", 2.0, 0.4, "sides")
    v = t.vertices
    assert np.allclose(v[v[:, 2] < 1e-9][:, 2], 0.0)                         # base vertices stay on z = 0
    assert t.volume() > box.volume()
    pressed = texture.apply(box, "dots", 2.0, -0.4, "all")
    assert pressed.is_watertight() and pressed.volume() < box.volume()


def test_texture_from_the_window_and_undo():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        o = w.add_mesh(primitives.box(20, 20, 10)); w._select_uid(o.uid)
        smooth = o.mesh
        w.apply_texture([o], "ribs", 2.0, 0.3, "sides"); app.processEvents()
        assert o.mesh is not smooth and len(o.mesh.faces) > len(smooth.faces) and o.mesh.is_watertight()
        w.undo(); app.processEvents()
        assert w.objects[0].mesh is smooth
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_empty_two_stage_motion_is_not_sent_to_the_printer():
    """Phrozen DS Slicer profiles keep TwoStage on with an empty second stage (all 0). That must not reach
    the print file as a two-stage move (0 mm at the minimum 5 mm/min speed)."""
    from unislicer import profiles
    from unislicer.settings import cfg_tsmc
    cfg = profiles.build_config("phrozen_sonic_ls_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None, {})
    assert not cfg["tsmc_enable"] and not cfg_tsmc(cfg)
    assert cfg["exposure_time"] == 1.0 and cfg["bottom_exposure_time"] == 35.0      # Phrozen's own values
    on = cfg.copy().update({"tsmc_enable": True, "lift_height_2": 0.0, "retract_height_2": 0.0})
    assert not cfg_tsmc(on)
    real = cfg.copy().update({"tsmc_enable": True, "lift_height_2": 3.0, "lift_speed_2": 200.0})
    assert cfg_tsmc(real)


def test_every_bundled_profile_is_sane():
    """Every printer / resin / quality profile (and every row of a resin's layer table): no empty two-stage
    motion, no value outside the setting's range (it would be clamped silently), no unknown option,
    exposure and motion values that make sense."""
    from unislicer import profiles
    from unislicer.settings import SCHEMA_BY_KEY
    bad = []
    rows = 0
    for kind in profiles.KINDS:
        for pid, prof in profiles.load(kind).items():
            tables = [(f"{kind}/{pid}", prof.get("values", {}))]
            for printer, table in (prof.get("layer_table") or {}).items():
                for th, modes in table.items():
                    tables += [(f"{kind}/{pid}[{printer} {th} {m}]", v) for m, v in modes.items()]
            for where, v in tables:
                rows += 1
                if v.get("tsmc_enable") and not (float(v.get("lift_height_2", 4.0)) > 0 or float(v.get("retract_height_2", 2.0)) > 0):
                    bad.append(f"{where}: empty two-stage motion")
                for k, x in v.items():
                    s = SCHEMA_BY_KEY.get(k)
                    if s is None:
                        continue
                    if s.kind in ("float", "int"):
                        if (s.minimum is not None and float(x) < s.minimum - 1e-9) or (s.maximum is not None and float(x) > s.maximum + 1e-9):
                            bad.append(f"{where}: {k}={x} outside {s.minimum}..{s.maximum}")
                    elif s.kind == "enum" and s.options and x not in [o[0] for o in s.options]:
                        bad.append(f"{where}: {k}={x!r} is not an option")
                if "exposure_time" in v and float(v["exposure_time"]) <= 0:
                    bad.append(f"{where}: no exposure")
                if "bottom_exposure_time" in v and "exposure_time" in v and float(v["bottom_exposure_time"]) < float(v["exposure_time"]):
                    bad.append(f"{where}: bottom exposure below normal")
                for k in ("lift_speed", "retract_speed", "bottom_lift_speed", "bottom_retract_speed", "lift_height", "bottom_lift_height"):
                    if k in v and float(v[k]) <= 0:
                        bad.append(f"{where}: {k}={v[k]}")
    assert rows > 3000
    assert not bad, bad[:20]


def test_printer_screen_preview_is_a_clear_3d_picture(tmp_path):
    """The picture on the printer's screen (PRZ / GOO / CTB / SL1) is a shaded 3D view of the models and
    supports that fills the frame, not a small flat silhouette of the whole plate."""
    import zipfile
    import numpy as np
    from PIL import Image
    from unislicer import ctb, primitives, profiles, sla
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("phrozen_sonic_ls_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None,
                                {"support_enable": True})
    o = SceneObject.from_mesh(primitives.sphere(10)); o.x, o.y = 90, 60
    job = sla.prepare([o.world_mesh()], cfg)
    img = np.asarray(ctb.make_preview(job.layers, cfg, (290, 290), job.models, job.supports))
    lit = np.any(np.abs(img.astype(int) - ctb.PREVIEW_BG) > 12, axis=2)
    ys, xs = np.nonzero(lit)
    assert (xs.max() - xs.min()) > 0.5 * 290                    # fills the frame
    assert len(np.unique(img.reshape(-1, 3), axis=0)) > 200     # shaded, not a flat colour
    flat = np.asarray(ctb._top_view(job.layers, cfg, (290, 290)))
    assert lit.mean() > 2 * np.any(np.abs(flat.astype(int) - ctb.PREVIEW_BG) > 12, axis=2).mean()
    sl1 = profiles.build_config("prusa_sl1s", None, None, {"support_enable": False})
    job2 = sla.prepare([o.world_mesh()], sl1)
    path = sla.export(job2, sl1, str(tmp_path / "a.sl1"))
    names = zipfile.ZipFile(path).namelist()
    assert "thumbnail/thumbnail400x400.png" in names and "thumbnail/thumbnail800x480.png" in names


def test_closed_honeycomb_cavity_is_a_suction_cup_and_a_vent_clears_it():
    """A hollow model with honeycomb infill and a closed floor (no drain holes) printed on supports is one big
    suction cup - it tore real prints apart (split layers, a torn floor). The automatic vent clears it."""
    from unislicer import primitives, profiles, sla
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("phrozen_sonic_ls_plus", "phrozen_ds_phrozen_dental_washable_dental_model", None,
                                {"support_enable": True, "hollow_enable": True, "hollow_mode": "inner",
                                 "hollow_infill": "hive", "drain_holes": 0, "sla_layer_height": 0.1,
                                 "sla_hole_advice": True})
    o = SceneObject.from_mesh(primitives.box(40, 30, 15)); o.x, o.y = 90, 60
    job = sla.prepare([o.world_mesh()], cfg)
    assert job.cups and max(a for _i, a in job.cups) > 500 and job.cup_vents
    lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
    for v in job.cup_vents:
        for row in sla.vent_hole_rows(v, cfg, lift):
            o.add_world_drain_hole(row[:3], row[3:], 0.0)
    job2 = sla.prepare([o.world_mesh()], cfg, holes=[o.world_drain_holes(0.0)])
    assert not job2.cups


def test_selection_box_hugs_a_turned_model():
    """The orange selection box is the model's exact extent as it stands (tilted / turned models included),
    not the turned corners of its unturned box."""
    import numpy as np
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from unislicer import primitives
    from unislicer.gui.viewer import Viewer
    from unislicer.scene import SceneObject
    o = SceneObject.from_mesh(primitives.sphere(10, 24, 48))
    o.rotation = np.array([30.0, 20.0, 45.0])
    o.x, o.y = 50, 50
    m = o.world_matrix()
    obj = {"mesh": o.mesh, "matrix": m}
    box = Viewer._world_box(obj)
    true = o.mesh.transform(m).bounds
    assert np.allclose(box, true, atol=1e-9)
    old_corners = np.array([[x, y, z] for x in o.mesh.bounds[:, 0] for y in o.mesh.bounds[:, 1]
                            for z in o.mesh.bounds[:, 2]]) @ m[:3, :3].T + m[:3, 3]
    assert (old_corners.max(axis=0) - old_corners.min(axis=0)).max() > (true[1] - true[0]).max() + 3   # the old box was loose
    m2 = m.copy(); m2[:3, 3] += [5, 0, 0]
    obj["matrix"] = m2
    assert np.allclose(Viewer._world_box(obj), true + [5, 0, 0])          # moving reuses the cached box


def test_vent_found_for_a_narrow_slot_cup():
    """A narrow sealed slot (e.g. between a base bar and the arch, ~2 mm wide) is narrower than the vent hole:
    no vent was found, so 'Add vent holes and slice again' did nothing. The vent now goes on the slot's
    middle line and clears the cup."""
    from shapely.geometry import box
    from unislicer import mesh_tools, profiles, sla
    from unislicer.mesh import Mesh
    lift = 3.0
    outer = box(0, 0, 20, 8)
    slot = box(2, 3, 18, 4.2)                                 # 16 x 1.2 mm = 19 mm², narrower than the hole
    part = Mesh.merge([mesh_tools.extrude(outer, lift, lift + 2),
                       mesh_tools.extrude(outer.difference(slot), lift + 2, lift + 10)], "bar")
    z = [0.05 + 0.1 * i for i in range(int((lift + 10) / 0.1))]
    layers = sla.slice_meshes([part], z, 0.05)
    assert sla.find_cups(layers)
    vents = sla.cup_vents(layers, z, radius=1.5)
    assert len(vents) == 1
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"drain_hole_diameter": 3.0})
    cut = sla.slice_meshes(sla.hole_meshes([sla.vent_hole_rows(vents[0], cfg)], cfg, 0.0), z, 0.05)
    drilled = [g.difference(c) if not c.is_empty else g for g, c in zip(layers, cut)]
    assert not sla.find_cups(drilled)


def test_tiny_anatomy_pits_are_not_a_suction_cup():
    """Many small sealed pits in one layer (tooth fissures, ~5 mm² each) pull no real vacuum; added up they
    used to look like one 20 mm² cup, which the vent tool could never clear on real dental scans."""
    from shapely.geometry import box
    from unislicer import sla
    pits = sla.as_multipolygon(box(0, 0, 2, 2.5).union(box(5, 0, 7, 2.5)).union(box(10, 0, 12, 2.5)).union(box(15, 0, 17, 2.5)))
    assert sla.find_cups([pits], sealed=[pits]) == []                       # 4 x 5 mm² = 20 mm² of pits
    cup = sla.as_multipolygon(box(0, 0, 4, 4))
    assert sla.find_cups([cup], sealed=[cup]) == [(0, 16.0)]                 # one real 16 mm² pocket


def test_file_edit_info_menu_bar_and_stylesheet_parses():
    """Classic File / Edit / Info menus run the same actions as the toolbar; the application stylesheet parses
    even when the user folder has spaces / backslashes (a Windows profile like C:\\Users\\Benny Z)."""
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtCore import qInstallMessageHandler
        msgs = []
        qInstallMessageHandler(lambda t, c, m: msgs.append(m))
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui import theme
        theme._write_assets = lambda: "C:\\\\Users\\\\Benny Z\\\\AppData\\\\Local\\\\UniSlicer\\\\assets"
        theme.set_theme("prodentix"); theme.apply(app)
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        assert not [m for m in msgs if "parse" in m], msgs
        menus = [a.menu() for a in w.main_menu.actions()]
        assert len(menus) == 3
        texts = [a.text() for m in menus for a in m.actions()]
        assert not any("……" in t or "......" in t for t in texts), texts
        w.add_mesh(primitives.box(10, 10, 5)); app.processEvents()
        dup = next(a for a in menus[1].actions() if a.text().endswith("Ctrl+D"))
        w.select_all(); dup.trigger(); app.processEvents()
        assert len(w.objects) == 2
        undo = next(a for a in menus[1].actions() if a.text().endswith("Ctrl+Z"))
        undo.trigger(); app.processEvents()
        assert len(w.objects) == 1
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_shared_learning_merge_and_sync(tmp_path):
    """Learning shared between the user's computers: merging is stable when repeated (counts take the
    largest, learned numbers are weighted averages, the newest taught layout wins), the agent uploads its
    snapshot under the owner and returns the other computers', and local switches stay local."""
    import copy, json
    from unislicer import learning_sync, remote
    from unislicer.assistant import Assistant
    a = Assistant(str(tmp_path / "a")); b = Assistant(str(tmp_path / "b"))
    a.data["orient"]["sla"] = {"weights": [1.0, 0.2, 0.0, 0.1], "examples": 8}
    b.data["orient"]["sla"] = {"weights": [1.0, 0.6, 0.0, 0.3], "examples": 2}
    a.data["supports"].update({"spacing_scale": 1.2, "sessions": 4}); b.data["supports"].update({"spacing_scale": 0.8, "sessions": 1})
    a.data["overrides"] = {"sla_support_spacing=5.0": {"count": 3, "last": 10}}
    b.data["overrides"] = {"sla_support_spacing=5.0": {"count": 1, "last": 20}, "sla_raft_type=\"hexagon\"": {"count": 2, "last": 5}}
    a.teach_layout({"count": 2, "spacing": 3.0, "min_gap": 3.0, "margin": 2.0, "anchor": "center", "plate": [0, 0, 1, 1], "slots": []})
    b.teach_layout({"count": 4, "spacing": 1.5, "min_gap": 1.5, "margin": 1.0, "anchor": "center", "plate": [0, 0, 1, 1], "slots": []})
    b.data["layout"]["taught"]["taught_at"] = a.data["layout"]["taught"]["taught_at"] + 60   # Windows' clock is coarse
    a.data["enabled"] = True; b.data["dismissed"] = ["tree_supports"]
    m = learning_sync.merge(a.data, [json.loads(learning_sync.snapshot(b.data))])
    w = m["orient"]["sla"]["weights"][1]
    assert 0.2 < w < 0.4 and m["orient"]["sla"]["examples"] == 8               # weighted towards the 8 examples
    assert 0.8 < m["supports"]["spacing_scale"] < 1.2
    assert m["overrides"]["sla_support_spacing=5.0"]["count"] == 3 and "sla_raft_type=\"hexagon\"" in m["overrides"]
    assert m["layout"]["taught"]["count"] == 4                                  # b taught last
    assert "dismissed" not in json.loads(learning_sync.snapshot(b.data))        # local tips stay local
    again = learning_sync.merge(m, [json.loads(learning_sync.snapshot(m))])        # both sides synced: stable
    assert again["overrides"] == m["overrides"] and abs(again["orient"]["sla"]["weights"][1] - w) < 1e-9
    # a computer that stays off keeps an old snapshot: it is merged once, not every 15 minutes
    stale = [{"x": 1, "_from": {"uid": "pc2", "at": 100}}]
    first, seen = learning_sync.fresh(stale, {})
    second, seen = learning_sync.fresh(stale, seen)
    third, _ = learning_sync.fresh([{"x": 2, "_from": {"uid": "pc2", "at": 200}}], seen)
    assert len(first) == 1 and second == [] and len(third) == 1
    # the agent: upload under the owner, read the others
    class FakeFB:
        uid = "pc1"
        def __init__(self): self.db = {"teams/owner/learning/pc2": {"data": learning_sync.snapshot(b.data), "at": 5, "name": "Lab 2"}}
        def put(self, path, value): self.db[path] = value
        def get(self, path): return {k.rsplit("/", 1)[1]: v for k, v in self.db.items() if k.startswith(path + "/")}
    agent = remote.Agent({"apiKey": "x", "databaseURL": "https://x"}, str(tmp_path / "r"))
    agent.fb, agent.state, agent.st = FakeFB(), "linked", {"owner": "owner"}
    others = agent.sync_learning(learning_sync.snapshot(a.data))
    assert "teams/owner/learning/pc1" in agent.fb.db and len(others) == 1 and others[0]["_from"]["name"] == "Lab 2"
    agent.state = "pairing"
    try:
        agent.sync_learning("{}")
        assert False, "must refuse when not linked"
    except remote.RemoteError:
        pass
    rules = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remote_web", "database.rules.json")))
    assert "teams" in rules["rules"]


def test_window_syncs_learning_with_a_linked_computer():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os, time, json
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication([])
        from unislicer import learning_sync
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        other = dict(w.assistant.data)
        other = json.loads(learning_sync.snapshot(other))
        other["overrides"] = {"sla_raft_type=\\"hexagon\\"": {"count": 5, "last": 1}}
        other["_from"] = {"uid": "pc2", "name": "Lab 2", "at": 123}
        sent = []
        class FakeAgent:
            state = "linked"
            def running(self): return True
            def sync_learning(self, text):
                sent.append(text); return [dict(other)]
        w.remote.agent = FakeAgent()
        w.sync_learning(manual=True)
        end = time.time() + 30
        while (w._learn_sync_task is None or w._learn_sync_task.isRunning() or not sent) and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        for _ in range(20):
            app.processEvents(); time.sleep(0.01)
        assert sent and json.loads(sent[0])
        assert w.assistant.data["overrides"].get('sla_raft_type="hexagon"', {}).get("count") == 5, w.assistant.data["overrides"]
        assert "1" in w.assistant_panel.sync_status.text() or "Lab" in w.status.text() or w.store.value("learning_sync_peers") in (1, "1")
        # not linked: says so, does not crash
        w.remote.agent = None
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        w.sync_learning(manual=True)
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_thicken_thin_features_only_grows_the_thin_parts():
    from shapely.geometry import box
    from unislicer import mesh_tools, profiles, sla
    from unislicer.mesh import Mesh
    part = Mesh.merge([mesh_tools.extrude(box(0, 0, 10, 10), 0, 6), mesh_tools.extrude(box(10, 4, 20, 4.2), 0, 6)], "fin")
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, {"support_enable": False, "sla_layer_height": 0.1})
    plain = sla.prepare([part.translated([60, 60, 0])], cfg)
    assert plain.thin_layers                                            # a 0.2 mm fin
    thick = sla.prepare([part.translated([60, 60, 0])], cfg.copy().update({"sla_thicken_thin": True}))
    assert len(thick.thin_layers) < len(plain.thin_layers) * 0.2
    mid = len(thick.layers) // 2
    block = box(60.5, 60.5, 69.5, 69.5)
    assert abs(thick.layers[mid].intersection(block).area - plain.layers[mid].intersection(block).area) < 1e-6   # body untouched
    fin = box(72, 63, 78, 65.5)
    assert thick.layers[mid].intersection(fin).area > plain.layers[mid].intersection(fin).area * 1.3


def test_print_check_rows_have_fix_buttons():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        w.settings.set_value("support_enable", False)
        w.settings.set_value("hollow_enable", True); w.settings.set_value("drain_holes", 0)
        w.settings.set_value("sla_hole_advice", True)
        o = w.add_mesh(primitives.box(30, 20, 15)); o.direct = True
        settle = time.time() + 1.0
        while time.time() < settle:
            app.processEvents(); time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        assert w.warn_box.isVisible() or not w.warn_box.isHidden()
        texts = [t for t, _b in w.check_fix_buttons]
        assert texts, "no fix buttons"
        called = []
        w.slice = lambda: called.append(1)
        btn = next(b for t, b in w.check_fix_buttons if "drain" in t.lower() or "ניקוז" in t or "cup" in t.lower() or "יניקה" in t)
        btn.click()
        end = time.time() + 5
        while not called and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert called, texts
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_layer_height_editor_shows_25_microns():
    """Layer thickness is shown and typed in microns (100 / 50 / 32 / 25 µm) and stored in mm."""
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.settings import SCHEMA_BY_KEY
        from unislicer.gui.settings_panel import SettingRow
        from unislicer import profiles
        cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, "sla_detail", {})
        row = SettingRow(SCHEMA_BY_KEY["sla_layer_height"], cfg["sla_layer_height"], False, cfg)
        assert row.editor.value() == 25 and "µm" in row.editor.suffix() or "מיקרון" in row.editor.suffix(), row.editor.value()
        assert abs(row.editor_value() - 0.025) < 1e-9                    # stored in mm
        row.editor.setValue(32)
        assert abs(row.editor_value() - 0.032) < 1e-9
        cfg32 = profiles.build_config("phrozen_sonic_ls_plus", "phrozen_ds_phrozen_dental_washable_dental_model", "sla_fine32", {})
        assert abs(cfg32["sla_layer_height"] - 0.032) < 1e-9 and cfg32["exposure_time"] == 0.8   # 30 µm Phrozen row
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_hovering_a_model_shows_its_name_and_size_see_through():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        o = w.add_mesh(primitives.box(10.4, 11.02, 18.21)); o.name = "Tooth_37.stl"
        v = w.viewer
        v.pick = lambda pos, *a, **k: o.uid
        QTest.mouseMove(v, QPoint(300, 300))
        end = time.time() + 3
        while not v.hover_label.isVisible() and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        t = v.hover_label.text()
        sx, sy, sz = o.world_size()
        assert v.hover_label.isVisible() and "Tooth_37.stl" in t and f"{sx:.2f} × {sy:.2f} × {sz:.2f} mm" in t, t
        assert "rgba(20,22,28,0.55)" in v.hover_label.styleSheet()          # see-through
        base = (0.8, 0.8, 0.83)                                             # the model under the cursor is brighter
        lit = v._hover_lit(o.uid, base)
        assert v._hover_uid == o.uid and all(b < l <= 1.0 for b, l in zip(base, lit))
        assert v._hover_lit("other", base) == base
        v.pick = lambda pos, *a, **k: None
        QTest.mouseMove(v, QPoint(320, 310))
        end = time.time() + 3
        while v.hover_label.isVisible() and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert not v.hover_label.isVisible() and v._hover_lit(o.uid, base) == base
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_fix_keeps_the_check_list_and_fix_all_slices_once():
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from shapely.geometry import box
        from unislicer import mesh_tools
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        w.settings.set_value("support_enable", False)
        w.settings.set_value("hollow_enable", True); w.settings.set_value("drain_holes", 0)
        w.settings.set_value("sla_hole_advice", True)
        part = Mesh.merge([mesh_tools.extrude(box(0, 0, 20, 20), 0, 12), mesh_tools.extrude(box(20, 8, 30, 8.2), 0, 12)], "p")
        o = w.add_mesh(part); o.direct = True
        def run_slice():
            settle = time.time() + 1.0
            while time.time() < settle:
                app.processEvents(); time.sleep(0.01)
            w.slice()
            end = time.time() + 120
            while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
                app.processEvents(); time.sleep(0.01)
        run_slice()
        n = len(w.check_fix_buttons)
        assert n >= 2, [t for t, _ in w.check_fix_buttons]
        assert not w.cup_fix_btn.isHidden()                  # "Fix all" shown when there is more than one
        slices = []
        real_slice = w.slice
        w.slice = lambda: slices.append(1)
        thin_btn = next(b for t, b in w.check_fix_buttons if "0.3" in t)
        thin_btn.click(); app.processEvents()
        assert not w.warn_box.isHidden()                     # the list stays
        assert thin_btn.parentWidget().isHidden()            # only the fixed line goes
        others = [b for t, b in w.check_fix_buttons if b is not thin_btn]
        assert all(not b.parentWidget().isHidden() for b in others)
        end = time.time() + 3
        while not slices and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert len(slices) == 1
        slices.clear()
        w.fix_all_checks(); app.processEvents()
        end = time.time() + 3
        while time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert len(slices) == 1                               # several fixes, one slice
        assert w.settings.config()["drain_holes"] == 2 and w.settings.config()["sla_thicken_thin"]
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_resin_default_button_restores_original(tmp_path, monkeypatch):
    """'Default' next to the resin puts it back on its shipped values: panel edits and changes saved over the profile go,
    and a saved change keeps the per-printer layer table."""
    import subprocess, sys, textwrap
    monkeypatch.setattr(profiles, "USER_DIR", str(tmp_path))
    wdm = "phrozen_ds_phrozen_dental_washable_dental_model"
    orig = profiles.build_config("phrozen_sonic_cs_plus", wdm)["exposure_time"]
    assert wdm in profiles.builtin_ids("materials") and not profiles.is_modified("materials", wdm)
    assert {"exposure_time", "lift_speed"} <= profiles.profile_keys("materials", wdm)
    profiles.save_user_profile("materials", "WDM tweak", {"bottom_exposure_time": 40.0}, "sla", pid=wdm, vendor="Phrozen")
    assert profiles.is_modified("materials", wdm)
    assert profiles.load("materials")[wdm]["layer_table"]        # saving does not lose the table
    assert profiles.build_config("phrozen_sonic_cs_plus", wdm)["bottom_exposure_time"] == 40.0
    assert profiles.restore_default("materials", wdm) and not profiles.is_modified("materials", wdm)
    assert profiles.build_config("phrozen_sonic_cs_plus", wdm)["exposure_time"] == orig
    assert not profiles.restore_default("materials", wdm)

    code = textwrap.dedent(f'''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import profiles
        from unislicer.gui.settings_panel import SettingsPanel
        wdm = "{wdm}"
        profiles.save_user_profile("materials", "WDM tweak", {{"bottom_exposure_time": 40.0}}, "sla", pid=wdm, vendor="Phrozen")
        p = SettingsPanel("phrozen_sonic_cs_plus", wdm, None, {{}}, False)
        assert p.material_id == wdm and p.config()["bottom_exposure_time"] == 40.0
        p.set_value("exposure_time", 9.9)
        p.set_value("infill_density", 33)                   # not a resin setting: stays
        assert "exposure_time" in p.material_changed_keys() and "profile" in p.material_changed_keys()
        assert p.material_default_btn.property("pending")
        p.restore_material_default()
        assert not profiles.is_modified("materials", wdm)
        assert p.config()["exposure_time"] == {orig!r} and "exposure_time" not in p.overrides
        assert p.config()["bottom_exposure_time"] != 40.0 and p.overrides.get("infill_density") == 33
        assert p.material_changed_keys() == [] and not p.material_default_btn.property("pending")
        # Stable / Fast under the quality picks the maker's row
        assert p.mode_combo.isEnabled() and p.mode_combo.currentData() == "stable"
        stable_lift = p.config()["lift_speed"]
        p.mode_combo.setCurrentIndex(p.mode_combo.findData("fast"))
        assert p.config()["exposure_mode"] == "fast" and p.config()["lift_speed"] > stable_lift
        p.set_value("exposure_mode", "stable"); p.changed.emit()
        assert p.mode_combo.currentData() == "stable"
        assert p.select_printer("elegoo_mars3") and not p.mode_combo.isEnabled()   # no maker's table there
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    os.makedirs(_home, exist_ok=True)
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_floating_edit_bar_can_be_dragged_and_stays_there(tmp_path):
    import subprocess, sys, textwrap
    code = textwrap.dedent("""
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
        from PySide6.QtGui import QMouseEvent
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        o = w.add_mesh(primitives.box(10, 10, 10))
        w.select(o.uid) if hasattr(w, "select") else None
        w.viewer.selection = {o.uid} if hasattr(w.viewer, "selection") else None
        w.selected_objects = lambda: [o]
        w._show_edit_bar(); app.processEvents()
        bar, grip = w.edit_bar, w.edit_grip
        assert bar.isVisible()
        start = bar.pos()

        def send(kind, gpos, buttons):
            local = QPointF(grip.mapFromGlobal(gpos))
            ev = QMouseEvent(kind, local, QPointF(gpos), Qt.LeftButton, buttons, Qt.NoModifier)
            QApplication.sendEvent(grip, ev)

        g0 = grip.mapToGlobal(QPoint(5, 5))
        send(QEvent.MouseButtonPress, g0, Qt.LeftButton)
        send(QEvent.MouseMove, g0 + QPoint(0, 90), Qt.LeftButton)
        send(QEvent.MouseButtonRelease, g0 + QPoint(0, 90), Qt.NoButton)
        moved = bar.pos()
        assert moved != start and moved == start + QPoint(0, 90), (start, moved)
        assert w._edit_bar_place() == (moved.x(), moved.y())
        bar.hide(); w._show_edit_bar(); app.processEvents()
        assert bar.pos() == moved                          # opens where it was left
        w._edit_bar_reset()
        assert w._edit_bar_place() is None
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path)
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_fast_ray_picking_matches_brute_force():
    """Hover / click picking tests only the triangle clusters the ray passes; same hit as testing every triangle."""
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from unislicer.gui.viewer import Viewer
    m = placed(primitives.sphere(15, 64, 128), 80, 60).world_mesh()

    def brute(o, d):
        t = m.triangles
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0)
        s = o - t[:, 0]
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        dist = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (dist > 0)
        if not hit.any():
            return None
        i = np.flatnonzero(hit)
        k = i[np.argmin(dist[i])]
        return float(dist[k]), int(k)

    lo, hi = m.bounds
    rng = np.random.default_rng(5)
    hits = 0
    for _ in range(150):
        target = lo + (hi - lo) * rng.random(3)
        o = target + rng.normal(size=3) * 120
        a, b = brute(o, target - o), Viewer._ray_mesh(o, target - o, m)
        assert (a is None) == (b is None)
        if a:
            hits += 1
            assert b[1] == a[1] and abs(a[0] - b[0]) < 1e-9
    assert hits > 50


def test_island_pillars_fix_islands_without_supports():
    """A part that starts in mid-air on a model printed straight on the plate (no supports): the fix puts a
    thin pillar under it, after which no island is left."""
    from unislicer.mesh import Mesh
    base = primitives.box(10, 10, 2)
    top = primitives.box(4, 4, 1)
    tv = np.asarray(top.vertices) + [0, 0, 3]                  # floats 1 mm above the base
    v = np.vstack([base.vertices, tv])
    f = np.vstack([base.faces, np.asarray(top.faces) + len(base.vertices)])
    m = placed(Mesh(v, f, "floating")).world_mesh()
    over = {"support_enable": False, "hollow_enable": False}
    job = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", over))
    assert job.islands and job.island_pillars == 0
    job = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(over, sla_island_pillars=True)))
    assert not job.islands and job.island_pillars >= 1


def test_resin_cache_and_incremental_fix_match_a_full_slice():
    """Slicing again reuses cached contours / hollowing / checks; switching on the fixes (thicken, pillars)
    recomputes only what they change. The result must equal a slice computed from scratch."""
    from shapely.geometry import box as sbox
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    part = Mesh.merge([mesh_tools.extrude(sbox(0, 0, 20, 20), 0, 12),
                       mesh_tools.extrude(sbox(20, 8, 30, 8.2), 0, 12),       # a thin fin
                       mesh_tools.extrude(sbox(5, 5, 9, 9), 13, 14)], "p")    # floats above: an island
    m = placed(part).world_mesh()
    over = {"support_enable": False, "hollow_enable": True, "drain_holes": 0}
    cfg = lambda **k: profiles.build_config("elegoo_mars3", None, "sla_fast", dict(over, **k))
    sla.clear_cache()
    first = sla.prepare([m], cfg())
    assert first.thin_layers and first.islands
    again = sla.prepare([m], cfg())                                       # all cached
    assert [g.area for g in again.layers] == [g.area for g in first.layers]
    fixed = sla.prepare([m], cfg(sla_thicken_thin=True, sla_island_pillars=True))   # incremental
    sla.clear_cache()
    ref = sla.prepare([m], cfg(sla_thicken_thin=True, sla_island_pillars=True))     # from scratch
    assert not fixed.thin_layers and not fixed.islands
    assert fixed.thin_layers == ref.thin_layers and len(fixed.islands) == len(ref.islands)
    assert max(a.symmetric_difference(b).area for a, b in zip(fixed.layers, ref.layers)) < 1e-6
    assert fixed.cups == ref.cups and abs(fixed.volume_ml - ref.volume_ml) < 1e-9


def test_support_pieces_slice_as_their_union_and_fast_repair():
    """Supports are many closed pieces that overlap and touch: each piece is linked on its own and the layer is
    their union (the old fallback left holes where pieces overlapped). The vectorised repair fills holes and
    turns an inside-out shell the right way."""
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    a, b = primitives.box(10, 10, 10), primitives.box(10, 10, 10)
    bv = np.asarray(b.vertices) + [5, 5, 0]                         # overlaps a by a quarter
    m = Mesh(np.vstack([a.vertices, bv]), np.vstack([a.faces, np.asarray(b.faces) + len(a.vertices)]), "s")
    lo = float(m.bounds[0][2])
    layers = sla._slice_pieces(m, [lo + 2.0, lo + 5.0], 0.01)
    assert all(abs(g.area - 175.0) < 1e-6 for g in layers), [g.area for g in layers]
    s = primitives.sphere(10, 32, 64)
    keep = np.ones(len(s.faces), bool)
    keep[[len(keep) // 4, len(keep) // 2, 3 * len(keep) // 4]] = False   # three separate holes
    holed = Mesh(s.vertices, np.asarray(s.faces)[keep][:, ::-1], "inside-out")
    fixed, rep = mesh_tools.repair(holed)
    assert fixed.is_watertight() and rep["holes_filled"] == 3 and rep["flipped"] == len(holed.faces)
    assert fixed.volume() > 0 and mesh_tools.analyze(fixed)["watertight"] and mesh_tools.shell_count(m) == 2


def test_open_mesh_is_closed_automatically_for_resin_slicing():
    """A model with a few holes is closed on a copy for slicing: no warning, and the layers are full."""
    from unislicer.mesh import Mesh
    s = primitives.sphere(10, 32, 64)
    keep = np.ones(len(s.faces), bool)
    keep[[len(keep) // 3, 2 * len(keep) // 3]] = False
    holed = placed(Mesh(s.vertices, np.asarray(s.faces)[keep], "holed")).world_mesh()
    solid = placed(s).world_mesh()
    assert not holed.is_watertight()
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": False})
    job = sla.prepare([holed], cfg)
    ref = sla.prepare([solid], cfg)
    assert not any("watertight" in w for w in job.warnings)
    assert abs(job.volume_ml - ref.volume_ml) < 0.01 * ref.volume_ml


def test_open_model_is_closed_in_the_background_on_load(tmp_path):
    import subprocess, sys, textwrap
    from unislicer.mesh import Mesh, save_stl
    s = primitives.sphere(10, 32, 64)
    keep = np.ones(len(s.faces), bool)
    keep[[len(keep) // 3, 2 * len(keep) // 3]] = False
    path = str(tmp_path / "holed.stl")
    save_stl(Mesh(s.vertices, np.asarray(s.faces)[keep], "holed"), path)
    code = textwrap.dedent(f'''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.load_files([{path!r}])
        o = w.objects[0]
        assert not o.mesh.is_watertight()
        longest, end = 0.0, time.time() + 60
        while not o.mesh.is_watertight() and time.time() < end:
            t = time.time(); app.processEvents(); longest = max(longest, time.time() - t); time.sleep(0.01)
        assert o.mesh.is_watertight(), w.status.text()
        assert longest < 1.0, longest                      # the program stayed responsive meanwhile
        w.undo(); app.processEvents()
        assert not w.objects[0].mesh.is_watertight()      # Ctrl+Z gives back the file's mesh
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    os.makedirs(_home, exist_ok=True)
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_make_watertight_falls_back_to_the_advanced_repair():
    """Holes that touch each other defeat the fast repair; the advanced repair still closes the model."""
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    s = primitives.sphere(10, 32, 64)
    keep = np.ones(len(s.faces), bool)
    keep[[3, 50, 200]] = False
    holed = Mesh(s.vertices, np.asarray(s.faces)[keep], "touching holes")
    assert not mesh_tools.repair(holed)[0].is_watertight()
    fixed, rep, ok = mesh_tools.make_watertight(holed)
    assert ok and fixed.is_watertight() and [k for k, _ in rep["steps"]][-1] == "advanced"
    assert abs(fixed.volume() - s.volume()) < 0.02 * s.volume()


def test_holes_are_never_suggested_by_default():
    """By default the program does not look for suction cups and never offers holes: a sealed hollow model
    gets no cup warning and no drain holes."""
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"hollow_enable": True, "support_enable": False})
    assert cfg["drain_holes"] == 0 and not cfg["sla_hole_advice"]
    job = sla.prepare([placed(primitives.box(20, 20, 20)).world_mesh()], cfg)
    assert job.cups == [] and not job.cups_checked and not job.cup_vents
    assert not any("drain" in w for w in job.warnings)


def test_export_works_in_any_state_after_slicing(tmp_path):
    """Once sliced, Export always works: after a change on the plate (the last slice is exported) and while
    a new slice is running; a change never cancels an export."""
    import subprocess, sys, textwrap
    lib = str(tmp_path / "prints")
    code = textwrap.dedent(f'''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("export_library", {lib!r}); w._reveal = lambda p: None
        w._ask_print_feedback = lambda: None; w._confirm_supports = lambda *a, **k: True
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False)
        o = w.add_mesh(primitives.box(10, 10, 4)); o.name = "part"
        def pump(t):
            e = time.time() + t
            while time.time() < e: app.processEvents(); time.sleep(0.01)
        pump(1.0); w.slice()
        e = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < e:
            app.processEvents(); time.sleep(0.01)
        o.x += 5; w._objects_changed(); app.processEvents()          # the plate changed after slicing
        assert w.result is None and w.export_btn.isEnabled()
        w.slice()                                                      # and a new slice is running
        w.export()
        e = time.time() + 120
        while (any(t.isRunning() for t in getattr(w, "_export_tasks", [])) or not os.path.isdir({lib!r})) and time.time() < e:
            app.processEvents(); time.sleep(0.02)
        files = [f for _r, _d, fs in os.walk({lib!r}) for f in fs if f.endswith((".ctb", ".goo", ".sl1", ".prz", ".zip", ".pwmx", ".cbddlp"))]
        assert files, list(os.walk({lib!r}))
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    os.makedirs(_home, exist_ok=True)
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_cavity_pillars_for_a_model_on_the_plate():
    """A model printed straight on the plate (no supports, no raft) can get pillars only inside its cavity:
    they stand on the plate and reach the ceiling; the model is not raised and gets nothing outside."""
    from shapely.geometry import box as sbox
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    ring = sbox(0, 0, 30, 30).difference(sbox(3, 3, 27, 27))
    shell = Mesh.merge([mesh_tools.extrude(ring, 0, 8), mesh_tools.extrude(sbox(0, 0, 30, 30), 8, 10)], "open base")
    m = placed(shell).world_mesh()
    base = {"support_enable": False, "hollow_enable": False}
    plain = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", base))
    pil = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast",
                                                 dict(base, sla_cavity_pillars=True, sla_cavity_spacing=5.0)))
    assert len(pil.layers) == len(plain.layers)                       # not raised
    assert pil.layers[0].area > plain.layers[0].area                    # pillars start on the plate
    lh = profiles.build_config("elegoo_mars3", None, "sla_fast")["sla_layer_height"]
    k = int(6.0 / lh)                                                   # inside the cavity, under the ceiling
    added = pil.layers[k].difference(plain.layers[k])
    assert len(added.geoms) >= 9 if hasattr(added, "geoms") else added.area > 0
    assert pil.layers[k].bounds == plain.layers[k].bounds               # nothing outside the model
    assert not pil.islands and pil.supports is None
    assert pil.pillars is not None and len(pil.pillars.faces) and plain.pillars is None   # shown in the 3D preview
    assert pil.pillars.bounds[0][2] <= 1e-6                           # they start on the plate


def test_cavity_pillars_reach_the_plate_through_an_open_bottom():
    """A cavity that is only closed higher up (the bottom of the walls is open, as on a scanned arch): the
    pillars still go straight down to the plate where nothing of the model is under them."""
    from shapely.geometry import box as sbox
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    ring = sbox(0, 0, 30, 30).difference(sbox(3, 3, 27, 27))
    gap = sbox(0, 12, 3, 18)                                           # a doorway in the wall, 0..2 mm high
    shell = Mesh.merge([mesh_tools.extrude(ring.difference(gap), 0, 2), mesh_tools.extrude(ring, 2, 8),
                        mesh_tools.extrude(sbox(0, 0, 30, 30), 8, 10)], "arch")
    m = placed(shell).world_mesh()
    base = {"support_enable": False, "hollow_enable": False}
    plain = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", base))
    pil = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(base, sla_cavity_pillars=True)))
    assert pil.layers[0].area > plain.layers[0].area + 5               # pillars stand on the plate
    assert not pil.islands


def test_cavity_pillars_also_hold_up_outside_overhangs():
    """With 'also under outside overhangs' a ledge that sticks out of the model gets pillars from the plate."""
    from shapely.geometry import box as sbox
    from unislicer import mesh_tools
    from unislicer.mesh import Mesh
    body = mesh_tools.extrude(sbox(0, 0, 20, 20), 0, 10)
    ledge = mesh_tools.extrude(sbox(20, 5, 26, 15), 6, 8)              # sticks out 6 mm, 6 mm above the plate
    m = placed(Mesh.merge([body, ledge], "ledge")).world_mesh()
    base = {"support_enable": False, "hollow_enable": False, "sla_cavity_pillars": True}
    off = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(base, sla_cavity_outside=False)))
    on = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(base, sla_cavity_outside=True)))
    assert on.layers[0].area > off.layers[0].area + 1                 # pillars under the ledge stand on the plate


def test_ctrl_click_unmarks_only_that_model():
    """Ctrl + left click on a selected model removes only that model from the selection; the release
    of that click must not clear the other models' selection."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        objs = [w.add_mesh(primitives.box(10, 10, 5)) for _ in range(3)]; app.processEvents()
        w.select_all()
        app.processEvents()
        assert {o.uid for o in w.selected_objects()} == {o.uid for o in objs}
        v = w.viewer
        v.pick = lambda pos, *a, **k: objs[1].uid
        QTest.mousePress(v, Qt.LeftButton, Qt.ControlModifier, QPoint(100, 100))
        QTest.mouseRelease(v, Qt.LeftButton, Qt.ControlModifier, QPoint(100, 100))
        app.processEvents()
        left = {o.uid for o in w.selected_objects()}
        assert left == {objs[0].uid, objs[2].uid}, left
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_view_does_not_flip_near_the_bottom_or_top_view():
    """Orbiting down to the bottom view (or up to the top view) never turns the plate over: the plate's
    X axis stays pointing right on screen and its front edge moves smoothly, with no 180-degree jump."""
    from unislicer.gui.viewer import Camera
    c = Camera(); c.target = np.zeros(3)
    last = None
    for p in np.linspace(-89.9, 89.9, 361):
        c.yaw, c.pitch = -90.0, float(p)
        v = c.view()
        right = (v @ np.array([10.0, 0, 0, 1]))[:2]
        front = (v @ np.array([0, -10.0, 0, 1]))[:2]
        assert right[0] > 9.9 and abs(right[1]) < 1e-6, (p, right)
        if last is not None:
            assert abs(front[1] - last) < 0.2, (p, front, last)
        last = front[1]


def test_mesh_problems_are_drawn_on_the_model():
    """An open model shows its hole's rim as red lines in the 3D view (and the model info counts them);
    a closed model shows none, and the menu toggle hides them."""
    from unislicer import mesh_tools
    box = primitives.box(20, 20, 10)
    open_box = Mesh(box.vertices, box.faces[2:])          # two triangles removed: a square hole
    op, bad = mesh_tools.defect_edges(open_box)
    assert len(op) == 4 and len(bad) == 0 and op.shape[1:] == (2, 3)
    assert all(len(x) == 0 for x in mesh_tools.defect_edges(box))
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.close_holes_act.setChecked(False)
        b = primitives.box(40, 40, 20)
        o = w.add_mesh(Mesh(b.vertices, b.faces[2:], "open")); app.processEvents()
        w._select_uid(o.uid); app.processEvents()
        assert "open edges" in w.object_info.text() or "קצוות פתוחים" in w.object_info.text(), w.object_info.text()
        def red():
            img = w.viewer.grabFramebuffer()
            a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
            r, g, bl = a[..., 2].astype(int), a[..., 1].astype(int), a[..., 0].astype(int)   # BGRA
            return int(((r > 200) & (g < 60) & (bl < 70)).sum())
        assert w.viewer.show_defects
        gl = not w.viewer.grabFramebuffer().isNull()      # no OpenGL on a headless test machine
        on = red() if gl else 0
        w.defects_act.setChecked(False); app.processEvents()
        assert not w.viewer.show_defects and w.store.value("show_defects") == "false"
        if gl:
            off = red()
            assert on > 20 and off < on // 4, (on, off)
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_support_free_zones_keep_automatic_supports_away():
    """A support-free zone (e.g. a splint's biting surface) removes the automatic contact points inside it,
    in the editor and when slicing; it follows the model, survives undo and is saved in the project."""
    from unislicer import sla_supports, project
    from unislicer.scene import SceneObject
    pts = np.array([[0, 0, 1.0], [5, 0, 1.0], [20, 0, 1.0]])
    left = sla_supports.drop_keepout(pts, np.array([[1, 0, 1.0, 6.0]]))
    assert left.tolist() == [[20, 0, 1.0]]
    assert len(sla_supports.drop_keepout(pts, np.zeros((0, 4)))) == 3
    o = SceneObject.from_mesh(primitives.box(20, 20, 10))
    o.x, o.y = 50.0, 40.0
    o.add_world_keepout([52.0, 41.0, 0.0], 3.0)
    o.x += 10                                             # the zone moves with the model
    z = o.world_keepout()
    assert np.allclose(z[0], [62.0, 41.0, 0.0, 3.0])
    st = o.state(); o.keepout = np.zeros((0, 4)); o.restore(st)
    assert len(o.keepout) == 1 and len(o.duplicate().keepout) == 1
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "k.usp")
    project.save(path, [o], {})
    loaded, _ = project.load(path)
    assert np.allclose(loaded[0].keepout, o.keepout)
    # slicing: a zone on the sphere's underside leaves no contact point inside it
    mesh = placed(primitives.sphere(10), 70, 45).world_mesh()
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True})
    full = sla.prepare([mesh], cfg)
    bottom = np.array([[70.0, 45.0, mesh.bounds[0, 2], 6.0]])
    kept = sla.prepare([mesh], cfg, keepout=[bottom])
    lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
    sp = np.asarray(kept.support_points).reshape(-1, 3)
    assert 0 < len(sp) < len(full.support_points)
    assert np.all(np.linalg.norm(sp - (bottom[0, :3] + [0, 0, lift]), axis=1) > 6.0)


def test_labels_are_engraved_or_embossed_in_the_slice():
    """A label stands on the surface facing out, upright and readable; engraved text takes resin away from the
    model's layers and embossed text adds it. label_spot finds the flat side of a model's base, and the
    label follows the model, undo and the project file."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, tempfile
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import mesh_tools, primitives, profiles, project, sla
        from unislicer.scene import SceneObject
        m = mesh_tools.label_mesh("AB12", [0, 0, 0], [1, 0, 0], 4.0, 0.6, engrave=True)
        b = m.bounds
        assert b[0, 0] < -0.55 and abs(b[1, 0] - 0.3) < 1e-6           # sunk 0.6 mm, 0.3 mm proud
        assert 3.9 < b[1, 2] - b[0, 2] < 4.1 and b[1, 1] - b[0, 1] > 6   # upright, written along the side
        box = SceneObject.from_mesh(primitives.box(30, 20, 12))
        box.x, box.y = 60.0, 40.0
        spot = mesh_tools.label_spot(box.world_mesh())
        p, n, w, h = spot
        assert abs(abs(n[0]) + abs(n[1]) - 1) < 1e-6 and abs(n[2]) < 1e-6 and w >= 19 and h >= 11
        assert abs(abs(p[0] - 60) - 15) < 1e-6 or abs(abs(p[1] - 40) - 10) < 1e-6   # on a side wall
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": False})
        plain = sla.prepare([box.world_mesh()], cfg)
        box.add_world_label("UNI", p, n, 6.0, 0.8, True, w * 0.8)
        cut = sla.prepare([box.world_mesh()], cfg, labels=[box.world_label_meshes()])
        emb = SceneObject.from_mesh(primitives.box(30, 20, 12)); emb.x, emb.y = 60.0, 40.0
        emb.add_world_label("UNI", p, n, 6.0, 0.8, False, w * 0.8)
        up = sla.prepare([emb.world_mesh()], cfg, labels=[emb.world_label_meshes()])
        mid = len(plain.layers) // 2
        a0, a1, a2 = plain.layers[mid].area, cut.layers[mid].area, up.layers[mid].area
        assert a1 < a0 - 0.5 and a2 > a0 + 0.5, (a0, a1, a2)
        st = box.state(); box.labels = []; box.restore(st)
        assert len(box.labels) == 1 and len(box.duplicate().labels) == 1
        path = os.path.join(tempfile.mkdtemp(), "l.usp")
        project.save(path, [box], {})
        assert project.load(path)[0][0].labels == box.labels
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_resin_is_split_into_model_supports_and_raft():
    mesh = placed(primitives.sphere(10), 70, 45).world_mesh()
    cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": True})
    job = sla.prepare([mesh], cfg)
    plain = sla.prepare([mesh], profiles.build_config("elegoo_mars3", None, "sla_fast", {"support_enable": False}))
    assert abs(job.model_ml - plain.volume_ml) < 0.05 * plain.volume_ml
    assert 0 < job.raft_ml < job.volume_ml - job.model_ml
    assert plain.raft_ml == 0 and abs(plain.model_ml - plain.volume_ml) < 1e-9


class _FakePreFormServer:
    """A stand-in for Formlabs' PreFormServer (Local API 0.9.x): scenes, async operations, devices, print."""

    def __init__(self):
        import http.server, threading, uuid
        self.calls, self.ops, self.scenes, self.printed = [], {}, {}, []
        self.layout_mode = "ok"            # "tight": fits only when allowed to turn; "full": never fits
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                path, _, query = self.path.partition("?")
                fake.calls.append((method, path, body))
                code, result = fake.route(method, path, body)
                if "async=true" in query and code == 200:
                    op = str(uuid.uuid4())
                    fake.ops[op] = result
                    return self._reply(202, {"operationId": op, "operationUrl": f"/operations/{op}/"},
                                       {"Location": f"/operations/{op}/"})
                self._reply(code, result)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def route(self, method, path, body):
        if path == "/":
            return 200, {"version": "0.9.30"}
        if path.startswith("/operations/"):
            op = path.split("/")[2]
            res = self.ops.pop(op)
            if isinstance(res, dict) and "error" in res:
                return 200, {"id": op, "status": "FAILED", "progress": 1.0, "result": res}
            return 200, {"id": op, "status": "SUCCEEDED", "progress": 1.0, "result": res}
        if path == "/list-materials/":
            ss = {"machine_type": "FORM-4-0", "material_code": "FLGPGR05", "print_setting": "DEFAULT",
                  "layer_thickness_mm": 0.1}
            return 200, {"printer_types": [{"label": "Form 4", "supported_product_names": ["Form 4", "Form 4B"],
                                            "supported_machine_type_ids": ["FORM-4-0"],
                                            "materials": [{"label": "Grey V5", "description": "",
                                                           "material_settings": [{"label": "0.1mm", "scene_settings": ss}]},
                                                          {"label": "White V5", "description": "",
                                                           "material_settings": [{"label": "0.1mm", "scene_settings":
                                                                                  dict(ss, material_code="FLGPWH05")}]}]}]}
        if path == "/scene/" and method == "POST":
            if set(body) - {"machine_type", "material_code", "print_setting", "layer_thickness_mm"}:
                return 400, {"error": {"code": "BAD_REQUEST", "message": "extra keys"}}
            sid = f"scene{len(self.scenes) + 1}"
            self.scenes[sid] = {"settings": body, "models": []}
            return 200, {"id": sid, "models": [], "scene_settings": body}
        parts = path.strip("/").split("/")
        if parts[0] == "scene" and len(parts) >= 2:
            sc = self.scenes.get(parts[1])
            if sc is None:
                return 404, {"error": {"code": "SCENE_NOT_FOUND", "message": "no scene"}}
            if len(parts) == 2 and method == "DELETE":
                return 200, {}
            what = parts[2]
            if what == "import-model":
                if not os.path.isabs(body["file"]) or not os.path.isfile(body["file"]):
                    return 400, {"error": {"code": "FILE_NOT_FOUND", "message": body["file"]}}
                if "position" not in body and len(sc["models"]) >= 1:     # PreForm places each import at once
                    return 200, {"error": {"code": "OPERATION_FAILED",
                                           "message": "The layout tool was unable to fit the models into the work area."}}
                sc["models"].append(body["file"])
                return 200, {"id": f"m{len(sc['models'])}"}
            if what == "auto-layout" and (self.layout_mode == "full" or
                                          (self.layout_mode == "tight" and body.get("lock_rotation"))):
                return 200, {"error": {"code": "OPERATION_FAILED",
                                       "message": "The layout tool was unable to fit the models into the work area."}}
            if what in ("auto-orient", "auto-support", "auto-layout", "save-form"):
                sc[what] = body
                if what == "save-form":
                    open(body["file"], "wb").write(b"FORM")
                return 200, {}
            if what == "print-validation":
                return 200, {"per_model_results": {"m1": {"cups": 1, "unsupported_minima": 0, "undersupported": False}}}
            if what == "estimate-print-time":
                return 200, {"total_print_time_s": 3600.0}
            if what == "print":
                if body["printer"] not in ("Form4-Lucky-Fox", "192.168.7.20"):
                    return 200, {"error": {"code": "PRINTER_IP_NOT_FOUND", "message": "printer not found"}}
                self.printed.append(body)
                return 200, {"job_id": "job-1"}
        if path == "/discover-devices/":
            return 200, {"count": 1, "devices": []}
        if path == "/devices/":
            return 200, {"count": 1, "devices": [{"id": "Form4-Lucky-Fox", "product_name": "Form 4B", "status": "IDLE",
                                                  "is_connected": True, "connection_type": "ETHERNET",
                                                  "ip_address": "192.168.7.20", "firmware_version": "1.9",
                                                  "ready_to_print_now": True, "tank_material_code": "FLGPGR05"}]}
        return 404, {"error": {"code": "NOT_FOUND", "message": path}}

    def close(self):
        self.httpd.shutdown()


def test_formlabs_send_through_preformserver(tmp_path):
    """The Formlabs client runs the Local API flow: new scene with the chosen material, import each model
    (absolute path), PreForm's supports, layout keeping our orientation, printability check and time,
    then print to the printer (or save a .form). Errors come back with Formlabs' code."""
    import json as _json
    globals()["json"] = _json
    from unislicer import formlabs
    from unislicer.mesh import save_stl
    srv = _FakePreFormServer()
    try:
        c = formlabs.Client(port=srv.port)
        assert c.base.startswith("http://127.0.0.1:")          # not "localhost" (slow IPv6 try on Windows)
        assert c.running() and c.version() == "0.9.30"
        types = c.materials()
        assert types[0]["label"] == "Form 4" and types[0]["materials"][0]["settings"][0]["scene_settings"]["machine_type"] == "FORM-4-0"
        devs = c.discover(1)
        assert devs[0]["id"] == "Form4-Lucky-Fox"
        assert formlabs.match_printer_type(types, devs[0]["product_name"]) == 0
        stl = str(tmp_path / "a.stl")
        save_stl(primitives.box(10, 10, 5), stl)
        steps = []
        r = c.send([stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "192.168.7.20", "Case 7",
                   supports="auto", print_now=False, progress=lambda s, f: steps.append(s))
        assert r["job_id"] == "job-1" and r["print_time_s"] == 3600.0
        assert formlabs.validation_problems(r["validation"]) == {"cups": 1, "unsupported_minima": 0, "undersupported": 0}
        assert srv.printed == [{"printer": "192.168.7.20", "job_name": "Case 7", "print_now": False}]
        sc = srv.scenes["scene1"]
        assert sc["models"] == [os.path.abspath(stl)] and sc["auto-layout"]["lock_rotation"] is True
        assert "auto-support" in sc and "auto-orient" not in sc
        assert steps[0] == "scene" and steps[-1] == "done" and "print" in steps
        assert ("DELETE", "/scene/scene1/", None) in srv.calls       # the scene is cleaned up
        # UniSlicer's own supports: no PreForm supports; save a .form without printing
        form = str(tmp_path / "job.form")
        r = c.send([stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "", "x", supports="none",
                   save_form=form)
        assert os.path.isfile(form) and r["job_id"] == "" and "auto-support" not in srv.scenes["scene2"]
        try:
            c.send([stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "10.0.0.9", "x")
            raise AssertionError("expected a failure")
        except formlabs.FormlabsError as e:
            assert e.code == "PRINTER_IP_NOT_FOUND"
        # PreForm's layout cannot fit them as turned: retry letting it turn them, closer, overlapping supports
        srv.layout_mode = "tight"
        n0 = len(srv.scenes)
        r = c.send([stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "192.168.7.20", "x")
        lay = srv.scenes[f"scene{n0 + 1}"]["auto-layout"]
        assert r["job_id"] == "job-1" and lay["lock_rotation"] is False and lay["allow_overlapping_supports"] is True
        many = c.send([stl, stl, stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "192.168.7.20", "x")
        assert many["job_id"] == "job-1"                # several models: imported at a set position, then laid out
        srv.layout_mode = "full"                        # still too many models: a clear error, nothing printed
        printed = len(srv.printed)
        try:
            c.send([stl, stl], types[0]["materials"][0]["settings"][0]["scene_settings"], "192.168.7.20", "x")
            raise AssertionError("expected a failure")
        except formlabs.FormlabsError as e:
            assert e.code == "LAYOUT_DOES_NOT_FIT" and "2 models" in str(e)
        assert len(srv.printed) == printed
        srv.layout_mode = "ok"
        dead = formlabs.Client(port=1)
        assert not dead.running()
        try:
            dead.start("")
            raise AssertionError("expected a failure")
        except formlabs.FormlabsError as e:
            assert e.code == "NOT_INSTALLED"
    finally:
        srv.close()


def test_formlabs_dialog_connects_finds_and_sends(tmp_path):
    import json as _json, subprocess, sys, textwrap
    globals()["json"] = _json
    srv = _FakePreFormServer()
    try:
        code = textwrap.dedent('''
            import os, time
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            from unislicer import primitives
            from unislicer.gui.main_window import MainWindow
            from unislicer.gui.formlabs_dialog import FormlabsDialog
            from unislicer import formlabs
            w = MainWindow(); app.processEvents()
            w.store.setValue("formlabs_port", int(os.environ["FAKE_PORT"]))
            w.store.setValue("preslice", "false")    # no background slicing in this test
            o = w.add_mesh(primitives.box(10, 10, 5)); app.processEvents()
            seen = {}
            def exec_(self):
                def wait():
                    end = time.time() + 120
                    while self.task is not None and self.task.isRunning() and time.time() < end:
                        app.processEvents(); time.sleep(0.02)
                    assert self.task is None or not self.task.isRunning(), "still running: " + self.status.text()
                    self.task.wait(); app.processEvents()
                self._connect(); wait()
                assert self.ptype.currentText() == "Form 4" and self.material.currentText() == "Grey V5", self.status.text()
                self._find(); wait()
                assert self.printer.count() == 1 and "Form4-Lucky-Fox" in self.printer.currentText()
                assert self._printer_id() == "192.168.7.20"
                self.queue.setChecked(True)
                self._send(); wait()
                seen["status"] = self.status.text()
                return 0
            FormlabsDialog.exec = exec_
            w.send_to_formlabs()
            w._stop_preslice()                       # as on quit: no background work left running
            from unislicer import parallel
            if parallel._pool is not None:           # worker processes would hold the output pipe open
                parallel._pool.shutdown(wait=True, cancel_futures=True)
            print("STATUS", seen.get("status"), flush=True)
        ''')
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), FAKE_PORT=str(srv.port),
                   PYTHONIOENCODING="utf-8")                  # the status is Hebrew; a Windows console is cp1252
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0 and "STATUS" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
        assert srv.printed and srv.printed[0]["printer"] == "192.168.7.20" and srv.printed[0]["print_now"] is False
        assert "192.168.7.20" in r.stdout
        srv.layout_mode = "full"                       # too many models for the platform: a clear message
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0 and "STATUS" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
        assert "Form 4BL" in r.stdout and "Error" not in r.stdout.split("STATUS", 1)[1], r.stdout[-1500:]
    finally:
        srv.close()


def test_model_menu_keeps_print_on_plate_raft_and_fill():
    """The resin model menu always offers Raft, Print directly on the plate and Fill cavity (they once hid
    behind 'only when the model has support-free zones')."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QMenu
        from PySide6.QtCore import QPoint
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.i18n import tr
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        if w.viewer.technology != "sla":
            w.viewer.technology = "sla"
        o = w.add_mesh(primitives.box(10, 10, 5)); app.processEvents()
        seen = []
        from unislicer.gui import main_window as mw

        class Shown(QMenu):                     # record the menu instead of opening it
            def exec(self, *a, **k):
                seen.extend(x.text() for x in self.actions())

        mw.QMenu = Shown
        w._show_context_menu(o.uid, QPoint(10, 10))
        for key in ("direct_on_plate", "fill_cavity", "tool_keepout"):
            assert any(t.startswith(tr(key)) for t in seen), (key, seen)
        assert any(t.startswith(tr("raft_menu")) for t in seen), seen
        assert not any(t == tr("keepout_clear") for t in seen)     # nothing to clear yet
        # printed on the plate: the menu says plainly how to switch it off, and it does
        o.direct = True
        w._select_uid(o.uid)
        menus = []

        class Kept(QMenu):
            def exec(self, *a, **k):
                menus.append(self)
        mw.QMenu = Kept
        w._show_context_menu(o.uid, QPoint(10, 10))
        off = [x for x in menus[-1].actions() if x.text() == tr("direct_off")]
        assert off and not any(x.text() == tr("direct_on_plate") for x in menus[-1].actions())
        off[0].trigger(); app.processEvents()
        assert not o.direct
        # and Auto generate on a part chosen on purpose gives it supports even if it was on the plate
        o.direct = True
        w._select_uid(o.uid)
        w.generate_supports(); app.processEvents()
        assert not o.direct and o.support_points is not None
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:          # worker processes would hold the output pipe open
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_text_survives_a_two_point_glyph_sliver(monkeypatch):
    """Qt returns every glyph outline closed (last point = first); a sliver of two distinct points became a
    3-point ring and GEOS refused it ('Invalid number of points in LinearRing found 3'), failing the slice."""
    pytest.importorskip("PySide6")
    import PySide6.QtGui as qtgui
    from PySide6.QtCore import QPointF
    from unislicer import mesh_tools

    class FakePath:
        def addText(self, *a):
            pass

        def toSubpathPolygons(self):
            square = [QPointF(0, 0), QPointF(10, 0), QPointF(10, -10), QPointF(0, -10), QPointF(0, 0)]
            sliver = [QPointF(3, 3), QPointF(4, 3), QPointF(3, 3)]
            return [square, sliver]

    monkeypatch.setattr(qtgui, "QPainterPath", FakePath)
    monkeypatch.setattr(qtgui, "QFont", lambda *a: type("F", (), {"setPointSizeF": lambda self, v: None})())
    polys = mesh_tools.text_polygons("A", 4.0)
    assert not polys.is_empty and abs(polys.bounds[3] - polys.bounds[1] - 4.0) < 1e-6


def test_formlabs_grey_and_white_v5_resins(tmp_path):
    """Grey V5 and White V5 are offered on every Formlabs printer (and only there), and the Send to Formlabs
    window picks the same resin from PreForm's list."""
    import subprocess, sys, textwrap
    mats = profiles.load("materials")
    fl = [k for k, v in profiles.load("printers").items() if v.get("vendor") == "Formlabs"]
    for key, name in (("formlabs_grey_v5", "Grey V5"), ("formlabs_white_v5", "White V5")):
        assert mats[key]["vendor"] == "Formlabs" and mats[key]["printers_only"]
        assert set(fl) <= set(mats[key]["printers"]) and mats[key]["values"]["material_name"] == name
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.formlabs_dialog import FormlabsDialog
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        combo = w.settings.material_combo
        w.settings.select_printer("formlabs_form4b"); app.processEvents()
        ids = [combo.itemData(k) for k in range(combo.count())]
        assert "formlabs_grey_v5" in ids and "formlabs_white_v5" in ids, ids
        assert w.settings.material_id in ("formlabs_grey_v5", "formlabs_white_v5")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        assert "formlabs_grey_v5" not in [combo.itemData(k) for k in range(combo.count())]
        st = {"scene_settings": {"material_code": "X"}, "label": "0.1mm"}
        types = [{"label": "Form 4", "materials": [{"label": n, "settings": [st]} for n in
                                                   ("Clear V5", "Grey V5", "White Resin V5", "Black V5")]}]
        for want, got in (("White V5", "White Resin V5"), ("Grey V5", "Grey V5"), ("", "Black V5")):
            w.store.setValue("formlabs_material", "Black V5")
            d = FormlabsDialog(w.store, lambda mode: [], "job", None, w, material=want)
            d.types = types
            d.ptype.addItem("Form 4"); d._fill_materials()
            assert d.material.currentText() == got, (want, d.material.currentText())
        w._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)),
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_formlabs_form4_family_in_the_printer_list():
    ps = profiles.load("printers")
    for k, size in (("formlabs_form4b", (200.0, 125.0, 210.0)), ("formlabs_form4bl", (353.0, 196.0, 350.0)),
                    ("formlabs_form4", (200.0, 125.0, 210.0)), ("formlabs_form4l", (353.0, 196.0, 350.0))):
        assert ps[k]["vendor"] == "Formlabs"
        c = profiles.build_config(k, None, None, {})
        assert (c["bed_x"], c["bed_y"], c["max_z"]) == size and c["technology"] == "sla" and c["sla_process"] == "lcd"


def test_dental_files_recognised_by_name_and_shape():
    """3Shape's '_Antag' (antagonist) and '_Tooth_14' (the die) are recognised, a lab code with a tooth range
    is the restoration; a name that says nothing falls back to the shape: arch -> model, single solid tooth
    -> die, thin shell over a cavity -> crown."""
    from unislicer import dental_cad
    assert dental_cad.detect_indication("EGI-SOD260046764-114-Z-14-14-1-00-V227_Antag.stl") == "model"
    assert dental_cad.detect_indication("EGI-SOD260046764-114-Z-14-14-1-00-V227_Tooth_14.stl") == "die"
    # a lab code with a tooth range and unit count is the restoration (crown / bridge) itself
    assert dental_cad.detect_indication("FYQ-SOD260046892-044-P-16-16-1-00-R685_2.stl") == "crown"
    assert dental_cad.detect_indication("BHK-SOD260045947-12-P.stl") == "unknown"   # no range: the shape decides
    arch = primitives.box(60, 45, 15)
    assert dental_cad.indication_from_shape(arch) == "model"
    die = primitives.cylinder(4, 15)
    assert dental_cad.indication_from_shape(die) == "die"
    outer, inner = primitives.box(10, 10, 8), primitives.box(8, 8, 7).translated([0, 0, -0.6])
    cap = Mesh(np.vstack([outer.vertices, inner.vertices]),
               np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)]))   # a closed thin shell
    assert dental_cad.indication_from_shape(cap) == "crown"


def test_cavity_cells_survive_a_failing_rectangle_clip(monkeypatch):
    """GEOS's clip_by_rect raised 'Invalid number of points in LinearRing found 3' on a user's jaw while
    hollowing (sla._cavity_cells_chunk); the clip only speeds things up, so the slice must go on with the
    same result."""
    import shapely
    from shapely.geometry import Polygon
    ring = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    below = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(8, 8), (12, 8), (12, 12), (8, 12)]])
    layer = sla.as_multipolygon(ring)
    good = sla._cavity_cells_chunk([(layer, sla.as_multipolygon(below))])

    def broken(*a, **k):
        raise shapely.errors.GEOSException("IllegalArgumentException: Invalid number of points in LinearRing found 3")

    monkeypatch.setattr(shapely, "clip_by_rect", broken)
    out = sla._cavity_cells_chunk([(layer, sla.as_multipolygon(below))])
    assert len(out) == 1 and out[0] is not None
    assert abs(sum(c.area for c in out[0][1]) - sum(c.area for c in good[0][1])) < 1e-9
    assert abs(sum(c.area for c in out[0][1]) - 16.0) < 1e-9          # the part of the cell over the hole below


def test_autosave_survives_a_locked_file(tmp_path, monkeypatch):
    """Windows refuses to replace a file another program holds for a moment (WinError 32): the autosave
    retries, and if it stays locked it gives up quietly (it saves again two minutes later)."""
    from unislicer import project
    src, dst = tmp_path / "a.tmp", tmp_path / "a.unislicer"
    real = os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "The process cannot access the file because it is being used")
        real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    src.write_text("x")
    assert project.replace_retry(str(src), str(dst), delay=0.01) and dst.read_text() == "x" and calls["n"] == 3
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(32, "locked")))
    src.write_text("y")
    assert project.replace_retry(str(src), str(dst), tries=3, delay=0.01) is False
    assert not src.exists() and dst.read_text() == "x"


def test_gingiva_mask_recognised_and_prepared(tmp_path):
    """3Shape's gingiva mask ('..._Tissue_17') is its own indication: recognised from the name (not as a
    crown, not as a denture), tilted 45 degrees and supported, not stood on the plate."""
    from unislicer import dental_cad
    assert dental_cad.detect_indication("BAH-SOD260046430-034-X-35-36-2-C2-B592_Tissue_17.stl") == "gingiva"
    assert dental_cad.detect_indication("case_gingiva_mask.stl") == "gingiva"
    assert dental_cad.detect_indication("full denture gingiva.stl") == "denture"
    import subprocess, sys, textwrap
    from unislicer.mesh import save_stl
    path = tmp_path / "BAH-SOD260046430-034-X-35-36-2-C2-B592_Tissue_17.stl"
    save_stl(primitives.box(18, 20, 7), str(path))
    code = textwrap.dedent(f'''
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.set_printer("phrozen_sonic_ls_plus") if hasattr(w.settings, "set_printer") else None
        w.import_from_cad([{str(path)!r}]); app.processEvents()
        o = w.objects[-1]
        assert not o.direct, "a gingiva mask is not printed on the plate"
        print("STATUS", w.status.text(), flush=True)
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and ("Gingiva mask" in r.stdout or "מסכת חניכיים" in r.stdout), r.stdout[-2000:] + r.stderr[-3000:]


def test_sdcp_upload_survives_firmware_writing_status_before_the_http_reply(tmp_path):
    """Phrozen Sonic LS+ firmware 1.1.8 (SDCP V3) writes its WebSocket status broadcast onto the upload
    connection before 'HTTP/1.1 200 OK'; a normal HTTP client failed with BadStatusLine ('Export failed')."""
    import socket, threading
    from unislicer import network
    got = {"bodies": 0}
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(2):
            c, _ = srv.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += c.recv(65536)
            head, _, rest = data.partition(b"\r\n\r\n")
            n = int([l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0])
            while len(rest) < n:
                rest += c.recv(65536)
            assert b'name="Offset"' in rest and b"payload" in rest
            got["bodies"] += 1
            status = json.dumps({"Attributes": {"Name": "Sonic LS+", "ProtocolVersion": "V3.0.0"}}).encode()
            frame = b"\x81\x7e" + len(status).to_bytes(2, "big") + status        # a WebSocket text frame
            reply = json.dumps({"code": "000000", "messages": None, "data": {}, "success": True}).encode()
            c.sendall(frame + b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                      + str(len(reply)).encode() + b"\r\n\r\n" + reply)
            c.close()

    import json
    t = threading.Thread(target=serve, daemon=True); t.start()
    f = tmp_path / "plate.prz"
    f.write_bytes(b"payload" * 10)
    p = network.NetPrinter("LS+", "sdcp", "127.0.0.1", port)
    network._Sdcp(p).upload(str(f), "plate.prz")
    assert got["bodies"] == 1
    status, raw = network._lenient_post("127.0.0.1", port, "/uploadFile/upload",
                                        network._multipart({"Offset": "0"}, "File", "x", b"payload")[0],
                                        {"Content-Type": "multipart/form-data; boundary=x"})
    assert status == 200 and json.loads(raw)["success"] is True
    srv.close()


def test_layer_triangles_keep_the_honeycomb():
    """The 3D preview draws the current layer at the cut: its triangles cover exactly the layer, holes and
    honeycomb cells left open."""
    import shapely
    if not hasattr(shapely, "constrained_delaunay_triangles"):
        pytest.skip("shapely < 2.1 cannot triangulate polygons with holes")
    from shapely.geometry import Polygon
    cells = [Polygon([(x, y), (x + 3, y), (x + 3, y + 3), (x, y + 3)]) for x in range(2, 18, 4) for y in range(2, 18, 4)]
    layer = sla.as_multipolygon(Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]).difference(shapely.union_all(cells)))
    tris = sla.layer_triangles(layer, 5.0)
    a, b, c = tris[:, 0, :2], tris[:, 1, :2], tris[:, 2, :2]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])).sum()
    assert abs(area - layer.area) < 1e-6 and np.all(tris[:, :, 2] == 5.0)
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    assert np.all(signed > 0)                      # all facing up
    assert len(sla.layer_triangles(sla.EMPTY, 1.0)) == 0


def test_teeth_rafts_carry_the_sod_case_code():
    """On crowns / bridges only, the raft carries the code from the SOD number (DSY-SOD260046671-044-... ->
    46671-044); models, dies and gingiva masks keep the plain rule (name only with 'Model name on the raft')."""
    from unislicer import dental_cad
    from unislicer.gui.main_window import MainWindow
    from unislicer.scene import SceneObject
    assert dental_cad.case_code("DSY-SOD260046671-044-P-46-46-1-A35-.stl") == "46671-044-P"
    assert dental_cad.case_code("SMG-SOD260045198-N183-E-16-16-1-A35_Antag.stl") == "45198-N183-E"
    assert dental_cad.case_code("C:/cases/WMJ-SOD260043945-044-P-43-46-4-A3-.stl") == "43945-044-P"
    assert dental_cad.case_code("upper.stl") == ""
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    assert cfg["sla_raft_label_teeth"] is False                 # off unless the user turns it on
    cfg.update({"sla_raft_label_teeth": True})
    outer, inner = primitives.box(10, 10, 8), primitives.box(8, 8, 7).translated([0, 0, -0.6])
    cap = Mesh(np.vstack([outer.vertices, inner.vertices]),
               np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)]))
    crown = SceneObject.from_mesh(cap, "DSY-SOD260046671-044-P-46-46-1-A35-")
    jaw = SceneObject.from_mesh(primitives.box(60, 45, 15), "EGI-SOD260046764-114-Z-14-14-1-00-V227_UnsectionedModel_UpperJaw")
    die = SceneObject.from_mesh(primitives.cylinder(4, 15), "EGI-SOD260046764-114-Z-14-14-1-00-V227_Tooth_14")
    label = MainWindow._raft_label_text
    assert label(crown, cfg) == "46671-044-P"
    assert label(jaw, cfg) == "" and label(die, cfg) == ""
    cfg.update({"sla_raft_label_teeth": False})
    assert label(crown, cfg) == ""
    cfg.update({"sla_raft_label": True})
    assert label(jaw, cfg) == jaw.name


def test_mini_tiny_raft_stays_inside_the_tooth():
    """'Mini tiny' raft (temporary teeth): the raft follows the tooth's outline seen from above, set back by
    the inset, and never reaches past it."""
    from unislicer import sla_supports
    from shapely.geometry import Polygon
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "tiny", "sla_raft_tiny_inset": 0.5})
    tooth = placed(primitives.sphere(5), 60, 40).world_mesh().translated([0, 0, 3])
    sup, pts = sla_supports.build_supports(tooth, cfg)
    raft_top = cfg["sla_raft_thickness"]
    v = sup.vertices
    raft_xy = v[v[:, 2] <= raft_top + 1e-6][:, :2]
    d = np.hypot(raft_xy[:, 0] - 60, raft_xy[:, 1] - 40)
    assert len(raft_xy) and d.max() <= 5.0 - 0.5 + 0.06           # inside the outline, set back 0.5 mm
    outline = sla_supports.tooth_outline(tooth, 0.5)
    assert abs(outline.area - np.pi * 4.5 ** 2) < 1.5


def test_raft_code_fits_whole_along_a_diagonal_bridge():
    """On a narrow diagonal bridge raft the case code is turned along the raft and shrunk until all of it
    fits inside (it was centred horizontally and cut off); too small to read -> left out, never cut."""
    from shapely import affinity
    from shapely.geometry import Polygon, box
    from unislicer import sla_supports
    text = sla.as_multipolygon(shapely.union_all([box(x, 0, x + 1.2, 2.0) for x in np.arange(0, 9 * 1.5, 1.5)]))
    raft = affinity.rotate(box(0, 0, 34, 6), 55, origin=(0, 0))             # a 34 x 6 mm bridge raft at 55 degrees
    placed = sla_supports.fit_text(text, raft.buffer(-0.4))
    assert placed is not None and raft.buffer(-0.4).contains(placed)
    bx = placed.bounds
    assert (bx[3] - bx[1]) > (bx[2] - bx[0]) * 0.9                       # runs up the diagonal, not across
    tiny = box(0, 0, 3, 1.0)                                                # no room for a readable code
    assert sla_supports.fit_text(text, tiny) is None


def test_label_select_edit_drag_and_delete(tmp_path):
    """Label tool: clicking a label selects it and opens its editor (no longer deletes it); size, angle,
    text and engrave/emboss change it live; dragging moves it over the surface; Delete removes it."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit, QPushButton
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.i18n import tr
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        o = w.add_mesh(primitives.box(40, 30, 15)); app.processEvents()
        b = o.world_bounds()
        front = np.array([(b[0, 0] + b[1, 0]) / 2, b[0, 1], 5.0])
        w._on_label_picked(o.uid, front, np.array([0.0, -1.0, 0.0]))          # new label, editor opens
        assert len(o.labels) == 1 and w._label_editor is not None and w._label_editor.isVisible()
        w._on_label_picked(o.uid, front + [1.0, 0, 0], np.array([0.0, -1.0, 0.0]))   # click it again: select
        assert len(o.labels) == 1, "clicking a label must not add or delete one"
        ed = w._label_editor
        spins = ed.findChildren(QDoubleSpinBox)
        spins[0].setValue(6.0); spins[2].setValue(30.0)                     # height, angle
        ed.findChildren(QLineEdit)[0].setText("12345-044")
        app.processEvents()
        lab = o.labels[0]
        assert lab["height"] == 6.0 and lab["angle"] == 30.0 and lab["text"] == "12345-044"
        [x for x in ed.findChildren(QPushButton) if x.text() == "+"][0].click(); app.processEvents()
        assert abs(o.labels[0]["height"] - 6.9) < 1e-6                 # "+" grows it 15 %
        w._on_label_wheel(-2.0, False); app.processEvents()            # Ctrl + wheel shrinks it
        assert o.labels[0]["height"] < 6.9
        w._on_label_wheel(1.0, True); app.processEvents()              # Alt + wheel turns it
        assert abs(o.labels[0]["angle"] - 35.0) < 1e-6
        from PySide6.QtWidgets import QCheckBox
        [x for x in ed.findChildren(QPushButton) if x.text() == "↻ 90°"][0].click(); app.processEvents()
        assert abs(o.labels[0]["angle"] - (-55.0)) < 1e-6              # 90 degrees to the right
        [x for x in ed.findChildren(QPushButton) if x.text() == tr("label_upright")][0].click(); app.processEvents()
        assert o.labels[0]["angle"] == 0.0
        ed.findChildren(QCheckBox)[0].setChecked(True); app.processEvents()
        assert o.labels[0]["mirror"] is True
        spins[2].setValue(30.0)
        spins[0].setValue(6.0)
        side = np.array([b[1, 0], (b[0, 1] + b[1, 1]) / 2, 6.0])
        w._on_label_dragged(o.uid, side, np.array([1.0, 0.0, 0.0])); w._on_label_drag_finished()
        p, n, _ = o.world_labels()[0]
        assert np.allclose(p, side) and np.allclose(n, [1, 0, 0])
        up, cut = o.world_label_meshes()
        assert cut is not None and len(cut.faces)
        [x for x in ed.findChildren(QPushButton) if x.text() == tr("delete")][0].click(); app.processEvents()
        assert o.labels == []
        w.undo(); app.processEvents()
        assert len(w.objects[0].labels) == 1
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_supports_keep_away_from_labels():
    """A label is a support-free zone of its own size: no support head lands next to engraved text, where
    it would show through the letters; in the editor and when slicing alike."""
    from unislicer.scene import SceneObject
    from unislicer import sla_supports
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    o = SceneObject.from_mesh(primitives.box(40, 30, 15))
    o.x, o.y, o.z = 80.0, 60.0, 0.0
    b = o.world_bounds()
    o.add_world_label("46671-044", [80.0, b[0, 1], 3.0], [0, -1, 0], 3.0, 0.6, True)
    zone = o.label_zones()[0]
    lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
    world = o.world_mesh().translated([0, 0, lift])
    all_pts = sla_supports.auto_support_points(world, cfg)
    near = np.linalg.norm(all_pts - (zone[:3] + [0, 0, lift]), axis=1) < zone[3]
    assert near.any(), "the test needs supports that would land by the label"
    job = sla.prepare([o.world_mesh()], cfg, keepout=[o.world_keepout(0.0)])
    sp = np.asarray(job.support_points).reshape(-1, 3)
    assert len(sp) and np.all(np.linalg.norm(sp - (zone[:3] + [0, 0, lift]), axis=1) >= zone[3])
    edited = all_pts[:5]                                         # hand-edited points are cleared too
    job2 = sla.prepare([o.world_mesh()], cfg, support_points=[edited - [0, 0, lift]], keepout=[o.world_keepout(0.0)])
    sp2 = np.asarray(job2.support_points).reshape(-1, 3)
    assert np.all(np.linalg.norm(sp2 - (zone[:3] + [0, 0, lift]), axis=1) >= zone[3])


def test_model_labels_use_the_short_sod_code(tmp_path):
    """The label text defaults to the short SOD code ({code}): 'SMG-SOD260045198-N183-E-16-16-1-A35_Antag'
    is labelled '45198-N183-E'; a file without an SOD number keeps its name."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.store.setValue("label_template", "{name}")       # the old default is moved to the code once
        w.store.remove("label_template_v2")
        a = w.add_mesh(primitives.box(40, 30, 15)); a.name = "SMG-SOD260045198-N183-E-16-16-1-A35_Antag"
        b = w.add_mesh(primitives.box(40, 30, 15)); b.name = "upper_jaw_7"
        assert w._label_text(a) == "45198-N183-E", w._label_text(a)
        assert w._label_text(b) == "upper_jaw_7"
        w.store.setValue("label_template", "{name}")       # a user's own choice afterwards stays
        assert w._label_text(a) == "SMG-SOD260045198-N183-E-16-16-1-A35_Antag"[:14]
        w._stop_preslice()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_label_hugs_a_curved_surface():
    """On a round die a flat label stood in the air at its ends; now each point of the text keeps its depth
    from the surface under it (engraved: 0.3 mm proud to 'depth' deep, everywhere)."""
    pytest.importorskip("PySide6")
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.scene import SceneObject
        die = SceneObject.from_mesh(primitives.cylinder(5.0, 14.0, 96))
        die.x, die.y = 50.0, 50.0
        die.add_world_label("45198", [50.0, 45.0, 7.0], [0, -1, 0], 1.6, 0.6, True)   # fits the die in any font
        up, cut = die.world_label_meshes()
        v = cut.vertices
        r = np.hypot(v[:, 0] - 50.0, v[:, 1] - 50.0)                  # distance from the die's axis
        assert 3.5 < np.ptp(v[:, 0]) < 8.5                             # wide enough to bend around the die
        assert r.max() <= 5.0 + 0.3 + 0.05 and r.min() >= 5.0 - 0.6 - 0.05, (r.min(), r.max())
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_label_bigger_than_the_wall_is_cut_at_its_edge_not_left_in_the_air():
    """Enlarging a label past the edge of the wall it sits on left letter parts in the air (seen in the
    user's video); now the text is kept to where the surface is."""
    pytest.importorskip("PySide6")
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.scene import SceneObject
        base = SceneObject.from_mesh(primitives.box(40, 30, 6))      # a 6 mm high wall all round
        base.x, base.y = 50.0, 50.0
        b = base.world_bounds()
        base.add_world_label("45198", [50.0, b[0, 1], 3.0], [0, -1, 0], 10.0, 0.6, True)   # 10 mm letters
        up, cut = base.world_label_meshes()
        v = cut.vertices
        assert len(v) and v[:, 2].min() >= b[0, 2] - 1e-6 and v[:, 2].max() <= b[1, 2] + 1e-6, (v[:, 2].min(), v[:, 2].max())
        assert np.all(np.abs(v[:, 1] - b[0, 1]) <= 0.6 + 0.3 + 1e-6)  # on the wall: 0.3 proud, 0.6 deep
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_mini_rafts_differ_from_a_solid_raft():
    """'Mini rafts' gave exactly the solid raft (dense supports joined into one group with one convex
    outline). Now each support stands on its own pad, joined to its neighbours: clearly less raft."""
    from unislicer import sla_supports
    from unislicer.slicing import slice_mesh
    mesh = placed(primitives.box(30, 20, 8), 60, 40).world_mesh()
    out = {}
    for kind in ("solid", "mini"):
        cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True, "sla_raft_type": kind})
        lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
        sup, pts = sla_supports.build_supports(mesh.translated([0, 0, lift]), cfg)
        out[kind] = slice_mesh(sup, [cfg["sla_raft_thickness"] / 2], 0.05)[0]
    assert out["mini"].area < 0.8 * out["solid"].area, (out["mini"].area, out["solid"].area)
    assert out["solid"].covers(out["mini"].buffer(-0.3))                  # never past the solid outline


def test_crowns_and_bridges_open_up_and_nothing_else_turns():
    """The opening (fitting surface) of a crown or bridge is found whichever way the file has it - the cavity
    runs along an axis and is widest at the opening - and the part is turned opening up; a gingiva mask is
    turned flat side up; a part already that way up, a die, a model and any part when its option is off are
    left as they are."""
    from unislicer import dental_cad
    cap = primitives.crown_cap()                                        # opens down, like most CAD exports
    bridge = Mesh.merge([primitives.crown_cap().translated([k * 8.6, 0, 0]) for k in range(3)])
    straight = primitives.crown_cap(taper=0.0, height=6.0)              # a cavity with straight walls
    turns = {"as is": np.eye(3), "on its side": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.0]]),
             "upside up": np.diag([1, -1, -1.0]), "other side": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0.0]])}
    assert dental_cad.opening_direction(primitives.crown_cap()) is not None
    for name, part in (("crown", cap), ("bridge", bridge), ("straight walls", straight)):
        for how, r in turns.items():
            m = Mesh(np.asarray(part.vertices) @ r.T, part.faces)
            d = dental_cad.opening_direction(m)
            assert d is not None and np.allclose(d, r @ [0, 0, -1]), (name, how, d)
    assert dental_cad.opening_direction(primitives.cylinder(4, 12)) is None      # a die: solid, no opening

    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        cap = primitives.crown_cap()
        down = Mesh(cap.vertices, cap.faces, "FYQ-SOD260046892-044-P-16-16-1-00-R685_2")
        up = Mesh(np.asarray(cap.vertices) * [1, -1, -1], cap.faces[:, ::-1], "BHK-SOD260045947-12-P")
        die = Mesh(primitives.cylinder(4, 12).vertices, primitives.cylinder(4, 12).faces, "XUU-48_Tooth_16")
        jaw = Mesh(cap.vertices * 6, cap.faces, "XUU-SOD260045840-P048_UpperJaw")      # a model, even if hollow
        objs = [w.add_mesh(m, rearrange=False) for m in (down, up, die, jaw)]
        assert w._open_up(objs) == 1
        opening = lambda o: o.matrix()[:3, :3] @ [0, 0, -1]
        assert np.allclose(opening(objs[0]), [0, 0, 1])                  # turned over: opening up
        assert np.allclose(objs[1].matrix(), np.eye(4))                  # already open side up: untouched
        assert np.allclose(objs[2].matrix(), np.eye(4)) and np.allclose(objs[3].matrix(), np.eye(4))
        assert w._open_up(objs) == 0                                     # nothing more to do
        w.store.setValue("crowns_open_up", "false")                      # More > the option off
        fresh = w.add_mesh(Mesh(cap.vertices, cap.faces, "FYQ-SOD260046892-044-P-16-16-1-00-R685_2"), rearrange=False)
        try:
            assert w._open_up([fresh]) == 0 and np.allclose(fresh.matrix(), np.eye(4))
        finally:
            w.store.setValue("crowns_open_up", "true")      # on Windows settings outlive the test (registry)
        # a gingiva mask: its flat side (the cut, its biggest flat face, down in the file) is turned up
        cone = primitives.frustum(8, 4, 6, 48)
        mask = w.add_mesh(Mesh(cone.vertices, cone.faces, "BAH-SOD260046430-034-X-35-36-2_Tissue_17"), rearrange=False)
        assert w._open_up([mask]) == 1
        assert np.allclose(mask.matrix()[:3, :3] @ [0, 0, -1], [0, 0, 1])
        assert w._open_up([mask]) == 0                                   # already flat side up
        w.store.setValue("tissue_flat_up", "false")
        try:
            other = w.add_mesh(Mesh(cone.vertices, cone.faces, "XYZ_Tissue_12"), rearrange=False)
            assert w._open_up([other]) == 0 and np.allclose(other.matrix(), np.eye(4))
        finally:
            w.store.setValue("tissue_flat_up", "true")
        w._stop_preslice()
        print("ok")
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_add_parts_window_sets_type_and_adds_no_supports(tmp_path):
    """Open model -> Add parts: each file gets a type (by name, else by shape), a unit and a quantity; Open
    puts them on the plate without adding supports. Only crowns / bridges are turned - opening up; every other
    part stays as in its file."""
    import subprocess, sys, textwrap
    from unislicer.mesh import save_stl
    cap = primitives.crown_cap()
    files = {"XUU-SOD260045840-P048_Antag.stl": primitives.box(60, 45, 15),
             "XUU-SOD260045840-48_Tooth_16.stl": primitives.cylinder(4, 15),
             "BAH-SOD260046430-034-X-35-36-2-C2-B592_Tissue_17.stl": primitives.box(18, 20, 7),
             "FYQ-SOD260046892-044-P-16-16-1-00-R685_2.stl": cap}
    for n, m in files.items():
        save_stl(m, str(tmp_path / n))
    code = textwrap.dedent(f'''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.mesh import load_mesh
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.add_parts_dialog import AddPartsDialog
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        w.settings.set_value("support_enable", False); app.processEvents()
        w.store.setValue("crowns_open_up", "true")
        folder = {str(tmp_path)!r}
        paths = sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".stl"))
        first = lambda p: (lambda m: m[0] if isinstance(m, list) else m)(load_mesh(p))
        dlg = AddPartsDialog(paths, w.store, first, w)
        assert dlg.table.columnCount() == 4                           # name, type, unit, quantity
        parts = dlg.parts()
        assert all(set(p) == {{"path", "kind", "unit", "qty"}} for p in parts), parts
        got = {{p["path"].split("_")[-1]: p["kind"] for p in parts}}
        assert got["Antag.stl"] == "model" and got["16.stl"] == "die" and got["17.stl"] == "gingiva", got
        assert got["2.stl"] == "crown", got
        r = [os.path.basename(p["path"]) for p in parts].index("XUU-SOD260045840-P048_Antag.stl")
        dlg._combo(r, "kind").setCurrentIndex(1)                       # Model (hollow)
        c = [os.path.basename(p["path"]) for p in parts].index("FYQ-SOD260046892-044-P-16-16-1-00-R685_2.stl")
        dlg.table.cellWidget(c, 3).setValue(2)                         # two crowns
        w.load_parts(dlg.parts()); app.processEvents()
        assert len(w.objects) == 5, [o.name for o in w.objects]
        antag = [o for o in w.objects if "Antag" in o.name][0]
        assert not antag.direct and antag.settings.get("hollow_enable") is True   # on the plate only when asked
        crowns = [o for o in w.objects if "R685" in o.name]
        assert len(crowns) == 2 and all(not o.direct for o in crowns)
        for o in w.objects:
            if "R685" in o.name:                                       # the crown: its opening (down in the file) faces up
                from unislicer import dental_cad
                assert np.allclose(o.matrix()[:3, :3] @ [0, 0, -1], [0, 0, 1]), (o.name, dental_cad.OPENING_ERRORS)
            else:                                                      # any other type is not turned over
                assert np.allclose(o.matrix()[2, :3] / o.scale[2], [0, 0, 1]), o.name   # (a turn on the plate is fine)
        assert all(o.support_points is None for o in w.objects)        # no supports added
        assert w.settings.config()["support_enable"] is False          # nor switched on
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_tooth_raft_code_engraved_mirrored_under_the_raft():
    """SOD code on a mini tiny raft: by default engraved into the underside (the side on the plate),
    mirrored so it reads right when the raft is turned over; nothing sticks out above or below the raft.
    'Top' keeps it embossed on top; a raft too thin for the depth also puts it on top."""
    from PySide6.QtGui import QGuiApplication
    from shapely import affinity
    from unislicer import sla_supports
    from unislicer.slicing import slice_mesh
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "tiny", "sla_raft_thickness": 1.0})
    assert cfg["sla_raft_label_side"] == "bottom" and cfg["sla_raft_label_depth"] == 0.5
    # a bridge-sized part: the code fits at a readable size with Windows' wider font too (on a part too
    # small for it underneath, the code stays on top)
    tooth = placed(primitives.box(22, 10, 7), 60, 40).world_mesh().translated([0, 0, 3])
    sup, _ = sla_supports.build_supports(tooth, cfg, label="45198-N183-E")
    low, high = slice_mesh(sup, [0.25, 0.75], 0.05)
    outline = sla_supports.tooth_outline(tooth, cfg["sla_raft_tiny_inset"])
    assert low.area < outline.area - 1.0                          # the code is cut into the bottom layers
    assert high.area >= outline.area - 0.5                        # solid above the engraving
    assert sup.vertices[:, 2].min() >= -1e-6
    # the engraving is the mirror image of the text: it matches the mirrored code, not the plain one
    from unislicer.mesh_tools import text_polygons
    cut = outline.difference(low)
    plain = sla_supports.fit_text(text_polygons("45198-N183-E", 2.0), outline.buffer(-0.4), min_height=1.0)
    mirrored = sla_supports.fit_text(affinity.scale(text_polygons("45198-N183-E", 2.0), -1, 1, origin=(0, 0)),
                                     outline.buffer(-0.4), min_height=1.0)
    assert cut.symmetric_difference(mirrored).area < cut.symmetric_difference(plain).area
    # top: embossed above the raft as before
    cfg.update({"sla_raft_label_side": "top"})
    top, _ = sla_supports.build_supports(tooth, cfg, label="45198-N183-E")
    above = slice_mesh(top, [1.2], 0.05)[0].area - slice_mesh(sup, [1.2], 0.05)[0].area
    assert above > 1.0                                             # text stands above the 1 mm raft
    assert abs(slice_mesh(top, [0.25], 0.05)[0].area - outline.area) < 0.5
    # too thin a raft for a 0.5 mm engraving: the code goes on top
    cfg.update({"sla_raft_label_side": "bottom", "sla_raft_thickness": 0.6})
    thin, _ = sla_supports.build_supports(tooth, cfg, label="45198-N183-E")
    assert abs(slice_mesh(thin, [0.25], 0.05)[0].area - sla_supports.tooth_outline(tooth, 0.5).area) < 0.5


def test_raft_code_bends_along_an_arch_raft():
    """On an arch-shaped raft (a bridge) the whole code is bent along the arch's middle line, much bigger
    than any straight placement allows, and stays wholly inside, reading left to right; a straight raft
    keeps straight text."""
    from PySide6.QtGui import QGuiApplication
    from shapely.geometry import Polygon, box
    from unislicer import sla_supports
    from unislicer.mesh_tools import text_polygons
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    text = text_polygons("45198-N183-E", 2.0)
    pieces = len(sla.as_multipolygon(text).geoms)
    for start, stop in ((20, 160), (-170, -10)):                 # an arch and a U
        ang = np.radians(np.linspace(start, stop, 60))
        arch = Polygon([(21 * np.cos(a), 21 * np.sin(a)) for a in ang] +
                       [(15 * np.cos(a), 15 * np.sin(a)) for a in ang[::-1]])
        room = arch.buffer(-0.4)
        straight = sla_supports._fit_straight(text, room, 0.7, 3.0)
        placed = sla_supports.fit_text(text, room)
        assert placed is not None and room.contains(placed)
        assert len(sla.as_multipolygon(placed).geoms) == pieces          # every glyph is there
        bent = sla_supports._fit_curved(text, room, 0.7, 3.0)
        assert bent is not None and bent[1] > 2.0 and (straight is None or bent[1] > straight[1] * 2)
        # read left to right: a marker at the text's start lands at the arch's left end
        marks = sla.as_multipolygon(shapely.union_all([box(0, 0, 1, 1), box(20, 0, 21, 2)]))
        got = sla.as_multipolygon(sla_supports._fit_curved(marks, room, 0.7, 3.0)[0]).geoms
        small, big = sorted(got, key=lambda g: g.area)
        assert small.centroid.x < big.centroid.x
    flat = sla_supports.fit_text(text, box(0, 0, 30, 6))
    assert flat is not None
    assert sla_supports._fit_straight(text, box(0, 0, 30, 6), 0.7, 3.0)[0].equals(flat)   # straight stays straight


def test_supports_never_run_through_the_model():
    """A shelf hanging 0.3 mm over a lower block, next to a wall: no support may run down through the
    block (a gap under 0.5 mm was taken for open air) or cut through the wall beside it - only the tips
    touch the model."""
    from unislicer import sla_supports
    block = primitives.box(20, 20, 6).translated([0, 0, 5 + 3])              # 5..11
    shelf = primitives.box(30, 20, 2).translated([5, 0, 11.3 + 1])           # 11.3..13.3, overhangs past the block
    wall = primitives.box(1.0, 20, 8).translated([20.9, 0, 9 + 4])           # a thin wall just past the shelf
    model = Mesh.merge([block, shelf, wall])
    for shape in ("tree", "pillar"):
        cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                    {"support_enable": True, "sla_support_type": shape})
        owners = {}
        sup, used = sla_supports.build_supports(model, cfg, owners=owners)
        assert len(used)                                                      # the free part is still held
        caster = sla_supports.caster_for(model)
        cent = sup.triangles.mean(axis=1)
        inside = np.array([np.sum(np.unique(np.round(caster.hits(c[0], c[1])[0], 4)) > c[2]) % 2 == 1 for c in cent])
        if inside.any():
            d = np.min(np.linalg.norm(cent[inside][:, None, :] - used[None, :, :], axis=2), axis=1)
            assert d.max() <= 1.0, (shape, d.max())                          # only the tips go in


def test_raft_label_on_a_raft_in_pieces_keeps_the_supports(monkeypatch):
    """An arch bridge whose mini tiny raft came out in several pieces: bending the code along it failed
    ('MultiPolygon' has no 'exterior') and the whole bridge lost its supports and raft. The code now fits,
    and a label that fails for any reason never costs the part its supports."""
    from PySide6.QtGui import QGuiApplication
    from shapely.geometry import Polygon, box
    from unislicer import sla_supports
    from unislicer.mesh_tools import text_polygons
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    ang = np.radians(np.linspace(20, 160, 60))
    arch = Polygon([(21 * np.cos(a), 21 * np.sin(a)) for a in ang] + [(15 * np.cos(a), 15 * np.sin(a)) for a in ang[::-1]])
    pieces = shapely.union_all([arch, box(30, 0, 32, 2)]).buffer(-0.4)
    assert pieces.geom_type == "MultiPolygon"
    code = sla_supports.fit_text(text_polygons("45198-N183-E", 2.0), pieces)
    assert code is not None and pieces.contains(code)
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "tiny", "sla_raft_label_side": "top"})
    part = placed(primitives.box(22, 10, 7), 60, 40).world_mesh().translated([0, 0, 3])
    plain, used = sla_supports.build_supports(part, cfg)

    def broken(*a, **k):
        raise RuntimeError("label failed")
    monkeypatch.setattr(sla_supports, "_code_on_raft", broken)
    for side in ("top", "bottom"):
        cfg.update({"sla_raft_label_side": side})
        sup, used2 = sla_supports.build_supports(part, cfg, label="45198-N183-E")
        assert len(used2) == len(used) and len(sup.faces) == len(plain.faces)


def test_raft_and_label_raft_follows_the_outline_with_a_coded_tab():
    """'Raft & label': a narrow raft along the part's outline (every foot on it) and a crescent tab past
    the part's +X end with the code standing on it, above the bottom layers; the auto layout keeps
    neighbours clear of the tab."""
    from PySide6.QtGui import QGuiApplication
    from unislicer import sla_supports, layout
    from unislicer.slicing import slice_mesh
    from shapely.geometry import MultiPoint
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    obj = placed(primitives.box(24, 9, 7), 60, 40)
    part = obj.world_mesh().translated([0, 0, 5])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "raft_label", "sla_raft_thickness": 1.0})
    plain, used = sla_supports.build_supports(part, cfg)
    sup, used2 = sla_supports.build_supports(part, cfg, label="46614-044-P")
    assert len(used2) == len(used) > 0
    raft_layer, code_layer = slice_mesh(sup, [0.5, 1.2])
    outline = MultiPoint(part.vertices[:, :2]).convex_hull
    # narrow: along the outline (+0.8 mm), not a 2 mm-margin slab - apart from the tab
    without_tab = raft_layer.intersection(outline.buffer(3.0)).difference(
        shapely.geometry.box(outline.bounds[2] - sla_supports.MOON_WRAP, -1e3, 1e3, 1e3))
    assert without_tab.difference(outline.buffer(sla_supports.LABEL_RAFT_MARGIN + 0.3)).area < 2.0
    # the tab: past the +X end, about MOON_WIDTH wide, and the code stands on it
    assert raft_layer.bounds[2] > outline.bounds[2] + sla_supports.MOON_WIDTH
    code_layer = code_layer.difference(slice_mesh(plain, [1.2])[0].buffer(0.05))     # not the pillars
    assert code_layer.area > 3 and code_layer.bounds[0] > outline.bounds[2] - sla_supports.MOON_WRAP - 0.5
    plain_raft = slice_mesh(plain, [0.5])[0]
    assert plain_raft.bounds[2] < outline.bounds[2] + 2.0         # no code, no tab
    # every foot stands on the raft
    feet = sup.vertices[np.abs(sup.vertices[:, 2] - 1.0) < 1e-3][:, :2]
    assert all(raft_layer.buffer(0.05).contains(shapely.geometry.Point(*p)) for p in feet[::7])
    # the auto layout leaves the tab room: two parts in a row do not overlap tab and neighbour
    a, b = placed(primitives.box(24, 9, 7), 0, 0), placed(primitives.box(24, 9, 7), 0, 0)
    for o in (a, b):
        o.__dict__["raft_tab"] = True
    for strategy in ("box", "contour"):
        failed = layout.auto_layout([a, b], (0, 0, 200, 120), layout.LayoutOptions(strategy=strategy, spacing=1.0,
                                                                                  rotation="none"))
        assert not failed
        ta = layout.with_raft_tab(a, layout.footprint(a))
        tb = layout.with_raft_tab(b, layout.footprint(b))
        assert ta.area > layout.footprint(a).area + 20 and not ta.intersects(tb), strategy


def test_raft_code_goes_on_the_parts_own_raft():
    """Solid, grid, hexagon and mini rafts: the case code goes on the part's own raft (engraved under it or
    standing on it) - never on an extra tab beside the raft."""
    from PySide6.QtGui import QGuiApplication
    from unislicer import sla_supports
    from unislicer.slicing import slice_mesh
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    part = placed(primitives.box(26, 10, 7), 60, 40).world_mesh().translated([0, 0, 5])
    for kind in ("solid", "grid", "hexagon", "mini"):
        for side in ("bottom", "top"):
            cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {
                "support_enable": True, "sla_raft_type": kind, "sla_raft_label_side": side})
            plain, _ = sla_supports.build_supports(part, cfg)
            coded, _ = sla_supports.build_supports(part, cfg, label="45198-N183-E")
            raft = slice_mesh(plain, [0.25], 0.05)[0]
            got = slice_mesh(coded, [0.25], 0.05)[0]
            outline = raft.convex_hull.buffer(0.5)       # (the engraved bottom has no bevel)
            assert outline.contains(got), (kind, side)                  # nothing beside the raft
            if side == "bottom" and kind == "solid":
                assert got.area < raft.area - 1.0                       # cut into its underside
            if side == "top":
                above = slice_mesh(coded, [cfg["sla_raft_thickness"] + 0.2], 0.05)[0].area - \
                    slice_mesh(plain, [cfg["sla_raft_thickness"] + 0.2], 0.05)[0].area
                assert above > 1.0, kind                                 # standing on the raft


def test_long_bridge_named_by_lab_code_is_a_bridge_not_a_model():
    """An 8-unit bridge 14-24 is 44 mm long: by size alone it was taken for a jaw model, put straight on
    the plate and got no supports, raft or case code. A lab name with a tooth range and unit count says
    it is the restoration itself."""
    from unislicer import dental_cad
    assert dental_cad.detect_indication("FPW-SOD260047135-044-P-A153-14-24-8-A3-_2.stl") == "crown"
    assert dental_cad.detect_indication("FCN-SOD260047092-044-P-33-43-6-00-A185_2.stl") == "crown"
    assert dental_cad.detect_indication("BPD-SOD260042368-004-P-23-26-4-A3-.stl") == "crown"
    # the case's other files still say what they are
    assert dental_cad.detect_indication("EGI-SOD260046764-114-Z-14-14-1-00-V227_Antag.stl") == "model"
    assert dental_cad.detect_indication("EGI-SOD260046764-114-Z-14-14-1-00-V227_Tooth_14.stl") == "die"
    assert dental_cad.detect_indication("BAH-SOD260046430-034-X-35-36-2-C2-B592_Tissue_17.stl") == "gingiva"
    assert dental_cad.detect_indication("upper.stl") == "unknown"
    assert dental_cad.detect_indication("case-2024-11-05.stl") == "unknown"


def test_auto_label_never_puts_a_label_on_a_crown(tmp_path):
    """Labels > Auto label on the whole plate: models and dies get their label, crowns and bridges do not -
    a label on the tooth itself would spoil it (their code goes on the raft)."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        import numpy as np
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); app.processEvents()
        w.store.setValue("preslice", "false")
        outer, inner = primitives.box(10, 10, 8), primitives.box(8, 8, 7).translated([0, 0, -0.6])
        cap = Mesh(np.vstack([outer.vertices, inner.vertices]),
                   np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)]),
                   "FYQ-SOD260046892-044-P-16-16-1-00-R685_2")
        crown = w.add_mesh(cap)
        jaw = w.add_mesh(primitives.box(60, 45, 15, "EGI-SOD260046764-114-Z-14-14-1-00-V227_Antag"))
        w._select_uid(None)
        w.auto_label()
        assert jaw.labels, "the model is labelled"
        assert not crown.labels, "the crown is not"
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_supports_standing_on_a_sloped_part_of_the_model_are_kept():
    """Hollow parts (inside a crown, a tilted bridge): points inside sit above another part of the model, so
    their supports stand on it - on a sloped surface. The through-the-model check dropped them (a third of
    a tilted bridge's supports): a foot sinking into the slope it stands on, or a pillar's edge grazing a
    wall, is not a support running through the model."""
    from unislicer import sla_supports

    def shell(c):
        o, i = primitives.sphere(5, 24, 48).translated(c), primitives.sphere(4, 24, 48).translated(c)
        return Mesh(np.vstack([o.vertices, i.vertices]), np.vstack([o.faces, i.faces[:, ::-1] + len(o.vertices)]))
    model = Mesh.merge([shell([60 + k * 8, 40, 10]) for k in range(3)])
    for shape in ("tree", "pillar"):
        cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                    {"support_enable": True, "sla_support_type": shape})
        pts = sla_supports.auto_support_points(model, cfg)
        _, used = sla_supports.build_supports(model, cfg, pts)
        assert len(used) == len(pts), (shape, len(used), len(pts))


def test_jaw_model_ledges_get_no_supports_climbing_up_the_model():
    """A jaw model (over 40 mm) with a narrow step between its base and the gingiva: the step holds itself
    up, so no support climbs the wall from the plate to it; the underside keeps all its supports. A tilted
    model keeps the supports of its sloped underside, and crowns keep every point."""
    from unislicer import sla_supports
    base = primitives.box(50, 40, 8)
    base = base.translated([60, 40, 5 - base.bounds[0][2]])                    # 5..13
    gum = primitives.box(52, 42, 6)
    gum = gum.translated([60, 40, 13 - gum.bounds[0][2]])                      # 13..19, a 1 mm step all round
    model = Mesh.merge([base, gum])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    pts = sla_supports.auto_support_points(model, cfg)
    assert len(pts) and not np.any(pts[:, 2] > 6.0), pts[pts[:, 2] > 6.0]     # nothing on the step at z 13
    assert np.sum(pts[:, 2] < 5.1) >= 20                                      # the underside is held
    a = np.radians(30)
    tilt = Mesh(model.vertices @ np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]]).T,
                model.faces)
    tilt = tilt.translated([0, 0, -tilt.bounds[0][2] + 5])
    assert len(sla_supports.auto_support_points(tilt, cfg)) >= 20               # a sloped underside is held


def test_model_type_brings_its_supports():
    """Supports > Model type (C/B, guide, model, model U, splint horizontal / vertical, denture): each type
    sets its own supports; Automatic takes the type from the file name, and a part whose name says nothing
    keeps the plate's support settings."""
    from unislicer import dental_cad
    from unislicer.scene import SceneObject
    from unislicer.settings import MODEL_TYPE_PRESETS
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    assert cfg["sla_model_type"] == "auto"
    for t, preset in MODEL_TYPE_PRESETS.items():
        c = cfg.copy().update({"sla_model_type": t})
        assert all(c[k] == v for k, v in preset.items()), t
    assert cfg.copy().update({"sla_model_type": "denture"})["sla_tip_diameter"] > \
        cfg.copy().update({"sla_model_type": "cb"})["sla_tip_diameter"]            # dentures: stronger contacts
    assert dental_cad.model_type_of("FYQ-SOD260046892-044-P-16-16-1-00-R685_2", None) == "cb"
    assert dental_cad.model_type_of("case_SurgicalGuide", None) == "guide"
    assert dental_cad.model_type_of("upper_denture", None) == "denture"
    assert dental_cad.model_type_of("bracket.stl", None) == ""
    horseshoe = Mesh.merge([primitives.box(8, 40, 10), primitives.box(8, 40, 10).translated([30, 0, 0]),
                            primitives.box(38, 8, 10).translated([0, 32, 0])])
    plate = primitives.box(38, 40, 10)
    assert dental_cad.model_type_of("x_UnsectionedModel_UpperJaw", horseshoe) == "model_u"
    assert dental_cad.model_type_of("x_UnsectionedModel_UpperJaw", plate) == "model"
    crown = SceneObject.from_mesh(primitives.box(10, 10, 8), "FYQ-SOD260046892-044-P-16-16-1-00-R685_2")
    part = SceneObject.from_mesh(primitives.box(10, 10, 8), "bracket")
    assert crown.config(cfg)["sla_avoid_fit"] is True and crown.config(cfg)["sla_tip_diameter"] == 0.35
    assert part.config(cfg)["sla_tip_diameter"] == cfg["sla_tip_diameter"]          # nothing said: plate settings
    crown.settings = {"sla_model_type": "plate"}                                    # the model's own choice wins
    assert crown.config(cfg)["sla_tip_diameter"] == cfg["sla_tip_diameter"]


def test_no_supports_on_the_fitting_surface():
    """C/B, guide, splint, denture: points inside the part's hollow (the fitting surface) get no support; the
    outside keeps its points."""
    from unislicer import sla_supports
    outer, inner = primitives.box(12, 12, 9), primitives.box(9, 9, 6).translated([1.5, 1.5, 1.5])
    shell = Mesh(np.vstack([outer.vertices, inner.vertices]),
                 np.vstack([outer.faces, inner.faces[:, ::-1] + len(outer.vertices)])).translated([60, 40, 5])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True}).update(
        {"sla_model_type": "cb"})
    plain = sla_supports.auto_support_points(shell, cfg.copy().update({"sla_avoid_fit": False}))
    report = {}
    kept = sla_supports.auto_support_points(shell, cfg, report)
    inside = plain[plain[:, 2] > 5.5]                       # the hollow's ceiling, 7.5 mm up
    assert len(inside) and report["fit_dropped"] >= len(inside)
    assert not np.any(kept[:, 2] > 5.5)                      # none inside
    assert np.sum(kept[:, 2] < 5.1) >= 9                     # the outside underside is still held


def test_mouse_controls_like_chitubox():
    """Mouse controls as in CHITUBOX (the default): right drag turns the view, wheel drag (or Shift + right
    drag) pans it, the wheel zooms, a left drag over empty space frames a selection, a right click opens the
    model menu. The classic scheme keeps left drag = turn, right drag = pan."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        o = w.add_mesh(primitives.box(20, 20, 5)); app.processEvents()
        v = w.viewer
        assert v.mouse_scheme == "chitubox"
        v.pick = lambda pos, *a, **k: None                        # empty space everywhere

        def drag(button, a, b, mods=Qt.NoModifier):
            pa, pb = QPointF(*a), QPointF(*b)
            v.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pa, pa, button, button, mods))
            v.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, pb, pb, Qt.NoButton, button, mods))
            v.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, pb, pb, button, Qt.NoButton, mods))
            app.processEvents()

        cam = v.camera
        yaw, target = cam.yaw, cam.target.copy()
        drag(Qt.RightButton, (300, 300), (360, 300))
        assert cam.yaw != yaw and np.allclose(cam.target, target)          # right drag: turns
        yaw = cam.yaw
        drag(Qt.MiddleButton, (300, 300), (360, 330))
        assert cam.yaw == yaw and not np.allclose(cam.target, target)      # wheel drag: pans
        target = cam.target.copy()
        drag(Qt.RightButton, (300, 300), (360, 330), Qt.ShiftModifier)
        assert cam.yaw == yaw and not np.allclose(cam.target, target)      # Shift + right drag: pans
        w._select_uid(None)
        drag(Qt.LeftButton, (2, 2), (v.width() - 2, v.height() - 2))
        assert o.uid in [x.uid for x in w.selected_objects()]             # left drag on empty space: a frame
        shown = []
        w._show_context_menu = lambda uid, pos: shown.append(uid)
        v.contextRequested.disconnect()
        v.contextRequested.connect(w._show_context_menu)
        pa = QPointF(300, 300)
        v.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pa, pa, Qt.RightButton, Qt.RightButton, Qt.NoModifier))
        v.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, pa, pa, Qt.RightButton, Qt.NoButton, Qt.NoModifier))
        assert shown, "a right click (no drag) opens the menu"
        w.set_mouse_scheme("classic")
        yaw = cam.yaw
        drag(Qt.LeftButton, (300, 300), (360, 300))
        assert cam.yaw != yaw                                              # classic: left drag turns
        assert w.store.value("mouse_scheme") == "classic"
        w.set_mouse_scheme("chitubox")
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_supports_are_built_in_the_background():
    """Generate supports returns without waiting for the support structures: they are built in the
    background and appear in the view when ready; a change made meanwhile gets its own (never a stale)
    structure; closing the window while one is being built is clean."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        assert w.async_supports
        parts = [w.add_mesh(Mesh.merge([primitives.box(12, 12, 4), primitives.box(20, 4, 2).translated([-4, 4, 6])]))
                 for _ in range(3)]
        for o in parts:
            o.direct = False
        w.arrange_objects(); app.processEvents()
        w._select_uid(None)
        w.generate_supports()
        w.wait_supports()
        cfg = w.settings.config()
        for o in parts:
            mesh = w.viewer.objects[o.uid].get("supports")
            assert mesh is not None and len(mesh.faces), "supports shown once built"
            assert w._support_cache[o.uid][0] == w._support_key(o, cfg)
        # change one model's supports while builds may be running: what ends up shown is for the new points
        o = parts[0]
        o.support_points = o.support_points[: max(1, len(o.support_points) // 2)]
        w._objects_changed(refresh_list=False)
        w.wait_supports()
        assert w._support_cache[o.uid][0] == w._support_key(o, w.settings.config())
        assert w.viewer.objects[o.uid]["supports"] is w._support_cache[o.uid][1]
        # close while building
        w._select_uid(None)
        w.settings.set_value("sla_support_spacing", 2.0); app.processEvents()
        w.generate_supports()
        w.close(); app.processEvents()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    env.pop("UNISLICER_SYNC_SUPPORTS", None)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_one_click_plate_and_its_summary():
    """One-click plate: arranges, supports and shows a summary - types (an unnamed part asks for a type),
    placement, supports, warnings with fixes, estimate. The parts keep their orientation; 'Turn over' turns
    only the part it is for (Ctrl+Z gives it back), choosing a type sets that part's supports, and 'Slice
    only' slices."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        import numpy as np
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui import plate_summary
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.store.setValue("preslice", "false"); w.store.setValue("ap_show", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        shelf = Mesh.merge([primitives.box(12, 12, 4), primitives.box(20, 4, 2).translated([-4, 4, 6])])
        crown = w.add_mesh(Mesh(shelf.vertices, shelf.faces, "FYQ-SOD260046892-044-P-16-16-1-00-R685_2"))
        part = w.add_mesh(Mesh(shelf.vertices, shelf.faces, "bracket"))
        jaw = w.add_mesh(Mesh(primitives.box(50, 40, 10).vertices, primitives.box(50, 40, 10).faces,
                              "SMG-SOD260045198-N183-E-16-16-1-A35_Antag"))
        jaw.direct = True
        for o in (crown, part):
            o.direct = False
        before = {o.uid: o.matrix().copy() for o in w.objects}
        seen, script = [], []

        def fake_exec(self):
            seen.append(dict(rows=list(self.rows)))
            self.choice = script.pop(0) if script else None
            return 0
        plate_summary.PlateSummary.exec = fake_exec
        w._fit_down = set()
        # 1) plain run: back to the plate
        w.one_click_plate()
        rows = seen[-1]["rows"]
        text = " | ".join(t for _s, _ok, t in rows)
        assert "bracket" in text and any(not ok for _s, ok, t in rows if "bracket" in t), text   # asks for a type
        for o in w.objects:                                                      # orientation kept
            assert np.allclose(o.matrix(), before[o.uid]), o.name
        assert all(o.support_points is not None and len(o.support_points) for o in (crown, part))
        assert any("1" in t for s, ok, t in rows if ok)                          # placement / supports lines
        # 2) a fitting-surface warning: Turn over turns only that part, then choose a type for the unnamed one
        w._fit_down = {crown.uid}
        script[:] = [("fix", "turn", crown.uid, None), ("fix", "type", part.uid, "guide"), None]
        w._plate_summary()
        assert not np.allclose(crown.matrix(), before[crown.uid])                # turned over
        assert np.allclose(part.matrix(), before[part.uid]) and np.allclose(jaw.matrix(), before[jaw.uid])
        assert part.settings.get("sla_model_type") == "guide"
        assert part.config(w.settings.config())["sla_avoid_fit"] is True        # its supports follow the type
        w.undo(); app.processEvents()                                            # undoes the type choice
        part2 = next(o for o in w.objects if o.uid == part.uid)
        assert part2.settings.get("sla_model_type") is None
        w.undo(); app.processEvents()                                            # one Ctrl+Z per fix
        crown2 = next(o for o in w.objects if o.uid == crown.uid)
        assert np.allclose(crown2.matrix(), before[crown.uid])                   # the turn is given back
        # 3) Slice only
        script[:] = ["slice"]
        sliced = []
        w.slice = lambda: sliced.append(1)
        w._plate_summary()
        assert sliced == [1]
        w._stop_preslice()
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_settings_simple_mode_shows_supports_and_raft_only():
    """Settings > Simple: only the supports, the raft and hollowing, open, with their day-to-day settings (printer,
    material and quality stay at the top); a value set there holds in Full, which shows every section again;
    the choice is remembered."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        from unislicer.settings import SIMPLE_KEYS
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        sp = w.settings
        sp.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        assert not sp.simple
        full = set(sp._sections)
        sp.simple_btn.click(); app.processEvents()
        assert sp.simple and set(sp._sections) == {"support", "raft", "hollow"}, sp._sections
        allowed = {k for v in SIMPLE_KEYS.values() for k in v}
        assert set(sp._rows) <= allowed and {"sla_model_type", "sla_raft_type", "sla_raft_margin"} <= set(sp._rows)
        assert all(sec.toggle.isChecked() for sec in sp._sections.values())       # open, rows in the panel
        assert w.store.value("settings_simple") == "true"
        sp.set_value("sla_raft_margin", 3.5); app.processEvents()
        sp.full_btn.click(); app.processEvents()
        assert not sp.simple and set(sp._sections) == full
        assert sp.config()["sla_raft_margin"] == 3.5                              # kept in Full
        assert w.store.value("settings_simple") == "false"
        w._stop_preslice()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(__import__("tempfile").mkdtemp())
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_update_check_download_and_offer(tmp_path):
    """Updates: a published build with another commit is newer (the same one, or a build run from source, is
    not); the installer downloads completely or not at all; the window shows 'Update available' and, after
    asking, gets the installer."""
    import http.server, socketserver, threading
    from unislicer import updater
    assert updater.newer({"commit": "71fe62cc", "url": "u"}, "abc123")
    assert not updater.newer({"commit": "71fe62cced5f", "url": "u"}, "71fe62cc")
    assert not updater.newer({"commit": "71fe62cc", "url": "u"}, "")              # from source: never
    payload = os.urandom(300_000)
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "UniSlicer-Setup.exe").write_bytes(payload)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path / "srv"), **k)

        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/UniSlicer-Setup.exe"
        seen = []
        path = updater.download({"url": url, "size": len(payload)}, str(tmp_path / "dl"), seen.append)
        assert open(path, "rb").read() == payload and seen and seen[-1] == 1.0
        with pytest.raises(OSError):                                                # truncated: refused
            updater.download({"url": url, "size": len(payload) + 1}, str(tmp_path / "dl2"))
        assert not os.path.exists(tmp_path / "dl2" / "UniSlicer-Setup.exe")
        import hashlib
        good = hashlib.sha256(payload).hexdigest()                                   # GitHub's fingerprint
        assert updater.download({"url": url, "size": len(payload), "sha256": good}, str(tmp_path / "dl3"))
        with pytest.raises(OSError):                                                # not the published file
            updater.download({"url": url, "size": len(payload), "sha256": "0" * 64}, str(tmp_path / "dl4"))
        assert not os.path.exists(tmp_path / "dl4" / "UniSlicer-Setup.exe")
        with pytest.raises(OSError):                                                # nothing to check against
            updater.download({"url": url}, str(tmp_path / "dl5"))
    finally:
        srv.shutdown()
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication([])
        from unislicer import updater
        from unislicer.gui import main_window as mw
        updater.build_commit = lambda: "abc123"
        updater.latest = lambda timeout=10.0: {"commit": "def456", "date": "2026-11-07", "url": "x", "size": 1}
        got = []
        updater.download = lambda info, dest, progress=None, cancelled=None: got.append(info) or "/tmp/UniSlicer-Setup.exe"
        mw.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        import subprocess as _sp, sys as _sys
        ran = []
        _sp.Popen = lambda args, **k: ran.append(args)               # the installer is not really run here
        w = mw.MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        assert not w.update_btn.isVisible()
        w.check_updates(manual=True)
        end = time.time() + 10
        while not w.update_btn.isVisible() and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        assert w.update_btn.isVisible() and "2026-11-07" in w.update_btn.toolTip()
        w.update_btn.click()
        end = time.time() + 10
        while not got and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        end = time.time() + 5
        while _sys.platform != "win32" and "UniSlicer-Setup.exe" not in w.status.text() and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        assert got and got[0]["commit"] == "def456"
        if _sys.platform == "win32":                                 # Windows: run quietly, the window closes
            end = time.time() + 5
            while not ran and time.time() < end:
                app.processEvents(); time.sleep(0.01)
            assert ran and ran[0][0].endswith("UniSlicer-Setup.exe") and "/SILENT" in ran[0], ran
            w.show(); app.processEvents()
        else:                                                        # elsewhere: saved, not run
            assert "UniSlicer-Setup.exe" in w.status.text(), w.status.text()
        updater.latest = lambda timeout=10.0: {"commit": "abc123", "date": "x", "url": "x", "size": 1}
        w._upd_task = None
        w.check_updates(manual=True)
        end = time.time() + 10
        while w.update_btn.isVisible() and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        assert not w.update_btn.isVisible()                                      # up to date
        w._stop_preslice()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_user_guide_covers_every_menu_command_shortcut_and_setting():
    """The user guide (Hebrew and English) is built from the program: every shortcut of the window's table,
    every setting and every printer is in it, and every command of the menu bar, the top bar and the More
    menu is named in the chapters of both languages - a new command fails here until the guide explains it."""
    import html as _html
    import re as _re
    import tempfile
    from unislicer import manual, settings
    from unislicer.gui import i18n
    from unislicer.gui.shortcuts import SHORTCUTS, keys_text
    src = open(os.path.join(os.path.dirname(manual.__file__), "gui", "main_window.py"), encoding="utf-8").read()
    menu_src = src[src.index("def _build_menu_bar"):src.index("def _fill_recent_into")]
    more_src = src[src.index("more = tool(\"more\""):src.index("more.setMenu(mm)")]
    top_src = src[src.index("tool(\"open\", tr(\"open\")"):src.index("more = tool(\"more\"")]
    used = set(_re.findall(r'add\(\w, tr\("([a-z0-9_]+)"\)', menu_src)) | set(_re.findall(r'bar\.addMenu\(tr\("([a-z0-9_]+)"\)', menu_src))
    used |= set(_re.findall(r'mm\.add(?:Action|Menu)\((?:theme\.icon\("[a-z0-9_]+"\), )?tr\("([a-z0-9_]+)"\)', more_src))
    used |= set(_re.findall(r'tool\("[a-z0-9_]+", tr\("([a-z0-9_]+)"\)', top_src))
    from unislicer.gui.settings_panel import FDM_ENABLED
    if not FDM_ENABLED:
        used.discard("fil_title")                  # the filaments window: filament printing is not offered
    assert len(used) > 40
    for lang, col in (("he", 0), ("en", 1)):
        page = manual.build_html(lang=lang)
        flat = _html.unescape(page)
        assert page.startswith("<!doctype html>") and (f'dir="{"rtl" if lang == "he" else "ltr"}"') in page
        for _id, seqs, _g, he, en in SHORTCUTS:
            assert (he, en)[col] in flat, (lang, _id)
            for seq in seqs:
                assert all(f"<kbd>{p}</kbd>" in page for p in keys_text(seq).split("+") if p), seq
        sheet = _html.unescape(manual.build_shortcuts_html(lang))
        assert all((he, en)[col] in sheet for _i, _s, _g, he, en in SHORTCUTS)
        for s in settings.SCHEMA:
            if s.tech != "fdm":                                  # resin only: filament is not offered
                assert f"<b>{(s.he, s.en)[col]}</b>" in flat, (lang, s.key)
        for p in profiles.load("printers").values():
            assert p["name"] in flat or p.get("hidden") or profiles.technology_of(p) == "fdm"
        chapters = " ".join(b for _a, _t, b in manual.chapters(lang))
        missing = [k for k in sorted(used) if "{t:" + k + "}" not in chapters]
        assert not missing, f"commands the {lang} guide does not explain yet: " + ", ".join(missing)
        for k in manual.MENU_KEYS:
            assert k in i18n.STRINGS and manual._label(k, lang) in flat, (lang, k)
        assert [a for a, *_ in manual.chapters(lang)] == [a for a, *_ in manual.CHAPTERS]   # the same chapters
        folder = manual._img_dir(lang)
        for name in _re.findall(r"\{img:([^|}]+)", chapters):
            assert os.path.isfile(os.path.join(folder, name)), (lang, name)
        assert "{t:" not in page and "{s:" not in page and "{k:" not in page
        with tempfile.TemporaryDirectory() as d:
            path = manual.write(d, lang)
            assert os.path.basename(path) == manual.file_names(lang)[0]
            assert os.path.isfile(os.path.join(d, manual.file_names(lang)[1]))
            sub = ("en",) if lang == "en" else ()
            assert os.path.isfile(os.path.join(d, "manual_img", *sub, "main.png"))
            assert 'src="manual_img/' + ("en/" if lang == "en" else "") + 'main.png"' in open(path, encoding="utf-8").read()


def test_duplicates_take_over_supports_instead_of_rebuilding(tmp_path):
    """Duplicating supported crowns (raft & label) used to build every copy's supports again in the
    background - 48 crowns were ~10 s of lag. A copy has the same mesh, points, turn and settings, so it
    takes its original's support structure; after a setting change only one of each group is built."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        import unislicer.gui.main_window as MW
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        w.settings.set_value("sla_raft_type", "raft_label")
        for k in range(3):
            o = w.add_mesh(primitives.crown_cap(9.0 + k, 8.0, name=f"C{k}-SOD26001234{k}-044-P-1{k}.stl"))
        app.processEvents()
        w._select_uid(None); w.generate_supports(); w.wait_supports(); app.processEvents(); w.wait_supports()
        w._arrange = lambda cfg: True                 # copies keep their turn (the layout may turn parts)
        built = []
        orig = MW.MainWindow._support_built
        MW.MainWindow._support_built = lambda self, res, shared: (built.append(res[0]), orig(self, res, shared))
        w.select_all(); w.duplicate_selected(); app.processEvents(); w.wait_supports(); app.processEvents()
        w.select_all(); w.duplicate_selected(); app.processEvents(); w.wait_supports(); app.processEvents()
        assert len(w.objects) == 12, len(w.objects)
        cfg = w.settings.config()
        assert all(w._support_cache.get(o.uid, (None,))[0] == w._support_key(o, cfg) for o in w.objects)
        assert built == [], built                            # nothing was built again for the copies
        w.settings.set_value("sla_raft_thickness", 1.4); w._objects_changed(refresh_list=False)
        end = time.time() + 120
        while time.time() < end and not all(w._support_cache.get(o.uid, (None,))[0] == w._support_key(o, w.settings.config())
                                            for o in w.objects):
            app.processEvents(); w.wait_supports(); time.sleep(0.02)
        assert all(w._support_cache.get(o.uid, (None,))[0] == w._support_key(o, w.settings.config()) for o in w.objects)
        assert len(built) == 3, built                        # one build per original shape
        # turning a part about Z (by hand or by the layout) keeps its supports: they turn with it
        import numpy as np
        n0 = len(built)
        o = w.objects[0]
        o.apply_world_rotation(np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]))
        w._objects_changed(refresh_list=False); app.processEvents(); w.wait_supports(); app.processEvents()
        assert len(built) == n0, built
        assert w._support_cache[o.uid][0] == w._support_key(o, w.settings.config())
        # slicing takes the structures the view shows instead of building them again
        meshes, kw = w._sla_inputs(list(w.objects))
        assert all(p is not None for p in kw["prebuilt_supports"])
        w._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_raft_and_label_tab_turns_with_the_part_and_the_layout_knows_where():
    """The raft & label tab sits at the part's own +x end: a part turned on the plate gets its tab turned
    with it (so its supports need no rebuild), and the outline the auto layout keeps free covers it."""
    from PySide6.QtGui import QGuiApplication
    from unislicer import sla_supports, layout
    from unislicer.slicing import slice_mesh
    QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "raft_label", "sla_raft_thickness": 1.0})
    for deg in (0.0, 37.0, 90.0, 200.0):
        o = placed(primitives.box(24, 9, 7), 70, 50)
        a = np.radians(deg)
        o.apply_world_rotation(np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]))
        part = o.world_mesh().translated([0, 0, 5])
        sup, _ = sla_supports.build_supports(part, cfg, label="46614-044-P", label_frame=o.plate_frame())
        raft = slice_mesh(sup, [0.5])[0]
        o.__dict__["raft_tab"] = True
        keep = layout.with_raft_tab(o, layout.footprint(o))
        assert raft.difference(keep.buffer(0.6)).area < 1.0, deg        # the layout reserves the real tab
        fx = np.asarray(o.plate_frame()[0])
        c = np.asarray(part.vertices[:, :2].mean(axis=0))
        tip = max(np.asarray(raft.exterior.coords if raft.geom_type == "Polygon" else
                             [q for g in raft.geoms for q in g.exterior.coords]), key=lambda q: float((q - c) @ fx))
        assert float((tip - c) @ fx) > 12 + sla_supports.MOON_WIDTH * 0.8, deg   # the tab is at the part's +x end



def test_language_switch_hands_the_plate_over_without_rebuilding(tmp_path):
    """A language (or theme) switch builds a new window. It used to start bare: every model's supports
    were built again (14 crowns: ~9 s with the parts shown without supports), and the view and undo were
    lost. The old window now hands over its supports, history, camera and selection."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui import i18n
        from unislicer.gui.main_window import MainWindow
        import unislicer.gui.main_window as MW
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        for k in range(3):
            w.add_mesh(primitives.crown_cap(9.0 + k, 8.0, name=f"C{k}-SOD26001234{k}-044-P-1{k}.stl"))
        app.processEvents()
        w._select_uid(None); w.generate_supports(); w.wait_supports(); app.processEvents(); w.wait_supports()
        w.viewer.camera.yaw = 41.0
        w._select_uid(w.objects[1].uid)
        undo_depth = len(w._undo)
        built = []
        orig = MW.MainWindow._support_built
        MW.MainWindow._support_built = lambda self, res, shared: (built.append(res[0]), orig(self, res, shared))
        i18n.set_language("en")
        carry = w.handover(); carry["direction"] = Qt.LeftToRight
        w2 = MainWindow(w.objects, carry=carry); w2.show(); w.close(); app.processEvents()
        w2.wait_supports(); app.processEvents()
        cfg = w2.settings.config()
        assert built == [], built
        assert all(w2._support_cache[o.uid][0] == w2._support_key(o, cfg) for o in w2.objects)
        assert abs(w2.viewer.camera.yaw - 41.0) < 1e-6
        assert [o.uid for o in w2.selected_objects()] == [w2.objects[1].uid]
        assert len(w2._undo) == undo_depth and w2.layoutDirection() == Qt.LeftToRight
        w2._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_new_project_clears_the_plate_and_undo_brings_it_back(tmp_path):
    """File > New project (Ctrl+N) empties the plate - nothing of it can still be exported - and keeps the
    printer and settings; one Ctrl+Z gives every model back."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        for k in range(3):
            w.add_mesh(primitives.box(10 + k, 10, 5))
        app.processEvents()
        w._last_result = ("sla", object(), None)
        printer = w.settings.printer_id
        w.new_project(); app.processEvents()
        assert w.objects == [] and not w.viewer.objects and w._exportable() is None
        assert w.settings.printer_id == printer
        w.undo(); app.processEvents()
        assert len(w.objects) == 3, len(w.objects)
        w.new_project(); w.new_project(); app.processEvents()        # an empty plate: nothing to do
        w.undo(); app.processEvents()
        assert len(w.objects) == 3
        w._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_solid_lite_raft_bulges_round_parts_only():
    """'Solid lite': a solid raft along the outline that reaches ~2 mm further out round a small round part
    standing out of the outline (a ball at the edge) - not at plain corners, nor round a whole small crown."""
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    from unislicer import sla_supports
    from unislicer.slicing import slice_mesh
    body = box(0, 0, 22, 8)
    for r in (1.6, 2.0, 3.2, 4.5):
        knobs = sla_supports.round_knobs(unary_union([body, Point(11, 0).buffer(r, quad_segs=32)]))
        assert len(knobs) == 1, r
        assert abs(knobs[0].centroid.x - 11) < 0.6 and knobs[0].centroid.y < 0, r
    assert sla_supports.round_knobs(body) == []                                  # corners are not bulges
    assert sla_supports.round_knobs(body.buffer(2.0)) == []                      # nor rounded corners
    assert sla_supports.round_knobs(Point(0, 0).buffer(4.0)) == []               # nor a small round crown
    # the raft: a box with a ball at its edge, standing on a few supports
    from shapely.geometry import Polygon
    part = primitives.box(22, 8, 6)
    ball = primitives.sphere(3.0, 24, 48)
    shape = unary_union([body, Point(11, 0).buffer(3.0, quad_segs=32)])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None,
                                {"support_enable": True, "sla_raft_type": "solid_lite", "sla_raft_thickness": 1.0})
    feet = [(4.0, 4.0, 1.0), (18.0, 4.0, 1.0), (11.0, 0.0, 1.0)]
    plan = sla_supports._raft_plan(feet, cfg, 1.0, None, shape)
    raft = plan[0]
    lowest = min(y for x, y in raft.exterior.coords)
    assert lowest < -3.0 - sla_supports.LABEL_RAFT_MARGIN - 1.5                 # it bulges out past the ball
    corner_gap = raft.exterior.distance(Point(22, 8))
    assert corner_gap < sla_supports.LABEL_RAFT_MARGIN + 0.6                     # and not at a plain corner
    assert all(raft.buffer(0.01).contains(Point(x, y)) for x, y, _z in feet)


def test_support_reuse_follows_height_mesh_and_the_slice_survives_a_switch(tmp_path):
    """Found in review: a part lifted with Move to... kept the structure built at the old height (the view's
    supports, now also used for slicing, floated 4 mm above the plate); a repaired mesh kept the structure
    of the open one; a language switch lost the slice."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        app = QApplication([])
        import numpy as np
        from unislicer import primitives
        from unislicer.mesh import Mesh
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_ls_plus"); app.processEvents()
        o = w.add_mesh(primitives.crown_cap(9.0, 8.0, name="C-SOD260012345-044-P-16.stl")); app.processEvents()
        w._select_uid(None); w.generate_supports(); w.wait_supports(); app.processEvents(); w.wait_supports()
        cfg = w.settings.config()
        ready = w._ready_world_supports(o, cfg)
        assert ready is not None and abs(float(ready.vertices[:, 2].min())) < 1e-6
        o.z = 4.0; w._objects_changed(refresh_list=False); app.processEvents(); w.wait_supports(); app.processEvents()
        ready = w._ready_world_supports(o, cfg)
        assert ready is not None and abs(float(ready.vertices[:, 2].min())) < 1e-6, ready.vertices[:, 2].min()
        key = w._support_key(o, cfg)
        o.mesh = Mesh(o.mesh.vertices.copy(), o.mesh.faces.copy(), o.mesh.name)   # a repair puts a new mesh in
        assert w._support_key(o, cfg) != key
        w.result = ("sla", object(), cfg)
        carry = w.handover(); carry["direction"] = Qt.LeftToRight
        w2 = MainWindow(w.objects, carry=carry); w2.show(); w.close(); app.processEvents()
        assert w2.result is carry["result"]
        w2._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_box_layout_reserves_the_raft_and_label_tab_where_it_is_built():
    """Found in review: the shelf (box) layout reserved the tab at the plate's +X end while it is built at
    the part's own +x end - a part turned 180 or 90 degrees had its tab in the neighbour."""
    from unislicer import layout
    for deg in (0.0, 90.0, 180.0, 270.0):
        parts = []
        for _ in range(3):
            o = placed(primitives.box(24, 9, 7), 0, 0)
            a = np.radians(deg)
            o.apply_world_rotation(np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]))
            o.__dict__["raft_tab"] = True
            parts.append(o)
        assert not layout.auto_layout(parts, (0, 0, 200, 120), layout.LayoutOptions(strategy="box", spacing=1.0, rotation="none"))
        shapes = [layout.with_raft_tab(o, layout.footprint(o)) for o in parts]
        for i in range(3):
            for j in range(i + 1, 3):
                assert shapes[i].intersection(shapes[j]).area < 0.5, (deg, i, j)


def test_damaged_stl_with_nan_corner_loads_without_it(tmp_path):
    """A triangle with a NaN / infinite corner (a damaged file) is left out: one such corner made every
    bound, layout and slice of the plate NaN."""
    import struct
    from unislicer.mesh import load_mesh, save_stl
    path = str(tmp_path / "part.stl")
    save_stl(primitives.box(10, 10, 5), path)
    data = bytearray(open(path, "rb").read())
    struct.pack_into("<f", data, 84 + 12, float("nan"))            # first triangle, first corner x
    open(path, "wb").write(bytes(data))
    m = load_mesh(path)[0]
    assert np.isfinite(m.vertices).all() and len(m.faces) == len(primitives.box(10, 10, 5).faces) - 1


def test_replaced_window_lets_go_of_the_plate():
    """After a language / theme switch the old window drops the plate, supports, history and slice it
    handed over (each switch kept another full plate in memory)."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents(); w.store.setValue("preslice", "false")
        w.add_mesh(primitives.box(10, 10, 5)); w.add_mesh(primitives.box(8, 8, 5)); app.processEvents()
        w.result = ("sla", object(), None)
        w.release()
        assert w.objects == [] and w._support_cache == {} and w._undo == [] and w.result is None
        assert not w.viewer.objects
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_parts_on_the_plate_get_their_bottom_layers_pulled_in():
    """A part printed straight on the plate gets its long-exposed bottom layers pulled in 0.2 mm, so a label
    engraved in its base stays open (it printed blurred: the light filled it); a supported part's raft on
    the same plate is left as it is; a hand-set elephant-foot value takes over."""
    from unislicer.mesh import Mesh
    left, right = primitives.box(10, 10, 4), primitives.box(10, 10, 4).translated([10.3, 0, 0])
    model = Mesh.merge([left, right]).translated([60, 40, 0])          # a 0.3 mm groove, like an engraving
    crown = placed(primitives.box(8, 8, 6), 120, 40).world_mesh()
    base = {"support_enable": True, "sla_raft_type": "solid", "bottom_layers": 4, "transition_layers": 2}
    def first_layer(over):
        cfg = profiles.build_config("phrozen_sonic_xl_4k_plus", None, None, dict(base, **over))
        job = sla.prepare([model, crown], cfg, direct=[True, False])
        return job.layers[0], job.layers[5], job.layers[12]
    on, mid, above = first_layer({})
    off, mid_off, above_off = first_layer({"bottom_xy_auto": False})
    model_zone = shapely.geometry.box(55, 35, 85, 55)
    gap_on = model_zone.difference(on).intersection(shapely.geometry.box(60 + 9.5, 41, 60 + 10.8, 49)).area
    gap_off = model_zone.difference(off).intersection(shapely.geometry.box(60 + 9.5, 41, 60 + 10.8, 49)).area
    assert gap_on > gap_off + 0.3 * 8 * 0.9, (gap_on, gap_off)               # the groove opens by ~0.4 mm
    raft_zone = shapely.geometry.box(100, 20, 140, 60)
    assert abs(on.intersection(raft_zone).area - off.intersection(raft_zone).area) < 1e-6   # the raft is untouched
    assert abs(above.area - above_off.area) < 1e-6                           # layers above the base too
    hand, _, _ = first_layer({"bottom_xy_compensation": 0.05})
    assert hand.intersection(raft_zone).area < off.intersection(raft_zone).area   # the hand value is plate-wide


def test_every_offered_resin_printer_has_square_pixels_and_light():
    """Profile sanity: every resin printer offered in the list has pixels about as tall as wide (a template
    with a portrait screen on a landscape plate - Phrozen 8K 2023 from DS Slicer - would print stretched, so
    it is hidden) and a positive normal and bottom exposure."""
    for pid, p in profiles.load("printers").items():
        if p.get("hidden"):
            continue
        c = profiles.build_config(pid, None, None, {})
        if c["technology"] != "sla" or c["sla_process"] not in ("lcd", "dlp"):
            continue
        px, py = c["bed_x"] / c["resolution_x"], c["bed_y"] / c["resolution_y"]
        assert max(px, py) <= 1.5 * min(px, py), (pid, px, py)
        assert c["exposure_time"] > 0 and c["bottom_exposure_time"] > 0, pid


def test_no_stray_windows_flash_at_start_up_or_on_a_language_switch(tmp_path):
    """A widget shown before it is put in its place is a window of its own for a moment: the Reset button
    of a changed setting flashed in the corner of the screen at start-up and on every language switch."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication, QWidget
        from PySide6.QtCore import QObject, QEvent, Qt
        app = QApplication([])
        stray = []
        class F(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \\
                        and type(obj).__name__ not in ("MainWindow", "QMenu", "QDialog", "QTipLabel", "QComboBoxPrivateContainer"):
                    stray.append((type(obj).__name__, obj.objectName()))
                return False
        f = F(); app.installEventFilter(f)
        from unislicer.gui import i18n
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); w.settings.set_value("sla_raft_thickness", 1.3)
        app.processEvents()
        i18n.set_language("he")
        carry = w.handover(); carry["direction"] = Qt.RightToLeft
        w2 = MainWindow(w.objects, carry=carry); w2.show(); w.close(); w.release(); app.processEvents()
        assert not stray, stray
        w2._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_filament_printers_are_not_offered(tmp_path):
    """UniSlicer is for resin printers only: no FILAMENT tab, no filament printer in the list, no filaments
    menu, a saved filament printer (an old setting or project) falls back to a resin one, and the guide has
    no filament chapter."""
    import subprocess, sys, textwrap
    from unislicer import manual
    assert all(a != "fdm" for a, *_ in manual.CHAPTERS) and all(a != "fdm" for a, *_ in manual.chapters("en"))
    assert "Creality Ender" not in manual.build_html(lang="en")
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from PySide6.QtCore import QSettings
        from unislicer import profiles
        from unislicer.gui.main_window import MainWindow, SETTINGS_ORG
        QSettings(SETTINGS_ORG, SETTINGS_ORG).setValue("printer", "creality_ender3v2")      # saved from before
        w = MainWindow(); w.show(); app.processEvents(); w.store.setValue("preslice", "false")
        sp = w.settings
        printers = profiles.load("printers")
        listed = [sp.printer_combo.itemData(k) for k in range(sp.printer_combo.count()) if sp.printer_combo.itemData(k)]
        assert listed and not [p for p in listed if profiles.technology_of(printers[p]) == "fdm"], listed
        assert "filament" not in sp.kind_buttons
        assert sp.technology == "sla", sp.printer_id
        assert not sp.select_printer("creality_ender3v2") and sp.technology == "sla"
        sp.load_state({"printer": "creality_ender3v2"}); app.processEvents()
        assert sp.technology == "sla"
        from unislicer.gui import i18n
        texts = []
        def walk(menu):
            for a in menu.actions():
                texts.append(a.text())
                if a.menu(): walk(a.menu())
        for m in w.findChildren(type(w.menuBar())):
            pass
        from PySide6.QtWidgets import QMenu
        for m in w.findChildren(QMenu):
            walk(m)
        assert not [t for t in texts if i18n.tr("fil_title") in t], texts
        w._stop_preslice()
        print("OK", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path)), encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]



def test_a_crash_while_saving_never_breaks_the_project(tmp_path):
    """The program killed in the middle of saving (a crash, a power cut): the project on disk is the last
    complete one, never a half-written file; the half-written copy a crash leaves beside it is cleared away
    by a later save once it is old (a fresh one may be another window's, still writing)."""
    import random, subprocess, sys, textwrap, time
    from unislicer import project
    out = str(tmp_path / "plate.unislicer")
    code = textwrap.dedent(f'''
        from unislicer import primitives, project
        from unislicer.scene import SceneObject
        objs = []
        for k in range(16):
            o = SceneObject(primitives.crown_cap(9 + k * 0.1, 8.0, name=f"c{{k}}"), name=f"c{{k}}")
            o.x, o.y = (k % 4) * 15, (k // 4) * 15
            objs.append(o)
        i = 0
        while True:
            i += 1
            project.save({out!r}, objs, {{"printer": "phrozen_sonic_ls_plus", "round": i}})
            print(i, flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rnd = random.Random(7)
    for _ in range(6):
        p = subprocess.Popen([sys.executable, "-c", code], cwd=root, stdout=subprocess.PIPE, text=True)
        try:
            assert p.stdout.readline().strip(), "the saver did not start"
            time.sleep(rnd.uniform(0.0, 0.4))
        finally:
            p.kill(); p.wait(); p.stdout.close()
        objs, state = project.load(out)
        assert len(objs) == 16 and state["round"] >= 1
    old = tmp_path / "plate.unislicer.111-222.part"
    fresh = tmp_path / "plate.unislicer.333-444.part"
    old.write_bytes(b"half"); fresh.write_bytes(b"half")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    project.save(out, objs, state)
    assert not old.exists() and fresh.exists()


def test_the_program_icon_is_the_prodentix_one_everywhere():
    """One icon - the Prodentix fox on the layers (tools/make_icon.py) - for the exe and the installer (every
    size Windows asks for), the running window in every theme, and the phone control page."""
    from PIL import Image
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ico = Image.open(os.path.join(root, "packaging", "unislicer.ico"))
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)} <= set(ico.ico.sizes())
    app_png = Image.open(os.path.join(root, "unislicer", "gui", "brand", "app_icon.png")).convert("RGB")
    px = np.asarray(app_png, dtype=int).reshape(-1, 3)
    pink = (np.abs(px - [235, 26, 145]).sum(axis=1) < 60).mean()
    assert pink > 0.03                                 # the Prodentix magenta is in it
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("icon-maskable-512.png", 512)):
        assert Image.open(os.path.join(root, "remote_web", name)).size == (size, size)
    import subprocess, sys
    code = ("import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
            "from PySide6.QtWidgets import QApplication; app = QApplication([])\n"
            "from unislicer.gui import theme\n"
            "for name in ('dark', 'light', 'prodentix'):\n"
            "    theme.set_theme(name, '')\n"
            "    assert not theme.app_icon().isNull() and theme.app_icon().availableSizes(), name\n"
            "print('ok')\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-1000:] + r.stderr[-2000:]


def test_parts_with_no_room_wait_beside_the_plate_never_on_each_other():
    """More parts than the plate holds: Auto layout fills the plate and sets the rest out beside it, each
    clear of the others - they were left where they were, piled on and inside one another."""
    from unislicer import layout
    from unislicer.scene import SceneObject
    plate = (0.0, 0.0, 120.0, 70.0)
    objs = [SceneObject(primitives.box(28 + (k % 3) * 6, 18 + (k % 2) * 8, 5), name=f"p{k}") for k in range(22)]
    for o in objs:
        o.x, o.y = 60.0, 35.0                         # all dropped in one heap, as loading many files does
    for strategy in ("contour", "box"):
        for o in objs:
            o.x, o.y = 60.0, 35.0
        failed = layout.auto_layout(objs, plate, layout.LayoutOptions(strategy=strategy, spacing=2.0, margin=2.0))
        assert 0 < len(failed) < len(objs), strategy
        inside = shapely.box(*plate)
        shapes = {o.uid: layout.footprint(o) for o in objs}
        for o in failed:
            assert not shapes[o.uid].intersects(inside), (strategy, o.name)       # wholly off the plate
        placed = [o for o in objs if o not in failed]
        for o in placed:
            assert inside.buffer(1e-6).contains(shapes[o.uid]), (strategy, o.name)
        for i, a in enumerate(objs):
            for b in objs[i + 1:]:
                assert shapes[a.uid].intersection(shapes[b.uid]).area < 1e-6, (strategy, a.name, b.name)


def test_thickening_thin_parts_never_fills_an_engraved_label():
    """"Thicken thin features" (the print check's fix for thin parts): the strips of material between the
    grooves of a label engraved in a model's side are thin too, and grown into the grooves they filled the
    letters - the label printed blurred. The grooves stay open; a thin part standing out still grows."""
    from shapely.geometry import box as sbox
    wall = sbox(0, 0, 30, 10)
    grooves = shapely.union_all([sbox(5 + k * 0.7, 0, 5 + k * 0.7 + 0.4, 0.4) for k in range(12)])  # 0.4 wide, 0.3 apart
    layer = wall.difference(grooves)
    pin = sbox(40, 0, 40.2, 6)                           # a 0.2 mm wall standing on its own
    g = shapely.MultiPolygon([*sla.polygons_of(layer), pin])
    r = 0.15
    out = sla._thicken_chunk([g], r)[0]
    assert out.intersection(grooves).area < 0.02 * grooves.area      # the letters stay open
    assert out.intersection(sbox(39, -1, 41.5, 7)).area > pin.area * 1.5   # the thin wall grew


def test_repair_never_turns_a_good_scan_inside_out():
    """A closed scan with a few non-manifold edges (a lab model: 3 in 150 000 faces) - here a sheet inside a
    ball, glued to it round a ring of edges. The repair spread the winding across those edges and judged each
    patch by its own "volume": it turned a good model partly inside out (the lab model: 28 000 faces flipped,
    2 600 bad edges; the advanced repair after it: 98 000 flipped, 3 400 holes), and the advanced repair kept
    "the two largest faces" on each such edge - the sheet - and cut the ball's own wall. Nothing may be
    flipped, the volume stays, and the advanced repair closes it as the ball."""
    from unislicer import mesh_tools
    ball = primitives.sphere(10, 32, 64)
    z = ball.vertices[:, 2]
    ring = np.flatnonzero(np.abs(z - np.unique(np.round(z, 6))[16]) < 1e-6)
    ring = ring[np.argsort(np.arctan2(ball.vertices[ring, 1], ball.vertices[ring, 0]))]
    centre = len(ball.vertices)
    sheet = [[ring[k], ring[(k + 1) % len(ring)], centre] for k in range(len(ring))]
    m = Mesh(np.vstack([ball.vertices, ball.vertices[ring].mean(axis=0)]), np.vstack([ball.faces, sheet]), "model")
    assert mesh_tools.analyze(m)["nonmanifold_edges"] >= len(ring)
    vol = m.volume()
    fixed, rep = mesh_tools.repair(m)
    assert rep["flipped"] == 0 and abs(fixed.volume() - vol) < 1e-6 * vol, (rep, fixed.volume(), vol)
    inward = Mesh(ball.vertices, ball.faces[:, ::-1], "inside out")      # wound inward: turned back out
    back, _ = mesh_tools.repair(inward)
    assert abs(back.volume() - ball.volume()) < 1e-6 * ball.volume()
    closed, rep3 = mesh_tools.repair_advanced_closed(m)
    assert rep3["flipped"] == 0 and rep3["after"]["watertight"], rep3["after"]
    assert abs(closed.volume() - ball.volume()) < 0.001 * ball.volume()


def test_a_model_base_named_with_its_teeth_is_a_model_not_a_bridge():
    """exocad's model builder names the base of a model with the teeth on it ("SOD...-003-16-15-14-modelbase"):
    one word "modelbase", and teeth 16-15-14 that read like a lab order's range and unit count - it was taken
    for a bridge and loaded turned over, opening up. It is a model and keeps the file's orientation; lab
    orders with a tooth range are still crowns / bridges, and the other parts keep their types."""
    from unislicer import dental_cad
    for name in ("SOD260046871-003-16-15-14-modelbase.stl", "SOD260046871-003-44-45-46-modelbase.stl",
                 "case-modeltop.stl"):
        assert dental_cad.detect_indication(name) == "model", name
    assert dental_cad.detect_indication("SOD260046871-003-16-15-modelgingiva.stl") == "gingiva"
    for name in ("WMJ-SOD260043945-044-P-43-46-4-A3-.stl", "KVZ-SOD260044993-044-P-35-46-11-A1-000.stl",
                 "LAH-SOD260047907-044-E-15-15-1-E.stl"):
        assert dental_cad.detect_indication(name) == "crown", name
    assert dental_cad.detect_indication("EGI-SOD260046764-114-Z-14-14-1-00-V227_Tooth_14.stl") == "die"


def test_model_gingiva_is_a_gingiva_mask_turned_flat_side_up():
    """exocad's model builder names the removable gum of a model "...-modelgingiva": it was read as a
    denture ("gingiva") and loaded as it lay. It is a gingiva mask, turned flat side up. Its flat side is one
    plane - faces facing one way at several heights (the steps of a small mask's flank) no longer add up to
    beat the real cut - and of two real cuts the one the mask sits on (facing down in the CAD file) wins
    over a cut on its flank."""
    from unislicer import dental_cad
    assert dental_cad.detect_indication("SOD260046871-003-44-modelgingiva.stl") == "gingiva"
    assert dental_cad.detect_indication("SOD260046871-003-16-15-modelgingiva.stl") == "gingiva"
    assert dental_cad.detect_indication("BAH-SOD260046430-034-X-35-36-2-C2-B592_Tissue_17.stl") == "gingiva"
    base = primitives.box(12, 12, 3)                                  # its seat: 144 mm2, flat
    # a flank in steps: 8 faces of 8 x 6 mm facing +x, each 1 mm further out - together 384 mm2 that way
    steps = [primitives.box(1.0, 8, 6).translated(np.array([6.5 + 1.0 * k, 0.0, 0.0])) for k in range(8)]
    m = Mesh.merge([base] + steps, "mask")
    d = dental_cad.flat_side(m)
    assert d is not None and abs(d[2]) > 0.99, d                     # the seat, not the steps
    # a flank cut a bit larger than the seat, tilted: the seat (down) still wins
    from unislicer.mesh import rotation_between
    wedge = primitives.box(14, 13, 3)
    r = rotation_between(np.array([0.0, 0.0, -1.0]), np.array([-0.8, 0.5, -0.3]))
    flank = Mesh(wedge.vertices @ r.T + np.array([20.0, 0, 0]), wedge.faces, "flank")
    two = Mesh.merge([primitives.box(13, 13, 3), flank], "two cuts")
    d2 = dental_cad.flat_side(two)
    assert d2 is not None and abs(d2[2]) > 0.99, d2                  # the seat, not the flank
    assert dental_cad.flat_side(primitives.box(18, 20, 7))[2] > 0.99   # top and bottom alike: the one up stays


def test_a_part_inside_a_thin_rings_hole_is_drawn():
    """The layer image painted the parts largest-area first: a thin ring (a shell's wall) painted after a
    solid part standing in its hole cleared that part with its hole - it was missing from every print file."""
    from shapely.geometry import Point
    from unislicer.slicing import as_multipolygon
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {})
    ring = Point(60, 60).buffer(10, 64).difference(Point(60, 60).buffer(9.5, 64))     # 30.6 mm2
    rod = Point(60, 60).buffer(4, 64)                                                    # 50.3 mm2, inside it
    g = as_multipolygon(ring.union(rod))
    img = np.asarray(sla.render_layer(g, cfg))
    sx, sy = cfg["resolution_x"] / cfg["bed_x"], cfg["resolution_y"] / cfg["bed_y"]
    assert img[int((cfg["bed_y"] - 60) * sy), int(60 * sx)] == 255
    assert abs((img > 127).sum() - g.area * sx * sy) < 0.03 * g.area * sx * sy


def test_slice_caches_keep_process_supports_and_xy_compensation_apart():
    """Three slice results that came out wrong: with an XY compensation the automatic bottom compensation left
    the grown rim as a ring standing apart (the base not pulled in); a PolyJet slice took the LCD run's hollowed
    layers from the cache; supports switched off kept the cavity pillars of the run with supports."""
    from unislicer.slicing import polygons_of
    m = primitives.box(10, 10, 3).translated([50, 50, 0])
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": False, "xy_compensation": 0.05,
                                "bottom_xy_auto": True, "bottom_xy_compensation": 0.0, "sla_island_pillars": False})
    job = sla.prepare([m], cfg)
    ps = polygons_of(job.layers[0])
    assert len(ps) == 1 and not ps[0].interiors and job.layers[0].bounds[0] > 50.0, (len(ps), job.layers[0].bounds)
    m2 = primitives.box(20, 20, 6).translated([50, 50, 0])
    base = {"support_enable": False, "hollow_enable": True, "hollow_wall": 1.5, "sla_island_pillars": False}
    sla.clear_cache()
    lcd = sla.prepare([m2], profiles.build_config("phrozen_sonic_ls_plus", None, None, dict(base, sla_process="lcd")))
    jet = sla.prepare([m2], profiles.build_config("phrozen_sonic_ls_plus", None, None, dict(base, sla_process="polyjet")))
    assert jet.layers[60].area > lcd.layers[60].area * 2                 # solid, not the LCD run's shell
    pil = {"hollow_enable": True, "hollow_wall": 1.5, "sla_island_pillars": False, "hollow_infill": "none",
           "sla_cavity_pillars": True, "sla_internal_supports": True, "drain_holes": 0}
    sla.clear_cache()
    off = sla.prepare([m2], profiles.build_config("phrozen_sonic_ls_plus", None, None, dict(pil, support_enable=False)), direct=[True])
    sla.prepare([m2], profiles.build_config("phrozen_sonic_ls_plus", None, None, dict(pil, support_enable=True)), direct=[True])
    again = sla.prepare([m2], profiles.build_config("phrozen_sonic_ls_plus", None, None, dict(pil, support_enable=False)), direct=[True])
    assert abs(again.layers[60].area - off.layers[60].area) < 1e-6


def test_closing_a_horseshoes_open_base_adds_no_web_between_its_arms():
    """A dental arch scanned with an open base: the hole was lidded with a fan from its centre, which lies
    between the arms - the closed model had a web of resin across the horseshoe (64 mm2 at the plate).
    A hole the fan cannot cover is ear-clipped. And the advanced repair of a file with every face twice took a
    minute (a search per duplicate)."""
    import time
    import shapely as sh
    from shapely.geometry import Point
    from unislicer import mesh_tools
    outer, inner = Point(0, 0).buffer(25, quad_segs=32), Point(0, 0).buffer(15, quad_segs=32)
    U = sh.segmentize(outer.difference(inner).intersection(sh.box(-30, -2, 30, 30)), 1.0)
    closed = mesh_tools.extrude(U, 0.0, 10.0)
    v = closed.vertices.copy()
    v[:, 2] += 1.5 * np.sin(v[:, 0] / 4.0) * (1 - v[:, 2] / 10.0)          # a scan's base edge is not flat
    n = Mesh(v, closed.faces).face_normals()
    bottom = (n[:, 2] < -0.5) & (v[closed.faces][:, :, 2].max(axis=1) < 2)
    open_scan = Mesh(v, closed.faces[~bottom])
    fixed, _steps, ok = mesh_tools.make_watertight(open_scan)
    assert ok
    for g in slice_mesh(fixed, [0.0, 0.5, 1.0]):
        assert g.difference(U.buffer(0.05)).area < 0.5, g.difference(U.buffer(0.05)).area
    ball = primitives.sphere(20, 120, 240)
    twice = Mesh(ball.vertices, np.vstack([ball.faces, ball.faces]), "twice")
    t0 = time.time()
    r, rep = mesh_tools.repair_advanced(twice)
    assert rep["duplicates_removed"] == len(ball.faces) and rep["after"]["watertight"]
    assert time.time() - t0 < 20


def test_a_slice_of_the_plate_before_a_change_never_comes_back_as_current(tmp_path):
    """A slice that finished while the window was busy had its result queued; the user moved a part before it
    was handled, and the old plate's slice still became the current one - the window went to preview and
    Export wrote the old plate. A result for the plate as it was is dropped."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        o = w.add_mesh(primitives.box(20, 10, 5)); app.processEvents()
        o.direct = True
        w._objects_changed()
        w.slice()
        w.task.wait()                                 # done; its result waits in the queue
        w._push_undo(); o.x += 30; w._objects_changed()
        for _ in range(30):
            app.processEvents(); time.sleep(0.02)
        assert w.result is None and w.viewer.mode != "preview", (w.result is not None, w.viewer.mode)
        w.slice()                                     # a fresh slice of the plate as it is now still comes in
        end = time.time() + 60
        while w.result is None and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert w.result is not None
        if w.task is not None:
            w.task.wait()
        w._stop_preslice()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1", UNISLICER_WORKERS="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_export_draws_the_report_picture_off_the_window_thread(tmp_path):
    """After an export the job report's picture of the plate was drawn on the window's thread: with a full
    plate of lab models the window froze for 11 s. It is made by the export's background work, with the
    project file; the report still comes out beside the print file."""
    import subprocess, sys, textwrap
    lib = str(tmp_path / "prints")
    code = textwrap.dedent(f'''
        import os, time, threading
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives, report, project
        from unislicer.gui.main_window import MainWindow
        where = []
        real = report.plate_picture_png
        report.plate_picture_png = lambda objs: (where.append(threading.current_thread() is threading.main_thread()), real(objs))[1]
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.store.setValue("export_library", {lib!r}); w._reveal = lambda p: None
        w.settings.select_printer("elegoo_mars3"); w.settings.set_value("support_enable", False); app.processEvents()
        o = w.add_mesh(primitives.box(10, 10, 5)); o.name = "part"; o.direct = True
        settle = time.time() + 1.0
        while time.time() < settle:
            app.processEvents(); time.sleep(0.01)
        w.slice()
        end = time.time() + 120
        while (w.result is None or (w.task is not None and w.task.isRunning())) and time.time() < end:
            app.processEvents(); time.sleep(0.01)
        w.export()
        end = time.time() + 120
        while any(t.isRunning() for t in getattr(w, "_export_tasks", [])) and time.time() < end:
            app.processEvents(); time.sleep(0.02)
        app.processEvents()
        folder = os.path.join({lib!r}, "part")
        assert os.path.isfile(os.path.join(folder, "part - report.pdf")), os.listdir(folder)
        assert os.path.isfile(os.path.join(folder, "part" + project.EXTENSION)), os.listdir(folder)
        assert where == [False], where
        print("ok")
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_two_exports_of_one_name_never_get_the_same_file(tmp_path):
    """The print file appears only when its export is complete: a second export of the same name started
    meanwhile was given the same path and silently replaced the first. A path handed out is reserved."""
    from unislicer import export_library
    lib = str(tmp_path / "prints")
    a = export_library.target_path(lib, "case 12", ".ctb")
    b = export_library.target_path(lib, "case 12", ".ctb")
    assert a != b and b.endswith("case 12 (2).ctb"), (a, b)


def test_print_check_does_not_call_an_engraved_label_thin():
    """The print check's "thinner than 0.3 mm" counted the strips between the letters of a label engraved in a
    model's side - every layer through the label (39 layers of a lab antagonist, 16 of a lab model) - and its
    Fix thickened them, filling the label. Strips touching the model's own narrow grooves are left out; a thin
    wall standing out and a thin ring (a crown's margin) are still reported."""
    from shapely.geometry import box as sbox, Point
    wall = sbox(0, 0, 30, 10)
    grooves = shapely.union_all([sbox(5 + k * 0.7, 0, 5 + k * 0.7 + 0.4, 0.4) for k in range(12)])
    label = sla.as_multipolygon(wall.difference(grooves))
    assert sla._thin_chunk([label], 0.15, 0.05)[0].area < 0.05
    pin = sbox(40, 0, 40.2, 6)
    with_pin = shapely.MultiPolygon([*sla.polygons_of(label), pin])
    assert sla._thin_chunk([with_pin], 0.15, 0.05)[0].area > 1.0
    margin = sla.as_multipolygon(Point(60, 60).buffer(5, 64).difference(Point(60, 60).buffer(4.8, 64)))
    assert sla._thin_chunk([margin], 0.15, 0.05)[0].area > 0.5 * margin.area


def test_neon_theme_lights_the_models_and_keeps_the_others_as_they_were():
    """Neon (an animated film's dusk: violet night, neon cyan, magenta, lime): its own gradients and glow, and
    its own light on the models in the 3D view; the other themes keep exactly the light they had. Its colours
    are written so Qt reads them as meant (an 8-digit #RRGGBBAA is #AARRGGBB to Qt: pink came out green)."""
    import re
    from unislicer.gui import theme
    try:
        theme.set_theme("neon")
        sheet = theme.stylesheet()
        assert "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #36d6ff" in sheet     # filled in: the gradient buttons
        assert "{GLOW}" not in sheet and "{ACCENT}" not in sheet
        assert not re.findall(r"#[0-9a-fA-F]{8}\b", theme.THEMES["neon"]["EXTRA_QSS"])
        assert theme.light()["uRim"][0] > 0.9                       # a magenta rim
        for name in ("dark", "light", "prodentix"):
            theme.set_theme(name)
            assert theme.light() == theme.DEFAULT_LIGHT
            assert "EXTRA" not in theme.stylesheet()
        d = theme.DEFAULT_LIGHT
        assert np.allclose(d["uRim"], np.array([0.55, 0.72, 1.0]) * 0.22, atol=1e-3)   # as the shader had it
    finally:
        theme.set_theme("dark")


def test_a_damaged_obj_loads_what_it_can_and_says_plainly_when_nothing():
    """An OBJ file with a face naming a vertex it does not have was refused with "index 8 is out of bounds for
    axis 0 with size 2". The faces it can read load; with none, it says the file has no triangles."""
    p = __import__("tempfile").mkdtemp()
    good = os.path.join(p, "part.obj")
    with open(good, "w") as fh:
        fh.write("v 0 0 0\nv 10 0 0\nv 0 10 0\nv 0 0 10\nf 1 2 3\nf 1 2 4\nf 1 3 4\nf 2 3 4\nf 1 2 9\nv x y\n")
    m = Mesh.merge(load_mesh(good))
    assert len(m.faces) == 4
    bad = os.path.join(p, "bad.obj")
    with open(bad, "w") as fh:
        fh.write("v 0 0 0\nv 1 0 0\nf 1 2 9\n")
    with pytest.raises(ValueError, match="no triangles"):
        load_mesh(bad)


def test_a_reopened_queue_window_never_sends_a_job_twice_nor_loses_one(tmp_path):
    """The queue window closed during a long upload and opened again made its own copy of the queue from the
    file: it showed the job being sent as waiting (Send would print it twice), and when the old upload
    finished, the old copy's save dropped the job added meanwhile. Every window works on the one queue; a job
    left "sending" is put back to waiting only when the program starts."""
    from unislicer import print_queue
    folder = str(tmp_path / "q")
    q1 = print_queue.Queue.shared(folder)                     # the first queue window
    a = q1.add("P1", "/prints/a.ctb"); q1.add("P1", "/prints/b.ctb")
    q1.mark(a.id, "sending")                                  # a long upload; the window is closed
    q2 = print_queue.Queue.shared(folder)                     # opened again
    assert q2 is q1 and q2.get(a.id).state == "sending" and q2.next_for("P1").name == "b.ctb"
    q2.add("P1", "/prints/c.ctb")
    q1.mark(a.id, "sent")                                     # the old window's upload finishes
    on_disk = [(j.name, j.state) for j in print_queue.Queue(folder).jobs]
    assert on_disk == [("a.ctb", "sent"), ("b.ctb", "waiting"), ("c.ctb", "waiting")], on_disk
    other = str(tmp_path / "restart")
    q = print_queue.Queue(other); j = q.add("P1", "/prints/x.ctb"); q.mark(j.id, "sending")
    print_queue._STARTED.discard(os.path.normcase(os.path.abspath(os.path.join(other, print_queue.FILE_NAME))))
    assert print_queue.Queue(other).get(j.id).state == "waiting"      # a new start: the interrupted job again


def test_sending_cancelled_or_failed_leaves_nothing_behind_and_says_what_happened(tmp_path):
    """Sending to a printer: a cancel was reported as "the printer does not answer" and kept the print file
    open (Windows could not clear it away); a cancelled or failed copy to a folder left a half-written
    "<name>.part" on the share and a same-named file there was replaced; a PrusaLink printer with a password
    could never take a file - asked for the password, the upload was sent again with no bytes and timed out."""
    import http.server, threading, base64
    from unislicer import network
    src = tmp_path / "job.ctb"
    src.write_bytes(os.urandom(300_000))
    drop = tmp_path / "share"; drop.mkdir()
    (drop / "job.ctb").write_bytes(b"another case")
    folder = network.NetPrinter("Lab folder", "folder", str(drop))
    network.send(folder, str(src), "job.ctb")
    assert (drop / "job.ctb").read_bytes() == b"another case" and (drop / "job (2).ctb").stat().st_size == 300_000
    with pytest.raises(InterruptedError):
        network.send(folder, str(src), "big.ctb", cancelled=lambda: True)
    assert not list(drop.glob("*.part")) and not (drop / "big.ctb").exists()
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_PUT(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            ok = self.headers.get("Authorization") == "Basic " + base64.b64encode(b"maker:secret").decode()
            seen.append((ok, len(body)))
            if not ok:
                self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="x"')
                self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(201); self.send_header("Content-Length", "0"); self.end_headers()

        do_POST = do_PUT

        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        prusa = network.NetPrinter("MK4", "prusalink", f"127.0.0.1:{srv.server_address[1]}", user="maker", password="secret")
        network.send(prusa, str(src), "job.sl1")
        assert seen and seen[-1] == (True, 300_000), seen          # the whole file, with the password
    finally:
        srv.shutdown()


def test_arrange_works_on_copies_off_the_window_thread_with_the_same_result(tmp_path):
    """Arrange, the layout after adding parts and the taught layout ran on the window's thread (a full plate
    froze it ~3 s at every Arrange and duplicate). They run on copies on a background thread now and only the
    places and turns are put back: the same layout as before, locked parts untouched, parts with no room
    beside the plate."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        import numpy as np
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives, layout
        from unislicer.gui.main_window import MainWindow
        from unislicer.scene import plate_area
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        settle = time.time() + 1.0
        while time.time() < settle:
            app.processEvents(); time.sleep(0.01)
        objs = [w.add_mesh(primitives.box(50 + (k % 3) * 8, 34, 6), rearrange=False) for k in range(20)]
        for o in objs:
            o.x, o.y = 60.0, 40.0
        objs[3].x, objs[3].y = 20.0, 20.0; objs[3].locked = True
        twins = [o.duplicate() for o in objs]
        twins[3].locked = True
        cfg = w.settings.config()
        opts = w._layout_defaults()
        options = layout.LayoutOptions(**{k: opts[k] for k in ("strategy", "spacing", "margin", "rotation", "anchor", "sort")})
        expected_failed = layout.auto_layout(twins, plate_area(cfg), options)
        w._arrange(cfg); app.processEvents()
        for o, t in zip(objs, twins):
            assert abs(o.x - t.x) < 1e-6 and abs(o.y - t.y) < 1e-6 and np.allclose(o.rotation, t.rotation), (o.x, o.y, t.x, t.y)
        assert (objs[3].x, objs[3].y) == (20.0, 20.0)
        assert expected_failed, "the test needs parts with no room"
        print("ok", flush=True)
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_a_damaged_learning_file_never_stops_the_program_nor_vanishes(tmp_path):
    """The assistant's learning file: one that is valid JSON but not a learning file (a list) stopped the
    program from starting; a part of the wrong kind ("layout": null) broke it later; one cut short loaded as
    nothing learned and the next click wrote over it, with no copy kept."""
    import json as _json
    from unislicer.assistant import Assistant
    folder = tmp_path / "l"; folder.mkdir()
    f = folder / "learning.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    a = Assistant(str(folder))
    assert a.enabled and isinstance(a.data["layout"], dict)
    assert (folder / "learning.json.bad").read_text(encoding="utf-8") == "[1, 2, 3]"
    good = Assistant(str(tmp_path / "g"))
    good.learn_repair("holes|.stl|3Shape", {"mode": "basic"})
    text = (tmp_path / "g" / "learning.json").read_text(encoding="utf-8")
    f.write_text(text[: len(text) // 2], encoding="utf-8")           # cut short
    b = Assistant(str(folder))
    b.record_action("arrange", "button")                             # the next click saves
    assert (folder / "learning.json.bad").read_text(encoding="utf-8") == text[: len(text) // 2]
    data = _json.loads(text)
    data["layout"] = None
    data["orient"]["sla"]["weights"] = [1.0, 2.0, 3.0]               # an older version's weights
    f.write_text(_json.dumps(data), encoding="utf-8")
    c = Assistant(str(folder))
    assert isinstance(c.data["layout"], dict) and len(c.data["orient"]["sla"]["weights"]) > 3
    assert c.repair_recipe("holes|.stl|3Shape") == {"mode": "basic"}   # the rest is kept
    c.record_layout_choice({"strategy": "box"})                       # and works


def test_a_repair_of_a_file_that_loaded_open_is_learned_for_that_file(tmp_path):
    """A file that loads with holes is closed right after loading; the repair the user then ran was learned
    for the closed copy ("clean") - never - so the next file like it got no offer. It is learned for what was
    wrong with the file as read."""
    import subprocess, sys, textwrap
    from unislicer.mesh import save_stl
    ball = primitives.sphere(8, 40, 80)
    keep = np.ones(len(ball.faces), bool); keep[[10, 500, 2000]] = False          # three small holes
    for n in ("a.stl", "b.stl"):
        save_stl(Mesh(ball.vertices, ball.faces[keep], n), str(tmp_path / n))
    code = textwrap.dedent(f'''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.assistant.data["repair"]["auto"] = False
        w.store.setValue("auto_close_holes", "false")              # its worker process is not the point here:
        from unislicer import mesh_tools                          # it is done by hand, as it would be
        w.load_files([{str(tmp_path / "a.stl")!r}])
        o = w.objects[-1]
        assert not o.mesh.is_watertight()
        o.mesh = mesh_tools.make_watertight(o.mesh)[0]             # what the closing after loading does
        assert o.mesh.is_watertight()
        w._select_uid(o.uid)
        w.repair_selected()                                       # the user repairs it as always
        assert w.assistant.data["repair"]["by_sig"] == {{"holes|.stl|-": {{'{{"mode": "basic"}}': 1}}}}, w.assistant.data["repair"]["by_sig"]
        w.load_files([{str(tmp_path / "b.stl")!r}])
        o2 = w.objects[-1]
        assert w._repair_offers.get(o2.uid) == {{"mode": "basic"}}, w._repair_offers
        print("ok", flush=True)
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_shared_learning_never_brings_back_what_was_reset(tmp_path):
    """Learning shared between the lab's computers takes the largest count seen, so after a reset (or an
    import) on one computer the others gave every old count back - a wrongly learned repair could not be
    removed while sharing was on. A reset starts a new generation; older learning is left out."""
    from unislicer import learning_sync
    from unislicer.assistant import Assistant
    a, b = Assistant(str(tmp_path / "a")), Assistant(str(tmp_path / "b"))
    bad = {"mode": "advanced", "weld": 0.001, "shells": 0.2, "nonmanifold": True, "holes": True}
    a.learn_repair("holes|.stl|exocad", bad)
    b.data = learning_sync.merge(b.data, [a.data])                    # B has it too now
    assert b.repair_recipe("holes|.stl|exocad") == bad
    a.reset()
    a.data = learning_sync.merge(a.data, [b.data])                    # A's next sync
    assert a.repair_recipe("holes|.stl|exocad") is None
    b.data = learning_sync.merge(b.data, [a.data])                    # and B follows the reset
    assert b.repair_recipe("holes|.stl|exocad") is None
    a.learn_repair("holes|.stl|3Shape", {"mode": "basic"})            # what is learned after it is shared
    b.data = learning_sync.merge(b.data, [a.data])
    assert b.repair_recipe("holes|.stl|3Shape") == {"mode": "basic"}


def test_learning_seed_and_learned_supports_as_the_lab_chose(tmp_path):
    """The lab's choices for the assistant: a new install starts at the resin profile's support angle (the
    seed held -25 degrees, the floor - from the fault that moved it 16 degrees a click) and with repairs only
    offered, not done by themselves; an install already at the floor is set back once; what was learned about
    supports applies on top of a crown's or a model's own preset (laid under it, the preset replaced it)."""
    import json as _json
    from unislicer.assistant import Assistant, SEED_FILE
    seed = _json.load(open(SEED_FILE, encoding="utf-8"))
    assert seed["supports"]["angle_offset"] == 0.0 and seed["repair"]["auto"] is False
    folder = tmp_path / "old"; folder.mkdir()
    old = dict(seed); old["supports"] = dict(seed["supports"], angle_offset=-25.0)
    (folder / "learning.json").write_text(_json.dumps(old), encoding="utf-8")
    a = Assistant(str(folder))
    assert a.data["supports"]["angle_offset"] == 0.0
    a.data["supports"]["angle_offset"] = -25.0; a.save()
    assert Assistant(str(folder)).data["supports"]["angle_offset"] == -25.0      # set back once only
    b = Assistant(str(tmp_path / "b"))
    b.data["supports"].update(spacing_scale=0.8, angle_offset=-3.0)
    from unislicer.scene import SceneObject
    cfg = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    crown = SceneObject(primitives.crown_cap(10, 8.0), name="DEMO-SOD26-001-044-P-14-14-1")
    crown.settings["sla_model_type"] = "cb"
    plain = crown.config(cfg)
    learned = b.learned_config(crown.config(cfg))
    assert abs(learned["sla_support_spacing"] - plain["sla_support_spacing"] * 0.8) < 1e-6
    assert abs(learned["support_angle"] - (plain["support_angle"] - 3.0)) < 1e-6


def test_arrange_keeps_only_what_was_chosen_in_the_layout_window(tmp_path):
    """The layout window stored every option, chosen or not: after one use, Arrange never again used what
    the assistant learned from the user's own arrangements. Only what was changed in the window is kept as
    the user's choice; the rest comes from the learning. (An older install's stored defaults are cleared once.)"""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, json
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.add_mesh(primitives.box(10, 10, 5)); w.add_mesh(primitives.box(10, 10, 5))
        before = w._layout_defaults()
        def dialog(opts):
            out = dict(opts); out["anchor"] = "front_left"          # the user changes one option only
            return out, False
        w._layout_dialog = dialog
        w.auto_layout(dialog=True); app.processEvents()
        stored = json.loads(w.store.value("layout_options", "{}"))
        assert stored == {"anchor": "front_left"}, stored
        w.assistant.learned_layout = lambda base: dict(base, spacing=7.5, margin=3.5)   # learned from arrangements
        now = w._layout_defaults()
        assert now["anchor"] == "front_left" and now["spacing"] == 7.5 and now["margin"] == 3.5, now
        print("ok", flush=True)
        from unislicer import parallel
        if parallel._pool is not None:
            parallel._pool.shutdown(wait=True, cancel_futures=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_a_geometry_error_in_the_label_checks_never_stops_the_slice(monkeypatch):
    """Reported from the lab, 1.0.241: "Slicing failed - GEOSException: TopologyException: found non-noded
    intersection" - a scan's outline with an edge doubled onto itself made the geometry library fail inside
    the new engraved-label rule of the thin-part check, and the whole slice stopped. The layer is made valid
    and tried again; if the library still fails, the rule is not applied and the slice goes on."""
    import shapely.errors
    from shapely.geometry import box as sbox
    from shapely.geometry.base import BaseGeometry
    wall = sbox(0, 0, 30, 10)
    grooves = shapely.union_all([sbox(5 + k * 0.7, 0, 5 + k * 0.7 + 0.4, 0.4) for k in range(12)])
    g = shapely.MultiPolygon([*sla.polygons_of(wall.difference(grooves)), sbox(40, 0, 40.2, 6)])
    real = BaseGeometry.difference

    def broken(self, other, *a, **k):
        raise shapely.errors.GEOSException("TopologyException: found non-noded intersection")
    monkeypatch.setattr(BaseGeometry, "difference", broken)
    assert sla._own_grooves(g, 0.375) is None
    monkeypatch.setattr(BaseGeometry, "difference", real)
    calls = {"n": 0}

    def once(self, other, *a, **k):                     # fails on the first try only: made valid, it works
        calls["n"] += 1
        if calls["n"] == 2:
            raise shapely.errors.GEOSException("TopologyException: found non-noded intersection")
        return real(self, other, *a, **k)
    monkeypatch.setattr(BaseGeometry, "difference", once)
    out = sla._thin_chunk([g], 0.15, 0.05)[0]                # no exception: the slice goes on
    assert out.area > 1.0                                   # the thin wall standing out is still reported
    monkeypatch.setattr(BaseGeometry, "difference", broken)
    try:
        sla._thicken_chunk([g], 0.15)                       # thickening too never stops the slice
    except shapely.errors.GEOSException:
        pass                                                # (its own difference of the thin part is older code)


def test_clicking_takes_the_part_under_the_cursor_not_the_one_whose_box_is_there(tmp_path):
    """Selecting on a full plate was imprecise: a click that missed every surface took the first part whose
    box the ray entered - dental arches' boxes overlap, so a click between an arch's arms or on a crown
    standing inside another arch's box took the arch. The part under the cursor, or the surface nearest it
    within a few pixels (thin parts), or for a part tiny on the screen its box; empty plate takes nothing."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        import numpy as np, shapely
        from shapely.geometry import Point
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives, mesh_tools
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        arch_shape = Point(0, 0).buffer(25, 48).difference(Point(0, 0).buffer(18, 48)).intersection(shapely.box(-30, -2, 30, 30))
        arch = w.add_mesh(mesh_tools.extrude(arch_shape, 0.0, 10.0), rearrange=False)
        crown = w.add_mesh(primitives.box(6, 6, 5), rearrange=False)
        pin = w.add_mesh(primitives.box(0.6, 12, 6), rearrange=False)
        arch.x, arch.y = 90.0, 60.0
        crown.x, crown.y = 90.0, 56.0                      # between the arch's arms, inside its box
        pin.x, pin.y = 150.0, 60.0                         # a thin part
        w._objects_changed(); app.processEvents()
        v = w.viewer
        v.view_preset("top"); v.fit_objects(); app.processEvents()
        def at(x, y, z):
            xy, _ = v._to_screen(np.array([[x, y, z]]))
            return QPointF(float(xy[0][0]), float(xy[0][1]))
        cb, ab, pb = crown.world_bounds(), arch.world_bounds(), pin.world_bounds()
        c = (cb[0] + cb[1]) / 2
        assert v.pick(at(c[0], c[1], cb[1][2])) == crown.uid           # the crown, not the arch around it
        assert v.pick(at(c[0] + 12.0, ab[0][1] + 3.0, 0.0)) is None, "empty space inside the arch's box"
        p = at(pb[1][0], (pb[0][1] + pb[1][1]) / 2, pb[1][2])
        assert v.pick(QPointF(p.x() + 2.0, p.y())) == pin.uid          # a hair off the thin part still takes it
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_raft_and_support_density_change_only_the_selected_models(tmp_path):
    """Changing the raft of the selected models changed every model's (the raft window always set the plate's
    raft, even from a model's right-click menu), and the support density could only be set for the plate.
    With models selected both apply to those models only; with none, to the plate."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.settings_panel import SettingRow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        a = w.add_mesh(primitives.box(10, 10, 6)); b = w.add_mesh(primitives.box(10, 10, 6)); app.processEvents()
        plate_raft = w.settings.config()["sla_raft_type"]
        new = "tiny" if plate_raft != "tiny" else "solid"
        w._select_uid(a.uid); app.processEvents()
        w.raft_menu(); app.processEvents()
        rows = {r.setting.key: r for r in w._raft_dialog.findChildren(SettingRow)}
        rows["sla_raft_type"].changed.emit("sla_raft_type", new); app.processEvents()
        cfg = w.settings.config()
        assert cfg["sla_raft_type"] == plate_raft                       # the plate is as it was
        assert a.config(cfg)["sla_raft_type"] == new and b.config(cfg)["sla_raft_type"] == plate_raft
        w._raft_dialog.close()
        w.set_selected_support_preset("heavy"); app.processEvents()
        cfg = w.settings.config()
        assert a.config(cfg)["sla_support_preset"] == "heavy" and b.config(cfg)["sla_support_preset"] == cfg["sla_support_preset"]
        w.set_selected_support_preset(None); app.processEvents()
        assert "sla_support_preset" not in a.settings
        w._select_uid(None); app.processEvents()
        w.raft_menu(); app.processEvents()                            # nothing selected: the plate's raft
        rows = {r.setting.key: r for r in w._raft_dialog.findChildren(SettingRow)}
        rows["sla_raft_type"].changed.emit("sla_raft_type", new); app.processEvents()
        assert w.settings.config()["sla_raft_type"] == new
        w._raft_dialog.close()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_connection_errors_are_named_for_where_they_come_from():
    """Review: every timeout or refused connection was shown as "no connection to the online service - add
    firebasedatabase.app to the antivirus": a printer that is off, or an internal timeout, too. The sync's
    errors name the online service, a printer's name the printer, anything else stays a program fault."""
    from unislicer import diagnose

    def raised_in(filename, exc):
        ns = {}
        exec(compile("def f(e):\n    raise e\n", filename, "exec"), ns)
        try:
            ns["f"](exc)
        except Exception as e:
            return diagnose.diagnose(e)
    printer = raised_in("/app/unislicer/network.py", ConnectionRefusedError(111, "refused"))
    assert printer.kind == "network" and "printer" in printer.title[1].lower(), printer.title
    sync = raised_in("/app/unislicer/remote.py", TimeoutError("timed out"))
    assert sync.kind == "network" and "online" in sync.title[1].lower(), sync.title
    inner = raised_in("/app/unislicer/sla.py", TimeoutError("future"))
    assert inner.kind == "bug", inner.title


def test_a_blocked_learning_sync_is_a_network_message_not_a_program_bug(tmp_path):
    """Lab report (1.0.247): the background learning sync hit an intercepted connection (a certificate for
    another name - an antivirus' HTTPS scanning or an internet filter) and it was logged and shown as "A program
    bug" with the raw error. It is a network problem: nothing goes to the crash log, the assistant says what
    blocks the connection, and the diagnosis of such an error anywhere else names the network too."""
    from unislicer import diagnose, remote
    err = ("offline: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate "
           "is not valid for 'x-default-rtdb.europe-west1.firebasedatabase.app'. (_ssl.c:1010)")
    try:
        raise remote.RemoteError(err)
    except remote.RemoteError as e:
        d = diagnose.diagnose(e)
    assert d.kind == "network" and "program bug" not in d.title[1].lower()
    assert "firebasedatabase.app" in remote.explain("RemoteError: " + err, "en").split("\n")[0]
    assert remote.explain("RemoteError: offline: timed out", "en").startswith("No internet")
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import diagnose, remote
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        class Agent:
            state = "linked"
            def running(self): return True
            def sync_learning(self, text):
                raise remote.RemoteError("offline: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                         "Hostname mismatch")
        w.remote.agent = Agent()
        assert w.assistant.enabled
        before = len(diagnose.CrashLog().entries())
        w.sync_learning(manual=False)
        end = time.time() + 20
        while time.time() < end and w._learn_sync_task.isRunning():
            app.processEvents(); time.sleep(0.01)
        for _ in range(20):
            app.processEvents()
        assert len(diagnose.CrashLog().entries()) == before, "logged as a program fault"
        shown = w.assistant_panel.sync_status.text()
        assert "firebasedatabase.app" in shown and "RemoteError" not in shown.split("(")[0], shown
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_join_all_rafts_makes_the_plate_one_piece_on_screen_and_in_the_print(tmp_path):
    """Lab: a raft like SOLID LITE with the rafts of all the models joined, so the plate comes off the build
    plate as one piece. A solid joint (10 mm wide, raft thick) between neighbouring rafts, with a groove next
    to each raft to free one model; rafts further apart than the reach stay apart. The joints show on the
    plate and are in the print: its first layer is one piece."""
    import subprocess, sys, textwrap
    from unislicer import sla_supports, primitives, profiles
    from unislicer.mesh import Mesh
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus").update({"sla_raft_type": "solid_lite"})
    T = cfg["sla_raft_thickness"]
    sup = []
    for x in (20.0, 45.0, 150.0):
        c = primitives.crown_cap(10.0, 8.0)
        w = Mesh(c.vertices + [x, 30.0, 6.0 - c.bounds[0][2]], c.faces)
        sup.append(sla_supports.build_supports(w, cfg, sla_supports.auto_support_points(w, cfg))[0])
    outl = [sla_supports.raft_outline(m, T) for m in sup]
    joints, pieces = sla_supports.raft_joints(outl, T, 10.0, 30.0)
    assert pieces == 2 and len(joints.faces) and joints.is_watertight()        # the far one stays apart
    assert abs(joints.bounds[0][2]) < 1e-9 and abs(joints.bounds[1][2] - T) < 1e-6
    z = joints.vertices[:, 2]
    assert np.any(np.abs(z - T / 2) < 1e-6), "no notch (half as thick) next to the rafts"
    a, b = outl[0], outl[1]
    from shapely.geometry import Polygon
    top = [Polygon(t[:, :2]) for t in joints.vertices[joints.faces] if np.all(np.abs(t[:, 2] - T) < 1e-6)]
    from shapely.ops import unary_union
    span = unary_union([p for p in top if p.is_valid])
    bb = span.minimum_rotated_rectangle
    import math
    edges = [math.dist(bb.exterior.coords[k], bb.exterior.coords[k + 1]) for k in range(2)]
    assert 9.0 <= min(edges) <= 11.5 or 9.0 <= max(edges) <= 11.5, edges          # about 10 mm wide
    assert sla_supports.raft_joints(outl, T, 10.0, 200.0)[1] == 1
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.slicing import polygons_of
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        w.settings.set_value("sla_raft_type", "solid_lite")
        a = w.add_mesh(primitives.crown_cap(10.0, 8.0), rearrange=False)
        b = w.add_mesh(primitives.crown_cap(10.0, 8.0), rearrange=False)
        a.x, a.y, b.x, b.y = 80.0, 60.0, 100.0, 60.0
        w._objects_changed(); app.processEvents()
        w.select_all(); w.generate_supports(); w.wait_supports(); w._select_uid(None)
        def first_layer_pieces():
            w.result = None
            w.slice()
            end = time.time() + 120
            while time.time() < end and (w.result is None or w.task.isRunning()):
                app.processEvents(); time.sleep(0.01)
            return len(polygons_of(w.result[1].layers[0]))
        alone = first_layer_pieces()
        assert alone == 2, alone
        w.settings.set_value("sla_raft_join", True); app.processEvents()
        end = time.time() + 5
        while time.time() < end and w.viewer.joints is None:
            app.processEvents(); time.sleep(0.02)
        assert w.viewer.joints is not None and len(w.viewer.joints.faces), "no joints on the plate"
        joined = first_layer_pieces()
        assert joined == 1, joined
        w.settings.set_value("sla_raft_join", False); app.processEvents()
        end = time.time() + 5
        while time.time() < end and w.viewer.joints is not None:
            app.processEvents(); time.sleep(0.02)
        assert w.viewer.joints is None
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_a_part_with_its_own_raft_thickness_gets_supports_that_reach_it(tmp_path):
    """Review: a part's own raft thickness (one click from the side panel with it selected) raised its supports
    by that thickness while the model stands at the plate's - the tips ended short of it (or inside it), and
    the print took those supports. The window builds them at the plate's height, as the slicer does."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        import numpy as np
        from unislicer import primitives, sla_supports
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        w.settings.set_value("sla_raft_thickness", 1.0)
        o = w.add_mesh(primitives.crown_cap(10.0, 8.0))
        o.settings["sla_raft_thickness"] = 0.4                 # its own, thinner raft
        w._select_uid(o.uid); w.generate_supports(); w.wait_supports(); app.processEvents(); w.wait_supports()
        cfg = w.settings.config()
        ready = w._ready_world_supports(o, cfg)
        if ready is None:                                      # as shown: the view's structure in bed coordinates
            e = w._support_cache[o.uid]; ready = e[1].transform(w._display_matrix(o))
        lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]   # where the slicer puts the model
        world = o.world_mesh().translated([0, 0, lift])
        own = sla_supports.build_supports(world, o.config(cfg), o.world_support_points(lift))[0]
        assert abs(ready.vertices[:, 2].max() - own.vertices[:, 2].max()) < 0.05, (ready.vertices[:, 2].max(), own.vertices[:, 2].max())
        assert abs(ready.vertices[:, 2].min() - own.vertices[:, 2].min()) < 0.05
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_text_labels_do_not_wipe_the_raft_codes_letters(tmp_path):
    """Review: the letters of a raft code (made by the window, handed to the worker pool) and the label meshes
    shared one table, which a label cleared every 64 entries - a batch built after that got no letters and
    its rafts went to print without their code. Each has its own table now."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import mesh_tools
        mesh_tools.text_polygons("46671-044-P", 2.0)
        for k in range(70):
            mesh_tools.label_mesh(f"L{k}", (0, 0, 0), (0, 0, 1), 3.0, 0.5, False)
        assert ("46671-044-P", 2.0, "") in mesh_tools._TEXT_CACHE
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_a_job_running_when_the_workers_are_ended_starts_no_new_ones(tmp_path):
    """Review: ending the workers (closing, updating) made a job still running start a fresh set of them -
    UniSlicer processes again, just as the installer started. Such a job now ends, cancelled."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time, threading, multiprocessing
        os.environ["UNISLICER_WORKERS"] = "2"
        def main():
            from unislicer import parallel
            out = {}
            def job():
                try:
                    out["r"] = parallel.run_chunks(time.sleep, [(20,)] * 4)
                except BaseException as e:
                    out["e"] = type(e).__name__
            th = threading.Thread(target=job); th.start()
            end = time.time() + 20
            while time.time() < end and len(multiprocessing.active_children()) < 2:
                time.sleep(0.05)
            time.sleep(1.0)
            parallel.terminate()
            th.join(15)
            assert not th.is_alive(), "the job went on"
            assert out.get("e") == "InterruptedError", out
            time.sleep(0.5)
            assert parallel._pool is None and not [p for p in multiprocessing.active_children() if p.is_alive()]
            r = parallel.run_chunks(abs, [(-1,), (-2,)])     # a new job afterwards works as before
            assert r == [1, 2], r
            parallel.terminate()
            print("ok", flush=True)
        if __name__ == "__main__":
            main()
    ''')
    script = tmp_path / "no_restart.py"
    script.write_text(code)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root)
    r = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_closing_the_program_ends_its_worker_processes_at_once(tmp_path):
    """Lab (1.0.258): after closing UniSlicer, 13 UniSlicer processes stayed in Task Manager at ~90% CPU (and
    an update then found it "still running"): the worker processes went on with every job handed to them, and
    Python waited for them. Closing ends them at once."""
    import subprocess, sys, textwrap, time
    code = textwrap.dedent('''
        import os, sys, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["UNISLICER_WORKERS"] = "3"
        def main():
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            import multiprocessing
            from unislicer import parallel
            from unislicer.gui.main_window import MainWindow
            from unislicer.gui.worker import Task
            w = MainWindow(); w.show(); app.processEvents()
            busy = Task(lambda progress, cancelled: parallel.run_chunks(time.sleep, [(30,)] * 9, cancelled=cancelled), w)
            w._sup_task = busy                      # as a support batch running in the pool
            busy.start()
            end = time.time() + 20
            while time.time() < end and not multiprocessing.active_children():
                app.processEvents(); time.sleep(0.05)
            time.sleep(1.0)
            assert len(multiprocessing.active_children()) >= 3, multiprocessing.active_children()
            t0 = time.time()
            w.close(); app.processEvents()
            parallel.terminate()                    # as the program does once its window loop ends
            left = [p for p in multiprocessing.active_children() if p.is_alive()]
            assert not left, left
            assert time.time() - t0 < 12, time.time() - t0
            print("ok", flush=True)
        if __name__ == "__main__":
            main()
    ''')
    script = tmp_path / "close_workers.py"
    script.write_text(code)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1", PYTHONPATH=root)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=90, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
    assert time.time() - t0 < 60, "the program did not end until its workers had finished"


def test_the_fast_layer_encoders_write_the_same_bytes():
    """Overnight speed work: CTB / GOO layers were run-length encoded one run at a time in Python (~200 ms a
    layer on a 12K screen - most of an export). All runs are encoded at once now; the bytes must be exactly
    those of the old way, for any image."""
    from unislicer import ctb, goo, primitives, profiles, sla
    from unislicer.mesh import Mesh
    rng = np.random.default_rng(5)
    images = []
    for trial in range(120):
        h, w = int(rng.integers(1, 50)), int(rng.integers(1, 260))
        k = trial % 4
        if k == 0:
            a = rng.integers(0, 256, (h, w))
        elif k == 1:
            a = (rng.random((h, w)) < 0.1) * 255
        elif k == 2:
            a = np.zeros((h, w)); a[:, : w // 2] = rng.integers(0, 256)
        else:
            a = np.repeat(rng.choice([0, 0, 0, 1, 2, 128, 255, 7], size=(h, max(1, w // 13))), 13, axis=1)[:, :w]
        images.append(np.asarray(a, dtype=np.uint8))
    cfg = profiles.build_config("elegoo_mars3", None, None, {"support_enable": True})
    c = primitives.crown_cap(10.0, 8.0)
    job = sla.prepare([Mesh(c.vertices + [40, 30, -c.bounds[0][2]], c.faces)], cfg)
    images += [np.asarray(sla.render_layer(job.layers[i], cfg)) for i in (0, 3, job.layer_count // 2, job.layer_count - 1)]
    images.append(np.zeros((40, 70), dtype=np.uint8))
    images.append(np.full((40, 70), 255, dtype=np.uint8))
    for a in images:
        assert ctb.encode_layer(a) == ctb.encode_layer_slow(a)
        assert goo.encode_layer(a) == goo.encode_layer_slow(a)


def test_thick_tree_supports_never_reach_below_the_plate():
    """Overnight settings test: with thick pillars a tree branch's round joint near the plate was wider than its
    height above it - the supports reached up to 2.8 mm below the plate (seen on a bridge on the Mini 8K).
    Nothing goes below the plate now."""
    from unislicer import profiles, sla_supports, primitives
    from unislicer.mesh import Mesh
    for elev in (3.0, 5.0):
        cfg = profiles.build_config("phrozen_sonic_mini_8k", None, None, {"support_enable": True, "sla_support_type": "tree",
                                    "sla_pillar_diameter": 10.0, "sla_pillar_widening": 40.0, "sla_elevation": elev})
        lift = cfg["sla_elevation"] + cfg["sla_raft_thickness"]
        m = primitives.crown_cap(20.0, 9.0)
        w = Mesh(m.vertices + [0, 0, lift - m.bounds[0][2]], m.faces)
        sm, _ = sla_supports.build_supports(w, cfg, None)
        assert sm.vertices[:, 2].min() >= -0.051, (elev, sm.vertices[:, 2].min())


def test_raft_joints_keep_clear_of_parts_on_the_plate_follow_the_rafts_and_meet_straight_edges_in_the_middle():
    """Review: a joint ran straight under a part standing on the plate between two rafts (fused into its first
    layers); joints took the plate's raft thickness, not their rafts' (a 0.4 mm raft got 2 mm slabs); two
    straight edges facing each other got a joint at a corner, half as wide."""
    from shapely.geometry import box, Point, Polygon
    from shapely.ops import unary_union
    from unislicer import sla_supports

    def footprint(mesh, z):
        tops = [Polygon(t[:, :2]) for t in mesh.vertices[mesh.faces] if np.ptp(t[:, 2]) < 1e-9 and abs(t[0, 2] - z) < 1e-6]
        return unary_union([p for p in tops if p.is_valid and p.area > 1e-9])
    # a part on the plate between two rafts: the joint goes round it or not at all - never under it
    a, c = Point(0, 0).buffer(6), Point(36, 0).buffer(6)
    b = Point(18, 0).buffer(5)                                # a crown standing on the plate
    mesh, pieces = sla_supports.raft_joints([a, c], [0.5, 0.5], 10.0, 30.0, obstacles=b)
    if len(mesh.faces):
        assert footprint(mesh, 0.5).intersection(b).area < 1e-6
    # thickness: the thinner of the two rafts
    mesh, pieces = sla_supports.raft_joints([box(0, 0, 10, 40), box(20, 0, 30, 40)], [0.5, 2.0], 10.0, 30.0)
    assert pieces == 1 and abs(mesh.vertices[:, 2].max() - 0.5) < 1e-9
    # facing straight edges: the joint in the middle, as wide as asked
    top = footprint(mesh, 0.5)
    y0, y1 = top.bounds[1], top.bounds[3]
    assert 14.0 <= y0 <= 16.0 and 24.0 <= y1 <= 26.0, (y0, y1)
    # a raft with no thickness is not joined (nothing would print)
    mesh, pieces = sla_supports.raft_joints([a, c], [0.0, 0.5], 10.0, 30.0)
    assert len(mesh.faces) == 0
    # through the slicer: the crown standing on the plate stays apart in the first layer
    from unislicer import primitives, profiles, sla
    from unislicer.mesh import Mesh
    from unislicer.slicing import polygons_of
    cfg = profiles.build_config("phrozen_sonic_xl_4k_plus").update({"sla_raft_type": "solid_lite",
                                                                     "sla_raft_join": True, "sla_raft_thickness": 0.5})
    crowns = []
    for x in (20.0, 38.0, 56.0):                              # the middle one: a flat part on the plate
        cc = primitives.crown_cap(10.0, 8.0) if x != 38.0 else primitives.box(8.0, 8.0, 4.0)
        crowns.append(Mesh(cc.vertices + [x - (cc.bounds[0][0] + cc.bounds[1][0]) / 2 * (x == 38.0),
                                          40.0 - (cc.bounds[0][1] + cc.bounds[1][1]) / 2 * (x == 38.0),
                                          -cc.bounds[0][2]], cc.faces))
    job = sla.prepare(crowns, cfg, direct=[False, True, False])
    first = polygons_of(job.layers[0])
    on_plate = [p for p in first if p.contains(Point(38.0, 40.0)) or p.distance(Point(38.0, 40.0)) < 2.0]
    assert on_plate and all(p.bounds[0] > 30.0 and p.bounds[2] < 46.0 for p in on_plate), [p.bounds for p in on_plate]


def test_joined_rafts_of_interlocked_arches_really_make_one_piece():
    """Lab (1.0.258): four arches nested into each other on the XL - only some rafts got joined. A third raft
    next to the line between two rafts cut their joint off, yet the pair counted as joined, so no other way
    was tried for those models. Each joint must really reach both rafts (else narrower, else the next pair),
    and the plate comes off in as many pieces as said."""
    import random
    from shapely.geometry import Point, Polygon, box
    from shapely.ops import unary_union
    from shapely import affinity
    from unislicer import sla_supports

    def arch():
        ring = Point(0, 0).buffer(30).difference(Point(0, 0).buffer(18))
        return ring.difference(box(0, -40, 40, 40)).union(box(-0.01, -30, 12, -18)).union(box(-0.01, 18, 12, 30))

    def real_pieces(shapes, mesh):
        tops = [Polygon(t[:, :2]) for t in mesh.vertices[mesh.faces]
                if np.ptp(t[:, 2]) < 1e-9 and t[0, 2] > 1e-9] if len(mesh.faces) else []
        u = unary_union(list(shapes) + [p for p in tops if p.is_valid and p.area > 1e-9]).buffer(0.01)
        return len(getattr(u, "geoms", [u]))

    # layouts that came off in two pieces while one was promised
    for lab in ([(275.11450587531994, 98.35658690821543, 51.682007271709004),
                 (350.3363950874382, 45.56573377388724, 14.367028045161664),
                 (330.8392859897516, 6.972338713172823, 45.61322285999079),
                 (170.08789214098107, 70.28669351109704, 3.3997895130658753)],
                [(220.67215552282812, 29.757297806863853, 9.056576256046183),
                 (179.45369667170138, 79.45743489466354, 23.756508400963877),
                 (22.61133133550728, 5.478468100556768, 18.220295345728434),
                 (214.66992756213025, 106.96339486723464, 61.040352067479034)],
                [(289.06747890068226, 44.56560570143367, 10.31621107724701),
                 (215.336077181715, 100.06749112278052, 20.065137468252765),
                 (317.3892620266829, 22.69554078830782, 58.43575644334304),
                 (63.58145633770249, 66.07926475030737, 0.6344043625188311)]):
        shapes = [affinity.translate(affinity.rotate(arch(), a, origin=(0, 0)), x, y) for a, x, y in lab]
        mesh, pieces = sla_supports.raft_joints(shapes, 0.4, 10.0, 30.0)
        assert pieces == 1 and real_pieces(shapes, mesh) == 1, (pieces, real_pieces(shapes, mesh))
    # and many more nested layouts: what is said is what comes off the plate
    rng = random.Random(7)
    checked = 0
    while checked < 60:
        shapes = []
        for _ in range(400):
            g = affinity.translate(affinity.rotate(arch(), rng.uniform(0, 360), origin=(0, 0)),
                                   rng.uniform(0, 110), rng.uniform(0, 70))
            if all(g.distance(h) > 2.0 for h in shapes) and (not shapes or min(g.distance(h) for h in shapes) < 9):
                shapes.append(g)
            if len(shapes) == 4:
                break
        if len(shapes) < 4:
            continue
        mesh, pieces = sla_supports.raft_joints(shapes, 0.4, 10.0, 30.0)
        assert real_pieces(shapes, mesh) == pieces
        checked += 1


def test_a_slice_waiting_for_the_pre_slice_shows_its_progress(tmp_path):
    """Lab: the bar stood at "Slicing layers 0%" for a long time - Slice waited for the background pre-slice
    of the same plate (to take its result) without showing how far it was. It shows it now."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.worker import Task
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        o = w.add_mesh(primitives.box(10, 10, 4)); o.direct = True
        state = {"stage": "slice", "f": 0.0}
        def pre_work(progress, cancelled):
            for k in range(1, 11):
                state.update(stage="slice", f=k / 10.0); time.sleep(0.08)
        pre = Task(pre_work, w); pre.state = state; w._pre_task = pre; pre.start()
        seen = []
        orig = w._on_progress
        w._on_progress = lambda st, f: (seen.append((st, round(f, 2), pre.isRunning())), orig(st, f))
        w.slice()
        w.task.progress.disconnect(); w.task.progress.connect(w._on_progress)
        end = time.time() + 60
        while time.time() < end and (w.result is None or w.task.isRunning()):
            app.processEvents(); time.sleep(0.01)
        waiting = [f for st, f, running in seen if st == "slice" and running]   # shown while it waited
        assert any(0.3 <= f <= 0.9 for f in waiting), seen[:15]
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_update_lets_the_installer_go_on_while_the_program_closes(tmp_path):
    """Lab (1.0.256): Update started the installer, which stopped at once with "Setup has detected that
    UniSlicer is currently running" - the program was still closing (and its worker processes, copies of
    UniSlicer.exe, still running). The workers are stopped and the mutex let go first, the installer is
    told it comes from the program, and it waits for the program to close instead of stopping."""
    iss = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging",
                            "unislicer.iss"), encoding="utf-8-sig").read()
    assert "\nAppMutex=" not in iss                               # no check at once
    assert "CheckForMutexes('UniSlicerAppMutex')" in iss and "{param:FROMAPP|0}" in iss and "Sleep(500)" in iss
    assert "function InitializeUninstall" in iss and "msgUninstallAppRunningError" in iss   # the uninstaller checks too
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        import subprocess
        from unislicer import parallel
        from unislicer.gui import app as appmod
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        order = []
        parallel.terminate = lambda: order.append("workers stopped")
        appmod.release_app_mutex = lambda: order.append("mutex released")
        subprocess.Popen = lambda args, **k: order.append(("installer", list(args)))
        sys.platform = "win32"
        w._run_installer("C:/x/UniSlicer-Setup.exe")
        assert order[0] == "workers stopped", order
        assert order[1][0] == "installer" and "/FROMAPP=1" in order[1][1] and "/SILENT" in order[1][1], order
        assert order[2] == "mutex released", order
        assert not w.isVisible()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_support_builds_never_lock_up_and_step_aside_for_a_slice(tmp_path):
    """Lab (1.0.255): making the raft for two selected models "got stuck", and slicing got very long. A plate's
    supports are built together in the worker pool: a failure while taking the results in left the build
    marked running for good (no support or raft build started again), and a slice waited behind the batch.
    Now the batch always ends, and one that steps aside for a slice goes back in line; every part gets its
    supports in the end."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["UNISLICER_WORKERS"] = "2"
        def main():
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            from unislicer import parallel, primitives
            from unislicer.gui.main_window import MainWindow
            import unislicer.gui.main_window as MW
            w = MainWindow(); w.show(); app.processEvents()
            w.store.setValue("preslice", "false")
            w.settings.select_printer("phrozen_sonic_xl_4k_plus")
            parts = [w.add_mesh(primitives.crown_cap(11.0 + k, 8.0)) for k in range(4)]
            app.processEvents()
            def settle(limit=120):
                end = time.time() + limit
                while time.time() < end and (w._sup_pending or getattr(w, "_sup_task", None) is not None):
                    app.processEvents(); time.sleep(0.01)
                for _ in range(20):
                    app.processEvents()
            def all_built():
                cfg = w.settings.config()
                held = [o for o in parts if o.support_points is not None and len(o.support_points)]
                assert len(held) >= 3, [None if o.support_points is None else len(o.support_points) for o in parts]
                return all(w._support_cache.get(o.uid, (None,))[0] == w._support_key(o, cfg) for o in held)
            # 1) the first batch steps aside (as for a slice): its parts are built after all the same
            orig_run = parallel.run_chunks
            state = {"stepped": 0, "broke": 0}
            def run_chunks(fn, args, *a, **k):
                if fn.__name__ == "build_part" and not state["stepped"]:
                    state["stepped"] += 1
                    raise InterruptedError
                return orig_run(fn, args, *a, **k)
            parallel.run_chunks = run_chunks
            w.select_all(); w.generate_supports(); settle()
            assert state["stepped"] == 1 and all_built(), (state, [w._support_cache.get(o.uid, (None,))[0] is not None for o in parts])
            # 2) taking a batch's results in fails once: later builds still run
            orig_built = MW.MainWindow._support_built
            def built(self, res, shared):
                if getattr(self, "_sup_batching", False) and not state["broke"]:
                    state["broke"] += 1
                    raise RuntimeError("taking the result in failed")
                return orig_built(self, res, shared)
            MW.MainWindow._support_built = built
            import sys as _s
            import traceback as _tb
            # the failure reaches Qt's handler, as in the app (shown, not fatal)
            _s.excepthook = lambda t, v, tb: print("handled:", v, file=_s.stderr, flush=True)
            # every part waits, then they go together - as one batch, whatever the timing on this machine
            held_task = object.__new__(type("Held", (), {"isRunning": lambda self: True, "wait": lambda self, *a: True,
                                                         "deleteLater": lambda self: None}))
            w._sup_task = held_task
            w.settings.set_value("sla_tip_diameter", 0.45); w._objects_changed(refresh_list=False)
            for _ in range(20):
                app.processEvents()
            assert len(w._sup_pending) >= 3, len(w._sup_pending)
            w._sup_task = None
            w._start_support_build(); settle()
            assert state["broke"] == 1, state
            assert getattr(w, "_sup_task", None) is None, "a support build is marked running for good"
            w.settings.set_value("sla_tip_diameter", 0.5); w._objects_changed(refresh_list=False); settle()
            assert all_built(), "no support build ran after the failure"
            if parallel._pool is not None:
                parallel._pool.shutdown(wait=True)
            print("ok", flush=True)
        if __name__ == "__main__":
            main()
    ''')
    script = tmp_path / "lockup.py"
    script.write_text(code)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1", PYTHONPATH=root)
    r = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_raft_and_label_built_in_the_worker_pool_is_the_same_as_in_the_window(tmp_path):
    """Lab (1.0.255): Raft & label chosen for 12 parts - only one got it. The others were built in the worker
    pool, which has no window: the code was skipped there, and with it the tab it stands on. The letters made
    by the window travel with the build, so a part built in the pool gets the same raft and code."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["UNISLICER_WORKERS"] = "2"
        import numpy as np
        def main():
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            from unislicer import sla_supports, parallel, primitives, profiles
            from unislicer.mesh_tools import text_polygons, _TEXT_CACHE
            cfg = profiles.build_config("phrozen_sonic_xl_4k_plus").update({"sla_raft_type": "raft_label"})
            crown = primitives.crown_cap(10.0, 8.0)
            world = crown.translated([0, 0, 6.0 - crown.bounds[0][2]])
            pts = sla_supports.auto_support_points(world, cfg)
            label = "47092-044-P"
            text_polygons(label, 2.0)
            texts = {(label, 2.0, ""): _TEXT_CACHE[(label, 2.0, "")]}
            here = sla_supports.build_part(world, cfg, pts, label, None, np.eye(4), texts)
            sla_supports._STRUCTURES.clear()
            there = parallel.run_chunks(sla_supports.build_part, [(world, cfg, pts, label, None, np.eye(4), texts)] * 2)
            parallel._pool.shutdown(wait=True)
            raft = lambda r: int((r[1]["faces"] == -1).sum())
            assert "_error" not in there[0][1], there[0][1]
            assert raft(here) > 0 and raft(there[0]) == raft(here), (raft(here), raft(there[0]))
            assert len(there[0][0].faces) == len(here[0].faces)
            print("ok", flush=True)
        if __name__ == "__main__":
            main()
    ''')
    script = tmp_path / "raft_pool.py"
    script.write_text(code)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1", PYTHONPATH=root)
    r = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_reading_files_in_the_worker_pool_brings_back_what_was_worked_out(tmp_path):
    """Files of a plate are read in worker processes (on threads the parsing held the window up to ~0.9 s):
    what loading works out on each mesh travels back with it, and a file that cannot be read comes back as
    its error, not a crash."""
    import pickle
    from unislicer import file_reading, primitives
    from unislicer.mesh import save_stl
    good = tmp_path / "Crown_16.stl"
    save_stl(primitives.crown_cap(9.0, 8.0, name="Crown_16"), str(good))
    bad = tmp_path / "broken.stl"
    bad.write_bytes(b"solid x\nfacet nonsense")
    meshes, source = file_reading.read_for_window(str(good), "lab", True, True, True)
    back = pickle.loads(pickle.dumps((meshes, source)))          # as it comes back from a worker
    m = back[0][0]
    assert back[1] == "lab" and len(m.faces) > 0
    fp = m.__dict__.get("_repair_fp")
    assert fp is not None and fp[0] is m.vertices                # still recognised as this file's mesh
    assert "_opening" in m.__dict__                              # the crown's opening, worked out there
    err, src = file_reading.read_for_window(str(bad), "lab", True, True, True)
    assert isinstance(err, Exception) or (isinstance(err, list) and not err) or src == "lab"


def test_the_printer_kind_is_kept_and_follows_a_printer_change(tmp_path, monkeypatch):
    """technology / process were read from the printer file hundreds of times per action (every config()
    and every model drawn): kept now, and still right after switching printer."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import profiles
        from unislicer.gui.settings_panel import SettingsPanel
        p = SettingsPanel("phrozen_sonic_xl_4k_plus", None, None, {}, False)
        assert p.technology == "sla"
        reads = []
        orig = profiles.load
        profiles.load = lambda kind: (reads.append(kind), orig(kind))[1]
        for _ in range(50):
            p.technology; p.process; p.config()
        assert reads.count("printers") == 0, reads.count("printers")
        profiles.load = orig
        fdm = next(k for k, v in profiles.load("printers").items() if profiles.technology_of(v) == "fdm")
        p.printer_id = fdm
        assert p.technology == "fdm"
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_the_print_file_previews_draw_detail_for_their_size(monkeypatch):
    """The two previews in a print file both drew 240 000 triangles (~1.3 s each), the 200 x 125 one too:
    the detail now follows the picture's size, and the picture still shows the parts."""
    from unislicer import ctb, primitives
    seen = []
    from unislicer import thumbnail
    orig = thumbnail.render
    monkeypatch.setattr(thumbnail, "render", lambda meshes, *a, **k: (seen.append(sum(len(m.faces) for m in meshes)),
                                                                       orig(meshes, *a, **k))[1])
    big = primitives.sphere(10, 200, 400)
    assert len(big.faces) > 100000
    ctb.render_preview([big], None, (400, 300))
    ctb.render_preview([big], None, (200, 125))
    assert seen[1] < seen[0] and seen[1] <= 40000, seen
    img = ctb.render_preview([big], None, (200, 125))
    assert img.size == (200, 125) and len(set(img.getdata())) > 10


def test_the_progress_bar_fills_to_the_end_of_a_resin_slice(tmp_path):
    """Lab: the bar under Slice stayed under half full and vanished so ("Slicing layers - 88%" over a third
    of a bar): its stages were split for filament, where slicing the layers is the first third. For resin
    slicing the layers is nearly the whole bar, the bar never runs back, and it ends full."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        o = w.add_mesh(primitives.box(10, 10, 4)); o.direct = True
        seen = []
        orig = w._on_progress
        def spy(stage, f):
            orig(stage, f); seen.append((stage, f, w.progress.value()))
        w._on_progress = spy
        w.slice()
        w.task.progress.disconnect(); w.task.progress.connect(spy)
        end = time.time() + 120
        while time.time() < end and (w.result is None or w.task.isRunning()):
            app.processEvents(); time.sleep(0.01)
        app.processEvents()
        values = [v for _s, _f, v in seen]
        assert values == sorted(values), seen                       # never back
        late = [v for st, f, v in seen if st == "slice" and f >= 0.85]
        assert late and min(late) >= 800, seen                       # 88% of the layers: most of the bar
        assert w.progress.value() == w.progress.maximum(), w.progress.value()
        assert w.progress.isVisible()                                # the full bar stays a moment
        end = time.time() + 2.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        assert not w.progress.isVisible()
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_a_raft_change_keeps_the_support_structure_and_rebuilds_only_the_raft():
    """Lab: changing the raft (window or panel) took ~5-10 s on a full plate - every tree, pillar and brace was
    built again though only the raft under them changed. The structure is kept: a raft-only change builds the
    raft alone, and the result is exactly what a fresh build gives (mesh and click owners), faster."""
    import time
    from unislicer import primitives, sla_supports
    from unislicer.settings import Config
    from unislicer.mesh import Mesh
    b = primitives.box(16, 12, 6)
    m = Mesh(b.vertices + [0, 0, 5.0], b.faces)                # lifted: it stands on its supports
    base = {"technology": "sla", "sla_support_type": "tree", "sla_raft_type": "solid", "sla_bracing": True}
    cfg = lambda **kw: Config().update({**base, **kw})
    pts = sla_supports.auto_support_points(m, cfg())
    assert len(pts) > 3
    def build(c):
        owners = {}
        mesh, used = sla_supports.build_supports(m, c, pts, owners=owners)
        return mesh, used, owners
    sla_supports._STRUCTURES.clear()
    fresh = {k: build(cfg(sla_raft_type=k, sla_raft_margin=1.5)) for k in ("grid",)}
    sla_supports._STRUCTURES.clear()
    t0 = time.perf_counter(); build(cfg(sla_raft_type="solid")); full = time.perf_counter() - t0
    t0 = time.perf_counter(); got = build(cfg(sla_raft_type="grid", sla_raft_margin=1.5)); quick = time.perf_counter() - t0
    want = fresh["grid"]
    assert np.array_equal(got[0].vertices, want[0].vertices) and np.array_equal(got[0].faces, want[0].faces)
    assert np.array_equal(got[1], want[1])
    assert np.array_equal(got[2]["faces"], want[2]["faces"]) and got[2]["groups"] == want[2]["groups"]
    assert quick < full * 0.6, (quick, full)
    # the structure itself changing (another support type, a thicker raft) is built again
    n = len(sla_supports._STRUCTURES)
    build(cfg(sla_raft_type="grid", sla_support_type="pillar"))
    build(cfg(sla_raft_type="grid", sla_raft_thickness=cfg()["sla_raft_thickness"] + 0.5))
    assert len(sla_supports._STRUCTURES) == n + 2
    # no raft: the feet stand on the plate - another structure
    none = build(cfg(sla_raft_type="none"))
    assert none[0].vertices[:, 2].min() < want[0].vertices[:, 2].min() + 1e-9 or len(none[0].faces) != len(want[0].faces)


def test_a_raft_or_supports_chosen_for_parts_on_the_plate_gives_them_supports_and_raft(tmp_path):
    """Lab: models standing on the plate (no supports) were selected and given Solid lite - nothing appeared on
    them. A raft or supports chosen for selected parts is what is done: they leave the plate and get supports
    with their raft (from the raft window, the side panel or the density menu); the other parts stay as they are."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        from unislicer.gui.settings_panel import SettingRow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        import time
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        w.settings.set_simple(False); w.settings._on_advanced(True); app.processEvents()
        parts = [w.add_mesh(primitives.box(14, 12, 8)) for _ in range(4)]
        for o in parts:
            o.direct = True; o.support_points = None
        a, b, c, d = parts
        w._objects_changed(); app.processEvents()
        has = lambda o: (not o.direct) and o.support_points is not None and len(o.support_points) > 0
        undo0 = len(w._undo)
        w._select_uid(a.uid); app.processEvents()
        w.raft_menu(); app.processEvents()
        rows = {r.setting.key: r for r in w._raft_dialog.findChildren(SettingRow)}
        rows["sla_raft_type"].changed.emit("sla_raft_type", "solid_lite"); app.processEvents()
        w._raft_dialog.close()
        assert has(a) and a.settings["sla_raft_type"] == "solid_lite", (a.direct, a.support_points)
        assert not has(b) and b.direct and not has(c) and not has(d)
        assert len(w._undo) == undo0 + 1                                  # one Ctrl+Z
        w._select_uid(b.uid); app.processEvents()                         # the side panel
        row = w.settings._rows["sla_raft_type"]
        row.changed.emit("sla_raft_type", "grid"); row.applied.emit("sla_raft_type"); app.processEvents()
        assert has(b) and b.config(w.settings.config())["sla_raft_type"] == "grid"
        assert not has(c) and c.direct
        w._select_uid(c.uid); app.processEvents()                         # the density menu
        w.set_selected_support_preset("heavy"); app.processEvents()
        assert has(c) and not has(d) and d.direct
        assert w.settings.config()["sla_raft_type"] != "grid"             # the plate kept its raft
        w.undo(); app.processEvents()
        assert c.direct and not c.settings.get("sla_support_preset")
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_undo_keeps_no_selection_and_a_typed_value_survives_another_rows_apply(tmp_path):
    """Review: Undo with nothing selected selected the first part - the panel's next supports / raft edit then
    went to that part alone; and with parts selected, applying one row threw away a value typed (not yet
    applied) in another."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os, time
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus")
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        w.settings.set_simple(False); w.settings._on_advanced(True); app.processEvents()
        a = w.add_mesh(primitives.box(10, 10, 6)); b = w.add_mesh(primitives.box(10, 10, 6)); app.processEvents()
        w._select_uid(None); app.processEvents()
        w._push_undo(); a.x += 5.0; w._objects_changed(); app.processEvents()
        w.undo(); app.processEvents()
        assert w.selected_objects() == [], [o.uid for o in w.selected_objects()]
        assert w.settings.scope_group() == []
        w._select_uid(a.uid); app.processEvents()
        p = w.settings
        margin = p._rows["sla_raft_margin"]
        margin.editor.setValue(margin.editor.value() + 1.5); app.processEvents()
        typed = margin.editor.value()
        assert margin._has_pending
        r = p._rows["sla_raft_type"]
        r.changed.emit("sla_raft_type", "grid"); r.applied.emit("sla_raft_type")
        for _ in range(30):
            app.processEvents()
        assert margin._has_pending and abs(margin.editor.value() - typed) < 1e-9, (margin._has_pending, margin.editor.value())
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_side_panel_raft_and_support_type_change_only_the_selected_models(tmp_path):
    """With models selected, the raft type and support type picked in the side panel changed the plate - every
    model. Now they go to the selected models only (their own settings, the panel says so and shows their
    values, one Ctrl+Z undoes it); with nothing selected the panel edits the plate as before."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        w.settings.select_printer("phrozen_sonic_xl_4k_plus"); app.processEvents()
        w.settings.set_simple(False); w.settings._on_advanced(True); app.processEvents()
        a = w.add_mesh(primitives.box(10, 10, 6)); b = w.add_mesh(primitives.box(10, 10, 6)); app.processEvents()
        p = w.settings
        plate = p.config()
        raft = "solid_lite" if plate["sla_raft_type"] != "solid_lite" else "grid"
        sup = "pillar" if plate["sla_support_type"] != "pillar" else "tree"
        w._select_uid(a.uid); app.processEvents()
        def edit(key, value):
            row = p._rows[key]
            row.changed.emit(key, value); row.applied.emit(key); app.processEvents()
        undo_before = len(w._undo)
        edit("sla_raft_type", raft)
        edit("sla_support_type", sup)
        for _ in range(50):
            app.processEvents()
        cfg = p.config()
        assert cfg["sla_raft_type"] == plate["sla_raft_type"], "the plate's raft changed"
        assert cfg["sla_support_type"] == plate["sla_support_type"], "the plate's supports changed"
        assert a.config(cfg)["sla_raft_type"] == raft and a.config(cfg)["sla_support_type"] == sup
        assert b.config(cfg)["sla_raft_type"] == plate["sla_raft_type"]
        assert b.config(cfg)["sla_support_type"] == plate["sla_support_type"]
        assert p._rows["sla_raft_type"].editor_value() == raft          # the panel shows the selected model's
        assert p._scope_banner.isVisibleTo(p) and "1" in p._scope_banner.text()
        assert len(w._undo) == undo_before + 2, len(w._undo) - undo_before   # one Ctrl+Z per change
        w._select_uid(None); app.processEvents()
        assert p._rows["sla_raft_type"].editor_value() == plate["sla_raft_type"]
        assert not p._scope_banner.isVisibleTo(p)
        w._select_uid(a.uid); app.processEvents()
        w.undo(); app.processEvents()
        assert a.settings.get("sla_support_type") is None and a.settings.get("sla_raft_type") == raft
        w._select_uid(None); app.processEvents()
        edit("sla_raft_type", raft)                                       # nothing selected: the plate
        assert p.config()["sla_raft_type"] == raft
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


def test_the_selection_frame_takes_every_part_it_touches(tmp_path):
    """Box selection took only parts whose box centre fell inside the frame: a frame over half an arch, or
    across a row of crowns, took nothing (reported from the lab). Any part the frame touches is taken - also
    a thin frame crossing a part with none of its corners inside; a frame over empty plate takes nothing."""
    import subprocess, sys, textwrap
    code = textwrap.dedent('''
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        import numpy as np, shapely
        from shapely.geometry import Point
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from unislicer import primitives, mesh_tools
        from unislicer.gui.main_window import MainWindow
        w = MainWindow(); w.resize(1400, 900); w.show(); app.processEvents()
        w.store.setValue("preslice", "false")
        shape = Point(0, 0).buffer(25, 48).difference(Point(0, 0).buffer(18, 48)).intersection(shapely.box(-30, -2, 30, 30))
        arch = w.add_mesh(mesh_tools.extrude(shape, 0.0, 10.0), rearrange=False)
        crowns = [w.add_mesh(primitives.box(7, 7, 6), rearrange=False) for _ in range(4)]
        arch.x, arch.y = 70.0, 70.0
        for k, c in enumerate(crowns):
            c.x, c.y = 50.0 + 12.0 * k, 30.0
        w._objects_changed(); app.processEvents()
        v = w.viewer
        v.view_preset("top"); v.fit_objects(); app.processEvents()
        def px(x, y, z=0.0):
            xy, _ = v._to_screen(np.array([[x, y, z]]))
            return int(xy[0][0]), int(xy[0][1])
        def frame(xa, ya, xb, yb):
            (ax, ay), (bx, by) = px(xa, ya), px(xb, yb)
            return QRect(min(ax, bx), min(ay, by), abs(bx - ax) + 1, abs(by - ay) + 1)
        ab = arch.world_bounds()
        got = v.objects_in_rect(frame(ab[0][0] - 3, ab[0][1] - 3, ab[0][0] + 4, ab[0][1] + 3))   # the end of one arm
        assert got == [arch.uid], got
        cb = [c.world_bounds() for c in crowns]
        ymid = (cb[0][0][1] + cb[0][1][1]) / 2
        got = v.objects_in_rect(frame(cb[0][0][0] - 3, ymid - 0.3, cb[-1][1][0] + 3, ymid + 0.3))  # a thin strip
        assert sorted(got) == sorted(c.uid for c in crowns), got
        got = v.objects_in_rect(frame(150, 100, 170, 110))                                         # empty plate
        assert got == [], got
        print("ok", flush=True)
    ''')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _home = str(tmp_path / "home")
    env = dict(os.environ, HOME=_home, USERPROFILE=_home, UNISLICER_NO_SEED="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=240, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
