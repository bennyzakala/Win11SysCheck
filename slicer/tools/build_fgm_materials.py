"""Builds unislicer/profiles/materials-fgm.json - FGM VoxelPrint resins (Ceramic HT / MT / LT, TEMP, Splint
Tough) for the Phrozen Sonic XL 4K Plus, CS+ and LS+, Stable and Fast, every layer height:

    python tools/build_fgm_materials.py

Where each value comes from:
- FGM's own Phrozen numbers (Sonic 4K, 405 nm, 0.1 mm): exposure, bottom exposure, 6 transition layers, the
  size compensation (``FGM`` below; Ceramic MT as FGM publish it, 4.5 s).
- Another printer and layer height: how Phrozen's own tables for resins of the same kind change between
  printers and heights (``REFS``: filled crown resins for the ceramics, temporary C&B for TEMP, splint
  resins for Splint Tough) - the geometric mean of their ratios to their XL 4K Plus 0.1 mm row.
- The ceramics' own layer curve: the Asiga Composer files of VoxelPrint Ceramic A1-LT and B1-HT
  (tools/asiga_materials) - energy by cure depth - blended with the printers' curve (they are the same resins
  as the A1-LT / B1-HT profiles imported from Asiga, which these replace).
- Bottom exposure: never under the highest of the reference resins' own on that printer (FGM's 25 s was set on a Sonic 4K;
  Phrozen's crown resins take 50-65 s on the CS+, and a short bottom exposure loses the print).
- Lift, speeds and rests: the first reference resin's own row on that printer (Phrozen's tested motion);
  Fast: that resin's Fast row (faster lift and retract), the exposure the same as Stable.
All exposures are starting points: an exposure test on each printer sets them (Profiles > edit)."""
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from unislicer import asiga_import, profiles  # noqa: E402

PRINTERS = ("phrozen_sonic_xl_4k_plus", "phrozen_sonic_cs_plus", "phrozen_sonic_ls_plus")
BASE_PRINTER, BASE_H = "phrozen_sonic_xl_4k_plus", 0.1
HEIGHTS = (0.03, 0.05, 0.1)
REFS = {
    "ceramic": ("phrozen_ds_saremco_crowntec", "phrozen_ds_nextdent_c_b_mfh"),
    "temp": ("phrozen_ds_jamghe_x_phrozen_temporary_c_b", "phrozen_ds_detax_freeprint_temp",
             "phrozen_ds_bego_varseosmile_temp"),
    "splint": ("phrozen_ds_keystone_keyprint_keysplint_hard", "phrozen_ds_pacdent_rodin_splint_2_0",
               "phrozen_ds_detax_freeprint_splint_2_0"),
}
# id: (name, kind, exposure s, bottom s, shrink xy %, shrink z %, colour, Asiga curves)
FGM = {
    "fgm_voxelprint_ceramic_ht": ("VoxelPrint Ceramic HT", "ceramic", 4.5, 25, 0.0, 0.0, (0.92, 0.92, 0.88), ("B1-HT",)),
    "fgm_voxelprint_ceramic_mt": ("VoxelPrint Ceramic MT", "ceramic", 4.5, 25, 0.0, 0.0, (0.92, 0.88, 0.78),
                                  ("B1-HT", "A1-LT")),
    "fgm_voxelprint_ceramic_lt": ("VoxelPrint Ceramic LT", "ceramic", 4.7, 25, 0.0, 0.0, (0.9, 0.84, 0.73), ("A1-LT",)),
    "fgm_voxelprint_temp": ("VoxelPrint TEMP", "temp", 5.2, 25, 0.4, 0.4, (0.94, 0.86, 0.75), ()),
    "fgm_voxelprint_splint_tough": ("VoxelPrint Splint Tough", "splint", 9.9, 35, 0.5, 0.0, (0.78, 0.98, 1.0), ()),
}


def _row(mat, printer, h, mode="stable"):
    rows = (mat or {}).get("layer_table", {}).get(printer) or {}
    r = rows.get(f"{h:.3f}") or {}
    return r.get(mode) or {}


def _ratio(mats, refs, printer, h, key):
    """Geometric mean over the reference resins of value(printer, h) / value(XL 4K Plus, 0.1 mm)."""
    logs = []
    for rid in refs:
        a = float(_row(mats.get(rid), printer, h).get(key) or 0)
        b = float(_row(mats.get(rid), BASE_PRINTER, BASE_H).get(key) or 0)
        if a > 0 and b > 0:
            logs.append(math.log(a / b))
    if not logs:
        raise ValueError(f"No reference for {printer} {h} {key}")
    return math.exp(sum(logs) / len(logs))


