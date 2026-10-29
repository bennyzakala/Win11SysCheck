# UniSlicer — סלייסר אוניברסלי למדפסות תלת-ממד

UniSlicer הוא סלייסר בקוד פתוח למדפסות **FDM** (פילמנט) ולמדפסות **שרף** (SLA / MSLA).
הוא טוען מודלים, מסדר אותם על המשטח, פורס אותם לשכבות ומייצא קובץ שהמדפסת מדפיסה:
G-code למדפסות פילמנט, ותמונות שכבה (`.sl1` או ZIP של PNG) למדפסות שרף.
הממשק בעברית (מימין לשמאל) ובאנגלית, ויש גם שורת פקודה.

![תצוגה מקדימה של מסלולי ההדפסה](docs/preview-fdm-he.jpg)

| הכנה (הדגשת אזורים תלויים) | שרף: תמיכות, רפסודה ומסכת שכבה |
|---|---|
| ![](docs/prepare-he.jpg) | ![](docs/preview-resin.jpg) |

## התקנה והפעלה

נדרש Python 3.10 ומעלה וכרטיס מסך שתומך ב-OpenGL 3.3.

```bash
cd slicer
pip install -r requirements.txt
python run_unislicer.py
```

ב-Windows אפשר פשוט ללחוץ פעמיים על `run_unislicer.bat`: הוא מתקין את התלויות ומפעיל את התוכנה.

## מה יש בתוכנה

**כללי**
- טעינת קובצי STL (בינארי ו-ASCII), OBJ ו-3MF, גם בגרירה לחלון.
- כמה מודלים על המשטח: סידור אוטומטי, שכפול, הזזה בגרירה, סיבוב, קנה מידה (אחיד או לפי ציר) ו"השכבה על פאה".
- סימון באדום של מודל שחורג מאזור ההדפסה, והדגשה של אזורים תלויים שיצטרכו תמיכה.
- 17 פרופילי מדפסות מובנים, 10 חומרים ו-7 רמות איכות. אפשר לשמור פרופילי מדפסת וחומר משלכם, כך שהתוכנה מתאימה לכל מדפסת.
- כ-80 הגדרות עם חיפוש. ליד כל הגדרה ששיניתם מופיע כפתור שמחזיר אותה לערך של הפרופיל.

**מדפסות פילמנט (FDM)**
- קירות, משטחים עליונים ותחתונים, ומילוי בחמש תבניות: רשת, קווים, משולשים, ג'ירואיד וקונצנטרי.
- תמיכות אוטומטיות שמתחילות מהמשטח או מכל מקום, עם שכבות ממשק צפופות ומרווח מהמודל.
- חצאית (skirt) או שוליים (brim).
- נסיגה (retraction), הרמת Z, נסיגת קושחה (G10/G11), ומעבר בתוך החלק בלי נסיגה כשאפשר.
- G-code ל-Marlin, ‏Klipper, ‏RepRapFirmware, ‏Prusa ו-Smoothieware, עם אקסטרוזיה יחסית או מוחלטת ומשטח שהראשית שלו במרכז (מדפסות דלתא).
- הערות `;TYPE:` / `;LAYER:` בסגנון Cura ו-`M73` להצגת התקדמות, כך ש-OctoPrint ומציגי G-code אחרים מזהים את הקובץ.
- תצוגה מקדימה תלת-ממדית של המסלולים לפי שכבה, בצבע לפי סוג. יש גם הערכה של זמן ההדפסה, אורך ומשקל הפילמנט והעלות.

**מדפסות שרף (SLA / MSLA)**
- תמיכות אוטומטיות: נקודות תמיכה מתחת לאזורים התלויים ובקצוות התחתונים, עמודים עם ראש מחודד ורפסודה. המודל מוגבה מעל המשטח.
- מסכות שכבה ברזולוציית המסך של המדפסת, עם החלקת קצוות ושיקוף.
- ייצוא ל-`.sl1` (פורמט Prusa, שגם UVtools קורא) או ל-ZIP של PNG עם קובץ `metadata.json`.
- הערכה של נפח השרף, המשקל, זמן ההדפסה והעלות.

