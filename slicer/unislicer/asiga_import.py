"""Asiga Composer material files (*.ini) as UniSlicer resin profiles for Phrozen printers.

An Asiga material gives the energy each layer thickness needs (``Z=[energy mJ/cm2, thickness mm, ...]``) for
its 385 nm light at ``Light Intensity`` mW/cm2, its first-layer (burn-in) settings, waits and its shrinkage
compensation (``XY Scale``). A Phrozen printer is a 405 nm LCD whose light is not published, so the Asiga
energy cannot be turned into seconds directly. What carries over exactly is the shape of the curve: how much
more each layer height needs than 0.05 mm. The level is a resin of the same kind that Phrozen set for each
printer - Saremco Crowntec, a ceramic-filled crown resin (a light-intensity guess gave 0.4 s on an LS+, far
under what a filled resin needs; under-exposure loses the print, over-exposure a little accuracy). It is an
ESTIMATE until an exposure test: the exposure found at 0.05 mm (``exposure_005``) sets every height and
printer.

Lift, speeds and rests come from the Phrozen printer's own table (Asiga separates differently), the shrinkage
compensation from the file. Support settings in the file are left out: supports belong to the printer and the
part's type here."""
from __future__ import annotations

import configparser
import os
import re

REFERENCE_RESIN = "phrozen_ds_saremco_crowntec"       # a ceramic-filled crown resin, Phrozen's own times
FALLBACK_RESIN = "phrozen_ds_phrozen_dental_washable_dental_model"
DEFAULT_PRINTERS = ("phrozen_sonic_xl_4k_plus", "phrozen_sonic_ls_plus", "phrozen_sonic_cs_plus")


def _num(text) -> float | None:
    m = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", str(text or ""))
    return float(m.group(1)) if m else None


def is_asiga(path: str) -> bool:
    """An Asiga Composer material file (a Version line, a [General] Name and a Z= curing table)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(200_000)
    except OSError:
        return False
    return "[General]" in text and re.search(r"^\s*Z\s*=\s*\[", text, re.M) is not None


def parse(path: str) -> dict:
    """The material's values that matter here. Raises ValueError on a file that is not an Asiga material."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not is_asiga(path):
        raise ValueError("Not an Asiga Composer material file")
    # the Z= curing rows repeat one key: read them before the ini reader (it keeps only the last)
    table = []
    for m in re.finditer(r"^\s*Z\s*=\s*\[([^\]]*)\]", text, re.M):
        parts = [_num(p) for p in m.group(1).split(",")]
        if len(parts) >= 2 and parts[0] is not None and parts[1] is not None and parts[1] > 0:
            table.append((float(parts[1]), float(parts[0])))          # (thickness mm, energy mJ/cm2)
    table.sort()
    body = "\n".join(line for line in text.splitlines()
                     if not re.match(r"^\s*(Z\s*=|Version\s*=)", line))
    ini = configparser.ConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(";",))
    ini.optionxform = str
    ini.read_string(body)

    def get(key, *sections):
        for sec in sections or ini.sections():
            if ini.has_section(sec) and ini.has_option(sec, key):
                return ini.get(sec, key)
        return None
    name = (get("Name", "General") or os.path.splitext(os.path.basename(path))[0]).strip().strip('"')
    out = {
        "name": name,
        "table": table,
        "light_intensity": _num(get("Light Intensity")),
        "slice_thickness": _num(get("Slice Thickness")),
        "burn_in_layers": _num(get("Burn-In Layers")),
        "burn_in_exposure": _num(get("Burn-In Exposure")),
        "xy_scale": _num(get("XY Scale")),
        "offset": _num(get("Offset")) or 0.0,
        "z_compensation": _num(get("Z Compensation")),
        "wait_after_approach": _num(get("Normal Wait Time (After Approach)")),
        "viscosity": _num(get("Viscosity Range")),
        "support": {k: ini.get("Support", k) for k in ini.options("Support")} if ini.has_section("Support") else {},
    }
    if not table:
        raise ValueError("The file has no curing table (Z= rows)")
    return out


def is_build_file(path: str) -> bool:
    """A Composer print's parameters (Build Wizard), not a material: it has no curing table to import."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return 'Type="BuildWizard"' in fh.read(2000)
    except OSError:
        return False


def layer_energy(asiga: dict, layer: float) -> float:
    """mJ/cm2 a layer of this height needs: the table gives the energy per cure DEPTH, and a layer is cured
    ``Offset`` deeper than its own height (into the one below) - 0.05 + 0.16 = 0.21 mm, 7.8 mJ/cm2, 1.857 s at
    4.2 mW/cm2: what Composer's own build file for this resin uses."""
    return energy_at(asiga, layer + float(asiga.get("offset") or 0.0))


def energy_at(asiga: dict, thickness: float) -> float:
    """mJ/cm2 for a layer thickness, interpolated along the file's curve (its ends held beyond it)."""
    t = asiga["table"]
    if thickness <= t[0][0]:
        return t[0][1]
    for (h0, e0), (h1, e1) in zip(t, t[1:]):
        if h0 <= thickness <= h1:
            return e0 + (e1 - e0) * (thickness - h0) / (h1 - h0)
    return t[-1][1]


