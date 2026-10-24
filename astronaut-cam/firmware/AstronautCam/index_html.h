// Control web-app served by the ESP32 at "/".
// Open it on a phone and use "Add to Home Screen" to get an app icon.
#pragma once

static const char INDEX_HTML[] = R"rawliteral(<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0b1026">
<title>Astronaut Cam</title>
<style>
  :root { --bg:#0b1026; --panel:#151c3d; --line:#2a3566; --text:#e8ecff; --muted:#8e98c8; --accent:#ffb020; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:radial-gradient(ellipse at top,#1a2350,var(--bg) 70%); color:var(--text);
         font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif; min-height:100vh; }
  header { display:flex; align-items:center; justify-content:space-between; padding:10px 16px; }
  header h1 { font-size:18px; margin:0; }
  #dot { width:10px; height:10px; border-radius:50%; background:#d33; display:inline-block; margin-inline-start:8px; }
  #dot.ok { background:#3c6; }
  main { max-width:900px; margin:0 auto; padding:0 16px 24px; display:grid; gap:14px; }
  @media (min-width:800px) { main { grid-template-columns:1.6fr 1fr; align-items:start; } }
  .video { position:relative; background:#000; border-radius:16px; overflow:hidden; aspect-ratio:4/3; border:1px solid var(--line); }
  .video img { width:100%; height:100%; object-fit:contain; display:block; }
  .hud { position:absolute; bottom:8px; inset-inline-start:10px; font:12px monospace; color:#fff; text-shadow:0 0 4px #000; direction:ltr; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:14px; display:grid; gap:12px; }
  #joy { width:220px; height:220px; margin:0 auto; border-radius:50%; position:relative; touch-action:none;
         background:radial-gradient(circle,#202a58 0,#121838 70%); border:2px solid var(--line); }
  #knob { width:76px; height:76px; border-radius:50%; position:absolute; left:72px; top:72px;
          background:radial-gradient(circle at 35% 35%,#ffd27a,var(--accent)); box-shadow:0 4px 16px #0008; }
  .row { display:flex; gap:8px; flex-wrap:wrap; }
  button, select { flex:1; min-height:44px; border-radius:12px; border:1px solid var(--line); background:#1f2852;
                   color:var(--text); font-size:15px; }
  button:active { background:#2c3874; }
  button.on { background:var(--accent); color:#1a1200; }
  label { font-size:13px; color:var(--muted); display:grid; gap:4px; }
  input[type=range] { width:100%; accent-color:var(--accent); direction:ltr; }
</style>
</head>
<body>
<header><h1>👨‍🚀 Astronaut Cam <span id="dot"></span></h1><span id="rssi" style="color:var(--muted);font-size:13px"></span></header>
<main>
  <div class="video"><img id="cam" alt="video"><div class="hud" id="hud"></div></div>
  <div class="panel">
    <div id="joy"><div id="knob"></div></div>
    <label>סיבוב ראש (Pan) <input id="pan" type="range"></label>
    <label>הטיית מצלמה (Tilt) <input id="tilt" type="range"></label>
    <div class="row" dir="ltr">
      <button data-p="180">⟵ שמאל</button><button id="center">מרכז</button><button data-p="0">ימין ⟶</button>
    </div>
    <div class="row">
      <button id="flash">💡 פנס</button><button id="snap">📸 צילום</button>
    </div>
    <div class="row">
      <select id="res">
        <option value="5">QVGA 320x240</option><option value="8" selected>VGA 640x480</option>
        <option value="9">SVGA 800x600</option><option value="10">XGA 1024x768</option><option value="11">HD 1280x720</option>
      </select>
      <button id="flip">↕ היפוך</button><button id="mirror">↔ מראה</button>
    </div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const host = location.hostname;
let st = {pan:90, tilt:90, panMin:0, panMax:180, tiltMin:55, tiltMax:125};
let busy = false, pending = null;

function apply(s) {
  st = Object.assign(st, s);
  $('pan').min = st.panMin; $('pan').max = st.panMax; $('pan').value = st.pan;
  $('tilt').min = st.tiltMin; $('tilt').max = st.tiltMax; $('tilt').value = st.tilt;
  $('flash').classList.toggle('on', !!st.flash);
  $('flip').classList.toggle('on', !!st.vflip);
  $('mirror').classList.toggle('on', !!st.hmirror);
  if (st.framesize !== undefined) $('res').value = st.framesize;
  $('hud').textContent = `PAN ${st.pan}°  TILT ${st.tilt}°`;
  $('rssi').textContent = st.rssi ? `WiFi ${st.rssi} dBm` : 'Hotspot';
  $('dot').classList.add('ok');
}

// One request in flight at a time; newest command wins.
async function send(url) {
  if (busy) { pending = url; return; }
  busy = true;
  try { const r = await fetch(url, {cache:'no-store'}); apply(await r.json()); }
  catch (e) { $('dot').classList.remove('ok'); }
  busy = false;
  if (pending) { const u = pending; pending = null; send(u); }
}
const move = (p, t) => send(`/move?pan=${Math.round(p)}&tilt=${Math.round(t)}`);

// ---- joystick: deflection = speed ----
const joy = $('joy'), knob = $('knob');
let jx = 0, jy = 0, dragging = false, timer = null;
function setKnob(x, y) { knob.style.transform = `translate(${x * 72}px, ${y * 72}px)`; }
function joyPos(e) {
  const r = joy.getBoundingClientRect();
  let x = (e.clientX - r.left - r.width / 2) / (r.width / 2);
  let y = (e.clientY - r.top - r.height / 2) / (r.height / 2);
  const m = Math.hypot(x, y); if (m > 1) { x /= m; y /= m; }
  jx = x; jy = y; setKnob(x, y);
}
joy.addEventListener('pointerdown', e => {
  dragging = true; joy.setPointerCapture(e.pointerId); joyPos(e);
  timer = setInterval(() => {
    const p = Math.min(st.panMax, Math.max(st.panMin, st.pan - jx * 6));   // right on screen = turn right
    const t = Math.min(st.tiltMax, Math.max(st.tiltMin, st.tilt - jy * 4)); // up on screen = look up
    if (p !== st.pan || t !== st.tilt) { st.pan = Math.round(p); st.tilt = Math.round(t); move(st.pan, st.tilt); }
  }, 80);
});
joy.addEventListener('pointermove', e => { if (dragging) joyPos(e); });
const release = () => { dragging = false; clearInterval(timer); jx = jy = 0; setKnob(0, 0); };
joy.addEventListener('pointerup', release);
joy.addEventListener('pointercancel', release);

// ---- other controls ----
$('pan').oninput = e => move(e.target.value, st.tilt);
$('tilt').oninput = e => move(st.pan, e.target.value);
$('center').onclick = () => send('/move?center=1');
document.querySelectorAll('[data-p]').forEach(b => b.onclick = () => move(b.dataset.p, st.tilt));
$('flash').onclick = () => send(`/flash?on=${st.flash ? 0 : 1}`);
$('flip').onclick = () => send(`/cam?var=vflip&val=${st.vflip ? 0 : 1}`);
$('mirror').onclick = () => send(`/cam?var=hmirror&val=${st.hmirror ? 0 : 1}`);
$('res').onchange = e => { send(`/cam?var=framesize&val=${e.target.value}`); setTimeout(startStream, 400); };
$('snap').onclick = () => {
  const a = document.createElement('a');
  a.href = `/capture?t=${Date.now()}`; a.download = `astronaut-${Date.now()}.jpg`; a.click();
};
document.addEventListener('keydown', e => {
  const k = {ArrowLeft:[6,0], ArrowRight:[-6,0], ArrowUp:[0,4], ArrowDown:[0,-4]}[e.key];
  if (k) { e.preventDefault(); send(`/move?dp=${k[0]}&dt=${k[1]}`); }
});

function startStream() { $('cam').src = `http://${host}:81/stream?t=${Date.now()}`; }
$('cam').onerror = () => setTimeout(startStream, 1500);
send('/status');
startStream();
</script>
</body>
</html>)rawliteral";