def _asiga_curves():
    out = {}
    for path in glob.glob(os.path.join(HERE, "asiga_materials", "*.ini")):
        a = asiga_import.parse(path)
        for tag in ("A1-LT", "B1-HT"):
            if tag in a["name"]:
                out[tag] = a
    return out


def build() -> dict:
    mats = profiles.load("materials")
    curves = _asiga_curves()
    out = {}
    for pid, (name, kind, exp, base, sxy, sz, colour, asiga) in FGM.items():
        refs = REFS[kind]
        layer_table = {}
        for p in PRINTERS:
            rows = {}
            for h in HEIGHTS:
                r = _ratio(mats, refs, p, h, "exposure_time")
                if asiga:                      # the resin's own curve, blended with this printer's
                    own = [asiga_import.layer_energy(curves[t], h) / asiga_import.layer_energy(curves[t], 0.1)
                           for t in asiga]
                    own = sum(own) / len(own)
                    r = _ratio(mats, refs, p, BASE_H, "exposure_time") * math.sqrt(
                        own * r / _ratio(mats, refs, p, BASE_H, "exposure_time"))
                exposure = round(exp * r, 1)
                # never under what Phrozen give resins of this kind on this printer: a short bottom exposure is
                # the one error that loses the print (it comes off the plate); more only widens the first layers
                floor = max(float(_row(mats.get(rid), p, h).get("bottom_exposure_time") or 0) for rid in refs)
                bottom = base * _ratio(mats, refs, p, h, "bottom_exposure_time")
                bottom = round(min(max(bottom, floor, 10.0), 90.0))
                ref = next(rid for rid in refs if _row(mats.get(rid), p, h))
                modes = {}
                for mode in ("stable", "fast"):
                    row = dict(_row(mats[ref], p, h, mode) or _row(mats[ref], p, h))
                    row.update({"sla_layer_height": h, "exposure_time": exposure,
                                "bottom_exposure_time": float(bottom), "transition_layers": 6,
                                "shrink_xy": sxy, "shrink_z": sz})
                    if kind == "ceramic":      # a filled resin settles before the light (Asiga: 1 s at least)
                        row["rest_after_retract"] = max(float(row.get("rest_after_retract") or 0), 1.0)
                    modes[mode] = row
                rows[f"{h:.3f}"] = modes
            layer_table[p] = rows
        first = layer_table[PRINTERS[0]]["0.050"]["stable"]
        values = {k: v for k, v in first.items() if k in profiles.SCHEMA_BY_KEY}
        values["technology"] = "sla"
        note = (f"FGM VoxelPrint - FGM's Phrozen values (Sonic 4K, 0.1 mm: {exp:g} s, bottom {base:g} s"
                + (f", size {100 + sxy:g}% XY" if sxy else "") + (f" / {100 + sz:g}% Z" if sz else "") +
                "), carried to each printer and layer height as Phrozen's own " +
                {"ceramic": "filled crown", "temp": "temporary C&B", "splint": "splint"}[kind] +
                " resins change" + (" and by the resin's own layer curve (Asiga Composer, "
                                     + " / ".join(asiga) + ")" if asiga else "") +
                ". Fast: the same exposure, faster lift and retract. STARTING VALUES: run an exposure test on "
                "each printer and set the exposure found.")
        out[pid] = {"name": name, "vendor": "FGM", "technology": "sla", "family": "dental", "processes": ["lcd"],
                    "printers": list(PRINTERS), "printers_only": True, "color": list(colour), "note": note,
                    "values": values, "layer_table": layer_table}
    return out


if __name__ == "__main__":
    data = build()
    dest = os.path.join(os.path.dirname(HERE), "unislicer", "profiles", "materials-fgm.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    print(f"{len(data)} materials -> {dest}")
    for pid, m in data.items():
        print(m["name"])
        for p, t in m["layer_table"].items():
            print("  ", p[13:], {h: (r["stable"]["exposure_time"], r["stable"]["bottom_exposure_time"],
                                     r["stable"]["lift_speed"], r["fast"]["lift_speed"],
                                     r["stable"].get("rest_after_retract")) for h, r in t.items()})
