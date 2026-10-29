import re
import zipfile

import numpy as np
import pytest

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
        assert sum(n.endswith(".png") for n in names) == job.layer_count
        assert "numFast = %d" % job.layer_count in zf.read("config.ini").decode()


def test_sla_mask_pixels():
    cfg = profiles.build_config("generic_msla", None, None, {"antialiasing": "1"})
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
