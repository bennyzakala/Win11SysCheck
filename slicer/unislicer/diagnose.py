"""Crash diagnosis for the assistant: turns an error into "what happened, where, why, and how to fix it".

When something goes wrong the program does not just close. The error is matched against known causes
(graphics driver, memory, disk, file access, broken mesh, corrupt profile, program bug), the part of the
program it came from is named, and the steps that fix it are listed. Every report is written to
``~/.unislicer/crashes.jsonl`` so repeated problems are recognised; nothing is sent anywhere.

A hard crash (inside Qt or the graphics driver, which Python cannot catch) leaves a trace in
``~/.unislicer/last_crash_<pid>.txt`` through faulthandler; it is diagnosed on the next start."""
from __future__ import annotations

import faulthandler
import hashlib
import json
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field

from . import __version__, profiles

LOG_NAME = "crashes.jsonl"
FAULT_NAME = "last_crash_{}.txt"           # one per running copy (process id)

# module (file name without .py) -> the part of the program as the user sees it
AREAS = {
    "viewcube": ("קוביית זוויות המבט", "View cube"),
    "viewer": ("תצוגת התלת-ממד", "3D view"),
    "layer_editor": ("עורך הפיקסלים בשכבות", "Layer pixel editor"),
    "settings_panel": ("לוח ההגדרות", "Settings panel"),
    "assistant_panel": ("חלונית הסוכן", "Assistant panel"),
    "profile_manager": ("מנהל הפרופילים", "Profile manager"),
    "sla_supports": ("יצירת התמיכות (SUPPORTS)", "Support generation"),
    "sla": ("חיתוך שרף (SLA/DLP/LCD)", "Resin slicing"),
    "fdm": ("חיתוך FDM", "FDM slicing"),
    "slicing": ("חיתוך השכבות", "Layer slicing"),
    "infill": ("מילוי (Infill)", "Infill"),
    "gcode": ("כתיבת G-code", "G-code output"),
    "ctb": ("ייצוא קובץ CTB", "CTB export"),
    "goo": ("ייצוא קובץ GOO", "GOO export"),
    "vector_formats": ("ייצוא SLC/CLI/3MF", "SLC/CLI/3MF export"),
    "layout": ("סידור אוטומטי על המשטח", "Auto layout"),
    "collision": ("בדיקת התנגשויות", "Collision check"),
    "mesh_tools": ("כלי המודל ותיקון הרשת", "Model tools and mesh repair"),
    "mesh": ("טעינת המודל", "Model loading"),
    "orient": ("אוריינטציה אוטומטית", "Auto orientation"),
    "project": ("שמירה/פתיחה של פרויקט", "Project save/open"),
    "profiles": ("פרופילי מדפסת/חומר", "Printer/material profiles"),
    "assistant": ("הסוכן הלומד", "Learning assistant"),
    "scene": ("המודלים על המשטח", "Models on the plate"),
    "main_window": ("החלון הראשי", "Main window"),
}

# Qt event handler -> what the user was doing
ACTIONS = {
    "paintEvent": ("בזמן ציור המסך", "while drawing"), "paintGL": ("בזמן ציור התלת-ממד", "while drawing the 3D view"),
    "mousePressEvent": ("בלחיצה על העכבר", "on a mouse click"), "mouseReleaseEvent": ("בשחרור לחיצת העכבר", "on releasing a click"),
    "mouseMoveEvent": ("בתזוזת העכבר", "while moving the mouse"), "mouseDoubleClickEvent": ("בלחיצה כפולה", "on a double click"),
    "wheelEvent": ("בגלילה / זום", "while scrolling / zooming"), "keyPressEvent": ("בלחיצה על מקש", "on a key press"),
    "resizeEvent": ("בשינוי גודל החלון", "while resizing the window"), "dropEvent": ("בגרירת קובץ לחלון", "on dropping a file"),
    "contextMenuEvent": ("בפתיחת תפריט קליק ימני", "opening the right-click menu"), "_step": ("בסיבוב המבט", "while turning the view"),
}


