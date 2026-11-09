"""The UniSlicer user guide (Hebrew), built from the program itself.

Hand-written chapters say how to work; the reference pages are made from the live program at the moment
the guide is built, so they always match it:
  * the keyboard shortcuts and the mouse - from ``gui.shortcuts`` (the window installs its shortcuts
    from the same table);
  * every button and menu name - from the interface's own strings (``{t:key}`` in the text);
  * every setting with its explanation - from ``settings.SCHEMA``;
  * the printers and how many resins each has - from the profiles.
The program opens it from More > User guide (F1); ``python -m unislicer.manual out.html`` writes it, and
the tests check that every menu command and every shortcut is in it."""
from __future__ import annotations

import datetime
import html
import os
import re
import sys

IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_img")

# ---------------------------------------------------------------------------------------------- chapters
# (anchor, title, body). In the body: {t:key} = the interface's name for a button / menu (in bold),
# {k:id} = the keys of a shortcut, {img:file|caption} = a picture from manual_img.
CHAPTERS = (
    ("intro", "ברוכים הבאים ל-UniSlicer", """
<p>UniSlicer מכינה מודלים תלת-ממדיים להדפסה: פותחים את הקבצים, מסדרים אותם על הפלטה, מוסיפים תמיכות
ורפסודה, חותכים לשכבות ושולחים למדפסת. התוכנה מותאמת במיוחד למעבדות שיניים - כתרים וגשרים זמניים,
מודלים, מסכות חניכיים, סדים ומדריכים כירורגיים - ומתאימה גם להדפסת פילמנט (FDM).</p>
{img:main.png|המסך הראשי: למעלה סרגל הכלים, משמאל המודלים והמיקום, באמצע תצוגת התלת-ממד ומימין ההגדרות}
<h3>סדר העבודה הרגיל</h3>
<ol>
<li>בוחרים מדפסת, חומר ואיכות בלוח ההגדרות מימין.</li>
<li>פותחים את הקבצים ({t:open}, {k:open}) או גוררים אותם לחלון.</li>
<li>לוחצים {t:ocp_button} - התוכנה מזהה כל מודל, מסדרת את הפלטה ויוצרת תמיכות, ואז מציגה סיכום.</li>
<li>בודקים, ומתקנים ביד אם צריך (תמיכות, מיקום, כיוון).</li>
<li>לוחצים {t:slice} ({k:slice}) ואז {t:export_print} ({k:export}) או שולחים למדפסת ברשת.</li>
</ol>
"""),
    ("install", "התקנה ועדכונים", """
<p>את קובץ ההתקנה מורידים מהכתובת:<br><code>https://github.com/bennyzakala/Win11SysCheck/releases/download/unislicer-latest/UniSlicer-Setup.exe</code></p>
<p>כשיש גרסה חדשה מופיע בסרגל העליון הכפתור <b class="ui">יש עדכון</b>. לחיצה מורידה את הגרסה, בודקת
שהקובץ שלם ותקין (חתימת SHA-256) ומתקינה אותה. אפשר גם לבדוק ביד: {t:more} ← {t:upd_check_now}.
האפשרות {t:upd_check_start} בודקת בכל הפעלה.</p>
<p>מספר הגרסה (למשל 1.0.215) מופיע בחלון העדכון ובדו״ח העבודה. כל שינוי בתוכנה מקבל מספר חדש.</p>
"""),
    ("screen", "המסך הראשי", """
<h3>הסרגל העליון</h3>
<ul>
<li>{t:open} - פתיחת קבצים. {t:shapes} - צורות מוכנות, {t:add_text} ו{t:void_title}.</li>
<li>{t:ocp_button} ו{t:auto_prepare} - הכנת הפלטה בלחיצה אחת (פרק 9).</li>
<li>{t:arrange} - סידור על הפלטה; החץ שלידו פותח את {t:auto_layout}, {t:lay_fill} ו{t:align_menu}.</li>
<li>{t:duplicate}, {t:delete}, {t:undo} ו{t:redo}.</li>
<li>{t:more} - כל שאר הפקודות: פרויקטים, ייצוא, רשת, תיקון רשת, חוברת ההדרכה ועוד.</li>
<li>במרכז: המעבר בין {t:prepare} ל{t:preview} (השכבות אחרי החיתוך).</li>
<li>מימין: {t:assistant}, {t:theme} (בהיר / כהה / לוגו של המעבדה) ושפה (עברית, English, Русский, العربية).</li>
</ul>
<h3>שורת התפריטים</h3>
<ul>
<li>{t:menu_file}: {t:open}, {t:open_project}, {t:recent_files}, {t:save_project}, {t:slice}, {t:export_print},
{t:export_as}, {t:export_stl}, {t:report_save}, ספריית ההדפסות, {t:net_title}, {t:queue_title} ו{t:menu_exit}.</li>
<li>{t:menu_edit}: {t:undo}, {t:redo}, {t:select_all}, {t:duplicate}, {t:multiply}, {t:delete}, הסידור והיישור,
{t:center}, {t:move_to}, {t:scale_to}, {t:rename}, {t:model_settings}, {t:merge_models}, {t:hide_models} ו{t:show_all_models}.</li>
<li>{t:menu_info}: {t:assistant}, {t:estimate_title}, {t:manual_menu} ({k:manual}), {t:menu_shortcuts},
{t:menu_updates} ו{t:menu_about}.</li>
</ul>
<h3>הלוח השמאלי</h3>
<p>רשימת {t:objects} (אפשר לבחור כמה עם Ctrl או Shift), ומתחתיה {t:transform}: מיקום, סיבוב, קנה מידה וגודל
במ״מ, כפתורי {t:rotate_90}, {t:auto_orient}, {t:lay_flat}, {t:center} ו{t:reset_transform}. בשרף מופיע
גם כרטיס {t:supports_card}. {k:panel_left} מסתיר ומציג את הלוח.</p>
<h3>תצוגת התלת-ממד</h3>
<p>בצד התצוגה סרגל הכלים: הזזה ({k:tool_move}), סיבוב ({k:tool_rotate}), פאה למשטח ({k:tool_face}),
חור ניקוז ({k:tool_hole}), אזור ללא תמיכות ({k:tool_keepout}), כיתוב ({k:tool_label}) ומדידה
({k:tool_measure}). למעלה: {t:view_iso}, {t:view_top}, {t:view_front}, {t:view_side} ו{t:view_fit},
וגם {t:overhangs}, הצגת משטחים שאין להם תמיכה ותצוגת חתך. הקובייה בפינה מסובבת את המבט בלחיצה על פאה.</p>
<h3>לוח ההגדרות</h3>
<p>למעלה בוחרים משפחת מדפסות: FILAMENT (FDM), RESIN (LCD / DLP / SLA) או UV (PolyJet). מתחת:
{t:printer} והמשטח שלה, {t:material}, {t:quality} ומצב יציב / מהיר, ואחריהם תיבת חיפוש הגדרות, מעבר בין
תצוגה פשוטה למלאה וכל ההגדרות לפי נושאים. {t:model_settings} (בתפריט {t:menu_edit}) נותן למודל אחד
הגדרות משלו. ליד כל הגדרה שהשתנתה
מופיע כפתור איפוס, ומעבר עם העכבר על השם מציג הסבר. {k:panel_right} מסתיר ומציג את הלוח. בתחתית:
{t:slice}, {t:export_print} ושליחה למדפסת ברשת.</p>
"""),
    ("files", "פתיחת קבצים וזיהוי סוג המודל", """
<p>פורמטים: STL, OBJ, 3MF, PLY, AMF, וקובץ ZIP שבתוכו מודלים. פותחים ב{t:open} ({k:open}), בגרירה לחלון
או מ{t:more} ← {t:recent_files}.</p>
<p>בפתיחה מופיע חלון <b class="ui">הוספת חלקים</b> שבו בוחרים לכל קובץ את הסוג (כתר / גשר, מודל, סד ועוד).
התוכנה מציעה סוג לפי שם הקובץ: קוד מעבדה עם טווח שיניים (למשל FYQ-SOD…-16-16-1) הוא כתר או גשר,
<code>_Antag</code> הוא אנטגוניסט, <code>_Tooth_14</code> הוא דאי, <code>_Tissue</code> הוא מסכת חניכיים.
כשהשם לא אומר כלום, הסוג נקבע לפי הצורה. אפשר לכבות את החלון ב{t:more} ← {t:ap_show_menu}.</p>
<h3>מה התוכנה עושה לבד בטעינה</h3>
<ul>
<li><b>{t:open_up_menu}</b> - כתרים וגשרים מסובבים כך שהפתח פונה למעלה (הצד הפנימי לא נוגע בתמיכות).</li>
<li><b>{t:tissue_up_menu}</b> - מסכות חניכיים מונחות עם הצד השטוח כלפי מעלה.</li>
<li><b>{t:auto_close_holes}</b> - חורים קטנים ברשת נסגרים. שולי כתר שלא חוברו מתוקנים כך שהפתח של הכתר
נשאר פתוח.</li>
<li>כל שאר המודלים נשארים בכיוון שבו הגיעו מהקובץ.</li>
</ul>
<p>שלוש האפשרויות האלה נמצאות בתפריט {t:more} ואפשר לכבות כל אחת. קווים אדומים על מודל מסמנים רשת
פתוחה או שבורה ({t:show_defects}); {t:repair_mesh} ו{t:repair_advanced} מתקנים אותה.</p>
"""),
    ("printer", "מדפסת, חומר ואיכות", """
<p>בוחרים {t:printer} מהרשימה (מקובצת לפי יצרן; כוכב מסמן מועדפים). הפלטה בתצוגה, פורמט הקובץ והגדרות
החשיפה מתעדכנים לפי המדפסת. רשימת המדפסות המלאה בנספח ד׳.</p>
<p>ב{t:material} מופיעים קודם, עם ★, השרפים שנבדקו למדפסת הזו. בשרפים עם טבלת יצרן (למשל Phrozen DS)
בוחרים ב<b class="ui">מצב פרופיל השרף</b> בין <b>יציב (Stable)</b> ל<b>מהיר (Fast)</b>, וערכי החשיפה
והתנועה נלקחים מהטבלה לפי המדפסת ועובי השכבה.</p>
<p>ב{t:quality} בוחרים את עובי השכבה. כל הגדרה שתשנה נשמרת; אפשר לשמור את השילוב כפרופיל משלך
({t:save_printer} / {t:save_material}).</p>
<p><b>Formlabs:</b> מדפסות Formlabs מדפיסות רק עבודות ש-PreForm הכינה. בוחרים את המדפסת ואת השרף
(למשל Grey V5 או White V5), מסדרים ב-UniSlicer, ושולחים ב{t:more} ← {t:fl_menu} (פרק 11).</p>
"""),
    ("place", "מיקום, כיוון וסידור", """
<p><b>הזזה:</b> גוררים מודל על הפלטה, או מקלידים מיקום בלוח השמאלי. גובה מעל הפלטה (Z) מרים את המודל.</p>
<p><b>סיבוב:</b> כלי הסיבוב ({k:tool_rotate}) מציג שלוש טבעות; גוררים טבעת כדי לסובב סביב הציר שלה
(Shift - בקפיצות של 15°), והזווית מוצגת ליד הסמן. אפשר גם להקליד זוויות, ללחוץ {t:rotate_90}, או
להשתמש ב{t:lay_flat} ובכלי הפאה ({k:tool_face}) - לוחצים על פאה והיא מונחת על הפלטה.</p>
<p>{t:auto_orient} מחפש כיוון שדורש מעט תמיכות; בשרף הוא מעדיף הטיה כדי שלא יהיו משטחים גדולים מקבילים
למסך. לחיצה ימנית על מודל פותחת תפריט עם כיוונים מוכנים, מראה, הדפסה ישירה על הפלטה ועוד.</p>
<h3>סידור על הפלטה</h3>
<ul>
<li>{t:layout_quick} ({k:arrange}) - מסדר את כל המודלים לפי ההגדרות שנלמדו.</li>
<li>{t:auto_layout} ({k:auto_layout}) - חלון עם שיטה (שורות, צורה אמיתית או רשת), מרווח, שוליים, סיבוב
וכיוון התחלה.</li>
<li>{t:lay_fill}, {t:multiply}, {t:duplicate} ({k:duplicate}) ו{t:align_menu}.</li>
</ul>
<p>הסידור משאיר מקום לרפסודה, וגם ללשונית של רפסודה + קוד.</p>
"""),
    ("supports", "תמיכות", """
<p>בשרף, הכרטיס {t:supports_card} בלוח השמאלי מרכז את העבודה:</p>
<ul>
<li>{t:sup_auto} - נקודות תמיכה למודל הנבחר, או לכולם כשאף מודל לא נבחר. התמיכות נבנות ברקע והתצוגה
נשארת חלקה.</li>
<li>{t:sup_add} ({k:sup_add}) - כל לחיצה על המודל מוסיפה תמיכה אחת בנקודה הירוקה. Esc לסיום.</li>
<li>{t:sup_remove} ({k:sup_remove}) - לחיצה על תמיכה מסירה אותה.</li>
<li>{t:sup_manual} - מתחיל בלי תמיכות ומוסיף תמיכה בכל לחיצה. {t:sup_clear} מוחק את כולן.</li>
<li>{t:raft_menu} - סוג הרפסודה, עובי, שוליים, שיפוע וגובה (פרק 7).</li>
</ul>
<p><b>סוג המודל</b> (בהגדרות התמיכות) קובע את התמיכות: בכתר / גשר, סד, מדריך ותותבת לא שמים תמיכות על
משטח ההתאמה. <b>סוג תמיכות</b> - קלות, בינוניות או כבדות. <b>תמיכות לנקודות נמוכות</b> שמה תמיכה בכל
קצה שמתחיל באוויר, כך ששום חלק לא נשאר בלי תמיכה.</p>
<p>הכלי {k:tool_keepout} מסמן אזור שבו לא יהיו תמיכות. הכפתור להצגת משטחים ללא תמיכה צובע באדום כל
משטח תלוי שאף תמיכה לא מגיעה אליו. מודל שמודפס ישירות על הפלטה (קליק ימני ← הדפסה ישירה) מקבל
תמיכות רק אם מבטלים את ההדפסה הישירה.</p>
{img:supports.png|כתר עם תמיכות ורפסודה + קוד}
"""),
    ("raft", "רפסודה וקוד המקרה", """
<p>סוגי הרפסודה (<b class="ui">סוג רפסודה</b>): מלאה, רשת, משושים, מיני (משטח קטן לכל קבוצת תמיכות),
מיני זעירה (בתוך קו השן), <b>רפסודה + קוד (raft&amp;label)</b> וללא.</p>
<h3>רפסודה + קוד (raft&amp;label)</h3>
<p>רפסודה צרה לפי קו המתאר של המודל, 0.8 מ״מ מסביב, וכל רגלי התמיכות עליה. בקצה המודל (בצד ימין של
הפלטה, או מאחור כשהחלק מונח לאורך הפלטה) יש לשונית בצורת חצי ירח ברוחב 5 מ״מ, ועליה קוד המקרה בולט
0.4 מ״מ. הקוד לקוח ממספר ה-SOD בשם הקובץ (46614-044-P מתוך DAA-SOD260046614-044-P-…), ובקובץ בלי
קוד - שם הקובץ. אם משנים לסוג הזה אחרי שהחלקים כבר מסודרים צמוד, לוחצים שוב {t:layout_quick}
({k:arrange}) כדי לפנות מקום ללשונית.</p>
{img:raft_label.png|רפסודה + קוד: רפסודה צרה ולשונית חצי ירח עם הקוד}
<h3>קוד ברפסודות אחרות</h3>
<p><b class="ui">קוד SOD על רפסודות של שיניים</b> שם את הקוד על הרפסודה של כתרים וגשרים, ו<b class="ui">שם
המודל על הרפסודה</b> שם את שם הקובץ. <b class="ui">מיקום הכיתוב ברפסודה</b>: בתחתית (חרוט והפוך) או
למעלה (בולט).</p>
<p class="note">שכבות הבסיס מוקרנות זמן רב, והאור החזק נוטה לסגור חריטה בתחתית. אם הקוד לא יוצא בהדפסה,
עדיף קוד בולט למעלה, או רפסודה + קוד.</p>
"""),
    ("tools", "כלים למודל", """
<ul>
<li><b>חלילה וחורי ניקוז</b> (הגדרות ← חלילה וחורי ניקוז): חלל סגור, פתוח מלמטה או בסיס מלא; חור ניקוז
מוסיפים בכלי {k:tool_hole} בלחיצה על המודל.</li>
<li><b>כיתוב על המודל</b> ({k:tool_label}): לוחצים במקום, מקלידים; Ctrl + גלגלת משנה גודל, Alt + גלגלת
מסובב.</li>
<li><b>מדידה</b> ({k:tool_measure}): שתי לחיצות - המרחק מוצג בתצוגה.</li>
<li>{t:cut_height}, {t:split_parts}, {t:repair_mesh}, {t:repair_advanced}, {t:texture_title}.</li>
<li>{t:cutter_role} - חלק שלא מודפס אלא נחתך מהמודלים שהוא חופף (לחריטה וחורים בצורה חופשית).</li>
<li>{t:void_title} ו{t:add_text} בתפריט {t:shapes}.</li>
</ul>
"""),
    ("oneclick", "פלטה בלחיצה אחת והכנה אוטומטית", """
<p>{t:ocp_button}: מזהה את הסוג של כל מודל, מסדר את הפלטה ויוצר תמיכות, ואז מציג סיכום עם תיקון לכל
אזהרה (מודל מחוץ לפלטה, חלק בלי תמיכה, כוס שתחזיק שרף ועוד) לפני החיתוך. הכיוון של המודלים לא משתנה.</p>
<p>{t:auto_prepare}: גם מכוון כל מודל אוטומטית, ואז מסדר ומוסיף תמיכות.</p>
"""),
    ("slice", "חיתוך ותצוגה מקדימה", """
<p>{t:slice} ({k:slice}) פורס את הפלטה לשכבות. בזמן העבודה התוכנה חותכת ברקע, כך שהחיתוך לרוב מוכן מיד.
אחרי החיתוך עוברים ל{t:preview}: גוררים את מחוון השכבות או משתמשים ב-{k:layer_up} ו-{k:layer_down},
ו{t:layers_play} מנגן את השכבות. מתחת מוצגים זמן ההדפסה, כמות החומר, משקל ועלות;
הפירוט ב{t:more} ← {t:estimate_title}.</p>
<p>תצוגת החתך חותכת את המודלים בגובה שבוחרים (או לרוחב, X / Y) כדי לראות פנימה.
{t:pixel_edit} מאפשרת לתקן פיקסלים בשכבה לפני הייצוא.</p>
{img:preview.png|תצוגה מקדימה של השכבות}
"""),
    ("export", "ייצוא ושליחה למדפסת", """
<p>{t:export_print} ({k:export}) שומר את קובץ ההדפסה בפורמט של המדפסת: PRZ ל-Phrozen, CTB, GOO ל-Elegoo,
SL1, ZIP של CHITUBOX, SLC / CLI ל-SLA תעשייתי, 3MF ל-PreForm / GrabCAD, ו-G-code ל-FDM. הקובץ נשמר
ב<b>ספריית ההדפסות</b> ({t:export_library_open} / {t:export_library_choose}), ו{t:export_as} שומר
למקום אחר. אם הפלטה שונתה אחרי החיתוך, חותכים שוב לפני הייצוא (אחרת מיוצא החיתוך האחרון, עם הודעה).</p>
<ul>
<li>{t:report_save} - דו״ח עבודה עם תמונה, זמנים וחומר; {t:report_auto} שומר אותו עם כל ייצוא.</li>
<li>{t:project_auto} - שומר קובץ פרויקט ליד כל קובץ הדפסה, כדי לפתוח את הפלטה שוב.</li>
<li>{t:export_stl} - המודלים עם התמיכות כ-STL.</li>
<li>{t:net_title} - איתור מדפסות ברשת ושליחה ישירה; {t:queue_title} - כמה עבודות ברצף.</li>
<li>{t:fl_menu} - דרך PreForm Server: פותחים PreForm ← File ← Start PreForm Server, לוחצים התחבר, בוחרים
מדפסת ושרף (נבחר מעצמו לפי השרף ב-UniSlicer) ושולחים.</li>
<li>{t:remote_title} - צפייה ושליטה במדפסות הרשת מהטלפון או מדפדפן, עם חשבון Google (התוכנה צריכה להישאר
פתוחה במחשב).</li>
<li>{t:cad_title} - מגדירים תיקייה כיעד הייצוא של תוכנת התכנון; כל קובץ חדש שמגיע אליה נטען, מזוהה לפי
סוג העבודה ומוכן להדפסה אוטומטית (כיוון, תמיכות וסידור).</li>
</ul>
"""),
    ("projects", "פרויקטים, ביטול והעדפות", """
<p>{t:save_project} ({k:save_project}) שומר את כל הפלטה - מודלים, מיקומים, תמיכות והגדרות; {t:open_project}
פותח אותו. {t:undo} ({k:undo}) ו{t:redo} ({k:redo}) עובדים על כל פעולה, כולל תמיכות וטעינה.</p>
<p>{t:mouse_menu}: {t:mouse_chitubox} או {t:mouse_classic} (נספח ב׳). {t:theme}: בהיר, כהה, ערכה ממותגת
ו{t:theme_logo} להצגת הלוגו של המעבדה.</p>
"""),
    ("assistant", "העוזר", """
<p>{t:assistant} בודק את המודלים ברקע, לומד מהשינויים שלך (סידורים, תיקוני תמיכות, הגדרות) ומציע שיפורים
בלוח העוזר. סידור פלטה שלימדת אותו מופעל ב{t:auto_layout}.</p>
"""),
    ("fdm", "הדפסת פילמנט (FDM)", """
<p>בבחירת מדפסת FDM מופיעות הגדרות הקירות, המילוי, הטמפרטורה, המהירות והנסיגה. {t:fil_title} מגדיר כמה
פילמנטים והחלפת צבעים (כולל Creality CFS), וכל מודל מקבל פילמנט משלו. הקובץ היוצא הוא G-code עם תמונה
מקדימה.</p>
"""),
    ("trouble", "פתרון בעיות", """
<table class="grid">
<tr><th>הבעיה</th><th>מה עושים</th></tr>
<tr><td>חלק נשאר באוויר בלי תמיכה</td><td>מפעילים את הצגת המשטחים ללא תמיכה ומוסיפים ב-{k:sup_add};
ודאו ש<b>תמיכות לנקודות נמוכות</b> פעיל.</td></tr>
<tr><td>הקוד על הרפסודה לא יוצא בהדפסה</td><td>רפסודה + קוד, או מיקום הכיתוב: למעלה, בולט.</td></tr>
<tr><td>הפתחים של כתר נסגרו</td><td>{t:auto_close_holes} שומר על פתח הכתר; אם עדיין קורה, מכבים אותו
ושולחים את הקובץ לבדיקה.</td></tr>
<tr><td>הכתר נטען הפוך</td><td>{t:open_up_menu} פעיל? אפשר לסובב ביד או לכבות את האפשרות.</td></tr>
<tr><td>מודלים נוגעים זה בזה</td><td>{t:layout_quick} ({k:arrange}) או הגדלת המרווח ב{t:auto_layout}.</td></tr>
<tr><td>התוכנה איטית במחשב ישן</td><td>התוכנה עוברת אוטומטית לתצוגת תוכנה כשאין OpenGL 3.3; עדכון מנהל
ההתקן של כרטיס המסך משפר את המהירות.</td></tr>
<tr><td>הופיעה הודעת שגיאה</td><td>העוזר תופס את השגיאה, משאיר את התוכנה פתוחה ומסביר מה קרה ואיך לתקן.</td></tr>
</table>
"""),
)

