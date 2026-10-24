(() => {
  'use strict';
  // ---------- Icons ----------
  const I = {
    heart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 20s-7-4.4-9.2-9A5 5 0 0 1 12 6a5 5 0 0 1 9.2 5c-2.2 4.6-9.2 9-9.2 9z"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4" stroke-linecap="round"/></svg>',
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
    ruler: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v18M8 7h4M9 11h3M8 15h4"/></svg>',
    cake: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="11" width="16" height="9" rx="2"/><path d="M12 11V7M4 15c2 1 4 1 4-1 0 2 4 2 4 0 0 2 4 2 4 0 0 2 2 2 4 1"/></svg>',
    star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8 6.6 19.7l1.1-6.1L3.2 9.4l6.1-.8z"/></svg>',
    discover: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 17h6"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    me: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4.5-6 8-6s7 2 8 6"/></svg>',
    rose: '<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16" style="vertical-align:-3px"><path d="M12 3c2 0 4 1.5 4 4.5S14 12 12 12 8 10.5 8 7.5 10 3 12 3z"/><path d="M12 12v9M12 17c-2-2.5-5-2.5-6-2 1 2 3 3 6 2zM12 16c2-2 4.5-2 5.5-1.5-1 1.8-3 2.5-5.5 1.5z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
  };
  const CITIES = ['תל אביב','ירושלים','חיפה','באר שבע','רמת גן','הרצליה','ראשון לציון','נתניה','אילת','מודיעין','פתח תקווה','אשדוד','רחובות','כפר סבא','חולון','נצרת','גבעתיים','רעננה','הוד השרון','אשקלון'];
  const LABELS = { gender: 'מגדר', zodiac: 'מזל', edu: 'השכלה', hometown: 'עיר הולדת', relig: 'דת ואמונה', intent: 'מה אני מחפש/ת', kids: 'ילדים', wantKids: 'רוצה ילדים', langs: 'שפות', pets: 'חיות מחמד', drink: 'אלכוהול', smoke: 'עישון', sport: 'ספורט', kosher: 'כשרות' };
  const ROWS = ['gender', 'edu', 'hometown', 'relig', 'intent', 'kids', 'wantKids', 'langs'];
  const HABITS = ['pets', 'drink', 'smoke', 'sport', 'kosher'];
  const KIDS_FILTERS = [['', 'ילדים: הכל'], ['none', 'בלי ילדים'], ['has', 'עם ילדים'], ['wants', 'רוצה ילדים']];

  // ---------- Helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  async function api(method, url, body, isForm) {
    const opts = { method, headers: { 'X-Kesher': '1' }, credentials: 'same-origin' };
    if (body && !isForm) { opts.headers['content-type'] = 'application/json'; opts.body = JSON.stringify(body); }
    if (isForm) opts.body = body;
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && url !== '/api/login' && url !== '/api/me') { showAuth(); throw new Error('צריך להתחבר מחדש'); }
    if (!r.ok) throw new Error(data.error || 'משהו השתבש. נסו שוב');
    return data;
  }
  function toast(msg) {
    const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  }
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

  // ---------- State ----------
  let OPT = null, ME = null, tab = 'discover', chatWith = null, pollTimer = null, likesCount = 0;
  let F = load('kesher-filters', { city: '', ageMax: 99, relig: '', kids: '' });
  let DISC = { profiles: [], total: 0, cityCounts: {} };

  // ---------- Auth ----------
  function showAuth(mode = 'register') {
    stopPoll();
    $('#tabs-nav').hidden = true;
    $('#top').innerHTML = `<div class="brand-row"><div class="brand">קֶ<span>שֶׁ</span>ר</div><span class="rose-count">חינם לגמרי</span></div>`;
    const v = $('#view');
    v.innerHTML = `<div class="auth">
      <h1 class="section-title">${mode === 'register' ? 'הצטרפות לקשר' : 'התחברות'}</h1>
      <p class="section-sub">${mode === 'register' ? 'אפליקציית היכרויות חינמית. נרשמים, מעלים תמונות ומתחילים להכיר.' : 'ברוכים השבים.'}</p>
      <form class="form" id="auth-form" novalidate>
        ${mode === 'register' ? `
        <div class="field"><label for="r-name">שם פרטי</label><input id="r-name" autocomplete="given-name" required maxlength="40"></div>` : ''}
        <div class="field"><label for="r-email">אימייל</label><input id="r-email" type="email" autocomplete="email" required dir="ltr"></div>
        <div class="field"><label for="r-pass">סיסמה${mode === 'register' ? ' (לפחות 8 תווים)' : ''}</label><input id="r-pass" type="password" autocomplete="${mode === 'register' ? 'new-password' : 'current-password'}" required minlength="8" dir="ltr"></div>
        ${mode === 'register' ? `
        <div class="two">
          <div class="field"><label for="r-birth">תאריך לידה</label><input id="r-birth" type="date" required></div>
          <div class="field"><label for="r-gender">מגדר</label><select id="r-gender">${OPT.choices.gender.map(g => `<option>${esc(g)}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label for="r-city">עיר</label><input id="r-city" list="r-cities" required maxlength="40"><datalist id="r-cities">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
        <label class="check"><input type="checkbox" id="r-adult"> אני בן/בת 18 ומעלה, ואעלה רק תמונות שלי</label>` : ''}
        <p class="form-error" id="auth-error" role="alert" hidden></p>
        <button class="btn" id="auth-submit">${mode === 'register' ? 'הרשמה' : 'התחברות'}</button>
        <button type="button" class="btn ghost" id="auth-switch">${mode === 'register' ? 'כבר יש לי חשבון' : 'עוד אין לי חשבון'}</button>
      </form></div>`;
    $('#auth-switch').onclick = () => showAuth(mode === 'register' ? 'login' : 'register');
    $('#auth-form').onsubmit = async e => {
      e.preventDefault();
      const err = $('#auth-error'); err.hidden = true;
      const btn = $('#auth-submit'); btn.disabled = true;
      try {
        if (mode === 'register') {
          if (!$('#r-adult').checked) throw new Error('צריך לאשר שגילך 18 ומעלה');
          await api('POST', '/api/register', {
            name: $('#r-name').value, email: $('#r-email').value, password: $('#r-pass').value,
            birthdate: $('#r-birth').value, gender: $('#r-gender').value, city: $('#r-city').value
          });
        } else {
          await api('POST', '/api/login', { email: $('#r-email').value, password: $('#r-pass').value });
        }
        await startApp();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
    };
  }

  async function startApp() {
    ME = await api('GET', '/api/me');
    $('#tabs-nav').hidden = false;
    if (!F.city && !load('kesher-city-set', false)) { F.city = ME.city; store('kesher-filters', F); store('kesher-city-set', true); }
    setTab(ME.photos.length ? 'discover' : 'me');
  }

  // ---------- Profile rendering ----------
  function avatarHTML(p) {
    return `<span class="avatar" style="background:var(--accent)" aria-hidden="true">${p.photos[0] ? `<img src="${esc(p.photos[0].url)}" alt="">` : esc((p.name || '?')[0])}</span>`;
  }
  function photoHTML(p, i, hearts) {
    const ph = p.photos[i];
    if (!ph) return '';
    return `<div class="photo"><img src="${esc(ph.url)}" alt="תמונה ${i + 1} של ${esc(p.name)}" loading="lazy">
      ${hearts ? `<button class="heart" data-kind="photo" data-i="${i}" aria-label="לייק לתמונה ${i + 1}">${I.heart}</button>` : ''}</div>`;
  }
  function promptHTML(p, i, hearts) {
    const pr = p.prompts[i];
    if (!pr || !pr.a) return '';
    return `<div class="prompt"><p class="q">${esc(pr.q)}</p><p class="a">${esc(pr.a)}</p>
      ${hearts ? `<button class="heart" data-kind="prompt" data-i="${i}" aria-label="לייק לתשובה: ${esc(pr.q)}">${I.heart}</button>` : ''}</div>`;
  }
  function detailsHTML(p) {
    const rows = ROWS.filter(k => p[k]);
    const habits = HABITS.filter(k => p[k]);
    return `<section class="details" aria-label="פרטים">
      <div class="vitals"><span>${I.cake}${p.age}</span>${p.height ? `<span>${I.ruler}${p.height} ס״מ</span>` : ''}
        <span style="color:var(--accent)">${I.pin}${esc(p.city)}</span>${p.zodiac ? `<span>${I.star}${esc(p.zodiac)}</span>` : ''}</div>
      <dl>${p.job ? `<div class="drow"><dt>עיסוק</dt><dd>${esc(p.job)}</dd></div>` : ''}
        ${rows.map(k => `<div class="drow"><dt>${LABELS[k]}</dt><dd>${esc(p[k])}</dd></div>`).join('')}</dl>
      ${habits.length ? `<div class="habits">${habits.map(k => `<span class="habit"><b>${LABELS[k]}:</b> ${esc(p[k])}</span>`).join('')}</div>` : ''}
    </section>`;
  }
  // Same order as Hinge: photo, details, prompt, two photos, prompt, two photos, prompt, photo
  function profileBody(p, hearts) {
    return [photoHTML(p, 0, hearts), detailsHTML(p), promptHTML(p, 0, hearts), photoHTML(p, 1, hearts), photoHTML(p, 2, hearts),
      promptHTML(p, 1, hearts), photoHTML(p, 3, hearts), photoHTML(p, 4, hearts), promptHTML(p, 2, hearts), photoHTML(p, 5, hearts)].join('');
  }

  function sheet(html, label) {
    const L = $('#layer');
    L.innerHTML = `<div class="sheet-bg" id="sheet-bg"><div class="sheet" dir="rtl" role="dialog" aria-modal="true" aria-label="${esc(label)}">${html}</div></div>`;
    $('#sheet-bg').onclick = e => { if (e.target.id === 'sheet-bg') closeSheet(); };
    return L;
  }
  const closeSheet = () => { $('#layer').innerHTML = ''; };
  function viewProfile(p) {
    sheet(`<div class="p-head"><h2 class="p-name">${esc(p.name)}</h2><button class="btn ghost" id="sheet-close">סגירה</button></div>
      <div class="profile">${profileBody(p, false)}</div>`, 'פרופיל של ' + p.name);
    $('#sheet-close').onclick = closeSheet;
  }

  // ---------- Header with city search and filters ----------
  function renderHeader() {
    const top = $('#top');
    const brand = `<div class="brand-row"><div class="brand">קֶ<span>שֶׁ</span>ר</div><span class="rose-count">חינם לגמרי</span></div>`;
    if (tab !== 'discover') { top.innerHTML = brand; return; }
    const cities = Object.entries(DISC.cityCounts).sort((a, b) => b[1] - a[1]).map(([c]) => c);
    top.innerHTML = `${brand}
      <div class="search"><div class="search-field">${I.search}
        <input id="city-q" type="search" list="city-list" placeholder="חיפוש לפי עיר, למשל חיפה" value="${esc(F.city)}" aria-label="חיפוש לפי עיר" autocomplete="off">
        <button class="clear" id="city-clear" aria-label="ניקוי החיפוש" ${F.city ? '' : 'hidden'}>×</button>
        <datalist id="city-list">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div></div>
      <div class="filters">
        <select class="filter-select ${F.ageMax < 99 ? 'on' : ''}" id="age-max" aria-label="גיל מקסימלי">
          ${[[99, 'גיל: הכל'], [25, 'עד 25'], [30, 'עד 30'], [35, 'עד 35'], [40, 'עד 40'], [50, 'עד 50']].map(([a, l]) => `<option value="${a}" ${F.ageMax == a ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
        <select class="filter-select ${F.relig ? 'on' : ''}" id="f-relig" aria-label="סינון לפי דת">
          <option value="">דת: הכל</option>${OPT.choices.relig.map(o => `<option ${F.relig === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}
        </select>
        <select class="filter-select ${F.kids ? 'on' : ''}" id="f-kids" aria-label="סינון לפי ילדים">
          ${KIDS_FILTERS.map(([v, l]) => `<option value="${v}" ${F.kids === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
      </div>
      <div class="chips" role="group" aria-label="ערים">
        <button class="chip" data-city="" aria-pressed="${!F.city}">כל הערים</button>
        ${cities.map(c => `<button class="chip" data-city="${esc(c)}" aria-pressed="${F.city === c}">${esc(c)}<span class="n">${DISC.cityCounts[c]}</span></button>`).join('')}
      </div>`;
    let t;
    $('#city-q').addEventListener('input', e => { F.city = e.target.value.trim(); clearTimeout(t); t = setTimeout(refreshDiscover, 300); });
    $('#city-clear').onclick = () => { F.city = ''; refreshDiscover(); };
    $('#age-max').onchange = e => { F.ageMax = +e.target.value; refreshDiscover(); };
    $('#f-relig').onchange = e => { F.relig = e.target.value; refreshDiscover(); };
    $('#f-kids').onchange = e => { F.kids = e.target.value; refreshDiscover(); };
    top.querySelectorAll('.chip').forEach(b => b.onclick = () => { F.city = b.dataset.city; refreshDiscover(); });
  }

  async function refreshDiscover() {
    store('kesher-filters', F);
    const qs = new URLSearchParams({ city: F.city, ageMax: F.ageMax, relig: F.relig, kids: F.kids });
    try { DISC = await api('GET', '/api/discover?' + qs); } catch (e) { toast(e.message); }
    if (tab !== 'discover') return;
    const focused = document.activeElement && document.activeElement.id === 'city-q';
    renderHeader(); renderView();
    if (focused) { const q = $('#city-q'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }

  // ---------- Discover ----------
  function renderDiscover(v) {
    if (!ME.photos.length) {
      v.innerHTML = `<div class="empty"><h2>קודם מעלים תמונה</h2><p>כדי לראות אחרים ולהופיע בחיפוש צריך לפחות תמונה אחת בפרופיל.</p><button class="btn" id="go-me">להוספת תמונות</button></div>`;
      $('#go-me').onclick = () => setTab('me'); return;
    }
    const p = DISC.profiles[0];
    const where = F.city ? `ב<b>${esc(F.city)}</b>` : 'בכל הערים';
    if (!p) {
      v.innerHTML = `<div class="empty"><h2>אין כרגע עוד פרופילים ${F.city ? 'ב' + esc(F.city) : ''}</h2>
        <p>נסו עיר אחרת או נקו חלק מהסינונים. אנשים חדשים מצטרפים כל הזמן.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center">
          ${F.city ? '<button class="btn" id="all-cities">חיפוש בכל הערים</button>' : ''}
          ${F.relig || F.kids || F.ageMax < 99 ? '<button class="btn ghost" id="clear-filters">ניקוי סינונים</button>' : ''}
        </div></div>`;
      const a = $('#all-cities'); if (a) a.onclick = () => { F.city = ''; refreshDiscover(); };
      const c = $('#clear-filters'); if (c) c.onclick = () => { F.relig = ''; F.kids = ''; F.ageMax = 99; refreshDiscover(); };
      return;
    }
    v.innerHTML = `<p class="result-line">${DISC.total === 1 ? 'פרופיל אחד' : DISC.total + ' פרופילים'} ${where}</p>
      <article class="profile" aria-label="פרופיל של ${esc(p.name)}">
        <div class="p-head"><h1 class="p-name">${esc(p.name)}</h1><button class="btn rose" id="send-rose">${I.rose} שליחת ורד</button></div>
        ${profileBody(p, true)}
        <div class="skip-wrap"><button class="skip" id="skip" aria-label="דילוג על ${esc(p.name)}">${I.x}</button></div>
        <button class="link-btn" id="report">דיווח וחסימה</button>
      </article>`;
    v.querySelectorAll('.heart').forEach(b => b.onclick = () => openLike(p, b.dataset.kind, +b.dataset.i, false));
    $('#send-rose').onclick = () => openLike(p, p.prompts.length ? 'prompt' : 'photo', 0, true);
    $('#skip').onclick = async () => { await api('POST', '/api/skip', { toId: p.id }).catch(e => toast(e.message)); nextProfile(); };
    $('#report').onclick = () => openReport(p, null);
  }
  function nextProfile() {
    DISC.profiles.shift(); DISC.total = Math.max(0, DISC.total - 1);
    window.scrollTo({ top: 0 });
    if (!DISC.profiles.length) refreshDiscover(); else renderView();
  }

  function openLike(p, kind, i, rose) {
    const preview = kind === 'photo' ? photoHTML(p, i, false) : `<p class="q">${esc(p.prompts[i].q)}</p><p class="a">${esc(p.prompts[i].a)}</p>`;
    sheet(`<strong style="font-size:18px">${rose ? 'שליחת ורד ל' : 'לייק ל'}${esc(p.name)}</strong>
      <div class="preview">${preview}</div>
      <textarea id="like-note" maxlength="140" placeholder="הוסיפו תגובה כדי לבלוט (לא חובה)"></textarea>
      <div class="btns"><button class="btn ghost" id="like-cancel">ביטול</button><button class="btn ${rose ? 'rose' : ''}" id="like-send">${rose ? 'שליחת ורד' : 'שליחת לייק'}</button></div>`, 'שליחת לייק');
    $('#like-cancel').onclick = closeSheet;
    $('#like-note').focus();
    $('#like-send').onclick = async () => {
      $('#like-send').disabled = true;
      try {
        const r = await api('POST', '/api/like', { toId: p.id, kind, idx: i, note: $('#like-note').value, rose });
        closeSheet();
        if (r.match) showMatch(r.profile, r.matchId); else toast(rose ? `שלחתם ורד ל${p.name}` : `הלייק נשלח ל${p.name}`);
        if (tab === 'discover') nextProfile();
      } catch (e) { toast(e.message); $('#like-send').disabled = false; }
    };
  }

  function showMatch(p, matchId) {
    sheet(`<div class="match-pop"><div class="pair">${avatarHTML(ME)}${avatarHTML(p)}</div>
      <h2>יש קשר!</h2><p style="margin:0">גם ${esc(p.name)} עשה/תה לך לייק. זה הזמן לכתוב משהו.</p></div>
      <div class="btns"><button class="btn ghost" id="m-later">אחר כך</button><button class="btn" id="m-chat">שליחת הודעה</button></div>`, 'התאמה חדשה');
    $('#m-later').onclick = closeSheet;
    $('#m-chat').onclick = () => { closeSheet(); chatWith = { id: matchId, other: p }; setTab('matches'); };
  }

  function openReport(p, matchId) {
    sheet(`<strong style="font-size:18px">דיווח על ${esc(p.name)}</strong>
      <p style="margin:0;color:var(--muted)">אחרי הדיווח לא תראו זה את זה יותר, וכל שיחה ביניכם תיסגר.</p>
      <div class="field"><label for="rep-reason">מה קרה? (לא חובה)</label>
        <select id="rep-reason"><option>פרופיל מזויף או תמונות של מישהו אחר</option><option>הטרדה או התנהגות פוגענית</option><option>מתחת לגיל 18</option><option>ספאם או ניסיון הונאה</option><option>אחר</option></select></div>
      <div class="btns"><button class="btn ghost" id="rep-cancel">ביטול</button><button class="btn rose" id="rep-send">דיווח וחסימה</button></div>`, 'דיווח');
    $('#rep-cancel').onclick = closeSheet;
    $('#rep-send').onclick = async () => {
      try {
        await api('POST', '/api/report', { toId: p.id, reason: $('#rep-reason').value });
        closeSheet(); toast('הדיווח התקבל. לא תראו זה את זה יותר');
        if (matchId) { chatWith = null; renderView(); } else nextProfile();
      } catch (e) { toast(e.message); }
    };
  }

  // ---------- Likes you ----------
  async function renderLikes(v) {
    v.innerHTML = '<p class="result-line">טוען...</p>';
    let likes = [];
    try { likes = await api('GET', '/api/likes'); } catch (e) { v.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
    likesCount = likes.length; renderTabs();
    if (tab !== 'likes') return;
    if (!likes.length) {
      v.innerHTML = `<div class="empty"><h2>עוד אין לייקים</h2><p>פרופיל עם כמה תמונות ותשובות לשאלות מקבל הרבה יותר לייקים.</p><button class="btn" id="go-me">לשיפור הפרופיל</button></div>`;
      $('#go-me').onclick = () => setTab('me'); return;
    }
    v.innerHTML = `<h1 class="section-title">אהבו אותך</h1><p class="section-sub">${likes.length} אנשים עשו לך לייק. לייק בחזרה יוצר התאמה.</p>
      <div class="list">${likes.map((l, n) => `<div class="like-card">
        ${photoHTML(l.from, 0, false)}
        <div class="body"><div><strong style="font-size:18px">${esc(l.from.name)}, ${l.from.age}</strong> · <span style="color:var(--accent);font-weight:700">${esc(l.from.city)}</span> ${l.rose ? '<span class="tag-rose">שלח/ה ורד</span>' : ''}</div>
          <div class="note ${l.rose ? 'rose' : ''}">${l.about.prompt ? `אהב/ה את התשובה שלך ל"${esc(l.about.prompt.q)}"` : l.about.photo ? 'אהב/ה תמונה שלך' : 'אהב/ה את הפרופיל שלך'}${l.note ? `<br><b>"${esc(l.note)}"</b>` : ''}</div>
          <div class="btns"><button class="btn ghost" data-view="${n}">לפרופיל</button><button class="btn ghost" data-remove="${n}">הסרה</button><button class="btn" data-back="${n}">לייק בחזרה</button></div>
        </div></div>`).join('')}</div>`;
    v.querySelectorAll('[data-view]').forEach(b => b.onclick = () => viewProfile(likes[+b.dataset.view].from));
    v.querySelectorAll('[data-back]').forEach(b => b.onclick = async () => {
      const p = likes[+b.dataset.back].from;
      try { const r = await api('POST', '/api/like', { toId: p.id, kind: 'profile' }); if (r.match) showMatch(r.profile, r.matchId); renderLikes(v); } catch (e) { toast(e.message); }
    });
    v.querySelectorAll('[data-remove]').forEach(b => b.onclick = async () => {
      await api('POST', `/api/likes/${likes[+b.dataset.remove].from.id}/remove`).catch(e => toast(e.message)); renderLikes(v);
    });
  }

  // ---------- Matches and chat ----------
  async function renderMatches(v) {
    if (chatWith) return renderChat(v);
    let list = [];
    try { list = await api('GET', '/api/matches'); } catch (e) { v.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
    if (tab !== 'matches' || chatWith) return;
    if (!list.length) {
      v.innerHTML = `<div class="empty"><h2>עוד אין התאמות</h2><p>עשו לייק לתמונה או לתשובה בפרופיל. כשהלייק הדדי, השיחה תיפתח כאן.</p><button class="btn" id="go-d">לגלות פרופילים</button></div>`;
      $('#go-d').onclick = () => setTab('discover'); return;
    }
    v.innerHTML = `<h1 class="section-title">התאמות</h1><p class="section-sub">${list.length} שיחות</p>
      <div class="list">${list.map((m, n) => `<button class="row" data-chat="${n}">${avatarHTML(m.other)}<div class="txt"><div class="t1">${esc(m.other.name)} · ${esc(m.other.city)}</div>
        <div class="t2">${m.last ? (m.last.mine ? 'את/ה: ' : '') + esc(m.last.body) : 'התאמה חדשה. כתבו משהו!'}</div></div></button>`).join('')}</div>`;
    v.querySelectorAll('[data-chat]').forEach(b => b.onclick = () => { chatWith = list[+b.dataset.chat]; renderView(); });
  }

  async function renderChat(v) {
    const m = chatWith, p = m.other;
    let lastId = 0, first = true;
    v.innerHTML = `<div class="chat-head"><button class="back" id="back" aria-label="חזרה להתאמות">→</button>${avatarHTML(p)}
      <button class="link-btn" id="chat-profile" style="text-align:start"><div style="font-weight:700">${esc(p.name)}, ${p.age}</div><div style="font-size:14px;color:var(--muted)">${esc(p.city)}${p.job ? ' · ' + esc(p.job) : ''}</div></button>
      <button class="btn ghost" id="chat-menu" style="margin-inline-start:auto">אפשרויות</button></div>
      <div class="chat" id="chat"><div class="bubble ctx">יש לכם התאמה. אל תשתפו פרטים אישיים כמו כתובת או פרטי בנק.</div></div>
      <form class="composer" id="composer"><input id="msg" placeholder="כתיבת הודעה" autocomplete="off" aria-label="הודעה" maxlength="1000"><button class="btn">שליחה</button></form>`;
    $('#back').onclick = () => { chatWith = null; stopPoll(); renderView(); };
    $('#chat-profile').onclick = () => viewProfile(p);
    $('#chat-menu').onclick = () => {
      sheet(`<strong style="font-size:18px">${esc(p.name)}</strong>
        <button class="btn ghost" id="cm-unmatch">ביטול ההתאמה</button><button class="btn rose" id="cm-report">דיווח וחסימה</button><button class="btn ghost" id="cm-close">סגירה</button>`, 'אפשרויות שיחה');
      $('#cm-close').onclick = closeSheet;
      $('#cm-report').onclick = () => openReport(p, m.id);
      $('#cm-unmatch').onclick = async e => {
        if (e.target.dataset.armed !== '1') { e.target.dataset.armed = '1'; e.target.textContent = 'לחיצה נוספת תבטל את ההתאמה'; return; }
        await api('POST', `/api/matches/${m.id}/unmatch`).catch(x => toast(x.message));
        closeSheet(); chatWith = null; stopPoll(); renderView(); toast('ההתאמה בוטלה');
      };
    };
    const chat = $('#chat');
    async function pull() {
      try {
        const msgs = await api('GET', `/api/matches/${m.id}/messages?after=${lastId}`);
        if (!msgs.length) return;
        const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
        chat.insertAdjacentHTML('beforeend', msgs.map(x => `<div class="bubble ${x.mine ? 'me' : 'them'}">${esc(x.body)}</div>`).join(''));
        lastId = msgs[msgs.length - 1].id;
        if (atBottom || first) window.scrollTo({ top: document.body.scrollHeight });
        first = false;
      } catch (e) { if (/לא נמצאה/.test(e.message)) { stopPoll(); chatWith = null; toast('השיחה נסגרה'); renderView(); } }
    }
    await pull();
    stopPoll(); pollTimer = setInterval(pull, 3000);
    $('#composer').onsubmit = async e => {
      e.preventDefault();
      const input = $('#msg'), body = input.value.trim(); if (!body) return;
      input.value = '';
      try { await api('POST', `/api/matches/${m.id}/messages`, { body }); await pull(); } catch (x) { toast(x.message); input.value = body; }
      input.focus();
    };
  }
  function stopPoll() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

  // ---------- My profile ----------
  // Shrinks big phone photos before upload
  function shrink(file) {
    return new Promise(resolve => {
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1600 / Math.max(img.width, img.height));
        const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(b => resolve(b || file), 'image/jpeg', 0.85);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  function renderMe(v) {
    const me = ME, prompts = [0, 1, 2].map(i => me.prompts[i] || { q: OPT.prompts[i], a: '' });
    const choice = k => `<div class="field"><label for="f-${k}">${LABELS[k]}</label><select id="f-${k}"><option value="">לא צוין</option>${OPT.choices[k].map(o => `<option ${me[k] === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></div>`;
    const text = (k, label, ph = '') => `<div class="field"><label for="f-${k}">${label}</label><input id="f-${k}" value="${esc(me[k] || '')}" placeholder="${esc(ph)}"></div>`;
    v.innerHTML = `<h1 class="section-title">הפרופיל שלי</h1>
      ${!me.photos.length ? '<p class="notice">כדי להופיע בחיפוש ולראות אחרים, העלו לפחות תמונה אחת שלכם.</p>' : '<p class="section-sub">התמונה הראשונה מופיעה ראשונה בפרופיל ובשיחות.</p>'}
      <div class="upload">${[0, 1, 2, 3, 4, 5].map(i => {
        const ph = me.photos[i];
        return ph ? `<div class="slot"><img src="${esc(ph.url)}" alt="תמונה ${i + 1}">
            <button class="slot-btn del" data-del="${ph.id}" aria-label="מחיקת תמונה ${i + 1}">×</button>
            ${i ? `<button class="slot-btn first" data-first="${ph.id}" aria-label="הצגה ראשונה">ראשונה</button>` : '<span class="slot-tag">ראשית</span>'}</div>`
          : `<label class="slot empty-slot" for="ph${i}">הוספת תמונה<input id="ph${i}" type="file" accept="image/jpeg,image/png,image/webp"></label>`;
      }).join('')}</div>
      <form class="form" id="me-form">
        <div class="two">${text('name', 'שם')}<div class="field"><label>גיל</label><input value="${me.age}" disabled></div></div>
        <div class="two"><div class="field"><label for="f-city">עיר</label><input id="f-city" list="me-cities" value="${esc(me.city)}"><datalist id="me-cities">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
          ${text('job', 'עיסוק', 'למשל מורה')}</div>
        <div class="two"><div class="field"><label for="f-height">גובה (ס״מ)</label><input id="f-height" type="number" min="120" max="230" value="${me.height || ''}"></div>${choice('gender')}</div>
        <h2>פרטים</h2>
        <div class="two">${text('edu', 'השכלה', 'למשל אוניברסיטת תל אביב')}${text('hometown', 'עיר הולדת')}${choice('relig')}${choice('intent')}${choice('kids')}${choice('wantKids')}${text('langs', 'שפות', 'עברית, אנגלית')}${choice('zodiac')}</div>
        <h2>הרגלים</h2>
        <div class="two">${HABITS.map(choice).join('')}</div>
        <h2>שאלות</h2>
        ${prompts.map((pr, i) => `<div class="field"><label for="pq${i}">שאלה ${i + 1}</label>
          <select id="pq${i}">${OPT.prompts.map(o => `<option ${pr.q === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>
          <textarea id="pa${i}" maxlength="200" placeholder="התשובה שלך" aria-label="תשובה לשאלה ${i + 1}">${esc(pr.a)}</textarea></div>`).join('')}
        <p class="form-error" id="me-error" role="alert" hidden></p>
        <button class="btn" id="me-save">שמירת הפרופיל</button>
        <button type="button" class="btn ghost" id="me-preview">איך אחרים רואים אותי</button>
        <button type="button" class="btn ghost" id="logout">התנתקות</button>
        <button type="button" class="link-btn danger" id="delete-account">מחיקת החשבון</button>
        <p class="demo-note">מחובר/ת בתור ${esc(me.email)}</p>
      </form>`;
    v.querySelectorAll('input[type=file]').forEach(inp => inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return;
      inp.closest('label').textContent = 'מעלה...';
      const fd = new FormData(); fd.append('photo', await shrink(f), 'photo.jpg');
      try { const r = await api('POST', '/api/me/photos', fd, true); ME.photos = r.photos; toast('התמונה עלתה'); } catch (e) { toast(e.message); }
      renderMe(v);
    });
    v.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
      try { ME.photos = (await api('DELETE', `/api/me/photos/${b.dataset.del}`)).photos; } catch (e) { toast(e.message); } renderMe(v);
    });
    v.querySelectorAll('[data-first]').forEach(b => b.onclick = async () => {
      try { ME.photos = (await api('POST', `/api/me/photos/${b.dataset.first}/first`)).photos; } catch (e) { toast(e.message); } renderMe(v);
    });
    $('#me-form').onsubmit = async e => {
      e.preventDefault();
      const err = $('#me-error'); err.hidden = true;
      const body = { name: $('#f-name').value, city: $('#f-city').value, job: $('#f-job').value, height: $('#f-height').value || null,
        prompts: [0, 1, 2].map(i => ({ q: $('#pq' + i).value, a: $('#pa' + i).value })).filter(p => p.a.trim()) };
      ['gender', 'edu', 'hometown', 'relig', 'intent', 'kids', 'wantKids', 'langs', 'zodiac', ...HABITS].forEach(k => { body[k] = $('#f-' + k).value; });
      try { const r = await api('PUT', '/api/me', body); ME = { ...ME, ...r }; toast('הפרופיל נשמר'); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
    };
    $('#me-preview').onclick = () => viewProfile(ME);
    $('#logout').onclick = async () => { await api('POST', '/api/logout').catch(() => {}); ME = null; showAuth('login'); };
    $('#delete-account').onclick = async e => {
      if (e.target.dataset.armed !== '1') { e.target.dataset.armed = '1'; e.target.textContent = 'לחיצה נוספת תמחק את החשבון, התמונות וההודעות לצמיתות'; return; }
      try { await api('DELETE', '/api/me'); ME = null; showAuth('register'); toast('החשבון נמחק'); } catch (x) { toast(x.message); }
    };
  }

  // ---------- Tabs ----------
  const TABS = [['discover', 'גילוי', I.discover], ['likes', 'אהבו אותך', I.heart], ['matches', 'התאמות', I.chat], ['me', 'פרופיל', I.me]];
  function renderTabs() {
    $('#tabs').innerHTML = TABS.map(([id, label, ic]) => `<button data-tab="${id}" ${tab === id ? 'aria-current="page"' : ''}>${ic}<span>${label}</span>
      ${id === 'likes' && likesCount ? `<span class="badge">${likesCount}</span>` : ''}</button>`).join('');
    $('#tabs').querySelectorAll('button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
  }
  function setTab(t) {
    if (t !== 'matches') chatWith = null;
    stopPoll(); closeSheet(); tab = t; renderTabs(); renderHeader(); window.scrollTo({ top: 0 });
    if (t === 'discover') { renderView(); refreshDiscover(); } else renderView();
  }
  function renderView() {
    const v = $('#view');
    ({ discover: renderDiscover, likes: renderLikes, matches: renderMatches, me: renderMe })[tab](v);
  }
  // Keep the "likes you" badge fresh
  setInterval(() => { if (ME && tab !== 'likes') api('GET', '/api/likes').then(l => { likesCount = l.length; renderTabs(); }).catch(() => {}); }, 30000);

  // ---------- Boot ----------
  (async () => {
    try { OPT = await api('GET', '/api/options'); } catch (e) { $('#view').innerHTML = '<p class="form-error">אין חיבור לשרת. נסו לרענן את הדף.</p>'; return; }
    try { await startApp(); api('GET', '/api/likes').then(l => { likesCount = l.length; renderTabs(); }).catch(() => {}); }
    catch (e) { showAuth('register'); }
  })();
})();