def _row(reference: dict, printer: str, height: float) -> dict:
    """The reference resin's own row for this printer, at the layer height nearest ``height`` (Stable)."""
    rows = (reference or {}).get("layer_table", {}).get(printer) or {}
    if not rows:
        return {}
    key = min(rows, key=lambda k: abs(float(k) - height))
    return dict((rows[key] or {}).get("stable") or {})


def to_material(asiga: dict, printers, reference: dict | None = None, exposure_005: float | None = None,
                calibrated_on: str | None = None) -> dict:
    """A UniSlicer resin profile (``name``, ``vendor``, ``values``, ``layer_table``, ...) for these Phrozen
    printers. ``exposure_005``: the exposure found by a test at 0.05 mm on ``calibrated_on`` (the first printer
    when not given) - every height and every other printer follow it; without it, the estimate."""
    printers = [p for p in printers if p]
    if not printers:
        raise ValueError("No printer chosen")
    reference = reference or {}
    e005 = layer_energy(asiga, 0.05)
    calibrated_on = calibrated_on or printers[0]
    scale = 1.0
    base_cal = float(_row(reference, calibrated_on, 0.05).get("exposure_time") or 0)
    if exposure_005 and exposure_005 > 0 and base_cal > 0:
        scale = float(exposure_005) / base_cal          # the test's correction, for every printer alike
    shrink = round((float(asiga["xy_scale"]) - 1.0) * 100.0, 3) if asiga.get("xy_scale") else 0.0
    layer_table = {}
    for p in printers:
        rows = (reference.get("layer_table", {}).get(p)) or {}
        if not rows:
            raise ValueError(f"No reference times for {p}")
        level = float(_row(reference, p, 0.05).get("exposure_time") or 0)
        heights = sorted({float(k) for k in rows} | {0.05})
        out = {}
        for h in heights:
            row = _row(reference, p, h)
            exposure = round(level * layer_energy(asiga, h) / e005 * scale, 2)  # the Asiga curve, this level
            ref_exp = float(row.get("exposure_time") or 0) or None
            ref_bottom = float(row.get("bottom_exposure_time") or 0) or None
            bottom = ref_bottom * exposure / ref_exp if ref_exp and ref_bottom else exposure * 10.0
            row.update({
                "sla_layer_height": round(h, 3),
                "exposure_time": exposure,
                "bottom_exposure_time": round(min(max(bottom, 10.0), 90.0), 1),
                "shrink_xy": shrink,
            })
            if asiga.get("burn_in_layers"):
                row["bottom_layers"] = max(int(row.get("bottom_layers", 0) or 0), int(asiga["burn_in_layers"]))
            wait = asiga.get("wait_after_approach")
            if wait:                         # a thick (filled) resin settles before the light: never less than Asiga
                row["rest_after_retract"] = max(float(row.get("rest_after_retract", 0) or 0), float(wait))
            # Fast: the same exposure (as in Composer's Fast Print Mode and in Phrozen's own tables) with the
            # printer's faster lift and retract; Asiga's fast mode drops its waits, so no Asiga wait is added
            rows_h = rows.get(min(rows, key=lambda k: abs(float(k) - h))) or {}
            fast = dict(rows_h.get("fast") or rows_h.get("stable") or {})
            for k in ("sla_layer_height", "exposure_time", "bottom_exposure_time", "shrink_xy", "bottom_layers"):
                if k in row:
                    fast[k] = row[k]
            out[f"{h:.3f}"] = {"stable": row, "fast": fast}
        layer_table[p] = out
    first = layer_table[printers[0]]
    values = dict(first.get("0.050", next(iter(first.values())))["stable"])
    values["technology"] = "sla"
    calibrated = bool(exposure_005 and exposure_005 > 0)
    note = (f"Converted from Asiga Composer ({asiga['name']}): exposure per layer height follows the Asiga "
            f"curve by cure depth (layer + {float(asiga.get('offset') or 0):g} mm: {e005:.2f} mJ/cm2 at 0.05 mm). "
            f"Fast mode: the same exposure, the printer's faster lift and retract. " +
            (f"Exposure set from a test: {exposure_005:g} s at 0.05 mm on {calibrated_on}."
             if calibrated else
             "EXPOSURE IS AN ESTIMATE (Asiga 385 nm -> Phrozen 405 nm; the level of Saremco Crowntec on each "
             "printer): run an exposure test and import again with the exposure found at 0.05 mm.") +
            (f" XY shrinkage compensation {shrink:g}% from the file." if shrink else ""))
    return {
        "name": f"{asiga['name']} (Asiga{'' if calibrated else ', estimate'})",
        "vendor": "Imported (Asiga)",
        "technology": "sla",
        "family": "dental",
        "processes": ["lcd"],
        "printers": list(printers),
        "printers_only": True,
        "note": note,
        "values": values,
        "layer_table": layer_table,
        "calibrated": calibrated,
        "report": {                         # i18n keys (the window shows them in the user's language)
            "imported": ["asiga_r_curve", "asiga_r_first", "asiga_r_wait", "asiga_r_shrink"],
            "estimated": [] if calibrated else ["asiga_r_level", "asiga_r_first_exp"],
            "from_printer": ["asiga_r_lift"],
            "left_out": ["asiga_r_cure", "asiga_r_heater", "asiga_r_support", "asiga_r_zcomp"],
            "z_compensation": asiga.get("z_compensation"),
        },
    }
