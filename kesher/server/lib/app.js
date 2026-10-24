// Kesher API: accounts, profiles, photo uploads, likes, matches and chat.
// Framework-free: takes a web Request and returns a web Response, so the same code runs
// as a Netlify Function and behind the local Node server. Data lives in Postgres; photos
// go to a pluggable store (Netlify Blobs in production, a folder on disk locally).

import crypto from 'node:crypto';

export const MAX_PHOTOS = 6;
const MAX_PHOTO_BYTES = 4 * 1024 * 1024; // Netlify Functions accept up to ~4.5MB of binary per request
const SESSION_MS = 30 * 864e5;

// Profile fields a user can fill in, with the allowed values for each choice field
export const CHOICES = {
  gender: ['אישה', 'גבר', 'א-בינארי'],
  zodiac: ['טלה', 'שור', 'תאומים', 'סרטן', 'אריה', 'בתולה', 'מאזניים', 'עקרב', 'קשת', 'גדי', 'דלי', 'דגים'],
  status: ['רווק/ה', 'גרוש/ה', 'פרוד/ה', 'אלמן/ה', 'בתהליך גירושין'],
  relig: ['חילוני/ת', 'מסורתי/ת', 'מסורתי/ת-דתי/ה', 'דתי/ה לאומי/ת', 'דתי/ה', 'חרדי/ת', 'חוזר/ת בתשובה', 'רוחני/ת', 'אחר'],
  intent: ['שותף/ה לחיים', 'קשר ארוך טווח', 'קשר ארוך, פתוח/ה גם לקצר', 'קשר קצר, פתוח/ה גם לארוך', 'משהו קליל', 'עוד מגלה מה אני רוצה'],
  kids: ['אין לי ילדים', 'יש לי ילדים'],
  wantKids: ['רוצה', 'לא רוצה', 'פתוח/ה לזה', 'עוד לא יודע/ת'],
  pets: ['כלב', 'חתול', 'כלב וחתול', 'אין', 'רוצה בעתיד'],
  drink: ['לא שותה', 'לפעמים', 'כן'],
  smoke: ['לא מעשן/ת', 'לפעמים', 'כן'],
  sport: ['לא', 'לפעמים', 'באופן קבוע'],
  kosher: ['לא שומר/ת', 'שומר/ת בבית', 'שומר/ת']
};
const TEXT_FIELDS = { name: 40, city: 40, job: 60, edu: 80, hometown: 40, langs: 80 };
export const PROMPT_QUESTIONS = [
  'הדרך לכבוש אותי', 'יום ראשון מושלם', 'אני גאה ב...', 'עובדה מוזרה עליי', 'אנחנו נסתדר אם...',
  'הדייט הראשון האידיאלי', 'הדבר הכי ספונטני שעשיתי', 'אני מחפש/ת', 'הנושא שאני יכול/ה לדבר עליו שעות',
  'דגל ירוק', 'השיר שתמיד יגרום לי לרקוד', 'המנה שאבשל לך', 'הקטע שלי'
];
// API field name -> Postgres column (unquoted identifiers are lower-case in Postgres)
const COL = { wantKids: 'want_kids' };
const col = k => COL[k] || k;
const PROFILE_KEYS = [...Object.keys(CHOICES), 'job', 'edu', 'hometown', 'langs'];

class HttpError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
const fail = (status, msg) => { throw new HttpError(status, msg); };

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
const now = () => Date.now();

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

