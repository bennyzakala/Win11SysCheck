# Kesher server (קשר)

גרסה אמיתית של קשר: הרשמה והתחברות, פרופילים, העלאת תמונות, לייקים, התאמות וצ'אט, עם חיפוש לפי עיר וסינון לפי טווח גילאים, סטטוס, דת וילדים. הכל חינם.

## הרצה מקומית

צריך Node.js 22.5 ומעלה.

```bash
cd kesher/server
npm install
npm start          # http://localhost:3000
npm test           # בדיקות API
```

בהרצה מקומית יש מסד נתונים מובנה (תואם Postgres) בתיקייה `data/db`, והתמונות נשמרות ב-`data/uploads`. אפשר להשתמש ב-Postgres אמיתי עם `DATABASE_URL`.

## מבנה

- `lib/app.js`: כל ה-API. מקבל Request ומחזיר Response, ולכן אותו קוד רץ גם ב-Netlify וגם בשרת המקומי.
- `lib/photos.js`: שמירת תמונות. ב-Netlify הן נשמרות ב-Netlify Blobs, ובהרצה מקומית בתיקייה על הדיסק.
- `netlify/functions/api.mjs`: נקודת הכניסה ב-Netlify (`/api/*` ו-`/uploads/*`).
- `netlify/database/migrations/`: מבנה מסד הנתונים. Netlify מריץ את הקבצים האלה אוטומטית בכל העלאה.
- `server.js`: שרת מקומי או לאירוח עצמי. מגיש את `public/` ואת ה-API.
- `public/`: הממשק (`index.html`, `app.js`, `style.css`).

## העלאה ל-Netlify

הפרויקט מוכן ל-Netlify: `netlify.toml` נמצא בתיקייה הזאת, הדפים ב-`public`, הפונקציה ב-`netlify/functions`, מסד הנתונים הוא Netlify Database (Postgres) והתמונות ב-Netlify Blobs. ב-Netlify מחברים את המאגר ב-GitHub ומגדירים **Base directory** = `kesher/server`. כל דחיפה לענף מעלה גרסה חדשה, ושינויים במסד הנתונים נכנסים דרך קבצי migration חדשים בלבד.

Netlify Database זמין רק בתוכניות מבוססות קרדיטים, וצורך קרדיטים לפי השימוש.

## משתני סביבה (שרת מקומי)

| משתנה | ברירת מחדל | מה עושה |
|---|---|---|
| `PORT` | `3000` | הפורט של השרת |
| `DATA_DIR` | `./data` | מסד הנתונים המובנה והתמונות |
| `DATABASE_URL` | | חיבור ל-Postgres חיצוני במקום המסד המובנה |
| `TRUST_PROXY` | | `1` מאחורי פרוקסי, כדי שהגבלת הניסיונות תעבוד לפי כתובת המבקר |

עוגיות ההתחברות מסומנות Secure אוטומטית כשהאתר רץ ב-HTTPS.

## אירוח עצמי ב-Docker

יש `Dockerfile`. צריך דיסק קבוע מחובר ל-`/data` (או `DATABASE_URL`), אחרת המשתמשים והתמונות יימחקו בכל הפעלה מחדש.

## אבטחה ופרטיות

- סיסמאות נשמרות כ-hash עם scrypt ו-salt. ההתחברות היא עוגיית סשן HttpOnly.
- הגבלת ניסיונות בהתחברות, בהרשמה ובשליחת הודעות. הספירה נשמרת במסד הנתונים, כך שהיא עובדת גם כשיש כמה מופעים של הפונקציה.
- כל בקשה שמשנה נתונים חייבת את הכותרת `X-Kesher: 1`, וזה חוסם שליחת טפסים מאתרים אחרים.
- ההרשמה מגיל 18 בלבד. האימייל ותאריך הלידה לא נחשפים למשתמשים אחרים, רק הגיל.
- תמונות: רק JPG, PNG או WEBP עד 4MB, עם בדיקה של תוכן הקובץ. התמונות מוגשות רק למשתמשים מחוברים, תחת שמות קבצים אקראיים.
- דיווח וחסימה מסתירים את שני הצדדים זה מזה וסוגרים את השיחה. ביטול התאמה סוגר את השיחה.
- מחיקת חשבון מוחקת את המשתמש, התמונות, הלייקים וההודעות.

לפני השקה לציבור כדאי להוסיף: אימות אימייל, איפוס סיסמה, ממשק ניהול לדיווחים, מדיניות פרטיות ותנאי שימוש, וגיבוי של `DATA_DIR`.

## API

| בקשה | תיאור |
|---|---|
| `POST /api/register` | הרשמה (email, password, name, birthdate, city, gender) |
| `POST /api/login`, `POST /api/logout` | התחברות והתנתקות |
| `GET /api/me`, `PUT /api/me`, `DELETE /api/me` | הפרופיל שלי, עדכון, מחיקת חשבון |
| `POST /api/me/photos` | העלאת תמונה (שדה `photo`, עד 6) |
| `DELETE /api/me/photos/:id`, `POST /api/me/photos/:id/first` | מחיקה, קביעה כתמונה ראשית |
| `GET /api/discover?city=&ageMin=&ageMax=&status=&relig=&kids=` | פרופילים לגילוי, עם ספירה לפי עיר |
| `POST /api/like`, `POST /api/skip` | לייק (לתמונה, תשובה או פרופיל, עם תגובה וורד), דילוג |
| `GET /api/likes`, `POST /api/likes/:fromId/remove` | מי שאהבו אותי, הסרה |
| `GET /api/matches`, `GET/POST /api/matches/:id/messages`, `POST /api/matches/:id/unmatch` | התאמות וצ'אט |
| `POST /api/report` | דיווח וחסימה |
