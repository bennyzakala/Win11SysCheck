"""Reference plate on real files: run the support / raft checks (unislicer.reference) on every STL in a
folder, with each configuration, and compare with a stored baseline.

    python tools/reference_plate.py <folder>            # check: exit code 1 on any fault or regression
    python tools/reference_plate.py <folder> --update   # store the current numbers as the baseline

Only numbers are stored (tests/reference_baseline.json), keyed by a hash of each file's content - never the
files, their names or their geometry, so patients' scans stay out of the repository.

A fault: a support sticking out of a part or running through it, beyond what the baseline already has; fewer
than 90% of a part's points supported, or 10% fewer than in the baseline; no raft where one is asked for.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BASELINE = os.path.join(ROOT, "tests", "reference_baseline.json")


def main(argv) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder, update = argv[0], "--update" in argv
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(["reference"])   # fonts for the raft codes  # noqa: F841
    from unislicer import dental_cad, profiles, reference
    from unislicer.mesh import load_mesh
    base = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    results, faults = {}, []
    files = sorted(glob.glob(os.path.join(folder, "*.stl")))
    t0 = time.time()
    for path in files:
        with open(path, "rb") as fh:
            key = hashlib.sha1(fh.read()).hexdigest()[:12]
        mesh = load_mesh(path)
        mesh = mesh[0] if isinstance(mesh, list) else mesh
        name = os.path.basename(path)
        mtype = dental_cad.model_type_of(name, mesh)
        label = dental_cad.case_code(name) if mtype == "cb" else ""
        for shape, raft in reference.CONFIGS:
            ck = f"{key}/{shape}/{raft}"
            r = reference.check_part(mesh, reference.config_for(base, shape, raft, mtype), label)
            results[ck] = r
            was = stored.get(ck)
            bad = []
            if r["sticking_out"] > (was or {}).get("sticking_out", 0):
                bad.append(f"{r['sticking_out']} sticking out")
            if r["through"] > (was or {}).get("through", 0):
                bad.append(f"{r['through']} through the part")
            if r["used"] < 0.9 * r["points"]:
                bad.append(f"only {r['used']}/{r['points']} points supported")
            if was and r["used"] < 0.9 * was["used"]:
                bad.append(f"{r['used']} supported, baseline {was['used']}")
            if raft != "none" and r["used"] and r["raft_area"] <= 0:
                bad.append("no raft")
            line = f"{key} {mtype or '-':8} {shape:6} {raft:5} points {r['used']:3}/{r['points']:3} " \
                   f"out {r['sticking_out']} through {r['through']} raft {r['raft_area']:7.1f}"
            print(("FAULT " if bad else "ok    ") + line + ("  <- " + "; ".join(bad) if bad else ""), flush=True)
            if bad:
                faults.append(ck)
    print(f"{len(files)} files, {len(results)} checks, {len(faults)} faults, {time.time() - t0:.0f} s")
    if update:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("baseline stored:", BASELINE)
        return 0
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