@dataclass
class Diagnosis:
    signature: str               # stable id of this error (same bug -> same signature)
    kind: str                    # bug | graphics | memory | disk | file | mesh | profile | hard_crash
    title: tuple                 # (he, en)
    cause: tuple                 # (he, en)
    fixes: list = field(default_factory=list)      # [(he, en), ...] steps, in order
    area: tuple = ("", "")
    where: str = ""              # file:line in function, for the developer
    error: str = ""              # "TypeName: message"
    details: str = ""            # full traceback
    time: float = 0.0
    version: str = __version__
    repeat: int = 1              # how many times this signature was seen (including this one)

    def text(self, lang: str = "he") -> dict:
        i = 0 if lang == "he" else 1
        return {"title": self.title[i], "cause": self.cause[i], "fixes": [f[i] for f in self.fixes],
                "area": self.area[i]}


# ------------------------------------------------------------------ rules
REPORT = ("שמור את הדוח (כפתור 'העתק דוח') ושלח אותו לתמיכה כדי שהתקלה תתוקן בגרסה הבאה.",
          "Copy the report (Copy report button) and send it to support so it is fixed in the next version.")
UPDATE = ("התקן את הגרסה האחרונה של UniSlicer - ייתכן שהתקלה כבר תוקנה.",
          "Install the latest UniSlicer - this may already be fixed.")
GPU_STEPS = [
    ("עדכן את הדרייבר של כרטיס המסך (NVIDIA / AMD / Intel) מאתר היצרן.",
     "Update the graphics driver (NVIDIA / AMD / Intel) from the maker's site."),
    ("במחשב נייד: הגדר ב-Windows (הגדרות > תצוגה > גרפיקה) ש-UniSlicer ירוץ על כרטיס המסך החזק.",
     "On a laptop: in Windows (Settings > Display > Graphics) set UniSlicer to the high-performance GPU."),
    ("אם זה חוזר: הפעל את UniSlicer עם ציור בתוכנה (משתנה סביבה UNISLICER_SOFTWARE_GL=1) - איטי יותר אבל יציב.",
     "If it keeps happening: start UniSlicer with software drawing (environment variable UNISLICER_SOFTWARE_GL=1) - slower but stable."),
]


