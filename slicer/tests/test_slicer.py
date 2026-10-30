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
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", over)
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
    base = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast"))
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
        cfg = profiles.build_config("elegoo_mars3", None, "sla_fast", {"hollow_enable": True, "drain_holes": 2, **over})
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
    vented = sla.prepare([m], profiles.build_config("elegoo_mars3", None, "sla_fast", dict(hollow_enable=True, drain_holes=0)),
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
    cfg = profiles.build_config("asiga_max2_62", None, None, {"support_enable": False})
    assert cfg["sla_process"] == "dlp" and cfg["sla_format"] == "slc"
    job = sla.prepare([obj.world_mesh()], cfg)
    path = sla.export(job, cfg, str(tmp_path / "part.slc"))
    f = vf.read_slc(path)
    assert "-SLCVER 2.0" in f["header"] and len(f["layers"]) == job.layer_count
    ring = f["layers"][3][1][0]
    assert sla_supports._signed_area(ring) > 0 and abs(sla_supports._signed_area(ring)) == pytest.approx(80, rel=1e-3)
    # laser SLA -> CLI with contours and hatches, time from scan length
    cfg = profiles.build_config("generic_laser_sla", None, None, {"support_enable": False})
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
    assert got["spacing"] == 2.5 and got["margin"] == 4.0
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
