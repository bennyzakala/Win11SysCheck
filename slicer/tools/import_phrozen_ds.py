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
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(HERE, "..", "unislicer", "profiles")

# DS Slicer printer name -> (UniSlicer printer id, priority: lower wins when two names share an id)
PRINTERS = {
    "Sonic XL 4K 2022": ("phrozen_sonic_xl_4k_2022", 0),
    "Sonic 4K 2022": ("phrozen_sonic_4k_2022", 0),
    "Sonic XL 4K": ("phrozen_sonic_xl_4k", 0),
    "Sonic XL 4K Plus": ("phrozen_sonic_xl_4k_plus", 0),
    "Lumii DLP 2K": ("phrozen_lumii_2k", 0),
    "Lumii DLP 2K Mini Plate": ("phrozen_lumii_2k", 1),
    "Sonic CS+": ("phrozen_sonic_cs_plus", 0),
    "Sonic CS+ Mini Plate": ("phrozen_sonic_cs_plus", 1),
    "Sonic LS+ Large Plate": ("phrozen_sonic_ls_plus", 0),
    "Sonic LS+ Standard Plate": ("phrozen_sonic_ls_plus", 1),
}


def _f(el, tag, default=0.0):
    try:
        return float((el.findtext(tag) or "").strip())
    except ValueError:
        return default


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def exposure_values(ed, thickness: float) -> dict:
    """DS Slicer ExposureData -> UniSlicer settings."""
    r = ed.find("Ratio")
    rx, ry, rz = (_f(r, "RatioX", 1.0), _f(r, "RatioY", 1.0), _f(r, "RatioZ", 1.0)) if r is not None else (1, 1, 1)
    v = {
        "sla_layer_height": round(thickness, 4),
        "exposure_time": _f(ed, "NormalCuringTime"),
        "bottom_exposure_time": _f(ed, "BaseCuringTime"),
        "bottom_layers": int(_f(ed, "NumBaseLayer")),
        "transition_layers": int(_f(ed, "BufferLayerNumber")),
        "lift_height": _f(ed, "NormalLiftHeight"),
        "bottom_lift_height": _f(ed, "BaseLiftHeight"),
        "lift_speed": _f(ed, "NormalPeelSpeed"),
        "bottom_lift_speed": _f(ed, "BasePeelSpeed"),
        "retract_speed": _f(ed, "NormalReturnSpeed"),
        "bottom_retract_speed": _f(ed, "BaseReturnSpeed"),
        "light_pwm": int(_f(ed, "ligthPwmInputFile", 255)) or 255,
        "light_pwm_bottom": int(_f(ed, "bottomLigthPwmInputFile", 255)) or 255,
        "wait_mode": "rest",
        "rest_after_retract": _f(ed, "NormalWaitBeforePrint"),     # wait before the light comes on
        "rest_before_lift": _f(ed, "NormalWaitAfterPrint"),        # wait after curing, before lifting
        "rest_after_lift": _f(ed, "NormalWaitLift"),
        "bottom_rest_after_lift": _f(ed, "BaseWaitLift"),
        "shrink_xy": round(((rx + ry) / 2 - 1) * 100, 3),
        "shrink_z": round((rz - 1) * 100, 3),
        "tsmc_enable": bool(int(_f(ed, "TwoStage"))),
    }
    if v["tsmc_enable"]:
        v.update({"lift_height_2": _f(ed, "liftSecondDistInputFile"), "lift_speed_2": _f(ed, "liftSecondSpeedInputFile"),
                  "retract_height_2": _f(ed, "retractSecondDistInputFile"),
                  "retract_speed_2": _f(ed, "retractSecondSpeedInputFile")})
    cs = ed.find("CompensateSize/Default")
    if cs is not None:
        out, base = _f(cs, "CompensateSizeOut"), _f(cs, "CompensateSizeOutBase")
        if out:
            v["xy_compensation"] = out
        if base:
            v["bottom_xy_compensation"] = base
    return {k: (round(x, 4) if isinstance(x, float) else x) for k, x in v.items()}


def convert(xml_path: str) -> tuple[dict, dict, dict]:
    root = ET.parse(xml_path).getroot()
    version = (root.findtext("SoftwareInfo/VersionInfo/Version") or "").strip()
    geometry: dict = {}
    resins: dict = {}
    for rc in root.iter("ResinConfig"):
        pname = (rc.findtext("PrinterName") or "").strip()
        if pname not in PRINTERS:
            continue
        pid, prio = PRINTERS[pname]
        g = {"bed_x": round(_f(rc, "DimensionX"), 2), "bed_y": round(_f(rc, "DimensionY"), 2),
             "max_z": round(_f(rc, "DimensionZ"), 1), "resolution_x": int(_f(rc, "ResolutionX")),
             "resolution_y": int(_f(rc, "ResolutionY"))}
        if pid not in geometry or prio < geometry[pid][0]:
            geometry[pid] = (prio, g)
        brand = (rc.findtext("BrandName") or "").strip()
        name = (rc.findtext("ResinName") or "").strip()
        if not name or brand.startswith("<"):
            continue
        brand = {"ID Print": "ID print", "ID3DPRINT": "ID3DPRINT"}.get(brand, brand)
        rid = "phrozen_ds_" + slug(f"{brand} {name}")
        entry = resins.setdefault(rid, {"brand": brand, "name": name, "tables": {}, "prio": {},
                                        "colour": [int(_f(rc, "ResinColorR", 200)), int(_f(rc, "ResinColorG", 200)),
                                                   int(_f(rc, "ResinColorB", 200))]})
        if pid in entry["prio"] and entry["prio"][pid] <= prio:
            continue
        table = {}
        for tc in rc.findall("ThicknessConfig"):
            th = _f(tc, "Thickness")
            if th <= 0:
                continue
            modes = {}
            for ed in tc.findall("ExposureData"):
                mode = (ed.findtext("ThicknessName") or "").strip().lower()
                if mode in ("stable", "fast"):
                    modes[mode] = exposure_values(ed, th)
            if modes:
                table[f"{th:.3f}"] = modes
        if table:
            entry["tables"][pid] = table
            entry["prio"][pid] = prio
    materials = {}
    for rid, e in sorted(resins.items()):
        if not e["tables"]:
            continue
        printers = sorted(e["tables"])
        first = e["tables"][printers[0]]
        default_th = "0.050" if "0.050" in first else sorted(first)[0]
        values = dict(first[default_th].get("stable") or next(iter(first[default_th].values())))
        values["material_name"] = f"{e['brand']} {e['name']}"
        materials[rid] = {
            "name": e["name"], "vendor": e["brand"], "technology": "sla", "family": "dental",
            "processes": ["lcd", "dlp"], "printers": printers, "printers_only": True,
            "color": [round(c / 255, 3) for c in e["colour"]],
            "note": (f"Phrozen DS Slicer {version} factory profile. Exposure, lift, speeds and shrink compensation follow "
                     f"the printer, the layer height ({', '.join(sorted({k for t in e['tables'].values() for k in t}))} mm) "
                     f"and the Stable / Fast mode."),
            "values": values,
            "layer_table": e["tables"],
        }
    return {pid: g for pid, (_p, g) in geometry.items()}, materials, {"version": version, "resins": len(materials)}


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