# every command of the More menu and the top bar: the guide must name each one (the tests check)
MENU_KEYS = ("open", "shapes", "add_text", "void_title", "ocp_button", "auto_prepare", "arrange", "layout_quick",
             "auto_layout", "lay_fill", "align_menu", "duplicate", "delete", "undo", "redo", "more",
             "open_project", "recent_files", "save_project", "export_stl", "estimate_title", "report_save",
             "report_auto", "project_auto", "auto_close_holes", "open_up_menu", "tissue_up_menu",
             "upd_check_now", "upd_check_start", "mouse_menu", "ap_show_menu", "show_defects", "export_as",
             "export_library_open", "export_library_choose", "net_title", "fl_menu", "queue_title",
             "remote_title", "fil_title", "cad_title", "multiply", "cut_height", "split_parts", "repair_mesh",
             "repair_advanced", "texture_title", "pixel_edit", "prepare", "preview", "assistant", "theme",
             "manual_menu", "menu_file", "menu_edit", "menu_info", "menu_exit", "select_all", "center", "move_to",
             "scale_to", "rename", "model_settings", "merge_models", "hide_models", "show_all_models",
             "menu_shortcuts", "menu_updates", "menu_about")

CSS = """
:root{--ink:#1d2430;--soft:#5b6575;--line:#d9dee6;--accent:#2f6fe4;--bg:#ffffff;--card:#f4f6f9;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Segoe UI","Noto Sans Hebrew",Arial,sans-serif}
main{max-width:880px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:34px;margin:0 0 4px} h2{font-size:24px;margin:48px 0 12px;padding-top:8px;border-top:2px solid var(--line)}
h3{font-size:17px;margin:22px 0 6px}
.sub{color:var(--soft);margin:0 0 24px}
b.ui{color:var(--accent);font-weight:600}
kbd{display:inline-block;min-width:22px;padding:1px 7px;border:1px solid #b9c1cd;border-bottom-width:2px;
    border-radius:5px;background:#fff;font:600 13px/1.5 Consolas,"Segoe UI",monospace;direction:ltr;text-align:center}
code{direction:ltr;unicode-bidi:embed;background:var(--card);padding:1px 5px;border-radius:4px;font-size:13px;word-break:break-all}
figure{margin:16px 0;text-align:center;break-inside:avoid}
figure img{max-width:100%;max-height:330px;border:1px solid var(--line);border-radius:8px}
figure.wide img{max-height:460px}
.keys{unicode-bidi:isolate;white-space:nowrap}
figcaption{color:var(--soft);font-size:13px;margin-top:4px}
table.grid{border-collapse:collapse;width:100%;margin:10px 0}
table.grid th,table.grid td{border:1px solid var(--line);padding:6px 9px;text-align:right;vertical-align:top}
table.grid th{background:var(--card)}
table.dense{font-size:13px} table.dense td{padding:3px 8px} table.dense td:first-child{width:34%}
h3.part{font-size:20px;margin-top:28px;color:var(--accent)} h4{font-size:15px;margin:16px 0 4px}
.note{background:#fff6e0;border-right:4px solid #e9a400;padding:8px 12px;border-radius:4px}
nav.toc ol{columns:2;gap:32px;padding-right:20px} nav.toc a{color:var(--ink);text-decoration:none}
.tip{color:var(--soft);font-size:13px}
.page{break-before:page}
@media print{body{font-size:12.5px} main{max-width:none;padding:0} h2{break-after:avoid} tr,figure{break-inside:avoid}}
"""


