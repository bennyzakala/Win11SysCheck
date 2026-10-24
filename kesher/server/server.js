'use strict';
// Kesher server: accounts, profiles, photo uploads, likes, matches and chat.
// Storage is a single SQLite file plus an uploads folder, both under DATA_DIR.

const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { DatabaseSync } = require('node:sqlite');
const express = require('express');
const multer = require('multer');

const MAX_PHOTOS = 6;
const MAX_PHOTO_BYTES = 8 * 1024 * 1024;
const SESSION_DAYS = 30;

// Profile fields a user can fill in, with the allowed values for each choice field
const CHOICES = {
  gender: ['אישה', 'גבר', 'א-בינארי'],
  zodiac: ['טלה', 'שור', 'תאומים', 'סרטן', 'אריה', 'בתולה', 'מאזניים', 'עקרב', 'קשת', 'גדי', 'דלי', 'דגים'],
  relig: ['חילוני/ת', 'מסורתי/ת', 'דתי/ה', 'חרדי/ת', 'אחר'],
  intent: ['קשר רציני', 'קשר רציני, בלי לחץ', 'עוד לא בטוח/ה', 'משהו קליל'],
  kids: ['אין לי ילדים', 'יש לי ילדים'],
  wantKids: ['רוצה', 'לא רוצה', 'פתוח/ה לזה', 'עוד לא יודע/ת'],
  pets: ['כלב', 'חתול', 'כלב וחתול', 'אין', 'רוצה בעתיד'],
  drink: ['לא שותה', 'לפעמים', 'כן'],
  smoke: ['לא מעשן/ת', 'לפעמים', 'כן'],
  sport: ['לא', 'לפעמים', 'באופן קבוע'],
  kosher: ['לא שומר/ת', 'שומר/ת בבית', 'שומר/ת']
};
const TEXT_FIELDS = { name: 40, city: 40, job: 60, edu: 80, hometown: 40, langs: 80 };
const PROMPT_QUESTIONS = [
  'הדרך לכבוש אותי', 'יום ראשון מושלם', 'אני גאה ב...', 'עובדה מוזרה עליי', 'אנחנו נסתדר אם...',
  'הדייט הראשון האידיאלי', 'הדבר הכי ספונטני שעשיתי', 'אני מחפש/ת', 'הנושא שאני יכול/ה לדבר עליו שעות',
  'דגל ירוק', 'השיר שתמיד יגרום לי לרקוד', 'המנה שאבשל לך', 'הקטע שלי'
];

