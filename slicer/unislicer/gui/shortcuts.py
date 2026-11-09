"""Every keyboard shortcut of the main window, in one table: the window installs them from here and the user
guide (``unislicer.manual``) prints its shortcut page from here, so the two never disagree.

Each entry: (action id, key sequence(s) as Qt writes them, group, Hebrew, English). A key sequence is a
QKeySequence string ("Ctrl+D") or the name of a QKeySequence.StandardKey ("Open", "Undo", ...), which
the guide shows as its Windows keys."""
from __future__ import annotations

# QKeySequence.StandardKey names used below -> the keys they mean on Windows (shown in the guide)
STANDARD_KEYS = {"Open": "Ctrl+O", "Save": "Ctrl+S", "Undo": "Ctrl+Z", "Redo": "Ctrl+Shift+Z",
                 "Delete": "Del", "SelectAll": "Ctrl+A"}
_SHOWN = {"Up": "↑", "Down": "↓", "Escape": "Esc"}

GROUPS = (
    ("file", "קבצים ופרויקט", "Files and project"),
    ("edit", "עריכה ובחירה", "Editing and selection"),
    ("tools", "כלים על המודל", "Tools on the model"),
    ("supports", "תמיכות", "Supports"),
    ("plate", "פלטה וחיתוך", "Plate and slicing"),
    ("view", "תצוגה", "View"),
    ("help", "עזרה", "Help"),
)

SHORTCUTS = (
    ("open", ("Open",), "file", "פתיחת קובצי מודל (STL / OBJ / 3MF / PLY / AMF / ZIP)", "Open model files"),
    ("save_project", ("Save",), "file", "שמירת הפרויקט", "Save the project"),
    ("export", ("Ctrl+E",), "file", "ייצוא קובץ ההדפסה", "Export the print file"),
    ("undo", ("Undo",), "edit", "ביטול הפעולה האחרונה", "Undo"),
    ("redo", ("Ctrl+Y", "Redo"), "edit", "ביצוע שוב", "Redo"),
    ("delete", ("Delete",), "edit", "מחיקת המודלים הנבחרים", "Delete the selected models"),
    ("duplicate", ("Ctrl+D",), "edit", "שכפול המודלים הנבחרים", "Duplicate the selected models"),
    ("select_all", ("SelectAll",), "edit", "בחירת כל המודלים", "Select all models"),
    ("escape", ("Escape",), "edit", "יציאה מהכלי הנוכחי (חזרה להזזה), סיום הוספה / הסרה של תמיכות",
     "Leave the current tool (back to Move), finish adding / removing supports"),
    ("tool_move", ("M",), "tools", "כלי הזזה", "Move tool"),
    ("tool_rotate", ("R",), "tools", "כלי סיבוב (טבעות הסיבוב)", "Rotate tool (rotation rings)"),
    ("tool_face", ("F",), "tools", "הנחת פאה על המשטח - לוחצים על הפאה הרצויה", "Face to plate - click the face"),
    ("tool_hole", ("H",), "tools", "חור ניקוז - לוחצים על המודל", "Drain hole - click the model"),
    ("tool_keepout", ("K",), "tools", "אזור ללא תמיכות - לוחצים על המודל", "Support keep-out zone - click the model"),
    ("tool_label", ("L",), "tools", "כיתוב על המודל - לוחצים במקום הרצוי", "Label on the model - click the spot"),
    ("tool_measure", ("D",), "tools", "מדידת מרחק - שתי לחיצות", "Measure a distance - two clicks"),
    ("sup_add", ("S",), "supports", "מצב הוספת תמיכה: כל לחיצה על המודל מוסיפה תמיכה (שוב S לסיום)",
     "Add supports: every click on the model adds one (S again to finish)"),
    ("sup_remove", ("Shift+S",), "supports", "מצב הסרת תמיכה: לחיצה על תמיכה מסירה אותה (שוב Shift+S לסיום)",
     "Remove supports: click a support to remove it (Shift+S again to finish)"),
    ("arrange", ("A",), "plate", "סידור מהיר של המודלים על הפלטה", "Quick arrange on the plate"),
    ("auto_layout", ("Shift+A",), "plate", "חלון הסידור האוטומטי (Auto layout)", "The Auto layout window"),
    ("slice", ("Ctrl+R",), "plate", "חיתוך (פריסה לשכבות) / עצירת החיתוך", "Slice / stop slicing"),
    ("layer_up", ("Up",), "view", "בתצוגה המקדימה: שכבה אחת למעלה", "Preview: one layer up"),
    ("layer_down", ("Down",), "view", "בתצוגה המקדימה: שכבה אחת למטה", "Preview: one layer down"),
    ("panel_left", ("Ctrl+[",), "view", "הסתרה / הצגה של הלוח השמאלי", "Hide / show the left panel"),
    ("panel_right", ("Ctrl+]",), "view", "הסתרה / הצגה של לוח ההגדרות", "Hide / show the settings panel"),
    ("manual", ("F1",), "help", "פתיחת חוברת ההדרכה", "Open the user guide"),
)

# the mouse in the 3D view, per "Mouse control" scheme (More > Mouse control)
MOUSE = {
    "chitubox": (
        ("לחיצה שמאלית על מודל", "בחירת המודל; גרירה מזיזה אותו על הפלטה"),
        ("Ctrl + לחיצה שמאלית", "הוספה / הורדה של מודל מהבחירה"),
        ("Shift + לחיצה שמאלית", "הוספת מודל לבחירה"),
        ("גרירה שמאלית על רקע ריק", "מלבן בחירה - בוחר את כל המודלים שבתוכו"),
        ("גרירה ימנית", "סיבוב התצוגה"),
        ("גרירה בגלגלת, או Shift + גרירה ימנית", "הזזת התצוגה"),
        ("גלגלת", "זום אל הנקודה שמתחת לסמן"),
        ("לחיצה ימנית על מודל", "תפריט המודל (כיוון, תמיכות, הדפסה ישירה, מראה ועוד)"),
        ("לחיצה כפולה", "התאמת התצוגה לכל המודלים"),
    ),
    "classic": (
        ("לחיצה שמאלית על מודל", "בחירת המודל; גרירה מזיזה אותו על הפלטה"),
        ("גרירה שמאלית על רקע ריק", "סיבוב התצוגה"),
        ("גרירה ימנית או בגלגלת", "הזזת התצוגה"),
        ("גלגלת", "זום"),
        ("Ctrl / Shift + גרירה שמאלית על רקע ריק", "מלבן בחירה"),
        ("לחיצה ימנית על מודל", "תפריט המודל"),
        ("לחיצה כפולה", "התאמת התצוגה לכל המודלים"),
    ),
    "rings": (
        ("גרירת טבעת (כלי סיבוב R)", "סיבוב המודל סביב הציר של הטבעת, בצעדים של מעלה"),
        ("Shift + גרירת טבעת", "סיבוב בקפיצות של 15°"),
    ),
    "label": (
        ("Ctrl + גלגלת (כלי כיתוב L)", "הגדלה / הקטנה של הכיתוב"),
        ("Alt + גלגלת (כלי כיתוב L)", "סיבוב הכיתוב"),
    ),
}


def keys_text(seq: str) -> str:
    """How a key sequence of the table reads in the guide (standard keys as their Windows keys)."""
    seq = STANDARD_KEYS.get(seq, seq)
    return _SHOWN.get(seq, seq)