def _strings():
    from .gui import i18n
    return i18n.STRINGS


def _label(key: str) -> str:
    """The interface's Hebrew name for ``key``, as it shows (shortcut hints and trailing dots removed)."""
    v = _strings().get(key)
    if v is None:
        raise KeyError(f"manual: no interface string '{key}'")
    text = v[0].split("\t")[0].strip().rstrip("…").strip()
    return text


def _keys_html(seqs) -> str:
    from .gui.shortcuts import keys_text
    out = []
    for seq in seqs:
        parts = keys_text(seq).split("+") if keys_text(seq) != "+" else ["+"]
        out.append('<span class="keys" dir="ltr">' + "+".join(f"<kbd>{html.escape(p)}</kbd>" for p in parts) + "</span>")
    return " או ".join(out)


def _shortcut(sid: str) -> str:
    from .gui.shortcuts import SHORTCUTS
    for name, seqs, *_ in SHORTCUTS:
        if name == sid:
            return _keys_html(seqs)
    raise KeyError(f"manual: no shortcut '{sid}'")


def _expand(body: str, images: str) -> str:
    def rep(m):
        kind, arg = m.group(1), m.group(2)
        if kind == "t":
            return f'<b class="ui">{html.escape(_label(arg))}</b>'
        if kind == "k":
            return _shortcut(arg)
        name, _, caption = arg.partition("|")
        if not os.path.isfile(os.path.join(IMG_DIR, name)):
            return ""
        src = f"{images}/{name}" if images else name
        wide = ' class="wide"' if name in ("main.png", "preview.png") else ""
        return f'<figure{wide}><img src="{html.escape(src)}" alt=""><figcaption>{html.escape(caption)}</figcaption></figure>'
    return re.sub(r"\{(t|k|img):([^}]+)\}", rep, body)


