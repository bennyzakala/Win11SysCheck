// In-browser stand-in for the Kesher server, so the demo runs as a single page.
// It answers the same /api routes as server.js, keeps its data in localStorage,
// and fills the app with the drawn sample profiles.
(() => {
  'use strict';
  const KEY = 'kesher-demo-v2';
  const CHOICES = {
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
  const PROMPTS = ['הדרך לכבוש אותי', 'יום ראשון מושלם', 'אני גאה ב...', 'עובדה מוזרה עליי', 'אנחנו נסתדר אם...',
    'הדייט הראשון האידיאלי', 'הדבר הכי ספונטני שעשיתי', 'אני מחפש/ת', 'הנושא שאני יכול/ה לדבר עליו שעות',
    'דגל ירוק', 'השיר שתמיד יגרום לי לרקוד', 'המנה שאבשל לך', 'הקטע שלי'];
  const REPLIES = ['היי! איזה כיף שכתבת', 'חחח בדיוק חשבתי על זה', 'נשמע מעולה, מתי נוח לך?', 'אז מה עוד את/ה אוהב/ת לעשות בעיר?', 'מסכים/ה לגמרי!', 'ספר/י עוד :)'];
  const FIELDS = ['name', 'city', 'job', 'edu', 'hometown', 'langs', ...Object.keys(CHOICES)];

  // The demo account starts signed in, with a drawn photo, so the app opens ready to browse
  function fresh() {
    const me = { id: 1000, email: 'demo@kesher.app', password: 'demo1234', name: 'אורח/ת', birthdate: '1995-06-01', city: 'תל אביב', gender: 'א-בינארי',
      status: 'רווק/ה', prompts: [{ q: 'יום ראשון מושלם', a: 'קפה על הים, ספר טוב וארוחה עם חברים' }], photos: [], height: null };
    // A neutral drawn portrait for the guest account; uploading a real photo replaces it
    const guest = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500"><rect width="400" height="500" fill="#EFE9FD"/><circle cx="330" cy="90" r="70" fill="#E3D8FB"/>'
      + '<circle cx="200" cy="200" r="78" fill="#6A3FD6"/><path d="M60 500c0-110 64-170 140-170s140 60 140 170z" fill="#6A3FD6"/></svg>';
    me.photos = [{ id: 1, url: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(guest) }];
    return {
      session: 1000, users: [me], nextId: 2000, nextPhoto: 10, nextMsg: 1,
      likes: SAMPLE.filter(p => p.likesMe).map(p => ({ from: p.id, to: 1000, kind: p.likeNote ? 'prompt' : 'profile', idx: 0, note: p.likeNote, rose: p.likeRose, removed: false })),
      skips: [], matches: [], messages: [], reports: []
    };
  }
  let DB;
  try { DB = JSON.parse(localStorage.getItem(KEY)) || fresh(); } catch (e) { DB = fresh(); }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(DB)); } catch (e) {} };

  const people = () => [...SAMPLE, ...DB.users];
  const byId = id => people().find(u => u.id === id);
  const ageOf = b => { const d = new Date(b + 'T00:00:00'), n = new Date(); let a = n.getFullYear() - d.getFullYear(); if (n < new Date(n.getFullYear(), d.getMonth(), d.getDate())) a--; return a; };
  const photosOf = u => (u.photos || []).map((p, i) => typeof p === 'string' ? { id: u.id * 10 + i, url: p } : p);
  function pub(u) {
    const out = { id: u.id, name: u.name, age: ageOf(u.birthdate), city: u.city, height: u.height || null, photos: photosOf(u), prompts: u.prompts || [] };
    for (const k of [...Object.keys(CHOICES), 'job', 'edu', 'hometown', 'langs']) out[k] = u[k] || '';
    return out;
  }
  const norm = s => String(s || '').replace(/[\s\-־"׳']/g, '').toLowerCase();
  const pairKey = (a, b) => a < b ? [a, b] : [b, a];
  class E extends Error { constructor(status, msg) { super(msg); this.status = status; } }
  const fail = (s, m) => { throw new E(s, m); };
  const me = () => { const u = DB.users.find(x => x.id === DB.session); if (!u) fail(401, 'צריך להתחבר'); return u; };

  function hidden(id) {
    return new Set([
      ...DB.skips.filter(s => s.from === id).map(s => s.to),
      ...DB.likes.filter(l => l.from === id).map(l => l.to),
      ...DB.matches.filter(m => m.a === id || m.b === id).map(m => m.a === id ? m.b : m.a),
      ...DB.reports.filter(r => r.from === id).map(r => r.to),
      ...DB.reports.filter(r => r.to === id).map(r => r.from)
    ]);
  }
  const kidsMatch = (u, f) => !f || (f === 'none' && u.kids === 'אין לי ילדים') || (f === 'has' && u.kids === 'יש לי ילדים') || (f === 'wants' && ['רוצה', 'פתוח/ה לזה'].includes(u.wantKids));

  function cleanProfile(b) {
    const out = {};
    for (const k of FIELDS) if (b[k] !== undefined) {
      const v = String(b[k]).trim();
      if (CHOICES[k] && v && !CHOICES[k].includes(v)) fail(400, 'ערך לא תקין');
      out[k] = v;
    }
    if (out.name === '' || out.city === '') fail(400, 'שם ועיר הם שדות חובה');
    if (b.height !== undefined) {
      if (b.height === null || b.height === '') out.height = null;
      else { const h = Number(b.height); if (!Number.isInteger(h) || h < 120 || h > 230) fail(400, 'הגובה צריך להיות בין 120 ל-230 ס״מ'); out.height = h; }
    }
    if (b.prompts !== undefined) out.prompts = b.prompts.slice(0, 3).map(p => ({ q: String(p.q), a: String(p.a).trim().slice(0, 200) }));
    return out;
  }

  // Downscale an uploaded photo so it fits in browser storage
  const shrink = blob => new Promise(res => {
    const url = URL.createObjectURL(blob), img = new Image();
    img.onload = () => {
      const k = Math.min(1, 720 / Math.max(img.width, img.height));
      const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
      res(c.toDataURL('image/jpeg', .8));
    };
    img.onerror = () => { URL.revokeObjectURL(url); res(null); };
    img.src = url;
  });

  const routes = [
    ['GET', /^\/api\/options$/, () => ({ choices: CHOICES, prompts: PROMPTS, maxPhotos: 6 })],
    ['POST', /^\/api\/register$/, (m, b) => {
      const email = String(b.email || '').trim().toLowerCase();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) fail(400, 'כתובת האימייל לא תקינה');
      if (String(b.password || '').length < 8) fail(400, 'הסיסמה צריכה להכיל לפחות 8 תווים');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(b.birthdate || '')) fail(400, 'תאריך הלידה לא תקין');
      if (ageOf(b.birthdate) < 18) fail(400, 'ההרשמה מגיל 18 ומעלה');
      if (DB.users.some(u => u.email === email)) fail(409, 'כבר קיים חשבון עם האימייל הזה');
      const prof = cleanProfile(b);
      if (!prof.name || !prof.city) fail(400, 'צריך למלא שם ועיר');
      const u = { id: DB.nextId++, email, password: b.password, birthdate: b.birthdate, prompts: [], photos: [], ...prof };
      DB.users.push(u); DB.session = u.id; save(); return { ok: true };
    }],
    ['POST', /^\/api\/login$/, (m, b) => {
      const u = DB.users.find(x => x.email === String(b.email || '').trim().toLowerCase() && x.password === b.password);
      if (!u) fail(401, 'האימייל או הסיסמה לא נכונים');
      DB.session = u.id; save(); return { ok: true };
    }],
    ['POST', /^\/api\/logout$/, () => { DB.session = null; save(); return { ok: true }; }],
    ['GET', /^\/api\/me$/, () => { const u = me(); return { ...pub(u), email: u.email, birthdate: u.birthdate }; }],
    ['PUT', /^\/api\/me$/, (m, b) => { const u = me(); Object.assign(u, cleanProfile(b)); save(); return pub(u); }],
    ['DELETE', /^\/api\/me$/, () => {
      const u = me();
      DB.users = DB.users.filter(x => x !== u);
      DB.likes = DB.likes.filter(l => l.from !== u.id && l.to !== u.id);
      DB.matches = DB.matches.filter(x => x.a !== u.id && x.b !== u.id);
      DB.session = null; save(); return { ok: true };
    }],
    ['POST', /^\/api\/me\/photos$/, async (m, b) => {
      const u = me();
      if (u.photos.length >= 6) fail(400, 'אפשר להעלות עד 6 תמונות');
      const file = b.get('photo'); const url = file && await shrink(file);
      if (!url) fail(400, 'הקובץ אינו תמונה תקינה');
      u.photos.push({ id: DB.nextPhoto++, url }); save(); return { photos: u.photos };
    }],
    ['DELETE', /^\/api\/me\/photos\/(\d+)$/, m => { const u = me(); u.photos = u.photos.filter(p => p.id !== +m[1]); save(); return { photos: u.photos }; }],
    ['POST', /^\/api\/me\/photos\/(\d+)\/first$/, m => {
      const u = me(), p = u.photos.find(x => x.id === +m[1]); if (!p) fail(404, 'התמונה לא נמצאה');
      u.photos = [p, ...u.photos.filter(x => x !== p)]; save(); return { photos: u.photos };
    }],
    ['GET', /^\/api\/discover$/, (m, b, q) => {
      const u = me(), hide = hidden(u.id);
      const ageMin = +q.get('ageMin') || 18, ageMax = +q.get('ageMax') || 99;
      const pool = people().filter(p => p.id !== u.id && photosOf(p).length && !hide.has(p.id))
        .filter(p => { const a = ageOf(p.birthdate); return a >= ageMin && a <= ageMax; })
        .filter(p => (!q.get('relig') || p.relig === q.get('relig')) && (!q.get('status') || p.status === q.get('status')) && kidsMatch(p, q.get('kids')));
      const cityCounts = {}; pool.forEach(p => { cityCounts[p.city] = (cityCounts[p.city] || 0) + 1; });
      const res = pool.filter(p => !q.get('city') || norm(p.city).includes(norm(q.get('city'))));
      return { total: res.length, cityCounts, profiles: res.slice(0, 20).map(pub) };
    }],
    ['POST', /^\/api\/skip$/, (m, b) => { DB.skips.push({ from: me().id, to: +b.toId }); save(); return { ok: true }; }],
    ['POST', /^\/api\/like$/, (m, b) => {
      const u = me(), to = +b.toId, target = byId(to);
      if (!target) fail(404, 'המשתמש לא נמצא');
      DB.likes = DB.likes.filter(l => !(l.from === u.id && l.to === to));
      const note = String(b.note || '').trim().slice(0, 140);
      DB.likes.push({ from: u.id, to, kind: b.kind || 'profile', idx: +b.idx || 0, note, rose: !!b.rose, removed: false });
      const theirs = DB.likes.find(l => l.from === to && l.to === u.id && !l.removed);
      if (!theirs) { save(); return { match: false }; }
      const [a, c] = pairKey(u.id, to);
      let mt = DB.matches.find(x => x.a === a && x.b === c);
      if (!mt) {
        mt = { id: DB.matches.length + 1, a, b: c, ended: false }; DB.matches.push(mt);
        if (theirs.note) DB.messages.push({ id: DB.nextMsg++, match: mt.id, from: to, body: theirs.note });
        if (note) DB.messages.push({ id: DB.nextMsg++, match: mt.id, from: u.id, body: note });
      }
      save(); return { match: true, matchId: mt.id, profile: pub(target) };
    }],
    ['GET', /^\/api\/likes$/, () => {
      const u = me(), hide = hidden(u.id), myPhotos = photosOf(u);
      return DB.likes.filter(l => l.to === u.id && !l.removed && !hide.has(l.from) && byId(l.from)).sort((x, y) => y.rose - x.rose).map(l => ({
        from: pub(byId(l.from)), kind: l.kind, idx: l.idx, note: l.note, rose: l.rose,
        about: l.kind === 'photo' ? { photo: myPhotos[l.idx] || null } : l.kind === 'prompt' ? { prompt: u.prompts[l.idx] || null } : {}
      }));
    }],
    ['POST', /^\/api\/likes\/(\d+)\/remove$/, m => {
      const u = me(); DB.likes.forEach(l => { if (l.from === +m[1] && l.to === u.id) l.removed = true; });
      DB.skips.push({ from: u.id, to: +m[1] }); save(); return { ok: true };
    }],
    ['GET', /^\/api\/matches$/, () => {
      const u = me();
      return DB.matches.filter(x => !x.ended && (x.a === u.id || x.b === u.id)).map(x => {
        const msgs = DB.messages.filter(g => g.match === x.id), last = msgs[msgs.length - 1];
        return { id: x.id, other: pub(byId(x.a === u.id ? x.b : x.a)), last: last ? { mine: last.from === u.id, body: last.body } : null, at: last ? last.id : 0 };
      }).sort((p, q) => q.at - p.at);
    }],
    ['GET', /^\/api\/matches\/(\d+)\/messages$/, (m, b, q) => {
      const u = me(), mt = DB.matches.find(x => x.id === +m[1] && !x.ended && (x.a === u.id || x.b === u.id));
      if (!mt) fail(404, 'השיחה לא נמצאה');
      return DB.messages.filter(g => g.match === mt.id && g.id > (+q.get('after') || 0)).map(g => ({ id: g.id, mine: g.from === u.id, body: g.body }));
    }],
    ['POST', /^\/api\/matches\/(\d+)\/messages$/, (m, b) => {
      const u = me(), mt = DB.matches.find(x => x.id === +m[1] && !x.ended && (x.a === u.id || x.b === u.id));
      if (!mt) fail(404, 'השיחה לא נמצאה');
      const body = String(b.body || '').trim(); if (!body) fail(400, 'ההודעה ריקה');
      const msg = { id: DB.nextMsg++, match: mt.id, from: u.id, body }; DB.messages.push(msg); save();
      // Sample people answer after a moment
      const other = mt.a === u.id ? mt.b : mt.a;
      if (SAMPLE.some(p => p.id === other)) setTimeout(() => {
        const n = DB.messages.filter(g => g.match === mt.id && g.from === other).length;
        DB.messages.push({ id: DB.nextMsg++, match: mt.id, from: other, body: REPLIES[n % REPLIES.length] }); save();
      }, 1800);
      return { id: msg.id, mine: true, body };
    }],
    ['POST', /^\/api\/matches\/(\d+)\/unmatch$/, m => { const mt = DB.matches.find(x => x.id === +m[1]); if (mt) mt.ended = true; save(); return { ok: true }; }],
    ['POST', /^\/api\/report$/, (m, b) => {
      const u = me(); DB.reports.push({ from: u.id, to: +b.toId });
      DB.matches.forEach(x => { if ((x.a === u.id && x.b === +b.toId) || (x.b === u.id && x.a === +b.toId)) x.ended = true; });
      save(); return { ok: true };
    }]
  ];

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (!url.pathname.startsWith('/api/')) return realFetch(input, opts);
    const method = (opts.method || 'GET').toUpperCase();
    const body = opts.body instanceof FormData ? opts.body : opts.body ? JSON.parse(opts.body) : {};
    const json = (status, data) => new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
    for (const [mth, re, fn] of routes) {
      const m = mth === method && re.exec(url.pathname);
      if (!m) continue;
      try { return json(method === 'POST' && /register|messages$/.test(url.pathname) ? 201 : 200, await fn(m, body, url.searchParams)); }
      catch (e) { return json(e.status || 500, { error: e.status ? e.message : 'משהו השתבש' }); }
    }
    return json(404, { error: 'לא נמצא' });
  };
})();
