"""Hebrew / English UI strings."""
from __future__ import annotations

LANG = "he"

STRINGS = {
    "app_subtitle": ("סלייסר אוניברסלי למדפסות תלת-ממד", "Universal 3D printer slicer"),
    "open": ("פתח מודל", "Open model"),
    "open_tip": ("פתיחת קובצי STL / OBJ / 3MF (Ctrl+O)", "Open STL / OBJ / 3MF files (Ctrl+O)"),
    "shapes": ("צורות", "Shapes"),
    "shape_cube": ("קובייה 20 מ\"מ", "Cube 20 mm"),
    "shape_cylinder": ("גליל", "Cylinder"),
    "shape_sphere": ("כדור", "Sphere"),
    "shape_bridge": ("גשר (בדיקת תמיכות)", "Bridge (support test)"),
    "arrange": ("סדר", "Arrange"),
    "arrange_tip": ("סידור אוטומטי של כל המודלים על המשטח (A)", "Auto-arrange all models (A)"),
    "duplicate": ("שכפל", "Duplicate"),
    "delete": ("מחק", "Delete"),
    "prepare": ("הכנה", "Prepare"),
    "preview": ("תצוגה מקדימה", "Preview"),
    "language": ("English", "עברית"),
    "language_tip": ("Switch to English", "החלף לעברית"),
    "objects": ("מודלים", "Objects"),
    "no_objects": ("גרור לכאן קובצי STL, OBJ או 3MF\nאו לחץ על \"פתח מודל\"", "Drop STL, OBJ or 3MF files here\nor click \"Open model\""),
    "transform": ("מיקום וטרנספורמציה", "Transform"),
    "position": ("מיקום", "Position"),
    "rotation": ("סיבוב", "Rotation"),
    "scale": ("קנה מידה", "Scale"),
    "size": ("גודל", "Size"),
    "uniform": ("אחיד", "Uniform"),
    "lay_flat": ("השכב על פאה", "Lay flat"),
    "lay_flat_tip": ("סובב כך שהפאה השטוחה הגדולה תונח על המשטח", "Rotate so the largest flat face rests on the bed"),
    "center": ("למרכז", "Center"),
    "reset_transform": ("איפוס", "Reset"),
    "printer": ("מדפסת", "Printer"),
    "material": ("חומר", "Material"),
    "quality": ("איכות", "Quality"),
    "custom": ("מותאם אישית", "Custom"),
    "save_printer": ("שמור כפרופיל מדפסת…", "Save as printer profile…"),
    "save_material": ("שמור כפרופיל חומר…", "Save as material profile…"),
    "delete_profile": ("מחק פרופיל משתמש", "Delete user profile"),
    "profile_name": ("שם הפרופיל:", "Profile name:"),
    "profile_saved": ("הפרופיל נשמר", "Profile saved"),
    "show_advanced": ("הצג הגדרות מתקדמות", "Show advanced settings"),
    "search": ("חיפוש הגדרה…", "Search settings…"),
    "reset_setting": ("אפס לערך הפרופיל", "Reset to profile value"),
    "reset_all": ("אפס את כל השינויים", "Reset all changes"),
    "slice": ("פרוס", "Slice"),
    "slicing": ("פורס…", "Slicing…"),
    "cancel": ("ביטול", "Cancel"),
    "export_gcode": ("ייצא G-code", "Export G-code"),
    "export_sla": ("ייצא קובץ הדפסה", "Export print file"),
    "exporting": ("מייצא…", "Exporting…"),
    "stage_slice": ("חיתוך לשכבות", "Slicing layers"),
    "stage_support": ("יצירת תמיכות", "Generating supports"),
    "stage_paths": ("תכנון מסלולים", "Planning toolpaths"),
    "stage_gcode": ("כתיבת G-code", "Writing G-code"),
    "stage_preview": ("הכנת תצוגה", "Building preview"),
    "stage_export": ("ייצוא שכבות", "Exporting layers"),
    "stat_time": ("זמן הדפסה", "Print time"),
    "stat_filament": ("פילמנט", "Filament"),
    "stat_weight": ("משקל", "Weight"),
    "stat_cost": ("עלות", "Cost"),
    "stat_layers": ("שכבות", "Layers"),
    "stat_resin": ("שרף", "Resin"),
    "estimate_note": ("ההערכות משוערות ואינן כוללות האצות", "Estimates ignore acceleration"),
    "layer": ("שכבה", "Layer"),
    "legend": ("מקרא", "Legend"),
    "feat_outer_wall": ("קיר חיצוני", "Outer wall"),
    "feat_inner_wall": ("קיר פנימי", "Inner wall"),
    "feat_skin": ("משטח עליון/תחתון", "Top / bottom"),
    "feat_infill": ("מילוי", "Infill"),
    "feat_support": ("תמיכה", "Support"),
    "feat_interface": ("ממשק תמיכה", "Support interface"),
    "feat_skirt": ("חצאית / שוליים", "Skirt / brim"),
    "feat_travel": ("תנועות", "Travel"),
    "view_iso": ("תצוגה איזומטרית", "Isometric view"),
    "view_top": ("מבט עליון", "Top view"),
    "view_front": ("מבט חזיתי", "Front view"),
    "view_side": ("מבט צד", "Side view"),
    "view_fit": ("התאם לתצוגה", "Fit to view"),
    "overhangs": ("הדגש אזורים תלויים", "Highlight overhangs"),
    "sla_layer_image": ("מסכת השכבה", "Layer mask"),
    "out_of_bounds": ("מודלים מחוץ לאזור ההדפסה: {}", "Models outside the build volume: {}"),
    "no_models": ("אין מודלים לפריסה", "No models to slice"),
    "load_error": ("לא ניתן לטעון את הקובץ", "Could not load file"),
    "slice_error": ("הפריסה נכשלה", "Slicing failed"),
    "export_done": ("נשמר: {}", "Saved: {}"),
    "export_error": ("הייצוא נכשל", "Export failed"),
    "gl_error": ("התצוגה התלת-ממדית אינה זמינה (נדרש OpenGL 3.3): {}", "3D view unavailable (OpenGL 3.3 required): {}"),
    "ready": ("מוכן", "Ready"),
    "loaded": ("נטען: {} ({} משולשים)", "Loaded: {} ({} triangles)"),
    "dirty": ("ההגדרות השתנו — יש לפרוס מחדש", "Settings changed — slice again"),
    "warnings": ("אזהרות", "Warnings"),
    "volume": ("נפח", "Volume"),
    "triangles": ("משולשים", "triangles"),
    "not_watertight": ("המודל אינו אטום — ייתכנו חורים", "Mesh is not watertight — may have holes"),
    "file_filter": ("מודלים תלת-ממדיים (*.stl *.obj *.3mf);;כל הקבצים (*)", "3D models (*.stl *.obj *.3mf);;All files (*)"),
    "unit_mm": ("מ\"מ", "mm"),
    "hint_nav": ("שמאלי: סיבוב · ימני: הזזה · גלגלת: זום · גרור מודל כדי להזיז", "Left: orbit · Right: pan · Wheel: zoom · Drag a model to move it"),
    "unsaved_tech": ("טכנולוגיה", "Technology"),
}


def set_language(lang: str) -> None:
    global LANG
    LANG = "en" if lang == "en" else "he"


def tr(key: str) -> str:
    he, en = STRINGS.get(key, (key, key))
    return he if LANG == "he" else en


def label(setting) -> str:
    return setting.he if LANG == "he" else setting.en


def tip(setting) -> str:
    return (setting.tip_he if LANG == "he" else setting.tip_en) or ""


def option_label(option) -> str:
    return option[1] if LANG == "he" else option[2]


def category_label(cat) -> str:
    return cat[1] if LANG == "he" else cat[2]
