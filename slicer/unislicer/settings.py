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
    ("raft", "רפסודה (Raft)", "Raft", "grid"),
    ("adhesion", "הידבקות למשטח", "Bed adhesion", "anchor"),
    ("hollow", "חלילה וחורי ניקוז", "Hollowing & drain holes", "drop"),
    ("exposure", "חשיפה ותנועה", "Exposure & motion", "sun"),
    ("laser", "סריקת לייזר (SLA)", "Laser scanning (SLA)", "sun"),
    ("jetting", "הזרקה (PolyJet)", "Jetting (PolyJet)", "drop"),
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
      options=(("fdm", "FDM (פילמנט)", "FDM (filament)"),
               ("sla", "פוטופולימר: SLA / DLP / LCD / PolyJet", "Photopolymer: SLA / DLP / LCD / PolyJet"))),
    S("sla_process", "enum", "lcd", "printer", "sla", "תהליך הדפסה", "Print process",
      options=(("lcd", "LCD / MSLA (מסך)", "LCD / MSLA (masked screen)"), ("dlp", "DLP (מקרן)", "DLP (projector)"),
               ("laser", "SLA (לייזר)", "SLA (laser)"), ("polyjet", "PolyJet (הזרקת חומר)", "PolyJet (material jetting)")),
      tip_he="LCD ו-DLP מאירים שכבה שלמה בבת אחת; SLA סורק בלייזר; PolyJet מזריק טיפות ומקשה ב-UV עם חומר תמיכה ג'ל",
      tip_en="LCD and DLP cure a whole layer at once; SLA scans it with a laser; PolyJet jets droplets and UV-cures them, with gel support"),
    S("bed_x", "float", 220.0, "printer", "both", "רוחב משטח (X)", "Bed width (X)", "mm", 10, 2000, 1),
    S("bed_y", "float", 220.0, "printer", "both", "עומק משטח (Y)", "Bed depth (Y)", "mm", 10, 2000, 1),
    S("max_z", "float", 250.0, "printer", "both", "גובה הדפסה מרבי", "Max build height", "mm", 10, 2000, 1),
    S("plate_width", "float", 0.0, "printer", "both", "רוחב משטח הדפסה (0 = מלא)", "Build plate width (0 = full)", "mm", 0, 2000, 1,
      advanced=True, tip_he="נקבע אוטומטית לפי המשטח שנבחר", tip_en="Set automatically from the chosen plate"),
    S("plate_depth", "float", 0.0, "printer", "both", "עומק משטח הדפסה (0 = מלא)", "Build plate depth (0 = full)", "mm", 0, 2000, 1, advanced=True),
    S("plate_offset_x", "float", 0.0, "printer", "both", "היסט משטח X", "Plate offset X", "mm", -1000, 1000, 0.5, advanced=True),
    S("plate_offset_y", "float", 0.0, "printer", "both", "היסט משטח Y", "Plate offset Y", "mm", -1000, 1000, 0.5, advanced=True),
    S("arrange_margin", "float", 2.0, "printer", "both", "שוליים משולי המשטח בסידור", "Arrange edge margin", "mm", 0, 30, 0.5, advanced=True,
      tip_he="מרחק מינימלי בין המודלים לשולי המשטח בסידור אוטומטי", tip_en="Minimum distance from parts to the plate edge when auto-arranging"),
    S("arrange_spacing", "float", 6.0, "printer", "both", "מרווח בסידור אוטומטי", "Auto-arrange spacing", "mm", 0, 50, 0.5, advanced=True),
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
      options=(("ctb", "CTB (Chitubox v4)", "CTB (Chitubox v4)"), ("goo", "GOO (Elegoo)", "GOO (Elegoo)"),
               ("prz", "PRZ (Phrozen Sonic Mini 8K S)", "PRZ (Phrozen Sonic Mini 8K S)"), ("sl1", "SL1 (Prusa / UVtools)", "SL1 (Prusa / UVtools)"),
               ("zip", "ZIP של CHITUBOX (Phrozen Sonic XL ומדפסות ChiTu)", "CHITUBOX ZIP (Phrozen Sonic XL, ChiTu boards)"),
               ("png_zip", "ZIP של תמונות PNG", "ZIP of PNG images"),
               ("slc", "SLC (קווי מתאר: Asiga Composer, SLA תעשייתי)", "SLC (contours: Asiga Composer, industrial SLA)"),
               ("cli", "CLI (קווי מתאר וקווי סריקה ללייזר)", "CLI (contours and hatches for laser SLA)"),
               ("3mf", "3MF (GrabCAD Print / PreForm / תוכנת היצרן)", "3MF (GrabCAD Print / PreForm / vendor software)"))),

    # ---------------------------------------------------------------- quality
    S("layer_height", "float", 0.2, "quality", "fdm", "גובה שכבה", "Layer height", "mm", 0.04, 1.2, 0.02),
    S("first_layer_height", "float", 0.24, "quality", "fdm", "גובה שכבה ראשונה", "First layer height", "mm", 0.05, 1.2, 0.02),
    S("line_width", "float", 0.0, "quality", "fdm", "רוחב קו (0 = לפי הנחיר)", "Line width (0 = nozzle)", "mm", 0, 3.0, 0.02),
    S("sla_layer_height", "float", 0.05, "quality", "sla", "גובה שכבה", "Layer height", "mm", 0.01, 0.3, 0.01),
    S("sla_adaptive", "bool", False, "quality", "sla", "עובי שכבה אדפטיבי", "Adaptive layer thickness",
      tip_he="שכבות דקות במדרונות מתונים (שם רואים מדרגות) ועבות בקירות זקופים; מקצר את ההדפסה בלי לפגוע במראה",
      tip_en="Thin layers on shallow slopes (where steps show), thick ones on steep walls: shorter prints at the same look"),
    S("sla_layer_min", "float", 0.025, "quality", "sla", "שכבה מינימלית", "Minimum layer", "mm", 0.01, 0.3, 0.005, advanced=True),
    S("sla_layer_max", "float", 0.1, "quality", "sla", "שכבה מקסימלית", "Maximum layer", "mm", 0.02, 0.3, 0.005, advanced=True),
    S("sla_adaptive_error", "float", 0.02, "quality", "sla", "גובה מדרגה מותר", "Allowed step height", "mm", 0.002, 0.2, 0.002, advanced=True,
      tip_he="ככל שקטן יותר, השכבות דקות יותר במדרונות", tip_en="Smaller means thinner layers on slopes"),
    S("sla_z_ranges", "text", "", "quality", "sla", "טווחי גובה (Multi-range)", "Height ranges (multi-range)", advanced=True,
      tip_he="שכבה וחשיפה שונות לפי גובה, כמו Multi-range ב-Asiga Composer. פורמט: מ-עד: שכבה, חשיפה; למשל 0-0.3: *, 40; 5-12: 0.025, 3  (* = ברירת מחדל)",
      tip_en="Different layer height and exposure by height, like Asiga Composer's multi-range. Format: from-to: layer, exposure; e.g. 0-0.3: *, 40; 5-12: 0.025, 3  (* = default)"),
    S("antialiasing", "enum", "2", "quality", "sla", "החלקת קצוות (Anti-aliasing)", "Anti-aliasing",
      options=(("1", "כבוי", "Off"), ("2", "x2", "x2"), ("4", "x4", "x4"), ("8", "x8", "x8"))),
    S("image_blur", "int", 0, "quality", "sla", "טשטוש תמונה", "Image blur", "px", 0, 8, 1,
      tip_he="טשטוש גאוסי של מסכת השכבה, מחליק עוד יותר את הקצוות", tip_en="Gaussian blur of the layer mask for smoother edges"),
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
    S("shrink_xy", "float", 0.0, "material", "sla", "פיצוי התכווצות XY", "Shrinkage compensation XY", "%", -5, 5, 0.05,
      tip_he="מגדיל את המודל כדי לפצות על התכווצות השרף בייבוש ובהקשיה", tip_en="Scales the model up to offset resin shrinkage"),
    S("shrink_z", "float", 0.0, "material", "sla", "פיצוי התכווצות Z", "Shrinkage compensation Z", "%", -5, 5, 0.05),
    S("xy_compensation", "float", 0.0, "material", "sla", "פיצוי מידות XY", "XY size compensation", "mm", -1, 1, 0.01,
      tip_he="חיובי מעבה את הדפנות, שלילי מדק אותן (מתקן חורים צרים מדי או חלקים עבים מדי)",
      tip_en="Positive grows walls, negative shrinks them (fixes holes that print too small)"),
    S("bottom_xy_compensation", "float", 0.0, "material", "sla", "פיצוי רגל פיל (שכבות תחתונות)", "Elephant-foot compensation", "mm", 0, 1, 0.01,
      tip_he="מקטין את השכבות התחתונות, שנחשפות זמן רב ומתרחבות", tip_en="Shrinks the long-exposed bottom layers that tend to spread"),
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
    S("sla_support_preset", "enum", "medium", "support", "sla", "סוג תמיכות", "Support preset",
      options=(("light", "קלות", "Light"), ("medium", "בינוניות", "Medium"), ("heavy", "כבדות", "Heavy"), ("custom", "מותאם אישית", "Custom")),
      tip_he="קובע את קוטר הראש, קוטר העמוד והמרווח. שינוי אחד מהם עובר ל'מותאם אישית'",
      tip_en="Sets tip diameter, pillar diameter and spacing; editing one of them switches to Custom"),
    S("sla_support_type", "enum", "tree", "support", "sla", "צורת תמיכות", "Support shape",
      options=(("tree", "עץ (מסתעפות)", "Tree (branching)"), ("pillar", "עמודים ישרים", "Straight pillars")),
      tip_he="תמיכות עץ מתאחדות לגזעים משותפים: פחות שרף, פחות נקודות מגע עם המשטח וקלות יותר להסרה",
      tip_en="Tree supports merge into shared trunks: less resin, fewer bed contacts, easier removal"),
    S("sla_branch_angle", "float", 35.0, "support", "sla", "זווית הסתעפות", "Branch angle", "°", 5, 75, 1, advanced=True,
      tip_he="הזווית המרבית של ענף ביחס לאנך", tip_en="Maximum branch angle from vertical"),
    S("sla_support_spacing", "float", 3.0, "support", "sla", "מרווח בין נקודות תמיכה", "Support point spacing", "mm", 0.5, 20, 0.5),
    S("sla_support_overhangs", "bool", True, "support", "sla", "תמיכות למשטחים תלויים", "Support overhangs",
      tip_he="נקודות תמיכה ברשת על משטחים שנוטים יותר מזווית הסף", tip_en="Grid of contact points on surfaces steeper than the overhang angle"),
    S("sla_support_minima", "bool", True, "support", "sla", "תמיכות לנקודות נמוכות (מינימום)", "Support minima",
      tip_he="תמיכה בכל נקודה שנמוכה מכל סביבתה (קצה תלוי שמתחיל באוויר)", tip_en="A support at every point lower than everything around it (a tip that would start in mid-air)"),
    S("sla_density", "float", 100.0, "support", "sla", "צפיפות תמיכות", "Support density", "%", 25, 400, 5,
      tip_he="מכפיל את מספר נקודות התמיכה (100% = לפי המרווח). חומר חלש או מודל כבד: יותר; חומר חזק: פחות",
      tip_en="Scales the number of support points (100% = as spaced). Weak resin or heavy part: more; strong resin: fewer"),
    S("sla_head_tilt", "float", 0.0, "support", "sla", "הטיית ראש לכיוון פני השטח", "Head tilt toward the surface", "°", 0, 60, 5,
      advanced=True, tip_he="ראש התמיכה נוטה בניצב לפני השטח עד הזווית הזו: מגע נקי יותר ופחות סימנים במדרונות (0 = אנכי)",
      tip_en="The support head leans to meet the surface square-on, up to this angle: cleaner contact and fewer marks on slopes (0 = vertical)"),
    S("sla_pillar_widening", "float", 0.0, "support", "sla", "הרחבת עמוד לכיוון הבסיס", "Pillar widening toward the base", "%/10mm", 0, 100, 5,
      advanced=True, tip_he="עמודים גבוהים מתעבים כלפי מטה כדי לא להתכופף", tip_en="Tall pillars get thicker toward the base so they don't bend"),
    S("sla_bracing", "bool", False, "support", "sla", "חיזוק אלכסוני בין עמודים", "Cross-bracing between pillars",
      tip_he="מוטות אלכסוניים בין עמודים סמוכים: יציבות לעמודים גבוהים ודקים", tip_en="Diagonal struts between neighbouring pillars: stability for tall, thin supports"),
    S("sla_brace_spacing", "float", 6.0, "support", "sla", "מרווח אנכי בין חיזוקים", "Vertical brace interval", "mm", 2, 30, 0.5, advanced=True),
    S("sla_branch_reach", "float", 0.0, "support", "sla", "מרחק איחוד ענפים (0 = אוטומטי)", "Branch merge distance (0 = auto)", "mm", 0, 40, 0.5,
      advanced=True, tip_he="המרחק המרבי בין ענפים שמתאחדים לגזע אחד", tip_en="Furthest apart two branches may be to join into one trunk"),
    S("sla_tip_diameter", "float", 0.5, "support", "sla", "קוטר ראש התמיכה", "Support tip diameter", "mm", 0.1, 3, 0.05),
    S("sla_pillar_diameter", "float", 1.2, "support", "sla", "קוטר עמוד", "Pillar diameter", "mm", 0.3, 10, 0.1),
    S("sla_tip_shape", "enum", "cone", "support", "sla", "צורת ראש המגע", "Contact shape",
      options=(("cone", "חרוט", "Cone"), ("sphere", "כדור", "Ball"))),
    S("sla_head_length", "float", 0.0, "support", "sla", "אורך ראש המגע (0 = אוטומטי)", "Contact length (0 = auto)", "mm", 0, 10, 0.1, advanced=True),
    S("sla_pillar_shape", "enum", "cylinder", "support", "sla", "צורת עמוד", "Pillar shape",
      options=(("cylinder", "גליל", "Cylinder"), ("prism", "מנסרה משושה", "Hexagonal prism"), ("cube", "ריבוע", "Square")), advanced=True),
    S("sla_base_shape", "enum", "cone", "support", "sla", "צורת בסיס על הרפסודה", "Base shape",
      options=(("cone", "חרוט", "Cone"), ("disk", "דיסק (סקייט)", "Disk (skate)"), ("cube", "קובייה", "Cube"), ("none", "ללא", "None"))),
    S("sla_base_diameter", "float", 0.0, "support", "sla", "קוטר בסיס (0 = כפול מהעמוד)", "Base diameter (0 = 2x pillar)", "mm", 0, 20, 0.1, advanced=True),
    S("sla_base_thickness", "float", 1.0, "support", "sla", "גובה בסיס", "Base height", "mm", 0.1, 10, 0.1, advanced=True),
    S("sla_contact_depth", "float", 0.2, "support", "sla", "עומק חדירת הראש", "Tip contact depth", "mm", 0, 2, 0.05,
      tip_he="כמה עמוק ראש התמיכה נכנס לתוך המודל", tip_en="How far the support tip penetrates the model"),
    S("sla_supports_on_model", "bool", True, "support", "sla", "תמיכות שנשענות על המודל", "Supports may rest on the model"),
    S("sla_elevation", "float", 5.0, "raft", "sla", "הגבהת המודל", "Model elevation", "mm", 0, 50, 0.5),
    S("sla_raft_thickness", "float", 1.0, "raft", "sla", "עובי רפסודה", "Raft thickness", "mm", 0, 10, 0.1),
    S("sla_raft_margin", "float", 2.0, "raft", "sla", "שוליים לרפסודה", "Raft margin", "mm", 0, 20, 0.5),
    S("sla_raft_type", "enum", "solid", "raft", "sla", "סוג רפסודה", "Raft type",
      options=(("solid", "מלאה", "Solid"), ("grid", "רשת", "Cross grid"), ("mini", "מיני (לכל קבוצה)", "Mini rafts"), ("none", "ללא", "None")),
      tip_he="מיני: רפסודה קטנה מתחת לכל קבוצת תמיכות, כמו ב-PreForm", tip_en="Mini: a small raft under each group of supports, as in PreForm"),
    S("sla_raft_area_ratio", "float", 100.0, "raft", "sla", "יחס שטח רפסודה", "Raft area ratio", "%", 50, 300, 5),
    S("sla_raft_slope", "float", 45.0, "raft", "sla", "שיפוע שולי הרפסודה", "Raft edge slope", "°", 10, 90, 5,
      tip_he="שוליים משופעים קלים יותר להפרדה מהמשטח", tip_en="Sloped edges are easier to pry off the plate"),
    S("sla_raft_label", "bool", False, "raft", "sla", "שם המודל על הרפסודה", "Model name on the raft",
      tip_he="השם מוטבע בבליטה בקצה הקדמי של הרפסודה (רפסודה מלאה או רשת)", tip_en="The name is embossed on the front edge of the raft (solid or grid raft)"),
    S("sla_raft_bar_spacing", "float", 5.0, "raft", "sla", "מרווח פסי רשת", "Grid bar spacing", "mm", 1, 30, 0.5,
      tip_he="ברפסודת רשת: המרחק בין הפסים", tip_en="Cross-grid raft: distance between the bars"),
    S("sla_raft_bar_width", "float", 1.2, "raft", "sla", "רוחב פס רשת", "Grid bar width", "mm", 0.4, 5, 0.1),
    S("sla_raft_group_reach", "float", 6.0, "raft", "sla", "מרחק איחוד מיני-רפסודות", "Mini raft joining distance", "mm", 1, 40, 0.5,
      tip_he="תמיכות שקרובות יותר מזה חולקות מיני-רפסודה אחת", tip_en="Supports closer than this share one mini raft"),
    S("sla_internal_supports", "bool", True, "support", "sla", "תמיכות פנימיות בחלל", "Internal supports in cavities",
      tip_he="עמודים בתוך מודל חלול שתומכים בתקרת החלל", tip_en="Pillars inside a hollowed model that hold up the cavity ceiling"),
    S("sla_internal_spacing", "float", 6.0, "support", "sla", "מרווח תמיכות פנימיות", "Internal support spacing", "mm", 2, 30, 0.5, advanced=True),

    # ------------------------------------------------------------- hollowing
    S("hollow_enable", "bool", False, "hollow", "sla", "חלול את המודל", "Hollow the model",
      tip_he="חוסך שרף ומפחית כוחות יניקה. מומלץ להוסיף חורי ניקוז", tip_en="Saves resin and reduces suction; add drain holes"),
    S("hollow_wall", "float", 2.0, "hollow", "sla", "עובי דופן", "Wall thickness", "mm", 0.5, 20, 0.1),
    S("hollow_base_height", "float", 3.0, "hollow", "sla", "גובה בסיס מלא", "Solid base height", "mm", 0.5, 50, 0.5,
      tip_he="במצב 'בסיס מלא': החלק התחתון נשאר מלא בגובה הזה (הידבקות טובה וחוזק), ומעליו המודל חלול",
      tip_en="'Solid base' mode: the bottom stays solid to this height (adhesion and strength), the model is hollow above"),
    S("hollow_mode", "enum", "inner", "hollow", "sla", "סוג חלילה", "Hollow mode",
      options=(("inner", "חלל סגור", "Inner (closed cavity)"), ("bottom", "פתוח מלמטה", "Bottom through (open base)"),
               ("solid_base", "בסיס מלא", "Solid base")),
      tip_he="פתוח מלמטה: בלי רצפה, כך שהשרף מתנקז בלי חורים", tip_en="Bottom through: no floor, so resin drains without holes"),
    S("hollow_infill", "enum", "none", "hollow", "sla", "מילוי פנימי", "Infill",
      options=(("none", "ללא", "None"), ("grid", "רשת", "Grid"), ("hive", "חלת דבש", "Hive (honeycomb)"))),
    S("hollow_infill_spacing", "float", 6.0, "hollow", "sla", "מרווח מילוי", "Infill spacing", "mm", 2, 40, 0.5),
    S("hollow_infill_thickness", "float", 0.8, "hollow", "sla", "עובי דפנות המילוי", "Infill wall thickness", "mm", 0.3, 5, 0.1),
    S("drain_holes", "int", 2, "hollow", "sla", "מספר חורי ניקוז", "Drain holes", "", 0, 8, 1,
      tip_he="חורים בתחתית החלל, כדי שהשרף הנוזלי יתנקז החוצה", tip_en="Holes at the bottom of the cavity so liquid resin can drain"),
    S("drain_hole_depth", "float", 0.0, "hollow", "sla", "עומק חור ידני (0 = אוטומטי)", "Manual hole depth (0 = auto)", "mm", 0, 50, 0.5,
      tip_he="אוטומטי: עובי הדופן ועוד 1 מ\"מ, כך שהחור חודר לחלל", tip_en="Auto: wall thickness plus 1 mm, so the hole reaches the cavity"),
    S("drain_hole_diameter", "float", 3.0, "hollow", "sla", "קוטר חור ניקוז", "Drain hole diameter", "mm", 0.5, 20, 0.5),

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
    S("transition_layers", "int", 0, "exposure", "sla", "שכבות מעבר", "Transition layers", "", 0, 50, 1,
      tip_he="זמן החשיפה יורד בהדרגה מזמן השכבות התחתונות לזמן הרגיל", tip_en="Exposure steps down gradually from bottom to normal"),
    S("light_pwm", "int", 255, "exposure", "sla", "עוצמת אור (PWM)", "Light intensity (PWM)", "", 1, 255, 5, advanced=True),
    S("bottom_lift_height", "float", 8.0, "exposure", "sla", "גובה הרמה בשכבות תחתונות", "Bottom lift height", "mm", 0.5, 30, 0.5),
    S("bottom_lift_speed", "float", 40.0, "exposure", "sla", "מהירות הרמה בשכבות תחתונות", "Bottom lift speed", "mm/min", 5, 600, 5),
    S("retract_speed", "float", 150.0, "exposure", "sla", "מהירות חזרה", "Retract speed", "mm/min", 5, 600, 5),
    S("bottom_retract_speed", "float", 150.0, "exposure", "sla", "מהירות חזרה בשכבות תחתונות", "Bottom retract speed", "mm/min", 5, 600, 5, advanced=True),
    S("light_pwm_bottom", "int", 255, "exposure", "sla", "עוצמת אור בשכבות תחתונות (PWM)", "Bottom light intensity (PWM)", "", 1, 255, 5, advanced=True),
    S("wait_mode", "enum", "rest", "exposure", "sla", "מצב המתנה", "Waiting mode",
      options=(("rest", "זמני המתנה (מומלץ)", "Resting time (recommended)"), ("light_off", "השהיית כיבוי (ישן)", "Light-off delay (legacy)")),
      tip_he="זמני המתנה נפרדים לפני הרמה, אחרי הרמה ואחרי חזרה; או זמן כולל אחד (מצב ישן)",
      tip_en="Separate rests before lift, after lift and after retract; or one total delay (legacy)"),
    S("rest_before_lift", "float", 0.0, "exposure", "sla", "המתנה לפני הרמה", "Rest before lift", "s", 0, 30, 0.5),
    S("rest_after_lift", "float", 0.0, "exposure", "sla", "המתנה אחרי הרמה", "Rest after lift", "s", 0, 30, 0.5),
    S("rest_after_retract", "float", 1.0, "exposure", "sla", "המתנה אחרי חזרה (לפני חשיפה)", "Rest after retract", "s", 0, 30, 0.5),
    S("bottom_rest_before_lift", "float", 0.0, "exposure", "sla", "המתנה לפני הרמה (תחתונות)", "Bottom rest before lift", "s", 0, 60, 0.5, advanced=True),
    S("bottom_rest_after_lift", "float", 0.0, "exposure", "sla", "המתנה אחרי הרמה (תחתונות)", "Bottom rest after lift", "s", 0, 60, 0.5, advanced=True),
    S("bottom_rest_after_retract", "float", 1.0, "exposure", "sla", "המתנה אחרי חזרה (תחתונות)", "Bottom rest after retract", "s", 0, 60, 0.5, advanced=True),
    S("light_off_delay", "float", 1.0, "exposure", "sla", "השהיית כיבוי (מצב ישן)", "Light-off delay (legacy)", "s", 0, 60, 0.5,
      tip_he="זמן כולל של הרמה, המתנה וחזרה; בשימוש רק במצב המתנה ישן", tip_en="Total time of lift, wait and retract; used only in the legacy waiting mode"),
    S("bottom_light_off_delay", "float", 1.0, "exposure", "sla", "השהיית כיבוי בשכבות תחתונות", "Bottom light-off delay", "s", 0, 60, 0.5, advanced=True),
    # laser SLA
    S("laser_spot", "float", 0.085, "laser", "sla", "קוטר נקודת הלייזר", "Laser spot diameter", "mm", 0.01, 1, 0.005,
      tip_he="קווי המתאר מוזזים פנימה בחצי קוטר (פיצוי קרן)", tip_en="Contours are offset inward by half of it (beam compensation)"),
    S("laser_contours", "int", 1, "laser", "sla", "מספר קווי מתאר", "Contour passes", "", 0, 5, 1),
    S("laser_hatch_spacing", "float", 0.1, "laser", "sla", "מרווח קווי סריקה", "Hatch spacing", "mm", 0.01, 2, 0.01),
    S("laser_hatch_angle", "float", 45.0, "laser", "sla", "זווית סריקה", "Hatch angle", "°", 0, 180, 1),
    S("laser_hatch_rotation", "float", 90.0, "laser", "sla", "סיבוב הסריקה בכל שכבה", "Hatch rotation per layer", "°", 0, 180, 1),
    S("laser_scan_speed", "float", 3000.0, "laser", "sla", "מהירות סריקה", "Scan speed", "mm/s", 10, 20000, 50,
      tip_he="משמשת להערכת זמן ההדפסה", tip_en="Used for the print time estimate"),
    S("laser_recoat_time", "float", 6.0, "laser", "sla", "זמן קילוף/ציפוי לשכבה", "Peel / recoat time per layer", "s", 0, 120, 0.5),

    # PolyJet
    S("pj_mode", "enum", "high_quality", "jetting", "sla", "מצב הדפסה", "Print mode",
      options=(("high_quality", "איכות גבוהה", "High quality"), ("high_speed", "מהירות גבוהה", "High speed"),
               ("high_mix", "ריבוי חומרים", "High mix"))),
    S("pj_finish", "enum", "matte", "jetting", "sla", "גימור פני שטח", "Surface finish",
      options=(("matte", "מט (מצופה בתמיכה)", "Matte (coated in support)"), ("glossy", "מבריק (תמיכה רק היכן שצריך)", "Glossy (support only where needed)"))),
    S("pj_material", "text", "VeroWhite", "jetting", "sla", "חומר ברירת מחדל", "Default model material",
      tip_he="שם החומר לחלקים שלא הוקצה להם חומר (קליק ימני על מודל: חומר)", tip_en="Material for parts without their own (right-click a model: Material)"),
    S("pj_support_material", "text", "SUP706", "jetting", "sla", "חומר תמיכה", "Support material"),
    S("pj_coat", "float", 0.1, "jetting", "sla", "עובי ציפוי תמיכה במצב מט", "Matte support coating", "mm", 0, 1, 0.05, advanced=True),
    S("pj_build_speed", "float", 12.0, "jetting", "sla", "קצב בנייה אנכי (הערכה)", "Vertical build rate (estimate)", "mm/h", 1, 100, 0.5,
      tip_he="להערכת זמן ההדפסה בלבד; הזמן המדויק מחושב ב-GrabCAD Print", tip_en="For the time estimate only; GrabCAD Print computes the exact time"),
    S("pj_model_cost", "float", 300.0, "jetting", "sla", "מחיר חומר מודל", "Model material price", "/kg", 0, 10000, 5),
    S("pj_support_cost", "float", 120.0, "jetting", "sla", "מחיר חומר תמיכה", "Support material price", "/kg", 0, 10000, 5),

    S("tsmc_enable", "bool", False, "exposure", "sla", "תנועה דו-שלבית (TSMC)", "Two-stage motion (TSMC)",
      tip_he="הרמה איטית בהתחלה ואז מהירה, וחזרה מהירה ואז איטית ליד המיכל", tip_en="Slow then fast lift; fast then slow retract near the vat"),
    S("lift_height_2", "float", 4.0, "exposure", "sla", "גובה הרמה שלב 2", "Lift height, stage 2", "mm", 0, 30, 0.5, advanced=True),
    S("lift_speed_2", "float", 180.0, "exposure", "sla", "מהירות הרמה שלב 2", "Lift speed, stage 2", "mm/min", 5, 600, 5, advanced=True),
    S("retract_height_2", "float", 2.0, "exposure", "sla", "גובה חזרה איטית (שלב 2)", "Slow retract distance, stage 2", "mm", 0, 30, 0.5, advanced=True),
    S("retract_speed_2", "float", 60.0, "exposure", "sla", "מהירות חזרה שלב 2", "Retract speed, stage 2", "mm/min", 5, 600, 5, advanced=True),

    # ------------------------------------------------------------------ gcode
    S("relative_e", "bool", False, "gcode", "fdm", "אקסטרוזיה יחסית (M83)", "Relative extrusion (M83)"),
    S("start_gcode", "text", "", "gcode", "fdm", "G-code התחלה (ריק = ברירת מחדל לקושחה)", "Start G-code (empty = firmware default)"),
    S("end_gcode", "text", "", "gcode", "fdm", "G-code סיום (ריק = ברירת מחדל לקושחה)", "End G-code (empty = firmware default)"),
]