def _rule(exc_type: str, msg: str, tb: str, area_key: str):
    """(kind, title, cause, fixes) for the first matching known cause."""
    low = msg.lower()
    tbl = tb.lower()
    if exc_type == "MemoryError" or "unable to allocate" in low or "out of memory" in low or "bad_alloc" in low:
        return ("memory", ("אין מספיק זיכרון (RAM)", "Not enough memory (RAM)"),
                ("הפעולה ניסתה להקצות יותר זיכרון ממה שפנוי במחשב - בדרך כלל מודלים גדולים מאוד, הרבה עותקים, "
                 "או חיתוך ברזולוציה גבוהה מאוד.",
                 "The operation needed more memory than is free - usually very large models, many copies or a very "
                 "high slicing resolution."),
                [("סגור תוכנות אחרות ונסה שוב.", "Close other programs and try again."),
                 ("הקטן את מספר המודלים/העותקים על המשטח, או חתוך בכמה פעמים.", "Put fewer models/copies on the plate, or slice in several batches."),
                 ("בתיקון רשת מתקדם אפשר לפשט מודלים כבדים מאוד (סריקות).", "Simplify very heavy (scanned) models with advanced repair."),
                 ("בחיתוך שרף: כבה Anti-aliasing גבוה או הגדל את עובי השכבה.", "For resin: lower anti-aliasing or use a thicker layer.")])
    if "no space left" in low or "errno 28" in low or "disk full" in low:
        return ("disk", ("הדיסק מלא", "The disk is full"),
                ("אין מקום פנוי בכונן שאליו נשמר הקובץ.", "There is no free space on the drive the file is written to."),
                [("פנה מקום בכונן (או בכרטיס/USB) ונסה שוב.", "Free some space on the drive (or card/USB) and try again."),
                 ("שמור לכונן אחר.", "Save to another drive.")])
    if exc_type == "PermissionError" or "access is denied" in low or "permission denied" in low:
        return ("file", ("אין הרשאה לכתוב או לקרוא את הקובץ", "No permission to read or write the file"),
                ("הקובץ פתוח בתוכנה אחרת, מוגן מכתיבה, או שהתיקייה מוגנת (למשל Program Files).",
                 "The file is open in another program, read-only, or the folder is protected (e.g. Program Files)."),
                [("סגור את הקובץ בתוכנות אחרות (כמו תוכנת המדפסת) ונסה שוב.", "Close the file in other programs (such as the printer's software) and try again."),
                 ("שמור לתיקייה אחרת, למשל 'מסמכים' או שולחן העבודה.", "Save to another folder, e.g. Documents or the desktop."),
                 ("בדוק שה-USB / כרטיס הזיכרון לא נעול.", "Check that the USB drive / memory card is not locked.")])
    if exc_type in ("FileNotFoundError", "NotADirectoryError") or "no such file" in low:
        return ("file", ("הקובץ לא נמצא", "File not found"),
                ("הקובץ או התיקייה הועברו, נמחקו, או שה-USB נותק.", "The file or folder was moved, deleted, or the USB drive was removed."),
                [("בדוק שהקובץ עדיין קיים ופתח אותו מחדש.", "Check that the file still exists and open it again."),
                 ("אם הוא על USB/רשת - חבר שוב ונסה.", "If it is on USB/network - reconnect and try again.")])
    if "opengl" in tbl or "opengl" in low or "glerror" in low or "shader" in low or "gl_invalid" in low \
            or (area_key == "viewer" and ("context" in low or "framebuffer" in low)):
        return ("graphics", ("תקלה בכרטיס המסך / OpenGL", "Graphics card / OpenGL problem"),
                ("הדרייבר של כרטיס המסך החזיר שגיאה בזמן ציור התלת-ממד. זה כמעט תמיד דרייבר ישן או כרטיס מסך משולב חלש.",
                 "The graphics driver returned an error while drawing the 3D view. This is almost always an old driver or a weak built-in GPU."),
                list(GPU_STEPS))
    if "geos" in tbl or ("shapely" in tbl and ("invalid" in low or "topology" in low or "geometry" in low)):
        return ("mesh", ("צורת המודל פגומה", "The model's shape is broken"),
                ("לרשת של המודל יש משטחים חותכים, חורים או קירות כפולים, ולכן לא ניתן היה לחשב את קווי המתאר.",
                 "The model's mesh has self-intersections, holes or double walls, so its outlines could not be computed."),
                [("בחר את המודל > תיקון רשת מתקדם, ואז נסה שוב.", "Select the model > Advanced repair, then try again."),
                 ("אם לא עוזר: ייצא מחדש את המודל מתוכנת התכנון (STL סגור/אטום).", "If not: export the model again from the design software (closed/watertight STL).")])
    if exc_type == "LinAlgError" or "singular matrix" in low:
        return ("mesh", ("המודל שטוח או בגודל אפס", "The model is flat or zero-sized"),
                ("אחד הצירים של המודל בגודל 0 (קנה מידה 0% או מודל שטוח לגמרי), אז אי אפשר לחשב סיבוב/מיקום.",
                 "One axis of the model is 0 (scale 0% or a completely flat model), so rotation/placement cannot be computed."),
                [("בדוק את קנה המידה של המודל (לא 0%).", "Check the model's scale (not 0%)."),
                 ("הרץ תיקון רשת מתקדם.", "Run advanced repair.")])
    if exc_type == "ZeroDivisionError" or "division by zero" in low or "divide by zero" in low:
        return ("profile", ("הגדרה עם ערך אפס", "A setting is zero"),
                ("אחת ההגדרות (למשל עובי שכבה, מהירות, רזולוציה או קנה מידה) היא 0 וגרמה לחלוקה באפס.",
                 "A setting (e.g. layer height, speed, resolution or scale) is 0 and caused a division by zero."),
                [("בדוק בהגדרות שאין ערך 0 בעובי שכבה, רזולוציה, מהירות או גודל משטח.", "Check that layer height, resolution, speed and plate size are not 0."),
                 ("החזר את הפרופיל לברירת המחדל (קליק ימני על ההגדרה > איפוס).", "Reset the profile to defaults (right-click the setting > Reset)."),
                 UPDATE])
    if exc_type in ("JSONDecodeError", "UnicodeDecodeError") or "expecting value" in low:
        place = "project" if area_key == "project" else "profiles"
        return ("profile", ("קובץ פגום", "A damaged file"),
                (("קובץ הפרויקט פגום או נשמר באמצע (למשל בזמן כיבוי)." if place == "project" else
                  "קובץ הגדרות/פרופיל של המשתמש פגום."),
                 ("The project file is damaged or was cut off while saving." if place == "project" else
                  "A user settings/profile file is damaged.")),
                [("פתח את השמירה האוטומטית (היא מוצעת בהפעלה אחרי קריסה).", "Open the autosave (offered at start after a crash)."),
                 ("פרופיל פגום: מחק אותו במנהל הפרופילים או את הקובץ בתיקייה ‎~/.unislicer‎.",
                  "Damaged profile: delete it in the profile manager or the file in ~/.unislicer."),
                 REPORT])
    if exc_type == "KeyError" and area_key in ("profiles", "settings_panel", "assistant"):
        return ("profile", ("חסרה הגדרה בפרופיל", "A profile is missing a setting"),
                ("פרופיל שנשמר בגרסה קודמת לא מכיל הגדרה שהגרסה הזו צריכה.",
                 "A profile saved by an older version lacks a setting this version needs."),
                [("פתח את מנהל הפרופילים ושמור את הפרופיל מחדש, או בחר את פרופיל היצרן.", "Open the profile manager and save the profile again, or pick the vendor profile."),
                 REPORT])
    if exc_type == "RecursionError":
        return ("mesh", ("המודל מורכב מדי לפעולה", "The model is too complex for this operation"),
                ("הפעולה נכנסה לעומק גדול מדי - בדרך כלל רשת עם מאות אלפי חלקים נפרדים.",
                 "The operation went too deep - usually a mesh with hundreds of thousands of separate pieces."),
                [("הרץ תיקון רשת מתקדם עם הסרת חלקיקים קטנים.", "Run advanced repair with small-shell removal."), REPORT])
    # a program bug: say where and what kind, and how to get past it
    what = _bug_kind(exc_type, msg)
    return ("bug", ("תקלה בתוכנה (באג)", "A program bug"), what,
            [("העבודה שלך לא אבדה - התוכנה ממשיכה לרוץ. אם משהו נראה לא תקין, שמור פרויקט והפעל מחדש.",
              "Your work is not lost - the program keeps running. If something looks wrong, save the project and restart."),
             ("עד לתיקון: הימנע מהפעולה שגרמה לתקלה או עשה אותה בדרך אחרת (תפריט במקום קיצור, וכו').",
              "Until it is fixed: avoid the action that caused it or do it another way (menu instead of shortcut, etc.)."),
             UPDATE, REPORT])


