(() => {
  'use strict';
  // ---------- Icons (24px line icons) ----------
  const svg = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
  const I = {
    heart: svg('<path d="M12 20s-7-4.4-9.2-9A5 5 0 0 1 12 6a5 5 0 0 1 9.2 5c-2.2 4.6-9.2 9-9.2 9z"/>'),
    heartFill: svg('<path d="M12 20s-7-4.4-9.2-9A5 5 0 0 1 12 6a5 5 0 0 1 9.2 5c-2.2 4.6-9.2 9-9.2 9z" fill="currentColor"/>'),
    x: svg('<path d="M6 6l12 12M18 6L6 18"/>', 'stroke-width="2.4"'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'),
    sliders: svg('<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>'),
    more: svg('<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="19" cy="12" r="1.3" fill="currentColor"/>'),
    back: svg('<path d="M9 5l7 7-7 7"/>'),
    send: svg('<path d="M20 4L3 11l7 2 2 7z"/><path d="M10 13l10-9"/>'),
    cake: svg('<rect x="4" y="11" width="16" height="9" rx="2"/><path d="M12 11V7M4 15c2 1 4 1 4-1 0 2 4 2 4 0 0 2 4 2 4 0 0 2 2 2 4 1"/>'),
    person: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4.5-6 8-6s7 2 8 6"/>'),
    ruler: svg('<path d="M12 3v18M8 7h4M9 11h3M8 15h4M9 19h3"/>'),
    pin: svg('<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
    ring: svg('<circle cx="12" cy="14" r="6"/><path d="M9 4h6l-3 4z"/>'),
    star: svg('<path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8 6.6 19.7l1.1-6.1L3.2 9.4l6.1-.8z"/>'),
    work: svg('<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>'),
    cap: svg('<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c3 2 9 2 12 0v-5"/>'),
    home: svg('<path d="M3 11l9-7 9 7v9H3z"/><path d="M10 20v-5h4v5"/>'),
    book: svg('<path d="M4 5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5"/>'),
    target: svg('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor"/>'),
    baby: svg('<circle cx="12" cy="7" r="3"/><path d="M7 21v-5a5 5 0 0 1 10 0v5M9 12l-3 3M15 12l3 3"/>'),
    globe: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>'),
    paw: svg('<circle cx="7" cy="9" r="2"/><circle cx="17" cy="9" r="2"/><circle cx="10" cy="5" r="2"/><circle cx="14" cy="5" r="2"/><path d="M8 17c0-3 2-5 4-5s4 2 4 5-2 3-4 3-4 0-4-3z"/>'),
    glass: svg('<path d="M7 3h10l-1 7a4 4 0 0 1-8 0z"/><path d="M12 14v7M8 21h8"/>'),
    smoke: svg('<rect x="2" y="14" width="16" height="4" rx="1"/><path d="M21 14v4M18 4c0 3 3 3 3 6"/>'),
    dumbbell: svg('<path d="M6 8v8M18 8v8M3 10v4M21 10v4M6 12h12"/>'),
    food: svg('<path d="M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M17 3c-2 2-2 6 0 8v10"/>'),
    discover: svg('<rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 17h6"/>'),
    chat: svg('<path d="M4 5h16v11H9l-5 4z"/>'),
    me: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4.5-6 8-6s7 2 8 6"/>'),
    rose: svg('<path d="M12 3c2 0 4 1.5 4 4.5S14 12 12 12 8 10.5 8 7.5 10 3 12 3z" fill="currentColor"/><path d="M12 12v9M12 17c-2-2.5-5-2.5-6-2 1 2 3 3 6 2zM12 16c2-2 4.5-2 5.5-1.5-1 1.8-3 2.5-5.5 1.5z"/>'),
    camera: svg('<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>', 'stroke-width="2.2"'),
    shield: svg('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>')
  };
  const CITIES = ['תל אביב','ירושלים','חיפה','באר שבע','רמת גן','הרצליה','ראשון לציון','נתניה','אילת','מודיעין','פתח תקווה','אשדוד','רחובות','כפר סבא','חולון','נצרת','גבעתיים','רעננה','הוד השרון','אשקלון'];
  const LABELS = { gender: 'מגדר', status: 'סטטוס', zodiac: 'מזל', edu: 'השכלה', hometown: 'עיר הולדת', relig: 'דת ואמונה', intent: 'מה אני מחפש/ת', kids: 'ילדים', wantKids: 'רוצה ילדים', langs: 'שפות', pets: 'חיות מחמד', drink: 'אלכוהול', smoke: 'עישון', sport: 'ספורט', kosher: 'כשרות', job: 'עיסוק' };
  // Hinge-style vitals: a scrolling strip of short facts, then one line per longer fact
  const STRIP = [['age', 'cake'], ['gender', 'person'], ['height', 'ruler'], ['city', 'pin'], ['status', 'ring'], ['zodiac', 'star'], ['pets', 'paw'], ['drink', 'glass'], ['smoke', 'smoke'], ['sport', 'dumbbell'], ['kosher', 'food']];
  const LINES = [['job', 'work'], ['edu', 'cap'], ['hometown', 'home'], ['relig', 'book'], ['intent', 'target'], ['kids', 'baby'], ['wantKids', 'baby'], ['langs', 'globe']];
  const HABITS = ['pets', 'drink', 'smoke', 'sport', 'kosher'];
  const KIDS_FILTERS = [['', 'הכל'], ['none', 'בלי ילדים'], ['has', 'עם ילדים'], ['wants', 'רוצה ילדים']];
  const AGES = Array.from({ length: 63 }, (_, i) => i + 18);

  // ---------- Helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const reduceMotion = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const wait = ms => new Promise(r => setTimeout(r, reduceMotion() ? 0 : ms));
  async function api(method, url, body, isForm) {
    const opts = { method, headers: { 'X-Kesher': '1' }, credentials: 'same-origin' };
    if (body && !isForm) { opts.headers['content-type'] = 'application/json'; opts.body = JSON.stringify(body); }
    if (isForm) opts.body = body;
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && url !== '/api/login' && url !== '/api/me') { showAuth('login'); throw new Error('צריך להתחבר מחדש'); }
    if (!r.ok) throw new Error(data.error || 'משהו השתבש. נסו שוב');
    return data;
  }
  function toast(msg) {
    document.querySelectorAll('.toast').forEach(t => t.remove());
    const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  }
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const brand = `<div class="brand" aria-label="קשר">קֶשֶׁר<span class="brand-dot"></span></div>`;

  // ---------- State ----------
  let OPT = null, ME = null, tab = 'discover', chatWith = null, pollTimer = null, likesCount = 0;
  const DEFAULT_F = { city: '', ageMin: 18, ageMax: 80, relig: '', kids: '', status: '' };
  let F = { ...DEFAULT_F, ...load('kesher-filters-v2', {}) };
  let DISC = { profiles: [], total: 0, cityCounts: {} };
  const activeFilters = () => (F.ageMin > 18 || F.ageMax < 80 ? 1 : 0) + (F.relig ? 1 : 0) + (F.kids ? 1 : 0) + (F.status ? 1 : 0);

  // ---------- Auth ----------
  function showAuth(mode = 'register') {
    stopPoll(); closeSheet();
    $('#tabs-nav').hidden = true;
    $('#top').innerHTML = '';
    const v = $('#view');
    v.innerHTML = `<div class="auth">
      <div class="auth-hero">${brand}<p class="auth-tag">${mode === 'register' ? 'נבנה כדי שתמחקו אותו.<br>היכרויות אמיתיות, בחינם.' : 'טוב לראות אותך שוב.'}</p></div>
      <form class="card form" id="auth-form" novalidate>
        <h1 class="h2">${mode === 'register' ? 'יצירת חשבון' : 'התחברות'}</h1>
        ${mode === 'register' ? `<div class="field"><label for="r-name">שם פרטי</label><input id="r-name" autocomplete="given-name" required maxlength="40"></div>` : ''}
        <div class="field"><label for="r-email">אימייל</label><input id="r-email" type="email" autocomplete="email" required dir="ltr"></div>
        <div class="field"><label for="r-pass">סיסמה${mode === 'register' ? ' (לפחות 8 תווים)' : ''}</label><input id="r-pass" type="password" autocomplete="${mode === 'register' ? 'new-password' : 'current-password'}" required dir="ltr"></div>
        ${mode === 'register' ? `
        <div class="two">
          <div class="field"><label for="r-birth">תאריך לידה</label><input id="r-birth" type="date" required></div>
          <div class="field"><label for="r-gender">מגדר</label><select id="r-gender">${OPT.choices.gender.map(g => `<option>${esc(g)}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label for="r-city">עיר</label><input id="r-city" list="r-cities" required maxlength="40"><datalist id="r-cities">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
        <label class="check"><input type="checkbox" id="r-adult"><span>אני בן/בת 18 ומעלה, ואעלה רק תמונות שלי</span></label>` : ''}
        <p class="form-error" id="auth-error" role="alert" hidden></p>
        <button class="btn primary big" id="auth-submit">${mode === 'register' ? 'בואו נתחיל' : 'התחברות'}</button>
        <button type="button" class="btn text" id="auth-switch">${mode === 'register' ? 'כבר יש לי חשבון' : 'עוד אין לי חשבון? להרשמה'}</button>
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
    setTab(ME.photos.length ? 'discover' : 'me');
    refreshLikesCount();
  }
  const refreshLikesCount = () => api('GET', '/api/likes').then(l => { likesCount = l.length; renderTabs(); }).catch(() => {});

  // ---------- Profile rendering ----------
  function avatarHTML(p, cls = '') {
    return `<span class="avatar ${cls}" aria-hidden="true">${p.photos[0] ? `<img src="${esc(p.photos[0].url)}" alt="">` : esc((p.name || '?')[0])}</span>`;
  }
  function photoHTML(p, i, hearts) {
    const ph = p.photos[i];
    if (!ph) return '';
    return `<figure class="photo"><img src="${esc(ph.url)}" alt="תמונה ${i + 1} של ${esc(p.name)}" ${i ? 'loading="lazy"' : ''}>
      ${hearts ? `<button class="like-btn" data-kind="photo" data-i="${i}" aria-label="לייק לתמונה ${i + 1}">${I.heart}</button>` : ''}</figure>`;
  }
  function promptHTML(p, i, hearts) {
    const pr = p.prompts[i];
    if (!pr || !pr.a) return '';
    return `<div class="prompt"><p class="q">${esc(pr.q)}</p><p class="a">${esc(pr.a)}</p>
      ${hearts ? `<button class="like-btn" data-kind="prompt" data-i="${i}" aria-label="לייק לתשובה: ${esc(pr.q)}">${I.heart}</button>` : ''}</div>`;
  }
  function vitalsHTML(p) {
    const val = k => k === 'age' ? p.age : k === 'height' ? (p.height ? p.height + ' ס״מ' : '') : p[k];
    const strip = STRIP.filter(([k]) => val(k));
    const lines = LINES.filter(([k]) => p[k]);
    return `<section class="vitals" aria-label="פרטים">
      <div class="strip">${strip.map(([k, ic]) => `<span title="${LABELS[k] || ''}">${I[ic]}${esc(val(k))}</span>`).join('')}</div>
      ${lines.map(([k, ic]) => `<div class="vline">${I[ic]}<span><span class="sr">${LABELS[k]}: </span>${k === 'wantKids' ? 'רוצה ילדים: ' : ''}${esc(p[k])}</span></div>`).join('')}
    </section>`;
  }
  // Same rhythm as Hinge: photo, vitals, prompt, two photos, prompt, two photos, prompt, photo
  function profileBody(p, hearts) {
    return [photoHTML(p, 0, hearts), vitalsHTML(p), promptHTML(p, 0, hearts), photoHTML(p, 1, hearts), photoHTML(p, 2, hearts),
      promptHTML(p, 1, hearts), photoHTML(p, 3, hearts), photoHTML(p, 4, hearts), promptHTML(p, 2, hearts), photoHTML(p, 5, hearts)].join('');
  }

  function sheet(html, label, cls = '') {
    const L = $('#layer');
    L.innerHTML = `<div class="sheet-bg" id="sheet-bg"><div class="sheet ${cls}" role="dialog" aria-modal="true" aria-label="${esc(label)}"><div class="grab" aria-hidden="true"></div>${html}</div></div>`;
    $('#sheet-bg').onclick = e => { if (e.target.id === 'sheet-bg') closeSheet(); };
    document.addEventListener('keydown', escClose);
    return L;
  }
  function escClose(e) { if (e.key === 'Escape') closeSheet(); }
  const closeSheet = () => { $('#layer').innerHTML = ''; document.removeEventListener('keydown', escClose); };

  // Full profile in a sheet, with optional actions at the bottom
  function viewProfile(p, actions = '') {
    sheet(`<div class="sheet-head"><h2 class="p-name">${esc(p.name)}</h2><button class="icon-btn" id="sheet-close" aria-label="סגירה">${I.x}</button></div>
      <div class="profile">${profileBody(p, false)}</div>${actions ? `<div class="sheet-actions">${actions}</div>` : ''}`, 'פרופיל של ' + p.name, 'tall');
    $('#sheet-close').onclick = closeSheet;
  }

  // ---------- Discover header: city search, city chips, filters button ----------
  function renderHeader() {
    const top = $('#top');
    if (tab !== 'discover') { top.innerHTML = `<div class="topbar">${brand}</div>`; return; }
    const cities = Object.entries(DISC.cityCounts).sort((a, b) => b[1] - a[1]).map(([c]) => c);
    const n = activeFilters();
    top.innerHTML = `<div class="topbar">${brand}
        <button class="filter-btn ${n ? 'on' : ''}" id="open-filters">${I.sliders}<span>סינון</span>${n ? `<b>${n}</b>` : ''}</button></div>
      <div class="search-field">${I.search}
        <input id="city-q" type="search" list="city-list" placeholder="באיזו עיר לחפש?" value="${esc(F.city)}" aria-label="חיפוש לפי עיר" autocomplete="off">
        <button class="clear" id="city-clear" aria-label="ניקוי החיפוש" ${F.city ? '' : 'hidden'}>${I.x}</button>
        <datalist id="city-list">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
      <div class="chips" role="group" aria-label="ערים">
        <button class="chip" data-city="" aria-pressed="${!F.city}">כל הערים</button>
        ${cities.map(c => `<button class="chip" data-city="${esc(c)}" aria-pressed="${F.city === c}">${esc(c)}<span class="n">${DISC.cityCounts[c]}</span></button>`).join('')}
      </div>`;
    let t;
    $('#city-q').addEventListener('input', e => { F.city = e.target.value.trim(); clearTimeout(t); t = setTimeout(refreshDiscover, 300); });
    $('#city-clear').onclick = () => { F.city = ''; refreshDiscover(); };
    $('#open-filters').onclick = openFilters;
    top.querySelectorAll('.chip').forEach(b => b.onclick = () => { F.city = b.dataset.city; refreshDiscover(); });
  }

  function openFilters() {
    const sel = (id, label, opts, cur) => `<div class="field"><label for="${id}">${label}</label><select id="${id}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(cur) === String(v) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>`;
    const any = list => [['', 'הכל'], ...list.map(o => [o, o])];
    sheet(`<h2 class="h2">מה חשוב לך?</h2>
      <div class="filter-group"><div class="filter-title">גיל</div>
        <div class="two">${sel('fl-min', 'מגיל', AGES.map(a => [a, a]), F.ageMin)}${sel('fl-max', 'עד גיל', AGES.map(a => [a, a === 80 ? '80+' : a]), F.ageMax)}</div></div>
      <div class="filter-group">${sel('fl-status', 'סטטוס', any(OPT.choices.status), F.status)}</div>
      <div class="filter-group">${sel('fl-relig', 'דת ואמונה', any(OPT.choices.relig), F.relig)}</div>
      <div class="filter-group">${sel('fl-kids', 'ילדים', KIDS_FILTERS, F.kids)}</div>
      <div class="sheet-actions"><button class="btn ghost" id="fl-clear">ניקוי</button><button class="btn primary" id="fl-apply">הצגת תוצאות</button></div>`, 'סינון');
    const min = $('#fl-min'), max = $('#fl-max');
    // Keep the range valid: "from" never above "to"
    min.onchange = () => { if (+min.value > +max.value) max.value = min.value; };
    max.onchange = () => { if (+max.value < +min.value) min.value = max.value; };
    $('#fl-clear').onclick = () => { Object.assign(F, { ...DEFAULT_F, city: F.city }); closeSheet(); refreshDiscover(); };
    $('#fl-apply').onclick = () => {
      Object.assign(F, { ageMin: +min.value, ageMax: +max.value, status: $('#fl-status').value, relig: $('#fl-relig').value, kids: $('#fl-kids').value });
      closeSheet(); refreshDiscover();
    };
  }

  async function refreshDiscover() {
    store('kesher-filters-v2', F);
    const qs = new URLSearchParams({ city: F.city, ageMin: F.ageMin, ageMax: F.ageMax >= 80 ? 99 : F.ageMax, relig: F.relig, kids: F.kids, status: F.status });
    try { DISC = await api('GET', '/api/discover?' + qs); } catch (e) { toast(e.message); }
    if (tab !== 'discover') return;
    const focused = document.activeElement && document.activeElement.id === 'city-q';
    renderHeader(); renderView();
    if (focused) { const q = $('#city-q'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }

  // ---------- Discover ----------
  function filterSummary() {
    const parts = [];
    if (F.ageMin > 18 || F.ageMax < 80) parts.push(F.ageMax >= 80 ? `מגיל ${F.ageMin}` : `גילאי ${F.ageMin}–${F.ageMax}`);
    if (F.status) parts.push(F.status);
    if (F.relig) parts.push(F.relig);
    if (F.kids) parts.push(KIDS_FILTERS.find(k => k[0] === F.kids)[1]);
    return parts;
  }
  function renderDiscover(v) {
    if (!ME.photos.length) {
      v.innerHTML = `<div class="empty"><div class="empty-art">${I.camera}</div><h2>קודם מעלים תמונה</h2><p>כדי לראות אחרים ולהופיע בחיפוש צריך לפחות תמונה אחת בפרופיל.</p><button class="btn primary" id="go-me">להוספת תמונות</button></div>`;
      $('#go-me').onclick = () => setTab('me'); return;
    }
    const p = DISC.profiles[0];
    const parts = filterSummary();
    if (!p) {
      v.innerHTML = `<div class="empty"><div class="empty-art">${I.search}</div><h2>אין כרגע עוד פרופילים ${F.city ? 'ב' + esc(F.city) : ''}</h2>
        <p>נסו עיר אחרת או הרחיבו את הסינון. אנשים חדשים מצטרפים כל הזמן.</p>
        <div class="row-btns">
          ${F.city ? '<button class="btn primary" id="all-cities">חיפוש בכל הערים</button>' : ''}
          ${activeFilters() ? '<button class="btn ghost" id="clear-filters">ניקוי סינון</button>' : ''}
        </div></div>`;
      const a = $('#all-cities'); if (a) a.onclick = () => { F.city = ''; refreshDiscover(); };
      const c = $('#clear-filters'); if (c) c.onclick = () => { Object.assign(F, { ...DEFAULT_F, city: F.city }); refreshDiscover(); };
      return;
    }
    v.innerHTML = `<p class="result-line">${DISC.total === 1 ? 'פרופיל אחד' : DISC.total + ' פרופילים'} ${F.city ? `ב<b>${esc(F.city)}</b>` : 'בכל הערים'}${parts.length ? ' · ' + esc(parts.join(' · ')) : ''}</p>
      <article class="profile entering" id="card" aria-label="פרופיל של ${esc(p.name)}">
        <div class="p-head"><h1 class="p-name">${esc(p.name)}</h1><button class="icon-btn" id="p-more" aria-label="אפשרויות">${I.more}</button></div>
        ${profileBody(p, true)}
      </article>
      <button class="skip" id="skip" aria-label="דילוג על ${esc(p.name)}">${I.x}</button>`;
    v.querySelectorAll('.like-btn').forEach(b => b.onclick = () => openLike(p, b.dataset.kind, +b.dataset.i));
    $('#skip').onclick = async () => {
      $('#skip').disabled = true;
      api('POST', '/api/skip', { toId: p.id }).catch(e => toast(e.message));
      await leave('left'); nextProfile();
    };
    $('#p-more').onclick = () => {
      sheet(`<h2 class="h2">${esc(p.name)}</h2><button class="btn ghost" id="pm-report">${I.shield} דיווח וחסימה</button><button class="btn text" id="pm-close">סגירה</button>`, 'אפשרויות');
      $('#pm-close').onclick = closeSheet; $('#pm-report').onclick = () => openReport(p, null);
    };
  }
  async function leave(dir) {
    const card = $('#card'); if (!card) return;
    card.classList.add(dir === 'left' ? 'leave-skip' : 'leave-like');
    await wait(260);
  }
  function nextProfile() {
    DISC.profiles.shift(); DISC.total = Math.max(0, DISC.total - 1);
    window.scrollTo({ top: 0 });
    if (!DISC.profiles.length) refreshDiscover(); else renderView();
  }

  // Like sheet: the liked photo or answer, an optional comment, and a choice of like or rose
  function openLike(p, kind, i) {
    const preview = kind === 'photo' ? photoHTML(p, i, false) : `<div class="prompt"><p class="q">${esc(p.prompts[i].q)}</p><p class="a">${esc(p.prompts[i].a)}</p></div>`;
    sheet(`<div class="like-preview">${preview}</div>
      <div class="comment"><textarea id="like-note" maxlength="140" rows="2" placeholder="הוסיפו תגובה"></textarea></div>
      <div class="sheet-actions"><button class="btn rose" id="like-rose">${I.rose} ורד</button><button class="btn primary" id="like-send">${I.heartFill} שליחת לייק</button></div>`, 'שליחת לייק ל' + p.name);
    $('#like-note').focus();
    const send = async rose => {
      $('#like-send').disabled = $('#like-rose').disabled = true;
      try {
        const r = await api('POST', '/api/like', { toId: p.id, kind, idx: i, note: $('#like-note').value, rose });
        closeSheet();
        if (tab === 'discover') { await leave('right'); nextProfile(); }
        if (r.match) showMatch(r.profile, r.matchId); else toast(rose ? `שלחת ורד ל${p.name}` : `הלייק נשלח ל${p.name}`);
      } catch (e) { toast(e.message); $('#like-send').disabled = $('#like-rose').disabled = false; }
    };
    $('#like-send').onclick = () => send(false);
    $('#like-rose').onclick = () => send(true);
  }

  function showMatch(p, matchId) {
    const L = $('#layer');
    L.innerHTML = `<div class="match-screen" role="dialog" aria-modal="true" aria-label="התאמה חדשה">
      <div class="match-inner"><div class="pair">${avatarHTML(ME, 'xl')}${avatarHTML(p, 'xl')}<span class="pair-heart">${I.heartFill}</span></div>
      <h2>יש קשר!</h2><p>גם ${esc(p.name)} אהב/ה אותך. מי כותב/ת ראשון/ה?</p>
      <button class="btn light big" id="m-chat">שליחת הודעה</button><button class="btn text light-text" id="m-later">להמשיך לגלות</button></div></div>`;
    $('#m-later').onclick = closeSheet;
    $('#m-chat').onclick = () => { closeSheet(); chatWith = { id: matchId, other: p }; setTab('matches'); };
  }

  function openReport(p, matchId) {
    sheet(`<h2 class="h2">דיווח על ${esc(p.name)}</h2>
      <p class="muted">אחרי הדיווח לא תראו זה את זה יותר, וכל שיחה ביניכם תיסגר.</p>
      <div class="field"><label for="rep-reason">מה קרה?</label>
        <select id="rep-reason"><option>פרופיל מזויף או תמונות של מישהו אחר</option><option>הטרדה או התנהגות פוגענית</option><option>מתחת לגיל 18</option><option>ספאם או ניסיון הונאה</option><option>אחר</option></select></div>
      <div class="sheet-actions"><button class="btn ghost" id="rep-cancel">ביטול</button><button class="btn danger" id="rep-send">דיווח וחסימה</button></div>`, 'דיווח');
    $('#rep-cancel').onclick = closeSheet;
    $('#rep-send').onclick = async () => {
      try {
        await api('POST', '/api/report', { toId: p.id, reason: $('#rep-reason').value });
        closeSheet(); toast('הדיווח התקבל. לא תראו זה את זה יותר');
        if (matchId) { chatWith = null; stopPoll(); renderView(); } else if (tab === 'discover') nextProfile(); else renderView();
      } catch (e) { toast(e.message); }
    };
  }

  // ---------- Likes you ----------
  async function renderLikes(v) {
    v.innerHTML = '<div class="loading" aria-label="טוען"></div>';
    let likes = [];
    try { likes = await api('GET', '/api/likes'); } catch (e) { v.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
    likesCount = likes.length; renderTabs();
    if (tab !== 'likes') return;
    if (!likes.length) {
      v.innerHTML = `<div class="empty"><div class="empty-art">${I.heart}</div><h2>עוד אין לייקים</h2><p>פרופיל עם כמה תמונות ותשובות לשאלות מקבל הרבה יותר לייקים.</p><button class="btn primary" id="go-me">לשיפור הפרופיל</button></div>`;
      $('#go-me').onclick = () => setTab('me'); return;
    }
    v.innerHTML = `<h1 class="title">אהבו אותך</h1><p class="sub">${likes.length === 1 ? 'מישהו אחד' : likes.length + ' אנשים'} עשו לך לייק. לייק בחזרה יוצר התאמה.</p>
      <div class="like-grid">${likes.map((l, n) => `<button class="like-tile ${l.rose ? 'has-rose' : ''}" data-open="${n}">
        <span class="tile-photo">${l.from.photos[0] ? `<img src="${esc(l.from.photos[0].url)}" alt="">` : ''}${l.rose ? `<span class="tile-rose">${I.rose}</span>` : ''}</span>
        <span class="tile-name">${esc(l.from.name)}, ${l.from.age}</span>
        <span class="tile-note">${l.note ? '"' + esc(l.note) + '"' : l.about.prompt ? 'אהב/ה את התשובה שלך' : l.about.photo ? 'אהב/ה תמונה שלך' : 'אהב/ה את הפרופיל שלך'}</span>
      </button>`).join('')}</div>`;
    v.querySelectorAll('[data-open]').forEach(b => b.onclick = () => {
      const l = likes[+b.dataset.open], p = l.from;
      const what = l.about.prompt ? `אהב/ה את התשובה שלך ל"${esc(l.about.prompt.q)}"` : l.about.photo ? 'אהב/ה תמונה שלך' : 'אהב/ה את הפרופיל שלך';
      viewProfile(p, `<div class="like-context ${l.rose ? 'rose' : ''}">${l.rose ? I.rose : I.heartFill}<div>${what}${l.note ? `<b>"${esc(l.note)}"</b>` : ''}</div></div>
        <div class="row-btns"><button class="btn ghost" id="lk-remove">${I.x} הסרה</button><button class="btn primary" id="lk-back">${I.heartFill} התאמה</button></div>`);
      $('#lk-back').onclick = async () => {
        try { const r = await api('POST', '/api/like', { toId: p.id, kind: 'profile' }); closeSheet(); if (r.match) showMatch(r.profile, r.matchId); renderLikes(v); } catch (e) { toast(e.message); }
      };
      $('#lk-remove').onclick = async () => { await api('POST', `/api/likes/${p.id}/remove`).catch(e => toast(e.message)); closeSheet(); renderLikes(v); };
    });
  }

  // ---------- Matches and chat ----------
  async function renderMatches(v) {
    if (chatWith) return renderChat(v);
    let list = [];
    try { list = await api('GET', '/api/matches'); } catch (e) { v.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
    if (tab !== 'matches' || chatWith) return;
    if (!list.length) {
      v.innerHTML = `<div class="empty"><div class="empty-art">${I.chat}</div><h2>עוד אין התאמות</h2><p>עשו לייק לתמונה או לתשובה בפרופיל. כשהלייק הדדי, השיחה תיפתח כאן.</p><button class="btn primary" id="go-d">לגלות פרופילים</button></div>`;
      $('#go-d').onclick = () => setTab('discover'); return;
    }
    const fresh = list.filter(m => !m.last), talking = list.filter(m => m.last);
    v.innerHTML = `<h1 class="title">התאמות</h1>
      ${fresh.length ? `<p class="sub">התאמות חדשות</p><div class="new-matches">${fresh.map(m => `<button class="nm" data-id="${m.id}">${avatarHTML(m.other, 'lg ring')}<span>${esc(m.other.name)}</span></button>`).join('')}</div>` : ''}
      ${talking.length ? `<p class="sub">שיחות</p><div class="list">${talking.map(m => `<button class="row" data-id="${m.id}">${avatarHTML(m.other, 'lg')}<span class="txt"><span class="t1">${esc(m.other.name)}${!m.last.mine ? '<span class="your-turn">תורך</span>' : ''}</span>
        <span class="t2">${m.last.mine ? 'את/ה: ' : ''}${esc(m.last.body)}</span></span></button>`).join('')}</div>` : ''}`;
    v.querySelectorAll('[data-id]').forEach(b => b.onclick = () => { chatWith = list.find(m => m.id === +b.dataset.id); renderView(); });
  }

  async function renderChat(v) {
    const m = chatWith, p = m.other;
    let lastId = 0, first = true, pulling = null;
    v.innerHTML = `<div class="chat-head"><button class="icon-btn" id="back" aria-label="חזרה להתאמות">${I.back}</button>
      <button class="chat-who" id="chat-profile">${avatarHTML(p)}<span><b>${esc(p.name)}</b><small>${esc(p.city)}${p.job ? ' · ' + esc(p.job) : ''}</small></span></button>
      <button class="icon-btn" id="chat-menu" aria-label="אפשרויות">${I.more}</button></div>
      <div class="chat" id="chat"><div class="bubble ctx">${I.shield} אל תשתפו פרטים כמו כתובת או פרטי בנק עם אנשים שעוד לא פגשתם.</div></div>
      <form class="composer" id="composer"><input id="msg" placeholder="כתיבת הודעה" autocomplete="off" aria-label="הודעה" maxlength="1000"><button class="send" aria-label="שליחה">${I.send}</button></form>`;
    $('#back').onclick = () => { chatWith = null; stopPoll(); renderView(); };
    $('#chat-profile').onclick = () => viewProfile(p);
    $('#chat-menu').onclick = () => {
      sheet(`<h2 class="h2">${esc(p.name)}</h2>
        <button class="btn ghost" id="cm-unmatch">ביטול ההתאמה</button><button class="btn ghost" id="cm-report">${I.shield} דיווח וחסימה</button><button class="btn text" id="cm-close">סגירה</button>`, 'אפשרויות שיחה');
      $('#cm-close').onclick = closeSheet;
      $('#cm-report').onclick = () => openReport(p, m.id);
      $('#cm-unmatch').onclick = async e => {
        const b = e.currentTarget;
        if (b.dataset.armed !== '1') { b.dataset.armed = '1'; b.textContent = 'לחיצה נוספת תבטל את ההתאמה'; b.classList.add('danger'); return; }
        await api('POST', `/api/matches/${m.id}/unmatch`).catch(x => toast(x.message));
        closeSheet(); chatWith = null; stopPoll(); renderView(); toast('ההתאמה בוטלה');
      };
    };
    const chat = $('#chat');
    // One fetch at a time, so overlapping polls never show a message twice
    function pull() { pulling ??= fetchNew().finally(() => { pulling = null; }); return pulling; }
    async function fetchNew() {
      try {
        const msgs = await api('GET', `/api/matches/${m.id}/messages?after=${lastId}`);
        if (!msgs.length || !document.body.contains(chat)) return;
        const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
        chat.insertAdjacentHTML('beforeend', msgs.map(x => `<div class="bubble ${x.mine ? 'me' : 'them'}">${esc(x.body)}</div>`).join(''));
        lastId = msgs[msgs.length - 1].id;
        if (atBottom || first) window.scrollTo({ top: document.body.scrollHeight, behavior: first ? 'auto' : 'smooth' });
        first = false;
      } catch (e) { if (/לא נמצאה/.test(e.message)) { stopPoll(); chatWith = null; toast('השיחה נסגרה'); renderView(); } }
    }
    // Attach the send handler before the first load, so an early tap never submits the form natively
    $('#composer').onsubmit = async e => {
      e.preventDefault();
      const input = $('#msg'), body = input.value.trim(); if (!body) return;
      input.value = '';
      try { await api('POST', `/api/matches/${m.id}/messages`, { body }); await pull(); } catch (x) { toast(x.message); input.value = body; }
      input.focus();
    };
    stopPoll(); pollTimer = setInterval(pull, 3000);
    await pull();
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
    const filled = [me.photos.length >= 3, me.prompts.filter(p => p.a).length >= 3, !!me.job, !!me.status, !!me.relig, !!me.intent].filter(Boolean).length;
    v.innerHTML = `<div class="me-card">${avatarHTML(me, 'xl')}<div><h1 class="title" style="margin:0">${esc(me.name)}, ${me.age}</h1>
        <div class="meter" aria-label="הפרופיל מלא ב-${Math.round(filled / 6 * 100)}%"><span style="width:${Math.round(filled / 6 * 100)}%"></span></div>
        <small class="muted">הפרופיל מלא ב-${Math.round(filled / 6 * 100)}%</small></div>
        <button class="btn ghost small" id="me-preview">צפייה</button></div>
      ${!me.photos.length ? '<p class="notice">כדי להופיע בחיפוש ולראות אחרים, העלו לפחות תמונה אחת שלכם.</p>' : ''}
      <section class="card"><h2 class="h3">התמונות שלי</h2><p class="muted small-text">עד 6 תמונות. הראשונה מופיעה ראשונה בפרופיל ובשיחות.</p>
      <div class="upload">${[0, 1, 2, 3, 4, 5].map(i => {
        const ph = me.photos[i];
        return ph ? `<div class="slot"><img src="${esc(ph.url)}" alt="תמונה ${i + 1}">
            <button class="slot-btn del" data-del="${ph.id}" aria-label="מחיקת תמונה ${i + 1}">${I.x}</button>
            ${i ? `<button class="slot-btn first" data-first="${ph.id}">לראשונה</button>` : '<span class="slot-tag">ראשית</span>'}</div>`
          : `<label class="slot empty-slot" for="ph${i}">${I.plus}<input id="ph${i}" type="file" accept="image/jpeg,image/png,image/webp" aria-label="הוספת תמונה ${i + 1}"></label>`;
      }).join('')}</div></section>
      <form class="form" id="me-form">
        <section class="card"><h2 class="h3">שאלות</h2>
        ${prompts.map((pr, i) => `<div class="prompt-edit"><select id="pq${i}" aria-label="שאלה ${i + 1}">${OPT.prompts.map(o => `<option ${pr.q === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>
          <textarea id="pa${i}" maxlength="200" rows="2" placeholder="התשובה שלך" aria-label="תשובה לשאלה ${i + 1}">${esc(pr.a)}</textarea></div>`).join('')}</section>
        <section class="card"><h2 class="h3">עליי</h2>
          <div class="two">${text('name', 'שם')}<div class="field"><label for="f-city">עיר</label><input id="f-city" list="me-cities" value="${esc(me.city)}"><datalist id="me-cities">${CITIES.map(c => `<option value="${esc(c)}">`).join('')}</datalist></div>
          ${text('job', 'עיסוק', 'למשל מורה')}<div class="field"><label for="f-height">גובה (ס״מ)</label><input id="f-height" type="number" min="120" max="230" value="${me.height || ''}"></div>
          ${choice('gender')}${choice('status')}${text('edu', 'השכלה', 'למשל אוניברסיטת תל אביב')}${text('hometown', 'עיר הולדת')}
          ${choice('relig')}${choice('intent')}${choice('kids')}${choice('wantKids')}${text('langs', 'שפות', 'עברית, אנגלית')}${choice('zodiac')}</div></section>
        <section class="card"><h2 class="h3">הרגלים</h2><div class="two">${HABITS.map(choice).join('')}</div></section>
        <p class="form-error" id="me-error" role="alert" hidden></p>
        <button class="btn primary big sticky-save" id="me-save">שמירה</button>
        <section class="card quiet"><p class="muted small-text">מחובר/ת בתור <span dir="ltr">${esc(me.email)}</span></p>
          <div class="row-btns"><button type="button" class="btn ghost" id="logout">התנתקות</button><button type="button" class="btn text danger-text" id="delete-account">מחיקת החשבון</button></div></section>
      </form>`;
    v.querySelectorAll('input[type=file]').forEach(inp => inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return;
      inp.closest('label').classList.add('busy');
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
      ['gender', 'status', 'edu', 'hometown', 'relig', 'intent', 'kids', 'wantKids', 'langs', 'zodiac', ...HABITS].forEach(k => { body[k] = $('#f-' + k).value; });
      try { const r = await api('PUT', '/api/me', body); ME = { ...ME, ...r }; toast('הפרופיל נשמר'); renderMe(v); }
      catch (x) { err.textContent = x.message; err.hidden = false; }
    };
    $('#me-preview').onclick = () => viewProfile(ME);
    $('#logout').onclick = async () => { await api('POST', '/api/logout').catch(() => {}); ME = null; showAuth('login'); };
    $('#delete-account').onclick = async e => {
      const b = e.currentTarget;
      if (b.dataset.armed !== '1') { b.dataset.armed = '1'; b.textContent = 'לחיצה נוספת תמחק את החשבון, התמונות וההודעות לצמיתות'; return; }
      try { await api('DELETE', '/api/me'); ME = null; showAuth('register'); toast('החשבון נמחק'); } catch (x) { toast(x.message); }
    };
  }

  // ---------- Tabs ----------
  const TABS = [['discover', 'גילוי', I.discover], ['likes', 'אהבו אותך', I.heart], ['matches', 'התאמות', I.chat], ['me', 'פרופיל', I.me]];
  function renderTabs() {
    $('#tabs').innerHTML = TABS.map(([id, label, ic]) => `<button data-tab="${id}" aria-label="${label}" ${tab === id ? 'aria-current="page"' : ''}>${id === 'me' && ME && ME.photos[0] ? avatarHTML(ME, 'tab-avatar') : ic}<span>${label}</span>
      ${id === 'likes' && likesCount ? `<span class="badge">${likesCount}</span>` : ''}</button>`).join('');
    $('#tabs').querySelectorAll('button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
  }
  function setTab(t) {
    if (t !== 'matches') chatWith = null;
    stopPoll(); closeSheet(); tab = t; renderTabs(); renderHeader(); window.scrollTo({ top: 0 });
    if (t === 'discover') { $('#view').innerHTML = '<div class="loading" aria-label="טוען"></div>'; refreshDiscover(); } else renderView();
  }
  function renderView() {
    const v = $('#view');
    ({ discover: renderDiscover, likes: renderLikes, matches: renderMatches, me: renderMe })[tab](v);
  }
  // Keep the "likes you" badge fresh
  setInterval(() => { if (ME && tab !== 'likes') refreshLikesCount(); }, 30000);

  // ---------- Boot ----------
  (async () => {
    try { OPT = await api('GET', '/api/options'); } catch (e) { $('#view').innerHTML = '<p class="form-error">אין חיבור לשרת. נסו לרענן את הדף.</p>'; return; }
    try { await startApp(); } catch (e) { showAuth('register'); }
  })();
})();