SCHEMA_BY_KEY = {s.key: s for s in SCHEMA}

# resin support presets: (tip diameter, pillar diameter, point spacing) in mm
SUPPORT_PRESETS = {
    "light": {"sla_tip_diameter": 0.3, "sla_pillar_diameter": 0.8, "sla_support_spacing": 3.5},
    "medium": {"sla_tip_diameter": 0.5, "sla_pillar_diameter": 1.2, "sla_support_spacing": 3.0},
    "heavy": {"sla_tip_diameter": 0.8, "sla_pillar_diameter": 1.8, "sla_support_spacing": 2.5},
}


# which settings each photopolymer process uses: hidden categories and keys per process
PROCESS_HIDES = {
    "lcd": {"categories": {"laser", "jetting"}, "keys": set()},
    "dlp": {"categories": {"laser", "jetting"}, "keys": set()},
    "laser": {"categories": {"jetting"},
              "keys": {"exposure_time", "bottom_exposure_time", "light_pwm", "light_pwm_bottom", "antialiasing", "image_blur",
                       "transition_layers", "light_off_delay", "bottom_light_off_delay", "resolution_x", "resolution_y"}},
    "polyjet": {"categories": {"laser", "exposure", "hollow", "raft"},
                "keys": {"resolution_x", "resolution_y", "antialiasing", "image_blur", "mirror_x", "mirror_y",
                         "sla_adaptive", "sla_layer_min", "sla_layer_max", "sla_adaptive_error", "sla_z_ranges"}},
}
POLYJET_SUPPORT_KEYS = {"support_enable"}     # gel support is automatic; the resin support settings don't apply


def applies_to_process(setting: Setting, process: str) -> bool:
    """False if a photopolymer setting is irrelevant for ``process`` (e.g. exposure time for PolyJet)."""
    if setting.tech != "sla" and setting.category not in ("laser", "jetting"):
        return True
    hide = PROCESS_HIDES.get(process)
    if hide is None:
        return True
    if process == "polyjet" and setting.category == "support":
        return setting.key in POLYJET_SUPPORT_KEYS
    return setting.category not in hide["categories"] and setting.key not in hide["keys"]


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
        preset = SUPPORT_PRESETS.get(self.values.get("sla_support_preset"))
        if preset and "sla_support_preset" in overrides:
            for k, v in preset.items():
                if k not in overrides:
                    self.values[k] = v
        return self

    def copy(self) -> "Config":
        return Config(dict(self.values))

    @property
    def line_width(self) -> float:
        w = float(self.values["line_width"])
        return w if w > 0 else float(self.values["nozzle_diameter"]) * 1.05