def _bug_kind(exc_type: str, msg: str) -> tuple:
    low = msg.lower()
    if "truth value of an array" in low:
        return ("השוואה פנימית בקוד נכשלה: ערך של כמה מספרים (וקטור) הושווה כמו ערך יחיד. זו טעות תכנות, לא משהו שעשית.",
                "An internal comparison failed: a multi-number value (vector) was compared like a single value. "
                "This is a programming mistake, not something you did.")
    if exc_type == "AttributeError" and "nonetype" in low:
        return ("הקוד ניסה להשתמש באובייקט שעוד לא נוצר או שכבר נסגר (למשל מודל שנמחק או חלון שנסגר).",
                "The code used an object that was not created yet or was already closed (e.g. a deleted model or a closed window).")
    if exc_type in ("IndexError",):
        return ("הקוד ניגש לפריט שלא קיים ברשימה (למשל שכבה, מודל או תמיכה שכבר נמחקו).",
                "The code accessed an item that does not exist (e.g. a layer, model or support already removed).")
    if exc_type == "RuntimeError" and ("deleted" in low or "already deleted" in low):
        return ("רכיב בממשק נסגר בזמן שפעולה עוד השתמשה בו.", "A UI element was closed while an action still used it.")
    if exc_type in ("TypeError", "ValueError"):
        return ("ערך מסוג או בצורה לא צפויים הגיע לפונקציה. זו טעות תכנות.",
                "A value of an unexpected type or shape reached a function. This is a programming mistake.")
    return ("שגיאה לא צפויה בקוד התוכנה.", "An unexpected error in the program code.")


