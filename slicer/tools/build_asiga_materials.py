"""Builds unislicer/profiles/materials-asiga.json - the Asiga Composer materials the lab uses, converted for the
Phrozen printers and shipped with UniSlicer (the same conversion as Profiles > Import, so they need no import):

    python tools/build_asiga_materials.py

Sources: tools/asiga_materials/*.ini (Asiga Composer material files)."""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from unislicer import asiga_import, profiles  # noqa: E402

VENDORS = {"voxelprint": "FGM"}          # Voxelprint is FGM's resin line


def build() -> dict:
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, "asiga_materials", "*.ini"))):
        if not asiga_import.is_asiga(path):
            continue
        pid, entry, _ = profiles.asiga_entry(path)
        low = entry["name"].lower()
        entry["vendor"] = next((v for k, v in VENDORS.items() if k in low), "Asiga materials")
        out[pid] = entry
    return out


if __name__ == "__main__":
    data = build()
    dest = os.path.join(os.path.dirname(HERE), "unislicer", "profiles", "materials-asiga.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    print(f"{len(data)} materials -> {dest}")
