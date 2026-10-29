"""Settings schema. One table drives defaults, profiles, the CLI and the GUI settings panel."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str                     # float | int | bool | enum | text
    default: Any
    category: str
    tech: str                     # fdm | sla | both
    he: str
    en: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float = 0.1
    options: tuple = ()           # ((value, he, en), ...) for enums
    advanced: bool = False
    tip_he: str = ""
    tip_en: str = ""


CATEGORIES = [
    # key, he, en, icon
    ("printer", "מדפסת", "Printer", "printer"),
    ("quality", "איכות", "Quality", "layers"),
    ("walls", "קירות ומשטחים", "Walls & surfaces", "square"),
    ("infill", "מילוי", "Infill", "grid"),
    ("material", "חומר וטמפרטורה", "Material & temperature", "flame"),
    ("speed", "מהירות", "Speed", "gauge"),
    ("retraction", "נסיגה", "Retraction", "undo"),
    ("support", "תמיכות", "Supports", "pillar"),
    ("adhesion", "הידבקות למשטח", "Bed adhesion", "anchor"),
    ("exposure", "חשיפה", "Exposure", "sun"),
    ("gcode", "G-code", "G-code", "code"),
]

FLAVORS = (
    ("marlin", "Marlin", "Marlin"),
    ("klipper", "Klipper", "Klipper"),
    ("reprap", "RepRapFirmware", "RepRapFirmware"),
    ("prusa", "Prusa (Marlin 2)", "Prusa (Marlin 2)"),
    ("smoothie", "Smoothieware", "Smoothieware"),
)

INFILL_PATTERNS = (
    ("grid", "רשת", "Grid"),
    ("lines", "קווים", "Lines"),
    ("triangles", "משולשים", "Triangles"),
    ("gyroid", "ג'ירואיד", "Gyroid"),
    ("concentric", "קונצנטרי", "Concentric"),
)

S = Setting
SCHEMA: list[Setting] = [
    # ---------------------------------------------------------------- printer
    S("technology", "enum", "fdm", "printer", "both", "טכנולוגיה", "Technology",
      options=(("fdm", "FDM (פילמנט)", "FDM (filament)"), ("sla", "SLA / MSLA (שרף)", "SLA / MSLA (resin)"))),
    S("bed_x", "float", 220.0, "printer", "both", "רוחב משטח (X)", "Bed width (X)", "mm", 10, 2000, 1),
    S("bed_y", "float", 220.0, "printer", "both", "עומק משטח (Y)", "Bed depth (Y)", "mm", 10, 2000, 1),
    S("max_z", "float", 250.0, "printer", "both", "גובה הדפסה מרבי", "Max build height", "mm", 10, 2000, 1),
    S("origin_center", "bool", False, "printer", "fdm", "ראשית במרכז המשטח", "Origin at bed center",
      tip_he="סמן עבור מדפסות דלתא ומדפסות שהראשית שלהן במרכז", tip_en="Enable for delta printers"),
    S("nozzle_diameter", "float", 0.4, "printer", "fdm", "קוטר נחיר", "Nozzle diameter", "mm", 0.1, 2.0, 0.05),
    S("filament_diameter", "float", 1.75, "printer", "fdm", "קוטר פילמנט", "Filament diameter", "mm", 1.0, 3.5, 0.05),
    S("heated_bed", "bool", True, "printer", "fdm", "משטח מחומם", "Heated bed"),
    S("gcode_flavor", "enum", "marlin", "printer", "fdm", "קושחה (סוג G-code)", "Firmware (G-code flavor)", options=FLAVORS),
    S("resolution_x", "int", 2560, "printer", "sla", "רזולוציית מסך X", "Screen resolution X", "px", 100, 20000, 1),
    S("resolution_y", "int", 1620, "printer", "sla", "רזולוציית מסך Y", "Screen resolution Y", "px", 100, 20000, 1),
    S("mirror_x", "bool", False, "printer", "sla", "שיקוף אופקי", "Mirror X"),
    S("mirror_y", "bool", False, "printer", "sla", "שיקוף אנכי", "Mirror Y"),
    S("sla_format", "enum", "sl1", "printer", "sla", "פורמט קובץ פלט", "Output format",
      options=(("sl1", "SL1 (Prusa / UVtools)", "SL1 (Prusa / UVtools)"), ("png_zip", "ZIP של תמונות PNG", "ZIP of PNG images"))),

    # ---------------------------------------------------------------- quality
    S("layer_height", "float", 0.2, "quality", "fdm", "גובה שכבה", "Layer height", "mm", 0.04, 1.2, 0.02),
    S("first_layer_height", "float", 0.24, "quality", "fdm", "גובה שכבה ראשונה", "First layer height", "mm", 0.05, 1.2, 0.02),
    S("line_width", "float", 0.0, "quality", "fdm", "רוחב קו (0 = לפי הנחיר)", "Line width (0 = nozzle)", "mm", 0, 3.0, 0.02),
    S("sla_layer_height", "float", 0.05, "quality", "sla", "גובה שכבה", "Layer height", "mm", 0.01, 0.3, 0.01),
    S("antialiasing", "enum", "2", "quality", "sla", "החלקת קצוות (Anti-aliasing)", "Anti-aliasing",
      options=(("1", "כבוי", "Off"), ("2", "x2", "x2"), ("4", "x4", "x4"))),
    S("resolution", "float", 0.02, "quality", "both", "רזולוציית קווי מתאר", "Contour resolution", "mm", 0.001, 0.5, 0.005,
      advanced=True, tip_he="פישוט קווי המתאר. ערך גבוה = קובץ קטן ומהיר יותר", tip_en="Contour simplification tolerance"),

    # ------------------------------------------------------------------ walls
    S("wall_count", "int", 3, "walls", "fdm", "מספר קירות", "Wall count", "", 0, 20, 1),
    S("top_layers", "int", 4, "walls", "fdm", "שכבות עליונות", "Top layers", "", 0, 50, 1),
    S("bottom_layers", "int", 3, "walls", "fdm", "שכבות תחתונות", "Bottom layers", "", 0, 50, 1),
    S("outer_wall_first", "bool", False, "walls", "fdm", "קיר חיצוני קודם", "Outer wall first"),
    S("seam_position", "enum", "aligned", "walls", "fdm", "מיקום תפר", "Seam position",
      options=(("aligned", "מיושר (אחורה)", "Aligned (rear)"), ("nearest", "הקרוב ביותר", "Nearest"), ("random", "אקראי", "Random"))),
    S("skin_overlap", "float", 15.0, "walls", "fdm", "חפיפת מילוי לקירות", "Infill/wall overlap", "%", 0, 100, 1, advanced=True),

    # ----------------------------------------------------------------- infill
    S("infill_density", "float", 20.0, "infill", "fdm", "צפיפות מילוי", "Infill density", "%", 0, 100, 1),
    S("infill_pattern", "enum", "grid", "infill", "fdm", "תבנית מילוי", "Infill pattern", options=INFILL_PATTERNS),
    S("infill_angle", "float", 45.0, "infill", "fdm", "זווית מילוי", "Infill angle", "°", 0, 180, 5, advanced=True),

    # --------------------------------------------------------------- material
    S("material_name", "text", "PLA", "material", "both", "שם החומר", "Material name"),
    S("nozzle_temp", "int", 205, "material", "fdm", "טמפרטורת נחיר", "Nozzle temperature", "°C", 0, 450, 5),
    S("first_layer_nozzle_temp", "int", 210, "material", "fdm", "טמפ' נחיר בשכבה ראשונה", "First layer nozzle temp", "°C", 0, 450, 5),
    S("bed_temp", "int", 60, "material", "fdm", "טמפרטורת משטח", "Bed temperature", "°C", 0, 150, 5),
    S("first_layer_bed_temp", "int", 65, "material", "fdm", "טמפ' משטח בשכבה ראשונה", "First layer bed temp", "°C", 0, 150, 5),
    S("fan_speed", "int", 100, "material", "fdm", "מהירות מאוורר", "Fan speed", "%", 0, 100, 5),
    S("fan_start_layer", "int", 2, "material", "fdm", "הפעלת מאוורר משכבה", "Fan starts at layer", "", 1, 100, 1),
    S("flow", "float", 100.0, "material", "fdm", "זרימה (Flow)", "Flow", "%", 50, 150, 1),
    S("filament_density", "float", 1.24, "material", "fdm", "צפיפות החומר", "Material density", "g/cm³", 0.5, 3.0, 0.01, advanced=True),
    S("filament_cost", "float", 80.0, "material", "fdm", "מחיר לק\"ג", "Cost per kg", "₪", 0, 2000, 1, advanced=True),
    S("min_layer_time", "float", 6.0, "material", "fdm", "זמן שכבה מינימלי", "Minimum layer time", "s", 0, 60, 1, advanced=True),
    S("resin_density", "float", 1.1, "material", "sla", "צפיפות השרף", "Resin density", "g/ml", 0.8, 2.0, 0.01, advanced=True),
    S("resin_cost", "float", 150.0, "material", "sla", "מחיר לליטר", "Cost per liter", "₪", 0, 2000, 1, advanced=True),

    # ------------------------------------------------------------------ speed
    S("print_speed", "float", 60.0, "speed", "fdm", "מהירות מילוי", "Infill speed", "mm/s", 1, 1000, 5),
    S("wall_speed", "float", 45.0, "speed", "fdm", "מהירות קירות פנימיים", "Inner wall speed", "mm/s", 1, 1000, 5),
    S("outer_wall_speed", "float", 30.0, "speed", "fdm", "מהירות קיר חיצוני", "Outer wall speed", "mm/s", 1, 1000, 5),
    S("top_bottom_speed", "float", 40.0, "speed", "fdm", "מהירות משטחים עליונים/תחתונים", "Top/bottom speed", "mm/s", 1, 1000, 5),
    S("support_speed", "float", 50.0, "speed", "fdm", "מהירות תמיכות", "Support speed", "mm/s", 1, 1000, 5),
    S("travel_speed", "float", 150.0, "speed", "fdm", "מהירות תנועה", "Travel speed", "mm/s", 1, 2000, 10),
    S("first_layer_speed", "float", 20.0, "speed", "fdm", "מהירות שכבה ראשונה", "First layer speed", "mm/s", 1, 500, 5),
    S("min_print_speed", "float", 10.0, "speed", "fdm", "מהירות מינימלית", "Minimum speed", "mm/s", 1, 100, 1, advanced=True),

    # ------------------------------------------------------------- retraction
    S("retract_length", "float", 5.0, "retraction", "fdm", "אורך נסיגה", "Retraction length", "mm", 0, 20, 0.1),
    S("retract_speed", "float", 45.0, "retraction", "fdm", "מהירות נסיגה", "Retraction speed", "mm/s", 1, 200, 1),
    S("retract_min_travel", "float", 1.5, "retraction", "fdm", "מרחק מינימלי לנסיגה", "Minimum travel for retraction", "mm", 0, 50, 0.1),
    S("z_hop", "float", 0.0, "retraction", "fdm", "הרמת Z בתנועה", "Z hop", "mm", 0, 10, 0.1),
    S("firmware_retraction", "bool", False, "retraction", "fdm", "נסיגת קושחה (G10/G11)", "Firmware retraction (G10/G11)", advanced=True),

    # ---------------------------------------------------------------- support
    S("support_enable", "bool", False, "support", "both", "צור תמיכות", "Generate supports"),
    S("support_type", "enum", "buildplate", "support", "fdm", "מיקום תמיכות", "Support placement",
      options=(("buildplate", "מהמשטח בלבד", "From build plate only"), ("everywhere", "בכל מקום", "Everywhere"))),
    S("support_angle", "float", 50.0, "support", "both", "זווית סף לתמיכה", "Overhang angle", "°", 0, 89, 1,
      tip_he="משטחים שנוטים יותר מזווית זו (ביחס לאנך) יקבלו תמיכה", tip_en="Surfaces steeper than this (from vertical) get support"),
    S("support_density", "float", 15.0, "support", "fdm", "צפיפות תמיכות", "Support density", "%", 1, 100, 1),
    S("support_xy_gap", "float", 0.7, "support", "fdm", "מרווח אופקי מהמודל", "Horizontal gap to model", "mm", 0, 5, 0.1),
    S("support_z_gap_layers", "int", 1, "support", "fdm", "שכבות מרווח מעל התמיכה", "Top gap layers", "", 0, 10, 1),
    S("support_interface_layers", "int", 2, "support", "fdm", "שכבות ממשק צפופות", "Dense interface layers", "", 0, 10, 1),
    S("sla_support_spacing", "float", 3.0, "support", "sla", "מרווח בין נקודות תמיכה", "Support point spacing", "mm", 0.5, 20, 0.5),
    S("sla_tip_diameter", "float", 0.5, "support", "sla", "קוטר ראש התמיכה", "Support tip diameter", "mm", 0.1, 3, 0.05),
    S("sla_pillar_diameter", "float", 1.2, "support", "sla", "קוטר עמוד", "Pillar diameter", "mm", 0.3, 10, 0.1),
    S("sla_elevation", "float", 5.0, "support", "sla", "הגבהת המודל", "Model elevation", "mm", 0, 50, 0.5),
    S("sla_raft_thickness", "float", 1.0, "support", "sla", "עובי רפסודה", "Raft thickness", "mm", 0, 10, 0.1),

    # --------------------------------------------------------------- adhesion
    S("adhesion_type", "enum", "skirt", "adhesion", "fdm", "סוג הידבקות", "Adhesion type",
      options=(("none", "ללא", "None"), ("skirt", "חצאית (Skirt)", "Skirt"), ("brim", "שוליים (Brim)", "Brim"))),
    S("skirt_loops", "int", 2, "adhesion", "fdm", "מספר לולאות חצאית", "Skirt loops", "", 1, 20, 1),
    S("skirt_distance", "float", 4.0, "adhesion", "fdm", "מרחק חצאית", "Skirt distance", "mm", 0, 50, 0.5),
    S("brim_width", "float", 6.0, "adhesion", "fdm", "רוחב שוליים", "Brim width", "mm", 0, 50, 0.5),

    # --------------------------------------------------------------- exposure
    S("exposure_time", "float", 2.5, "exposure", "sla", "זמן חשיפה", "Exposure time", "s", 0.1, 120, 0.1),
    S("bottom_exposure_time", "float", 30.0, "exposure", "sla", "זמן חשיפה לשכבות תחתונות", "Bottom exposure time", "s", 1, 300, 1),
    S("bottom_layers", "int", 5, "exposure", "sla", "מספר שכבות תחתונות", "Bottom layer count", "", 0, 100, 1),
    S("lift_height", "float", 6.0, "exposure", "sla", "גובה הרמה", "Lift height", "mm", 0.5, 30, 0.5),
    S("lift_speed", "float", 60.0, "exposure", "sla", "מהירות הרמה", "Lift speed", "mm/min", 5, 600, 5),
    S("light_off_delay", "float", 1.0, "exposure", "sla", "השהיה לפני חשיפה", "Light-off delay", "s", 0, 30, 0.5),

    # ------------------------------------------------------------------ gcode
    S("relative_e", "bool", False, "gcode", "fdm", "אקסטרוזיה יחסית (M83)", "Relative extrusion (M83)"),
    S("start_gcode", "text", "", "gcode", "fdm", "G-code התחלה (ריק = ברירת מחדל לקושחה)", "Start G-code (empty = firmware default)"),
    S("end_gcode", "text", "", "gcode", "fdm", "G-code סיום (ריק = ברירת מחדל לקושחה)", "End G-code (empty = firmware default)"),
]

SCHEMA_BY_KEY = {s.key: s for s in SCHEMA}


def defaults() -> dict[str, Any]:
    return {s.key: s.default for s in SCHEMA}


def coerce(key: str, value: Any) -> Any:
    """Convert a raw value (e.g. a CLI string) into the setting's type, clamped to its range."""
    s = SCHEMA_BY_KEY[key]
    if s.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if s.kind in ("float", "int"):
        v = float(value)
        if s.minimum is not None:
            v = max(s.minimum, v)
        if s.maximum is not None:
            v = min(s.maximum, v)
        return int(round(v)) if s.kind == "int" else v
    if s.kind == "enum":
        value = str(value)
        allowed = [o[0] for o in s.options]
        if value not in allowed:
            raise ValueError(f"{key}: '{value}' is not one of {allowed}")
        return value
    return str(value)


def visible_settings(category: str, technology: str, show_advanced: bool) -> list[Setting]:
    return [s for s in SCHEMA if s.category == category and s.tech in (technology, "both")
            and (show_advanced or not s.advanced)]


@dataclass
class Config:
    """Effective settings: defaults <- printer <- material <- quality <- user overrides."""
    values: dict = field(default_factory=defaults)

    def __getitem__(self, key):
        return self.values[key]

    def get(self, key, default=None):
        return self.values.get(key, default)

    def update(self, overrides: dict) -> "Config":
        for k, v in overrides.items():
            if k in SCHEMA_BY_KEY:
                self.values[k] = coerce(k, v)
        return self

    def copy(self) -> "Config":
        return Config(dict(self.values))

    @property
    def line_width(self) -> float:
        w = float(self.values["line_width"])
        return w if w > 0 else float(self.values["nozzle_diameter"]) * 1.05
