"""Import the factory machine/resin profiles exported from Phrozen DS Slicer (all_PHROZEN_machine_profile.xml).

    python tools/import_phrozen_ds.py all_PHROZEN_machine_profile.xml

- corrects the Phrozen printers' build volume and screen resolution (unislicer/profiles/printers.json)
- writes unislicer/profiles/materials-phrozen_ds.json: one resin per brand/name, linked to the printers it has
  data for, with a table per printer -> layer thickness -> Stable / Fast mode (exposure, bottom layers, lift,
  speeds, waits, light PWM, two-stage motion, shrink compensation). UniSlicer applies the row that matches the
  printer, the layer height and the chosen mode (see profiles.apply_layer_table).

Re-run it on a newer export to update the values."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(HERE, "..", "unislicer", "profiles")

sys.path.insert(0, os.path.join(HERE, ".."))
from unislicer.phrozen_ds import convert  # noqa: E402  (the converter lives in the program: Profiles > Import)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    geometry, materials, info = convert(argv[1])
    path = os.path.join(PROFILES, "printers.json")
    with open(path, encoding="utf-8") as fh:
        printers = json.load(fh)
    for pid, g in geometry.items():
        if pid in printers:
            printers[pid]["values"].update(g)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(printers, indent=1, ensure_ascii=False) + "\n")
    out = os.path.join(PROFILES, "materials-phrozen_ds.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(materials, fh, ensure_ascii=False, separators=(",", ":"))
    missing = sorted(set(geometry) - set(printers))
    print(f"DS Slicer {info['version']}: {info['resins']} resins, geometry for {len(geometry)} printers"
          + (f"; not in printers.json (add them): {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