def _shortcuts_page() -> str:
    from .gui.shortcuts import GROUPS, MOUSE, SHORTCUTS
    out = ['<h2 id="shortcuts" class="page">נספח א׳ - קיצורי מקשים</h2>',
           '<p class="tip">הטבלה נבנית מהתוכנה עצמה, כך שהיא תמיד מעודכנת.</p>']
    for gid, he, _en in GROUPS:
        rows = [(seqs, d) for _n, seqs, g, d, _e in SHORTCUTS if g == gid]
        if not rows:
            continue
        out.append(f"<h3>{html.escape(he)}</h3><table class='grid'><tr><th style='width:34%'>מקש</th><th>פעולה</th></tr>")
        out += [f"<tr><td>{_keys_html(seqs)}</td><td>{html.escape(d)}</td></tr>" for seqs, d in rows]
        out.append("</table>")
    out.append('<h2 id="mouse">נספח ב׳ - העכבר בתצוגת התלת-ממד</h2>')
    titles = {"chitubox": _label("mouse_chitubox") + " (ברירת מחדל)", "classic": _label("mouse_classic"),
              "rings": "סיבוב מודל", "label": "כיתוב"}
    for scheme, rows in MOUSE.items():
        out.append(f"<h3>{html.escape(titles[scheme])}</h3><table class='grid'><tr><th style='width:40%'>פעולה</th><th>מה קורה</th></tr>")
        out += [f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in rows]
        out.append("</table>")
    out.append(f"<p>מחליפים שיטה ב{_expand('{t:more} ← {t:mouse_menu}', '')}.</p>")
    return "\n".join(out)