# ------------------------------------------------------------------ analysis
def _frames(tb_obj):
    return traceback.extract_tb(tb_obj) if tb_obj is not None else []


def _our_frame(frames):
    """Deepest frame inside unislicer (where our code went wrong)."""
    for f in reversed(frames):
        norm = f.filename.replace("\\", "/")
        if "/unislicer/" in norm:
            return f
    return frames[-1] if frames else None


def signature(exc_type: str, frame) -> str:
    where = f"{os.path.basename(frame.filename)}:{frame.name}" if frame is not None else ""
    return hashlib.sha1(f"{exc_type}|{where}".encode()).hexdigest()[:12]


def diagnose(exc: BaseException, tb_obj=None) -> Diagnosis:
    tb_obj = tb_obj if tb_obj is not None else exc.__traceback__
    exc_type = type(exc).__name__
    msg = str(exc)
    # PySide wraps errors raised in Qt overrides: the real message follows the prefix
    m = re.match(r"Error calling Python override of \S+: (.*)", msg, re.S)
    if m:
        msg = m.group(1)
    details = "".join(traceback.format_exception(type(exc), exc, tb_obj))
    frames = _frames(tb_obj)
    frame = _our_frame(frames)
    area_key = os.path.splitext(os.path.basename(frame.filename))[0] if frame is not None else ""
    kind, title, cause, fixes = _rule(exc_type, msg, details, area_key)
    area = AREAS.get(area_key, (area_key, area_key))
    handler = next((f.name for f in reversed(frames) if f.name in ACTIONS), None)
    if handler:
        act = ACTIONS[handler]
        area = (f"{area[0]} - {act[0]}", f"{area[1]} - {act[1]}")
    elif frame is not None and area_key == "main_window" and frame.name:
        area = (f"{area[0]} ({frame.name})", f"{area[1]} ({frame.name})")
    where = f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}" if frame is not None else ""
    return Diagnosis(signature(exc_type, frame), kind, title, cause, fixes, area, where,
                     f"{exc_type}: {msg}".strip(), details, time.time())


def diagnose_hard_crash(text: str) -> Diagnosis | None:
    """A faulthandler dump from the previous run (the program closed without Python seeing an error)."""
    if not text.strip():
        return None
    low = text.lower()
    files = re.findall(r'File "([^"]+)", line (\d+) in (\S+)', text)
    ours = [f for f in files if "unislicer" in f[0].replace("\\", "/")]
    top = ours[0] if ours else (files[0] if files else None)      # faulthandler lists the newest frame first
    area_key = os.path.splitext(os.path.basename(top[0]))[0] if top else ""
    gl = any(k in low for k in ("paintgl", "opengl", "initializegl", "glbuffer", "gldraw")) or area_key == "viewer"
    if gl:
        kind, title = "graphics", ("התוכנה נסגרה בגלל הדרייבר של כרטיס המסך", "The program closed because of the graphics driver")
        cause = ("בפעם הקודמת UniSlicer נסגר בפתאומיות בזמן ציור התלת-ממד. הקריסה הייתה בתוך הדרייבר של כרטיס המסך.",
                 "Last time UniSlicer closed suddenly while drawing the 3D view. The crash was inside the graphics driver.")
        fixes = list(GPU_STEPS)
    elif "memoryerror" in low or "out of memory" in low:
        kind, title = "memory", ("התוכנה נסגרה כי נגמר הזיכרון", "The program closed because memory ran out")
        cause = ("בפעם הקודמת נגמר הזיכרון (RAM) באמצע פעולה.", "Last time the memory (RAM) ran out during an operation.")
        fixes = [("סגור תוכנות אחרות, הקטן את מספר המודלים או חתוך בכמה פעמים.", "Close other programs, use fewer models, or slice in batches.")]
    else:
        kind, title = "hard_crash", ("התוכנה נסגרה בפתאומיות בפעם הקודמת", "The program closed suddenly last time")
        cause = ("הקריסה הייתה ברכיב מערכת (Qt) ולא בקוד Python, ולכן לא הוצגה הודעה.",
                 "The crash was in a system component (Qt), not in Python code, so no message was shown.")
        fixes = [UPDATE, ("אם זה קרה בפעולה מסוימת - נסה אותה שוב לאט, ואם זה חוזר שלח את הדוח.", "If it happened during a certain action - try it again slowly; if it repeats, send the report."), REPORT]
    area = AREAS.get(area_key, (area_key, area_key))
    where = f"{os.path.basename(top[0])}:{top[1]} in {top[2]}" if top else ""
    sig = hashlib.sha1(f"hard|{where}".encode()).hexdigest()[:12]
    return Diagnosis(sig, kind, title, cause, fixes, area, where, "Fatal error (native crash)", text, time.time())