const IMAGE_TYPES = { jpg: 'image/jpeg', png: 'image/png', webp: 'image/webp' };
// Recognises an image by its first bytes, so a renamed non-image is rejected
function imageExt(buf) {
  const b = new Uint8Array(buf.slice(0, 12));
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'jpg';
  if ([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((x, i) => b[i] === x)) return 'png';
  const s = String.fromCharCode(...b);
  if (s.slice(0, 4) === 'RIFF' && s.slice(8, 12) === 'WEBP') return 'webp';
  return null;
}

/**
 * @param {{ pool: { query: Function }, photos: { put: Function, get: Function, remove: Function } }} deps
 * @returns {(req: Request, info?: { ip?: string }) => Promise<Response>}
 */
export function createHandler({ pool, photos }) {
  const q = async (sql, params = []) => (await pool.query(sql, params)).rows;
  const one = async (sql, params) => (await q(sql, params))[0];

  const photosOf = async id => (await q('SELECT id, file FROM photos WHERE user_id = $1 ORDER BY position, id', [id]))
    .map(p => ({ id: p.id, url: '/uploads/' + p.file }));
  async function publicProfile(u) {
    const out = { id: u.id, name: u.name, age: ageOf(u.birthdate), city: u.city, height: u.height || null, photos: await photosOf(u.id), prompts: u.prompts || [] };
    for (const k of PROFILE_KEYS) out[k] = u[col(k)] || '';
    return out;
  }
  const userById = id => one('SELECT * FROM users WHERE id = $1', [id]);

  // Login, registration and message limits, counted in the database so they hold across function instances
  async function limit(key, max, windowMs) {
    const since = now() - windowMs;
    const { n } = await one('SELECT COUNT(*)::int AS n FROM attempts WHERE key = $1 AND at > $2', [key, since]);
    if (n >= max) fail(429, 'יותר מדי ניסיונות. נסו שוב בעוד כמה דקות');
    await q('INSERT INTO attempts (key, at) VALUES ($1, $2)', [key, now()]);
    if (Math.random() < 0.02) await q('DELETE FROM attempts WHERE at < $1', [now() - 864e5]);
  }

  // People I've already acted on, matched with, reported or been reported by are hidden
  async function hiddenFor(me) {
    const rows = await q(`
      SELECT to_id AS id FROM skips WHERE from_id = $1
      UNION SELECT to_id FROM likes WHERE from_id = $1
      UNION SELECT CASE WHEN a = $1 THEN b ELSE a END FROM matches WHERE a = $1 OR b = $1
      UNION SELECT to_id FROM reports WHERE from_id = $1
      UNION SELECT from_id FROM reports WHERE to_id = $1`, [me]);
    return new Set(rows.map(r => r.id));
  }
  const kidsMatch = (u, f) => !f || (f === 'none' && u.kids === 'אין לי ילדים') || (f === 'has' && u.kids === 'יש לי ילדים')
    || (f === 'wants' && ['רוצה', 'פתוח/ה לזה'].includes(u.want_kids));
  async function matchFor(id, me) {
    const m = await one('SELECT * FROM matches WHERE id = $1 AND (a = $2 OR b = $2) AND NOT ended', [id, me]);
    if (!m) fail(404, 'השיחה לא נמצאה');
    return m;
  }

  // ---------- Routes: [method, pattern, needsLogin, handler] ----------
  const routes = [
    ['GET', /^\/api\/options$/, false, () => ({ choices: CHOICES, prompts: PROMPT_QUESTIONS, maxPhotos: MAX_PHOTOS })],

    ['POST', /^\/api\/register$/, false, async c => {
      const b = await c.json();
      await limit('reg:' + c.ip, 10, 60 * 60 * 1000);
      const email = String(b.email || '').trim().toLowerCase();
      const password = String(b.password || '');
      const birthdate = String(b.birthdate || '');
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 120) fail(400, 'כתובת האימייל לא תקינה');
      if (password.length < 8) fail(400, 'הסיסמה צריכה להכיל לפחות 8 תווים');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(birthdate) || isNaN(new Date(birthdate))) fail(400, 'תאריך הלידה לא תקין');
      const age = ageOf(birthdate);
      if (age < 18) fail(400, 'ההרשמה מגיל 18 ומעלה');
      if (age > 100) fail(400, 'תאריך הלידה לא תקין');
      const prof = cleanProfile(b, { requireCore: true });
      if (await one('SELECT 1 FROM users WHERE email = $1', [email])) fail(409, 'כבר קיים חשבון עם האימייל הזה');
      const cols = Object.keys(prof);
      const u = await one(`INSERT INTO users (email, pass, birthdate, created_at, ${cols.map(col).join(', ')})
        VALUES ($1, $2, $3, $4, ${cols.map((_, i) => '$' + (i + 5)).join(', ')}) RETURNING id`,
        [email, hashPassword(password), birthdate, now(), ...cols.map(k => prof[k])]);
      await c.startSession(u.id);
      c.status = 201;
      return { ok: true };
    }],

    ['POST', /^\/api\/login$/, false, async c => {
      const b = await c.json();
      const email = String(b.email || '').trim().toLowerCase();
      await limit('login:' + c.ip + ':' + email, 10, 15 * 60 * 1000);
      const u = await one('SELECT id, pass FROM users WHERE email = $1', [email]);
      if (!u || !checkPassword(String(b.password || ''), u.pass)) fail(401, 'האימייל או הסיסמה לא נכונים');
      await c.startSession(u.id);
      return { ok: true };
    }],

    ['POST', /^\/api\/logout$/, false, async c => {
      if (c.token) await q('DELETE FROM sessions WHERE token = $1', [c.token]);
      c.clearSession();
      return { ok: true };
    }],

    ['GET', /^\/api\/me$/, true, async c => {
      const u = await userById(c.userId);
      return { ...(await publicProfile(u)), email: u.email, birthdate: u.birthdate };
    }],

    ['PUT', /^\/api\/me$/, true, async c => {
      const prof = cleanProfile(await c.json());
      const cols = Object.keys(prof);
      if (cols.length) await q(`UPDATE users SET ${cols.map((k, i) => `${col(k)} = $${i + 1}`).join(', ')} WHERE id = $${cols.length + 1}`, [...cols.map(k => prof[k]), c.userId]);
      return publicProfile(await userById(c.userId));
    }],

    ['DELETE', /^\/api\/me$/, true, async c => {
      for (const p of await q('SELECT file FROM photos WHERE user_id = $1', [c.userId])) await photos.remove(p.file);
      await q('DELETE FROM users WHERE id = $1', [c.userId]); // related rows go with ON DELETE CASCADE
      c.clearSession();
      return { ok: true };
    }],

    // ---------- Photos ----------
    ['POST', /^\/api\/me\/photos$/, true, async c => {
      const { n } = await one('SELECT COUNT(*)::int AS n FROM photos WHERE user_id = $1', [c.userId]);
      if (n >= MAX_PHOTOS) fail(400, `אפשר להעלות עד ${MAX_PHOTOS} תמונות`);
      if (Number(c.req.headers.get('content-length') || 0) > MAX_PHOTO_BYTES + 64 * 1024) fail(413, 'התמונה גדולה מדי (עד 4MB)');
      let file;
      try { file = (await c.req.formData()).get('photo'); } catch (e) { fail(400, 'ההעלאה נכשלה'); }
      if (!file || typeof file === 'string') fail(400, 'לא נבחרה תמונה');
      if (file.size > MAX_PHOTO_BYTES) fail(413, 'התמונה גדולה מדי (עד 4MB)');
      const buf = await file.arrayBuffer();
      const ext = imageExt(buf);
      if (!ext) fail(400, 'אפשר להעלות רק תמונות JPG, PNG או WEBP');
      const name = crypto.randomBytes(16).toString('hex') + '.' + ext;
      await photos.put(name, buf, IMAGE_TYPES[ext]);
      await q('INSERT INTO photos (user_id, file, position) VALUES ($1, $2, (SELECT COALESCE(MAX(position), -1) + 1 FROM photos WHERE user_id = $1))', [c.userId, name]);
      c.status = 201;
      return { photos: await photosOf(c.userId) };
    }],
    ['DELETE', /^\/api\/me\/photos\/(\d+)$/, true, async (c, m) => {
      const p = await one('DELETE FROM photos WHERE id = $1 AND user_id = $2 RETURNING file', [Number(m[1]), c.userId]);
      if (!p) fail(404, 'התמונה לא נמצאה');
      await photos.remove(p.file);
      return { photos: await photosOf(c.userId) };
    }],
    ['POST', /^\/api\/me\/photos\/(\d+)\/first$/, true, async (c, m) => {
      const id = Number(m[1]);
      if (!await one('SELECT 1 FROM photos WHERE id = $1 AND user_id = $2', [id, c.userId])) fail(404, 'התמונה לא נמצאה');
      await q('UPDATE photos SET position = CASE WHEN id = $1 THEN 0 ELSE position + 1 END WHERE user_id = $2', [id, c.userId]);
      return { photos: await photosOf(c.userId) };
    }],
    // Photos are only visible to signed-in members
    ['GET', /^\/uploads\/([a-f0-9]{32}\.(jpg|png|webp))$/, true, async (c, m) => {
      const data = await photos.get(m[1]);
      if (!data) fail(404, 'לא נמצא');
      return new Response(data, { headers: { 'content-type': IMAGE_TYPES[m[2]], 'cache-control': 'private, max-age=86400' } });
    }],

    // ---------- Discover ----------
    ['GET', /^\/api\/discover$/, true, async c => {
      const me = c.userId, hidden = await hiddenFor(me), sp = c.url.searchParams;
      const city = sp.get('city') || '', relig = sp.get('relig') || '', kids = sp.get('kids') || '', status = sp.get('status') || '';
      const ageMax = Number(sp.get('ageMax')) || 99, ageMin = Number(sp.get('ageMin')) || 18;
      const pool = (await q('SELECT * FROM users u WHERE id != $1 AND EXISTS (SELECT 1 FROM photos WHERE user_id = u.id) ORDER BY created_at DESC LIMIT 2000', [me]))
        .filter(u => !hidden.has(u.id))
        .filter(u => { const a = ageOf(u.birthdate); return a >= ageMin && a <= ageMax; })
        .filter(u => (!relig || u.relig === relig) && (!status || u.status === status) && kidsMatch(u, kids));
      const cityCounts = {};
      for (const u of pool) cityCounts[u.city] = (cityCounts[u.city] || 0) + 1;
      const results = pool.filter(u => !city || normCity(u.city).includes(normCity(city)));
      return { total: results.length, cityCounts, profiles: await Promise.all(results.slice(0, 20).map(publicProfile)) };
    }],

    ['POST', /^\/api\/skip$/, true, async c => {
      const to = Number((await c.json()).toId);
      if (to === c.userId || !await userById(to)) fail(404, 'המשתמש לא נמצא');
      await q('INSERT INTO skips (from_id, to_id) VALUES ($1, $2) ON CONFLICT DO NOTHING', [c.userId, to]);
      return { ok: true };
    }],

    // ---------- Likes and matches ----------
    ['POST', /^\/api\/like$/, true, async c => {
      const b = await c.json(), me = c.userId, to = Number(b.toId);
      const target = to !== me && await userById(to);
      if (!target) fail(404, 'המשתמש לא נמצא');
      const theirsBefore = await one('SELECT 1 FROM likes WHERE from_id = $1 AND to_id = $2 AND NOT removed', [to, me]);
      if ((await hiddenFor(me)).has(to) && !theirsBefore) fail(409, 'כבר הגבת לפרופיל הזה');
      const kind = ['photo', 'prompt', 'profile'].includes(b.kind) ? b.kind : 'profile';
      const idx = Math.max(0, Math.min(5, Number(b.idx) || 0));
      const note = String(b.note || '').trim().slice(0, 140);
      await q(`INSERT INTO likes (from_id, to_id, kind, idx, note, rose, removed, created_at) VALUES ($1, $2, $3, $4, $5, $6, false, $7)
        ON CONFLICT (from_id, to_id) DO UPDATE SET kind = $3, idx = $4, note = $5, rose = $6, removed = false, created_at = $7`, [me, to, kind, idx, note, !!b.rose, now()]);
      const theirs = await one('SELECT * FROM likes WHERE from_id = $1 AND to_id = $2 AND NOT removed', [to, me]);
      if (!theirs) return { match: false };
      const [a, bb] = pair(me, to);
      const created = await one('INSERT INTO matches (a, b, created_at) VALUES ($1, $2, $3) ON CONFLICT (a, b) DO NOTHING RETURNING id', [a, bb, now()]);
      const m = created || await one('SELECT id FROM matches WHERE a = $1 AND b = $2', [a, bb]);
      // The comments sent with each like open the conversation
      if (created) {
        if (theirs.note) await q('INSERT INTO messages (match_id, from_id, body, created_at) VALUES ($1, $2, $3, $4)', [m.id, to, theirs.note, Number(theirs.created_at)]);
        if (note) await q('INSERT INTO messages (match_id, from_id, body, created_at) VALUES ($1, $2, $3, $4)', [m.id, me, note, now()]);
      }
      return { match: true, matchId: m.id, profile: await publicProfile(target) };
    }],

    ['GET', /^\/api\/likes$/, true, async c => {
      const me = c.userId, hidden = await hiddenFor(me);
      const rows = (await q('SELECT * FROM likes WHERE to_id = $1 AND NOT removed ORDER BY rose DESC, created_at DESC', [me])).filter(l => !hidden.has(l.from_id));
      const meU = await userById(me), myPhotos = await photosOf(me), myPrompts = meU.prompts || [];
      return Promise.all(rows.map(async l => ({
        from: await publicProfile(await userById(l.from_id)), kind: l.kind, idx: l.idx, note: l.note, rose: l.rose,
        about: l.kind === 'photo' ? { photo: myPhotos[l.idx] || null } : l.kind === 'prompt' ? { prompt: myPrompts[l.idx] || null } : {}
      })));
    }],

    ['POST', /^\/api\/likes\/(\d+)\/remove$/, true, async (c, m) => {
      const from = Number(m[1]);
      await q('UPDATE likes SET removed = true WHERE from_id = $1 AND to_id = $2', [from, c.userId]);
      if (await userById(from)) await q('INSERT INTO skips (from_id, to_id) VALUES ($1, $2) ON CONFLICT DO NOTHING', [c.userId, from]);
      return { ok: true };
    }],

    ['GET', /^\/api\/matches$/, true, async c => {
      const me = c.userId;
      const list = await Promise.all((await q('SELECT * FROM matches WHERE (a = $1 OR b = $1) AND NOT ended', [me])).map(async m => {
        const last = await one('SELECT from_id, body, created_at FROM messages WHERE match_id = $1 ORDER BY id DESC LIMIT 1', [m.id]);
        const at = Number(last ? last.created_at : m.created_at);
        return { id: m.id, other: await publicProfile(await userById(m.a === me ? m.b : m.a)), last: last ? { mine: last.from_id === me, body: last.body, at } : null, at };
      }));
      return list.sort((x, y) => y.at - x.at);
    }],
    ['GET', /^\/api\/matches\/(\d+)\/messages$/, true, async (c, m) => {
      const mt = await matchFor(Number(m[1]), c.userId);
      const after = Number(c.url.searchParams.get('after')) || 0;
      return (await q('SELECT id, from_id, body, created_at FROM messages WHERE match_id = $1 AND id > $2 ORDER BY id LIMIT 500', [mt.id, after]))
        .map(x => ({ id: x.id, mine: x.from_id === c.userId, body: x.body, at: Number(x.created_at) }));
    }],
    ['POST', /^\/api\/matches\/(\d+)\/messages$/, true, async (c, m) => {
      const mt = await matchFor(Number(m[1]), c.userId);
      const body = String((await c.json()).body || '').trim();
      if (!body) fail(400, 'ההודעה ריקה');
      if (body.length > 1000) fail(400, 'ההודעה ארוכה מדי');
      await limit('msg:' + c.userId, 60, 60 * 1000);
      const r = await one('INSERT INTO messages (match_id, from_id, body, created_at) VALUES ($1, $2, $3, $4) RETURNING id', [mt.id, c.userId, body, now()]);
      c.status = 201;
      return { id: r.id, mine: true, body, at: now() };
    }],
    ['POST', /^\/api\/matches\/(\d+)\/unmatch$/, true, async (c, m) => {
      const mt = await matchFor(Number(m[1]), c.userId);
      await q('UPDATE matches SET ended = true WHERE id = $1', [mt.id]);
      return { ok: true };
    }],

    ['POST', /^\/api\/report$/, true, async c => {
      const b = await c.json(), to = Number(b.toId);
      if (to === c.userId || !await userById(to)) fail(404, 'המשתמש לא נמצא');
      await q('INSERT INTO reports (from_id, to_id, reason, created_at) VALUES ($1, $2, $3, $4)', [c.userId, to, String(b.reason || '').slice(0, 500), now()]);
      const [a, bb] = pair(c.userId, to);
      await q('UPDATE matches SET ended = true WHERE a = $1 AND b = $2', [a, bb]);
      return { ok: true };
    }]
  ];

  const SECURITY_HEADERS = { 'x-content-type-options': 'nosniff', 'referrer-policy': 'same-origin', 'x-frame-options': 'DENY' };

  return async function handle(req, info = {}) {
    const url = new URL(req.url);
    const secure = url.protocol === 'https:';
    const cookies = [];
    const c = {
      req, url, ip: info.ip || 'unknown', status: 200, userId: null, token: null,
      async json() { try { return await req.json(); } catch (e) { fail(400, 'בקשה לא תקינה'); } },
      async startSession(userId) {
        const token = crypto.randomBytes(32).toString('hex');
        await q('INSERT INTO sessions (token, user_id, created_at) VALUES ($1, $2, $3)', [token, userId, now()]);
        cookies.push(`sid=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${SESSION_MS / 1000}${secure ? '; Secure' : ''}`);
      },
      clearSession() { cookies.push(`sid=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0${secure ? '; Secure' : ''}`); }
    };
    const respond = (status, body) => {
      const res = body instanceof Response ? body : Response.json(body, { status });
      for (const [k, v] of Object.entries(SECURITY_HEADERS)) res.headers.set(k, v);
      for (const ck of cookies) res.headers.append('set-cookie', ck);
      return res;
    };
    try {
      // State-changing API calls must come from our own page (blocks cross-site form posts)
      if (req.method !== 'GET' && req.method !== 'HEAD' && req.headers.get('x-kesher') !== '1') fail(403, 'בקשה לא מורשית');
      const sid = /(?:^|;\s*)sid=([a-f0-9]{64})/.exec(req.headers.get('cookie') || '');
      if (sid) {
        const s = await one('SELECT user_id, created_at FROM sessions WHERE token = $1', [sid[1]]);
        if (s && now() - Number(s.created_at) < SESSION_MS) { c.userId = s.user_id; c.token = sid[1]; }
      }
      for (const [method, re, needsLogin, fn] of routes) {
        const m = re.exec(url.pathname);
        if (!m || method !== req.method) continue;
        if (needsLogin && !c.userId) fail(401, 'צריך להתחבר');
        const result = await fn(c, m);
        return respond(c.status, result);
      }
      fail(404, 'לא נמצא');
    } catch (e) {
      if (e instanceof HttpError) return respond(e.status, { error: e.message });
      console.error(e);
      return respond(500, { error: 'שגיאת שרת. נסו שוב' });
    }
  };
}
