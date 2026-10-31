"""Multi-filament (multi-colour) FDM: the filament list, purge volumes and colour helpers.

One nozzle, several filaments fed by a changer (Creality CFS, Bambu AMS, Prusa MMU, Klipper ERCF...):
each model prints with its own filament, and the printer swaps filament between them with T<n>
(n = filament number - 1). The purge needed at a change grows with the colour difference, most of all
from dark to light, as in Creality Print / OrcaSlicer."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

DEFAULT_COLOURS = ("#FCE94F", "#2E3436", "#EF2929", "#3465A4", "#73D216", "#F57900", "#75507B", "#EEEEEC")
MIN_PURGE = 100.0            # mm³, as Creality Print
MAX_PURGE = 800.0
PURGE_ONE_TIME = 135.0       # mm³ per purge cycle at the chute (Creality Print g_purge_volume_one_time)
MAX_PURGE_CYCLES = 5


@dataclass
class Filament:
    index: int               # 0-based tool number (T<index>)
    colour: str              # #RRGGBB
    type: str                # PLA, PETG...
    temp: int                # nozzle temperature
    first_temp: int          # first layer


def _split(text: str) -> list[str]:
    return [t.strip() for t in str(text or "").split(";")]


def normalise_colour(c: str, fallback: str = "#FCE94F") -> str:
    c = (c or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
        return "#" + c.lstrip("#").upper()
    if re.fullmatch(r"#?[0-9a-fA-F]{8}", c):           # #RRGGBBAA from 3MF
        return "#" + c.lstrip("#")[:6].upper()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", c):
        h = c.lstrip("#")
        return "#" + "".join(ch * 2 for ch in h).upper()
    return fallback


def filament_list(cfg) -> list[Filament]:
    """The print's filaments with colour, type and temperatures (missing entries fall back sensibly)."""
    n = max(1, int(cfg["filament_count"]))
    colours, types, temps = _split(cfg["filament_colors"]), _split(cfg["filament_types"]), _split(cfg["filament_temps"])
    out = []
    for i in range(n):
        colour = normalise_colour(colours[i] if i < len(colours) else "", DEFAULT_COLOURS[i % len(DEFAULT_COLOURS)])
        ftype = (types[i] if i < len(types) and types[i] else (types[0] if types and types[0] else "PLA")).upper()
        try:
            temp = int(float(temps[i])) if i < len(temps) and temps[i] else int(cfg["nozzle_temp"])
        except ValueError:
            temp = int(cfg["nozzle_temp"])
        first = temp + int(cfg["first_layer_nozzle_temp"]) - int(cfg["nozzle_temp"])
        out.append(Filament(i, colour, ftype, temp, first))
    return out


def rgb(colour: str) -> tuple[float, float, float]:
    c = normalise_colour(colour).lstrip("#")
    return tuple(int(c[k:k + 2], 16) / 255.0 for k in (0, 2, 4))


def _lab(colour: str) -> tuple[float, float, float]:
    def lin(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb(colour))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def purge_volume(from_colour: str, to_colour: str, multiplier: float = 1.0) -> float:
    """mm³ of filament to purge when changing colour: more for bigger differences and dark to light."""
    l1, a1, b1 = _lab(from_colour)
    l2, a2, b2 = _lab(to_colour)
    delta = math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)
    lighter = max(0.0, l2 - l1)
    vol = MIN_PURGE + 2.6 * delta + 3.6 * lighter
    return float(min(MAX_PURGE, max(MIN_PURGE, vol)) * max(0.1, multiplier))


def purge_cycles(volume: float, filament_diameter: float) -> list[float]:
    """Creality Print's split of a purge into chute cycles: round(volume / 135) cycles (1..5) of equal length (mm)."""
    if volume <= 0:
        return []
    area = math.pi / 4 * filament_diameter ** 2
    length = volume / area
    count = min(MAX_PURGE_CYCLES, max(1, int(round(volume / PURGE_ONE_TIME))))
    return [length / count] * count


def tools_in_use(tools: list[int]) -> list[int]:
    return sorted(set(int(t) for t in tools))


def colours_to_filaments(colours: list[str], limit: int = 16) -> tuple[list[str], list[int]]:
    """Distinct model colours (e.g. from a 3MF) -> (filament colour list, filament number per model, 1-based)."""
    palette: list[str] = []
    numbers = []
    for c in colours:
        c = normalise_colour(c, "") if c else ""
        if not c:
            numbers.append(1)
            continue
        if c not in palette and len(palette) < limit:
            palette.append(c)
        numbers.append(palette.index(c) + 1 if c in palette else 1)
    return palette, numbers