def _settings_page() -> str:
    from .settings import CATEGORIES, SCHEMA
    out = ['<h2 id="settings" class="page">נספח ג׳ - כל ההגדרות</h2>',
           '<p class="tip">ההגדרות וההסברים נלקחים מהתוכנה. קודם הגדרות השרף (SLA / DLP / LCD / PolyJet), '
           'אחריהן הגדרות הפילמנט (FDM). הגדרות שמשותפות לשתיהן מופיעות בחלק של השרף.</p>']
    for part, techs, title in (("sla", ("sla", "both"), "הגדרות שרף"), ("fdm", ("fdm",), "הגדרות פילמנט (FDM)")):
        out.append(f"<h3 class='part'>{title}</h3>")
        for cat, he, _en, _icon in CATEGORIES:
            rows = [s for s in SCHEMA if s.category == cat and s.tech in techs]
            if not rows:
                continue
            out.append(f"<h4>{html.escape(he)}</h4><table class='grid dense'>")
            for s in rows:
                unit = f" <span class='tip'>({html.escape(s.unit)})</span>" if s.unit else ""
                tip = html.escape(s.tip_he) if s.tip_he else ""
                if s.options:
                    opts = " · ".join(html.escape(o[1]) for o in s.options)
                    tip = (tip + "<br>" if tip else "") + f"<span class='tip'>אפשרויות: {opts}</span>"
                out.append(f"<tr><td><b>{html.escape(s.he)}</b>{unit}</td><td>{tip}</td></tr>")
            out.append("</table>")
    return "\n".join(out)