function createApp({ dataDir = process.env.DATA_DIR || path.join(__dirname, 'data'), secureCookies = process.env.NODE_ENV === 'production' } = {}) {
  const uploadDir = path.join(dataDir, 'uploads');
  fs.mkdirSync(uploadDir, { recursive: true });
  const db = new DatabaseSync(path.join(dataDir, 'kesher.db'));
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, pass TEXT NOT NULL,
      name TEXT NOT NULL, birthdate TEXT NOT NULL, gender TEXT, city TEXT NOT NULL,
      job TEXT, height INTEGER, edu TEXT, hometown TEXT, langs TEXT, zodiac TEXT, relig TEXT, intent TEXT,
      kids TEXT, wantKids TEXT, pets TEXT, drink TEXT, smoke TEXT, sport TEXT, kosher TEXT,
      prompts TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, file TEXT NOT NULL UNIQUE, position INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS likes (
      from_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      kind TEXT NOT NULL, idx INTEGER NOT NULL, note TEXT, rose INTEGER NOT NULL DEFAULT 0, removed INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL, PRIMARY KEY (from_id, to_id)
    );
    CREATE TABLE IF NOT EXISTS skips (from_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY (from_id, to_id));
    CREATE TABLE IF NOT EXISTS matches (
      id INTEGER PRIMARY KEY, a INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, b INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      ended INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, UNIQUE (a, b)
    );
    CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE, from_id INTEGER NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, from_id INTEGER, to_id INTEGER, reason TEXT, created_at INTEGER NOT NULL);
  `);
  const q = sql => db.prepare(sql);
  const now = () => Date.now();

  // ---------- Helpers ----------
  function hashPassword(pw) {
    const salt = crypto.randomBytes(16);
    return salt.toString('hex') + ':' + crypto.scryptSync(pw, salt, 64).toString('hex');
  }
  function checkPassword(pw, stored) {
    const [salt, hash] = stored.split(':');
    const got = crypto.scryptSync(pw, Buffer.from(salt, 'hex'), 64);
    return crypto.timingSafeEqual(got, Buffer.from(hash, 'hex'));
  }
  function ageOf(birthdate) {
    const b = new Date(birthdate + 'T00:00:00Z'), n = new Date();
    let a = n.getUTCFullYear() - b.getUTCFullYear();
    if (n.getUTCMonth() < b.getUTCMonth() || (n.getUTCMonth() === b.getUTCMonth() && n.getUTCDate() < b.getUTCDate())) a--;
    return a;
  }
  const normCity = s => String(s || '').replace(/[\s\-־"׳']/g, '').toLowerCase();
  const pair = (x, y) => (x < y ? [x, y] : [y, x]);
  const photosOf = id => q('SELECT id, file FROM photos WHERE user_id = ? ORDER BY position, id').all(id)
    .map(p => ({ id: p.id, url: '/uploads/' + p.file }));

  function publicProfile(u) {
    const out = { id: u.id, name: u.name, age: ageOf(u.birthdate), city: u.city, height: u.height || null, photos: photosOf(u.id), prompts: JSON.parse(u.prompts) };
    for (const k of [...Object.keys(CHOICES), 'job', 'edu', 'hometown', 'langs']) out[k] = u[k] || '';
    return out;
  }
  const userById = id => q('SELECT * FROM users WHERE id = ?').get(id);

  class HttpError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
  const fail = (status, msg) => { throw new HttpError(status, msg); };

  // Validates profile fields from a request body; returns only the fields that were sent
  function cleanProfile(body, { requireCore = false } = {}) {
    const out = {};
    for (const [k, max] of Object.entries(TEXT_FIELDS)) {
      if (body[k] === undefined) continue;
      const v = String(body[k]).trim();
      if (v.length > max) fail(400, `השדה ${k} ארוך מדי`);
      out[k] = v;
    }
    for (const [k, opts] of Object.entries(CHOICES)) {
      if (body[k] === undefined) continue;
      const v = String(body[k]);
      if (v && !opts.includes(v)) fail(400, `ערך לא תקין בשדה ${k}`);
      out[k] = v;
    }
    if (body.height !== undefined && body.height !== null && body.height !== '') {
      const h = Number(body.height);
      if (!Number.isInteger(h) || h < 120 || h > 230) fail(400, 'הגובה צריך להיות בין 120 ל-230 ס״מ');
      out.height = h;
    } else if (body.height !== undefined) out.height = null;
    if (body.prompts !== undefined) {
      if (!Array.isArray(body.prompts) || body.prompts.length > 3) fail(400, 'אפשר לענות על עד 3 שאלות');
      out.prompts = JSON.stringify(body.prompts.map(p => {
        const qn = String(p && p.q || ''), a = String(p && p.a || '').trim();
        if (!PROMPT_QUESTIONS.includes(qn)) fail(400, 'שאלה לא מוכרת');
        if (a.length > 200) fail(400, 'התשובה ארוכה מדי (עד 200 תווים)');
        return { q: qn, a };
      }));
    }
    if (requireCore && (!out.name || !out.city)) fail(400, 'צריך למלא שם ועיר');
    if (out.name === '' || out.city === '') fail(400, 'שם ועיר הם שדות חובה');
    return out;
  }

  // Simple in-memory limiter for login and registration attempts
  const attempts = new Map();
  function limit(key, max, windowMs) {
    const t = now(), list = (attempts.get(key) || []).filter(x => t - x < windowMs);
    if (list.length >= max) fail(429, 'יותר מדי ניסיונות. נסו שוב בעוד כמה דקות');
    list.push(t); attempts.set(key, list);
  }

  // ---------- App ----------
  const app = express();
  app.disable('x-powered-by');
  if (process.env.TRUST_PROXY) app.set('trust proxy', 1); // behind a hosting proxy, so req.ip is the visitor's address
  app.use(express.json({ limit: '50kb' }));
  app.use((req, res, next) => {
    res.set({ 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY' });
    next();
  });

  // Sessions: random token in an HttpOnly cookie
  function cookieToken(req) {
    const m = /(?:^|;\s*)sid=([a-f0-9]{64})/.exec(req.headers.cookie || '');
    return m && m[1];
  }
  function startSession(res, userId) {
    const token = crypto.randomBytes(32).toString('hex');
    q('INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)').run(token, userId, now());
    res.cookie('sid', token, { httpOnly: true, sameSite: 'lax', secure: secureCookies, maxAge: SESSION_DAYS * 864e5, path: '/' });
  }
  app.use((req, res, next) => {
    const token = cookieToken(req);
    if (token) {
      const s = q('SELECT user_id, created_at FROM sessions WHERE token = ?').get(token);
      if (s && now() - s.created_at < SESSION_DAYS * 864e5) { req.userId = s.user_id; req.token = token; }
    }
    // State-changing API calls must come from our own page (blocks cross-site form posts)
    if (req.method !== 'GET' && req.path.startsWith('/api/') && req.get('X-Kesher') !== '1') {
      return res.status(403).json({ error: 'בקשה לא מורשית' });
    }
    next();
  });
  const auth = (req, res, next) => (req.userId ? next() : res.status(401).json({ error: 'צריך להתחבר' }));
  const wrap = fn => (req, res, next) => { try { const r = fn(req, res); if (r !== undefined) res.json(r); } catch (e) { next(e); } };

  // ---------- Accounts ----------
  app.get('/api/options', (req, res) => res.json({ choices: CHOICES, prompts: PROMPT_QUESTIONS, maxPhotos: MAX_PHOTOS }));

  app.post('/api/register', wrap((req, res) => {
    limit('reg:' + req.ip, 10, 60 * 60 * 1000);
    const email = String(req.body.email || '').trim().toLowerCase();
    const password = String(req.body.password || '');
    const birthdate = String(req.body.birthdate || '');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 120) fail(400, 'כתובת האימייל לא תקינה');
    if (password.length < 8) fail(400, 'הסיסמה צריכה להכיל לפחות 8 תווים');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(birthdate) || isNaN(new Date(birthdate))) fail(400, 'תאריך הלידה לא תקין');
    const age = ageOf(birthdate);
    if (age < 18) fail(400, 'ההרשמה מגיל 18 ומעלה');
    if (age > 100) fail(400, 'תאריך הלידה לא תקין');
    const prof = cleanProfile(req.body, { requireCore: true });
    if (q('SELECT 1 FROM users WHERE email = ?').get(email)) fail(409, 'כבר קיים חשבון עם האימייל הזה');
    const cols = Object.keys(prof);
    const r = q(`INSERT INTO users (email, pass, birthdate, created_at, ${cols.join(', ')}) VALUES (?, ?, ?, ?, ${cols.map(() => '?').join(', ')})`)
      .run(email, hashPassword(password), birthdate, now(), ...cols.map(c => prof[c]));
    startSession(res, Number(r.lastInsertRowid));
    res.status(201);
    return { ok: true };
  }));

  app.post('/api/login', wrap((req, res) => {
    const email = String(req.body.email || '').trim().toLowerCase();
    limit('login:' + req.ip + ':' + email, 10, 15 * 60 * 1000);
    const u = q('SELECT id, pass FROM users WHERE email = ?').get(email);
    if (!u || !checkPassword(String(req.body.password || ''), u.pass)) fail(401, 'האימייל או הסיסמה לא נכונים');
    startSession(res, u.id);
    return { ok: true };
  }));

  app.post('/api/logout', wrap((req, res) => {
    if (req.token) q('DELETE FROM sessions WHERE token = ?').run(req.token);
    res.clearCookie('sid', { path: '/' });
    return { ok: true };
  }));

  app.get('/api/me', auth, wrap(req => {
    const u = userById(req.userId);
    return { ...publicProfile(u), email: u.email, birthdate: u.birthdate };
  }));

  app.put('/api/me', auth, wrap(req => {
    const prof = cleanProfile(req.body);
    const cols = Object.keys(prof);
    if (cols.length) q(`UPDATE users SET ${cols.map(c => c + ' = ?').join(', ')} WHERE id = ?`).run(...cols.map(c => prof[c]), req.userId);
    return publicProfile(userById(req.userId));
  }));

  app.delete('/api/me', auth, wrap((req, res) => {
    for (const p of q('SELECT file FROM photos WHERE user_id = ?').all(req.userId)) fs.rmSync(path.join(uploadDir, p.file), { force: true });
    q('DELETE FROM messages WHERE match_id IN (SELECT id FROM matches WHERE a = ? OR b = ?)').run(req.userId, req.userId);
    q('DELETE FROM users WHERE id = ?').run(req.userId);
    res.clearCookie('sid', { path: '/' });
    return { ok: true };
  }));

  // ---------- Photos ----------
  const EXT = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp' };
  const upload = multer({
    storage: multer.diskStorage({
      destination: uploadDir,
      filename: (req, file, cb) => cb(null, crypto.randomBytes(16).toString('hex') + EXT[file.mimetype])
    }),
    limits: { fileSize: MAX_PHOTO_BYTES, files: 1 },
    fileFilter: (req, file, cb) => cb(null, Boolean(EXT[file.mimetype]))
  });
  // Checks the file's first bytes so a renamed non-image is rejected
  function looksLikeImage(file) {
    const fd = fs.openSync(file, 'r'), b = Buffer.alloc(12);
    fs.readSync(fd, b, 0, 12, 0); fs.closeSync(fd);
    return (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff)
      || b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
      || (b.toString('ascii', 0, 4) === 'RIFF' && b.toString('ascii', 8, 12) === 'WEBP');
  }
  app.post('/api/me/photos', auth, (req, res, next) => {
    const count = q('SELECT COUNT(*) AS n FROM photos WHERE user_id = ?').get(req.userId).n;
    if (count >= MAX_PHOTOS) return res.status(400).json({ error: `אפשר להעלות עד ${MAX_PHOTOS} תמונות` });
    upload.single('photo')(req, res, err => {
      if (err) return res.status(400).json({ error: err.code === 'LIMIT_FILE_SIZE' ? 'התמונה גדולה מדי (עד 8MB)' : 'ההעלאה נכשלה' });
      if (!req.file) return res.status(400).json({ error: 'אפשר להעלות רק תמונות JPG, PNG או WEBP' });
      if (!looksLikeImage(req.file.path)) { fs.rmSync(req.file.path, { force: true }); return res.status(400).json({ error: 'הקובץ אינו תמונה תקינה' }); }
      const pos = q('SELECT COALESCE(MAX(position), -1) + 1 AS p FROM photos WHERE user_id = ?').get(req.userId).p;
      q('INSERT INTO photos (user_id, file, position) VALUES (?, ?, ?)').run(req.userId, req.file.filename, pos);
      res.status(201).json({ photos: photosOf(req.userId) });
    });
  });
  app.delete('/api/me/photos/:id', auth, wrap(req => {
    const p = q('SELECT file FROM photos WHERE id = ? AND user_id = ?').get(Number(req.params.id), req.userId);
    if (!p) fail(404, 'התמונה לא נמצאה');
    q('DELETE FROM photos WHERE id = ?').run(Number(req.params.id));
    fs.rmSync(path.join(uploadDir, p.file), { force: true });
    return { photos: photosOf(req.userId) };
  }));
  app.post('/api/me/photos/:id/first', auth, wrap(req => {
    const id = Number(req.params.id);
    if (!q('SELECT 1 FROM photos WHERE id = ? AND user_id = ?').get(id, req.userId)) fail(404, 'התמונה לא נמצאה');
    q('UPDATE photos SET position = position + 1 WHERE user_id = ?').run(req.userId);
    q('UPDATE photos SET position = 0 WHERE id = ?').run(id);
    return { photos: photosOf(req.userId) };
  }));
  // Photos are only visible to signed-in members
  app.get('/uploads/:file', auth, (req, res) => {
    if (!/^[a-f0-9]{32}\.(jpg|png|webp)$/.test(req.params.file)) return res.sendStatus(404);
    res.set('Cache-Control', 'private, max-age=86400');
    res.sendFile(path.join(uploadDir, req.params.file), err => err && !res.headersSent && res.sendStatus(404));
  });

  // ---------- Discover ----------
  // People I've already acted on, matched with, reported or been reported by are hidden
  const hiddenFor = me => new Set([
    ...q('SELECT to_id AS id FROM skips WHERE from_id = ?').all(me),
    ...q('SELECT to_id AS id FROM likes WHERE from_id = ?').all(me),
    ...q('SELECT CASE WHEN a = ? THEN b ELSE a END AS id FROM matches WHERE a = ? OR b = ?').all(me, me, me),
    ...q('SELECT to_id AS id FROM reports WHERE from_id = ?').all(me),
    ...q('SELECT from_id AS id FROM reports WHERE to_id = ?').all(me)
  ].map(r => r.id));
  const kidsMatch = (u, f) => !f || (f === 'none' && u.kids === 'אין לי ילדים') || (f === 'has' && u.kids === 'יש לי ילדים')
    || (f === 'wants' && ['רוצה', 'פתוח/ה לזה'].includes(u.wantKids));

  app.get('/api/discover', auth, wrap(req => {
    const me = req.userId, hidden = hiddenFor(me);
    const { city = '', relig = '', kids = '' } = req.query;
    const ageMax = Number(req.query.ageMax) || 99, ageMin = Number(req.query.ageMin) || 18;
    const pool = q('SELECT * FROM users WHERE id != ? AND EXISTS (SELECT 1 FROM photos WHERE user_id = users.id) ORDER BY created_at DESC LIMIT 2000').all(me)
      .filter(u => !hidden.has(u.id))
      .filter(u => { const a = ageOf(u.birthdate); return a >= ageMin && a <= ageMax; })
      .filter(u => (!relig || u.relig === relig) && kidsMatch(u, kids));
    const cityCounts = {};
    for (const u of pool) cityCounts[u.city] = (cityCounts[u.city] || 0) + 1;
    const results = pool.filter(u => !city || normCity(u.city).includes(normCity(city)));
    return { total: results.length, cityCounts, profiles: results.slice(0, 20).map(publicProfile) };
  }));

  app.post('/api/skip', auth, wrap(req => {
    const to = Number(req.body.toId);
    if (!userById(to) || to === req.userId) fail(404, 'המשתמש לא נמצא');
    q('INSERT OR IGNORE INTO skips (from_id, to_id) VALUES (?, ?)').run(req.userId, to);
    return { ok: true };
  }));

  // ---------- Likes and matches ----------
  app.post('/api/like', auth, wrap(req => {
    const me = req.userId, to = Number(req.body.toId);
    const target = userById(to);
    if (!target || to === me) fail(404, 'המשתמש לא נמצא');
    if (hiddenFor(me).has(to) && !q('SELECT 1 FROM likes WHERE from_id = ? AND to_id = ? AND removed = 0').get(to, me)) fail(409, 'כבר הגבת לפרופיל הזה');
    const kind = ['photo', 'prompt', 'profile'].includes(req.body.kind) ? req.body.kind : 'profile';
    const idx = Math.max(0, Math.min(5, Number(req.body.idx) || 0));
    const note = String(req.body.note || '').trim().slice(0, 140);
    const rose = req.body.rose ? 1 : 0;
    q('INSERT OR REPLACE INTO likes (from_id, to_id, kind, idx, note, rose, removed, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)').run(me, to, kind, idx, note, rose, now());
    const theirs = q('SELECT * FROM likes WHERE from_id = ? AND to_id = ? AND removed = 0').get(to, me);
    if (!theirs) return { match: false };
    const [a, b] = pair(me, to);
    q('INSERT OR IGNORE INTO matches (a, b, created_at) VALUES (?, ?, ?)').run(a, b, now());
    const m = q('SELECT id FROM matches WHERE a = ? AND b = ?').get(a, b);
    // The comments sent with each like open the conversation
    if (theirs.note) q('INSERT INTO messages (match_id, from_id, body, created_at) VALUES (?, ?, ?, ?)').run(m.id, to, theirs.note, theirs.created_at);
    if (note) q('INSERT INTO messages (match_id, from_id, body, created_at) VALUES (?, ?, ?, ?)').run(m.id, me, note, now());
    return { match: true, matchId: m.id, profile: publicProfile(target) };
  }));

  app.get('/api/likes', auth, wrap(req => {
    const me = req.userId, hidden = hiddenFor(me);
    const rows = q('SELECT * FROM likes WHERE to_id = ? AND removed = 0 ORDER BY rose DESC, created_at DESC').all(me).filter(l => !hidden.has(l.from_id));
    const meU = userById(me), myPhotos = photosOf(me), myPrompts = JSON.parse(meU.prompts);
    return rows.map(l => ({
      from: publicProfile(userById(l.from_id)), kind: l.kind, idx: l.idx, note: l.note, rose: !!l.rose,
      about: l.kind === 'photo' ? { photo: myPhotos[l.idx] || null } : l.kind === 'prompt' ? { prompt: myPrompts[l.idx] || null } : {}
    }));
  }));

  app.post('/api/likes/:fromId/remove', auth, wrap(req => {
    const from = Number(req.params.fromId);
    q('UPDATE likes SET removed = 1 WHERE from_id = ? AND to_id = ?').run(from, req.userId);
    q('INSERT OR IGNORE INTO skips (from_id, to_id) VALUES (?, ?)').run(req.userId, from);
    return { ok: true };
  }));

  const matchFor = (id, me) => {
    const m = q('SELECT * FROM matches WHERE id = ? AND (a = ? OR b = ?) AND ended = 0').get(id, me, me);
    if (!m) fail(404, 'השיחה לא נמצאה');
    return m;
  };
  app.get('/api/matches', auth, wrap(req => {
    const me = req.userId;
    return q('SELECT * FROM matches WHERE (a = ? OR b = ?) AND ended = 0 ORDER BY created_at DESC').all(me, me).map(m => {
      const last = q('SELECT from_id, body, created_at FROM messages WHERE match_id = ? ORDER BY id DESC LIMIT 1').get(m.id);
      return { id: m.id, other: publicProfile(userById(m.a === me ? m.b : m.a)), last: last ? { mine: last.from_id === me, body: last.body, at: last.created_at } : null, at: last ? last.created_at : m.created_at };
    }).sort((x, y) => y.at - x.at);
  }));
  app.get('/api/matches/:id/messages', auth, wrap(req => {
    const m = matchFor(Number(req.params.id), req.userId);
    const after = Number(req.query.after) || 0;
    return q('SELECT id, from_id, body, created_at FROM messages WHERE match_id = ? AND id > ? ORDER BY id LIMIT 500').all(m.id, after)
      .map(x => ({ id: x.id, mine: x.from_id === req.userId, body: x.body, at: x.created_at }));
  }));
  app.post('/api/matches/:id/messages', auth, wrap((req, res) => {
    const m = matchFor(Number(req.params.id), req.userId);
    const body = String(req.body.body || '').trim();
    if (!body) fail(400, 'ההודעה ריקה');
    if (body.length > 1000) fail(400, 'ההודעה ארוכה מדי');
    limit('msg:' + req.userId, 60, 60 * 1000);
    const r = q('INSERT INTO messages (match_id, from_id, body, created_at) VALUES (?, ?, ?, ?)').run(m.id, req.userId, body, now());
    res.status(201);
    return { id: Number(r.lastInsertRowid), mine: true, body, at: now() };
  }));
  app.post('/api/matches/:id/unmatch', auth, wrap(req => {
    const m = matchFor(Number(req.params.id), req.userId);
    q('UPDATE matches SET ended = 1 WHERE id = ?').run(m.id);
    return { ok: true };
  }));

  app.post('/api/report', auth, wrap(req => {
    const to = Number(req.body.toId);
    if (!userById(to) || to === req.userId) fail(404, 'המשתמש לא נמצא');
    q('INSERT INTO reports (from_id, to_id, reason, created_at) VALUES (?, ?, ?, ?)').run(req.userId, to, String(req.body.reason || '').slice(0, 500), now());
    const [a, b] = pair(req.userId, to);
    q('UPDATE matches SET ended = 1 WHERE a = ? AND b = ?').run(a, b);
    return { ok: true };
  }));

  // ---------- Page and errors ----------
  app.use(express.static(path.join(__dirname, 'public'), { index: 'index.html' }));
  app.use('/api', (req, res) => res.status(404).json({ error: 'לא נמצא' }));
  app.use((err, req, res, next) => {
    if (err instanceof HttpError) return res.status(err.status).json({ error: err.message });
    if (err.type === 'entity.parse.failed') return res.status(400).json({ error: 'בקשה לא תקינה' });
    console.error(err);
    res.status(500).json({ error: 'שגיאת שרת. נסו שוב' });
  });

  app.locals.db = db;
  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 3000;
  createApp().listen(port, () => console.log(`Kesher is running on http://localhost:${port}`));
}

module.exports = { createApp };
