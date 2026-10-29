"""Entry point: ``python -m unislicer`` opens the GUI, ``python -m unislicer slice ...`` runs headless."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__


def _progress(stage: str, fraction: float) -> None:
    sys.stderr.write(f"\r{stage:<8} {fraction * 100:5.1f}%")
    sys.stderr.flush()


def slice_command(args) -> int:
    from . import profiles
    from .mesh import load_mesh
    from .scene import SceneObject, arrange, out_of_bounds
    from .settings import SCHEMA_BY_KEY

    overrides = {}
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        if key not in SCHEMA_BY_KEY:
            raise SystemExit(f"Unknown setting '{key}'. Run 'list-settings' to see all keys.")
        overrides[key] = value
    cfg = profiles.build_config(args.printer, args.material, args.quality, overrides)

    objects = []
    for path in args.models:
        for mesh in load_mesh(path):
            obj = SceneObject.from_mesh(mesh)
            if args.scale != 100:
                obj.scale[:] = args.scale / 100.0
            if args.lay_flat:
                obj.lay_flat()
            objects.append(obj)
    arrange(objects, cfg["bed_x"], cfg["bed_y"])
    bad = out_of_bounds(objects, cfg["bed_x"], cfg["bed_y"], cfg["max_z"])
    if bad:
        print(f"warning: does not fit on the printer: {', '.join(o.name for o in bad)}", file=sys.stderr)
    meshes = [o.world_mesh() for o in objects]
    job_name = os.path.splitext(os.path.basename(args.models[0]))[0]
    t0 = time.time()
    if cfg["technology"] == "sla":
        from . import sla
        job = sla.prepare(meshes, cfg, _progress)
        out = args.output or job_name + {"sl1": ".sl1", "ctb": ".ctb"}.get(cfg["sla_format"], ".zip")
        sla.export(job, cfg, out, job_name, lambda f: _progress("export", f))
        sys.stderr.write("\n")
        for w in job.warnings:
            print("warning:", w, file=sys.stderr)
        print(f"{out}: {job.layer_count} layers, {job.volume_ml:.2f} ml resin, "
              f"~{job.time_s / 3600:.2f} h  (sliced in {time.time() - t0:.1f}s)")
    else:
        from . import fdm, gcode
        plan = fdm.plan(meshes, cfg, _progress)
        text, stats = gcode.generate(plan, cfg, job_name)
        out = args.output or job_name + ".gcode"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("\n")
        for w in plan.warnings:
            print("warning:", w, file=sys.stderr)
        print(f"{out}: {stats.layer_count} layers, {stats.filament_mm / 1000:.2f} m / {stats.filament_g:.1f} g filament, "
              f"~{gcode.format_duration(stats.time_s)}  (sliced in {time.time() - t0:.1f}s)")
    return 0


def list_profiles(_args) -> int:
    from . import profiles
    for kind in profiles.KINDS:
        print(f"[{kind}]")
        for pid, p in profiles.load(kind).items():
            print(f"  {pid:<28} {profiles.display_name(p)}  ({profiles.technology_of(p)})")
    return 0


def list_settings(_args) -> int:
    from .settings import SCHEMA
    for s in SCHEMA:
        extra = f" options: {', '.join(o[0] for o in s.options)}" if s.options else ""
        print(f"{s.key:<26} {s.kind:<6} default={s.default!r:<10} [{s.tech}] {s.en}{extra}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unislicer", description="Universal FDM + resin slicer")
    parser.add_argument("--version", action="version", version=f"UniSlicer {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("slice", help="slice models without the GUI")
    p.add_argument("models", nargs="+", help="STL / OBJ / 3MF files")
    p.add_argument("-p", "--printer", default="generic_fdm", help="printer profile id (see list-profiles)")
    p.add_argument("-m", "--material", default=None, help="material profile id")
    p.add_argument("-q", "--quality", default=None, help="quality profile id")
    p.add_argument("-s", "--set", action="append", metavar="KEY=VALUE", help="override any setting")
    p.add_argument("--scale", type=float, default=100.0, help="uniform scale in percent")
    p.add_argument("--lay-flat", action="store_true", help="rotate so the largest flat face is on the bed")
    p.add_argument("-o", "--output", help="output file (.gcode, .sl1 or .zip)")
    p.set_defaults(func=slice_command)
    sub.add_parser("list-profiles", help="list built-in and user profiles").set_defaults(func=list_profiles)
    sub.add_parser("list-settings", help="list every setting key").set_defaults(func=list_settings)
    g = sub.add_parser("gui", help="open the graphical interface (default)")
    g.add_argument("models", nargs="*")

    args = parser.parse_args(argv)
    if args.command in (None, "gui"):
        from .gui.app import run
        return run(getattr(args, "models", []) or [])
    return args.func(args)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