def _printers_page() -> str:
    from . import profiles
    printers = profiles.load("printers")
    mats = profiles.load("materials")
    by_vendor: dict[str, list] = {}
    for pid, p in printers.items():
        by_vendor.setdefault(p.get("vendor", "Generic"), []).append((pid, p))
    out = ['<h2 id="printers" class="page">נספח ד׳ - המדפסות בתוכנה</h2>',
           f'<p class="tip">{len(printers)} מדפסות. "שרפים מותאמים" = שרפים שנבדקו למדפסת ומופיעים עם ★.</p>',
           "<table class='grid'><tr><th>יצרן</th><th>מדפסת</th><th>סוג</th><th>שרפים מותאמים</th></tr>"]
    for vendor in sorted(by_vendor, key=lambda v: (v == "Generic", v.lower())):
        for pid, p in sorted(by_vendor[vendor], key=lambda kv: kv[1].get("name", "")):
            vals = p.get("values", {})
            kind = "FDM" if vals.get("technology", "fdm") == "fdm" else (vals.get("sla_process") or "sla").upper()
            linked = sum(1 for m in mats.values() if pid in m.get("printers", []))
            out.append(f"<tr><td>{html.escape(vendor)}</td><td>{html.escape(p.get('name', pid))}</td>"
                       f"<td>{html.escape(kind)}</td><td>{linked or ''}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def build_html(images: str = "") -> str:
    """The whole guide as one HTML page (RTL). ``images``: where the page finds manual_img's pictures
    ("" = next to the page)."""
    from . import __version__
    toc = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t, _ in CHAPTERS)
    toc += ('<li><a href="#shortcuts">נספח א׳ - קיצורי מקשים</a></li><li><a href="#mouse">נספח ב׳ - העכבר</a></li>'
            '<li><a href="#settings">נספח ג׳ - כל ההגדרות</a></li><li><a href="#printers">נספח ד׳ - המדפסות</a></li>')
    chapters = "\n".join(f'<h2 id="{a}">{n}. {html.escape(t)}</h2>\n{_expand(b, images)}'
                         for n, (a, t, b) in enumerate(CHAPTERS, 1))
    today = datetime.date.today().strftime("%d.%m.%Y")
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>UniSlicer - חוברת הדרכה</title><style>{CSS}</style></head>
<body><main>
<h1>UniSlicer - חוברת הדרכה</h1>
<p class="sub">גרסה {html.escape(__version__)} · נוצרה מהתוכנה ב-{today}</p>
<nav class="toc"><h3>תוכן העניינים</h3><ol>{toc}</ol></nav>
{chapters}
{_shortcuts_page()}
{_settings_page()}
{_printers_page()}
</main></body></html>"""


def build_shortcuts_html() -> str:
    """One printable page with every shortcut and the mouse (Info > Keyboard shortcuts)."""
    from . import __version__
    from .gui.shortcuts import GROUPS, MOUSE, SHORTCUTS
    blocks = []
    for gid, he, _en in GROUPS:
        rows = [(seqs, d) for _n, seqs, g, d, _e in SHORTCUTS if g == gid]
        if rows:
            blocks.append(f"<section><h3>{html.escape(he)}</h3><table>" + "".join(
                f"<tr><td class='k'>{_keys_html(seqs)}</td><td>{html.escape(d)}</td></tr>" for seqs, d in rows)
                + "</table></section>")
    mouse = MOUSE["chitubox"] + MOUSE["rings"] + MOUSE["label"]
    blocks.append("<section><h3>עכבר (כמו CHITUBOX)</h3><table>" + "".join(
        f"<tr><td class='k'>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in mouse) + "</table></section>")
    css = CSS + """