# ------------------------------------------------------------------ log
class CrashLog:
    def __init__(self, folder: str | None = None):
        self.folder = folder or profiles.USER_DIR
        self.path = os.path.join(self.folder, LOG_NAME)
        self.fault_path = os.path.join(self.folder, FAULT_NAME.format(os.getpid()))
        self._fault_file = None

    def entries(self, limit: int = 200) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def count(self, sig: str) -> int:
        return sum(1 for e in self.entries() if e.get("signature") == sig)

    def record(self, d: Diagnosis) -> Diagnosis:
        d.repeat = self.count(d.signature) + 1
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")
            if os.path.getsize(self.path) > 2_000_000:          # keep the log small
                keep = self.entries(100)
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in keep)
        except OSError:
            pass
        return d

    # hard crashes ----------------------------------------------------------
    def arm(self) -> Diagnosis | None:
        """Diagnose a hard crash left by the previous run, then start catching the next one."""
        found = None
        import glob
        for path in glob.glob(os.path.join(self.folder, FAULT_NAME.format("*"))):
            try:
                if time.time() - os.path.getmtime(path) < 5:      # another copy that is running now
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    d = diagnose_hard_crash(fh.read())
                os.remove(path)
            except OSError:
                continue
            if d is not None:
                found = self.record(d)
        try:
            os.makedirs(self.folder, exist_ok=True)
            self._fault_file = open(self.fault_path, "w", encoding="utf-8")
            faulthandler.enable(self._fault_file, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            self._fault_file = None
        return found

    def disarm(self) -> None:
        """Clean exit: the fault file stays empty, so nothing is reported next time."""
        if self._fault_file is not None:
            try:
                faulthandler.disable()
                self._fault_file.close()
                os.remove(self.fault_path)
            except (OSError, RuntimeError, ValueError):
                pass
            self._fault_file = None


def report_text(d: Diagnosis, lang: str = "he") -> str:
    t = d.text(lang)
    he = lang == "he"
    lines = [f"UniSlicer {d.version} - {time.strftime('%Y-%m-%d %H:%M', time.localtime(d.time))}",
             f"{'מה קרה' if he else 'What happened'}: {t['title']}",
             f"{'איפה' if he else 'Where'}: {t['area']}  [{d.where}]",
             f"{'הסיבה' if he else 'Cause'}: {t['cause']}",
             f"{'איך מתקנים' if he else 'How to fix'}:"]
    lines += [f"  {i + 1}. {s}" for i, s in enumerate(t["fixes"])]
    if d.repeat > 1:
        lines.append(("התקלה הזו חזרה {} פעמים" if he else "This problem happened {} times").format(d.repeat))
    lines += ["", f"{d.error}", f"Python {sys.version.split()[0]} / {sys.platform}", "", d.details]
    return "\n".join(lines)