## שורת פקודה

```bash
python -m unislicer slice model.stl -p prusa_mk4 -m petg -q fine -s infill_pattern=gyroid -s support_enable=true
python -m unislicer slice model.stl -p elegoo_mars3 -s support_enable=true -o model.sl1
python -m unislicer list-profiles      # כל פרופילי המדפסות, החומרים והאיכות
python -m unislicer list-settings      # כל שמות ההגדרות לשימוש עם -s
```

## הגבלות ידועות (גרסה 1.0)

- **לא נבדק עדיין על מדפסת אמיתית.** קובצי ה-G-code נבדקו בבדיקות אוטומטיות: הקואורדינטות בתוך גבולות המשטח, כמות הפילמנט מתאימה לנפח המודל (בטווח של 5%), וכל סוגי הקושחה נבדקו. עדיין מומלץ לצפות בהדפסה הראשונה, ולבדוק את קוד ההתחלה (Start G-code) של המדפסת שלכם.
- מדפסות Bambu Lab דורשות קובץ `.gcode.3mf` חתום, ולכן אינן נתמכות.
- מדפסות שרף של Elegoo ו-Anycubic משתמשות בפורמטים קנייניים (`.ctb`, ‏`.goo`, ‏`.pwmx`). את קובץ ה-`.sl1` אפשר להמיר אליהם בעזרת התוכנה החינמית [UVtools](https://github.com/sn4k3/UVtools). לפני שמדפיסים, כדאי לוודא שהרזולוציה וגודל המסך בפרופיל תואמים לדגם שלכם.
- עדיין לא קיימים: גובה שכבה משתנה, ריבוי חומרים, תמיכות עץ, זיהוי גשרים, ironing ומילוי פערים צרים (gap fill).
- הערכת הזמן אינה מתחשבת בהאצות, ולכן ההדפסה בפועל בדרך כלל ארוכה יותר.

## מבנה הקוד

| קובץ | תפקיד |
|---|---|
| `unislicer/mesh.py` | טעינת STL/OBJ/3MF, ריתוך קודקודים, טרנספורמציות |
| `unislicer/slicing.py` | חיתוך הרשת לפוליגונים בכל שכבה |
| `unislicer/fdm.py`, `infill.py` | קירות, משטחים, מילוי, תמיכות, סדר המסלולים |
| `unislicer/gcode.py` | כתיבת G-code לכל סוג קושחה, והערכת זמן וחומר |
| `unislicer/sla.py`, `sla_supports.py` | תמיכות שרף, מסכות שכבה, ייצוא |
| `unislicer/settings.py` | טבלת ההגדרות שממנה נבנים הממשק, שורת הפקודה והפרופילים |
| `unislicer/profiles/*.json` | פרופילי מדפסות, חומרים ואיכות |
| `unislicer/gui/` | ממשק PySide6 ותצוגת OpenGL |

הרצת הבדיקות: `pip install pytest && python -m pytest`

---

## English

UniSlicer is an open-source slicer for **FDM** (filament) and **resin** (SLA/MSLA) printers, with a Hebrew/English GUI and a CLI.
It loads STL/OBJ/3MF files and outputs G-code (Marlin, Klipper, RepRapFirmware, Prusa, Smoothieware) or resin layer images (`.sl1` / PNG zip).
Features: five infill patterns, automatic FDM supports with interface layers, skirt and brim, retraction with z-hop, automatic resin supports with a raft,
a shaded 3D toolpath preview, and 17 built-in printer profiles plus profiles you save yourself.

```bash
cd slicer && pip install -r requirements.txt && python run_unislicer.py
```

The limitations above apply. In particular, the output has been tested automatically but not yet printed on real hardware, so watch your first print.