body{font-size:10.5px;line-height:1.4} main{max-width:none;padding:0} h1{font-size:19px;margin:0}
.sub{margin:0 0 8px}
.cols{columns:3;column-gap:16px} section{break-inside:avoid;margin:0 0 7px}
section h3{font-size:12.5px;margin:3px 0 2px;color:var(--accent)}
section table{border-collapse:collapse;width:100%} section td{border-bottom:1px solid var(--line);padding:2px 3px;vertical-align:top}
td.k{width:36%} kbd{font-size:10px;padding:0 4px;min-width:16px;line-height:1.35}
@page{size:A4 landscape;margin:10mm}"""
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>UniSlicer - קיצורי מקשים</title><style>{css}</style></head>
<body><main><h1>UniSlicer - קיצורי מקשים</h1>
<p class="sub">גרסה {html.escape(__version__)} · הטבלה נבנית מהתוכנה עצמה · חוברת ההדרכה המלאה: F1</p>
<div class="cols">{''.join(blocks)}</div></main></body></html>"""


def write(folder: str) -> str:
    """Write the guide (and its pictures) into ``folder``; returns the page's path."""
    import shutil
    os.makedirs(folder, exist_ok=True)
    img_out = os.path.join(folder, "manual_img")
    if os.path.isdir(IMG_DIR):
        os.makedirs(img_out, exist_ok=True)
        for name in os.listdir(IMG_DIR):
            shutil.copyfile(os.path.join(IMG_DIR, name), os.path.join(img_out, name))
    path = os.path.join(folder, "UniSlicer-guide.html")
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(build_html("manual_img"))
    os.replace(tmp, path)
    sheet = os.path.join(folder, "UniSlicer-shortcuts.html")
    with open(f"{sheet}.{os.getpid()}.part", "w", encoding="utf-8") as f:
        f.write(build_shortcuts_html())
    os.replace(f"{sheet}.{os.getpid()}.part", sheet)
    return path


if __name__ == "__main__":       # python -m unislicer.manual [folder]
    print(write(sys.argv[1] if len(sys.argv) > 1 else "manual"))
