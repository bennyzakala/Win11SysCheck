import { test, before, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { startServer } from '../server.js';

let app, base, dir;
before(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kesher-test-'));
  app = await startServer({ port: 0, dataDir: dir, memory: true, quiet: true });
  base = `http://127.0.0.1:${app.port}`;
});
after(async () => { await app.close(); fs.rmSync(dir, { recursive: true, force: true }); });

// A tiny client that keeps its own session cookie
function client() {
  let cookie = '';
  return async (method, url, body, { form } = {}) => {
    const headers = { 'X-Kesher': '1' };
    if (cookie) headers.cookie = cookie;
    if (body && !form) headers['content-type'] = 'application/json';
    const r = await fetch(base + url, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) });
    const set = r.headers.get('set-cookie');
    if (set) cookie = set.split(';')[0];
    const type = r.headers.get('content-type') || '';
    return { status: r.status, data: type.includes('json') ? await r.json() : await r.arrayBuffer() };
  };
}
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082', 'hex');
const photoForm = (buf = PNG, type = 'image/png', name = 'a.png') => { const f = new FormData(); f.append('photo', new Blob([buf], { type }), name); return f; };
const person = (email, extra = {}) => ({ email, password: 'secret123', name: email.split('@')[0], birthdate: '1995-05-05', city: 'חיפה', gender: 'אישה', ...extra });

test('registration validates input and blocks under 18', async () => {
  const c = client();
  assert.equal((await c('POST', '/api/register', person('bad'))).status, 400);
  assert.equal((await c('POST', '/api/register', person('kid@x.co', { birthdate: '2015-01-01' }))).status, 400);
  assert.equal((await c('POST', '/api/register', person('short@x.co', { password: '123' }))).status, 400);
  assert.equal((await c('POST', '/api/register', person('ok@x.co', { relig: 'not-a-choice' }))).status, 400);
});

test('requests without the app header are rejected', async () => {
  const r = await fetch(base + '/api/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
  assert.equal(r.status, 403);
});

test('full flow: register, upload, discover by city, like, match, chat', async () => {
  const a = client(), b = client(), c = client();
  assert.equal((await a('POST', '/api/register', person('dana@x.co', { city: 'תל אביב', relig: 'חילוני/ת' }))).status, 201);
  assert.equal((await b('POST', '/api/register', person('yoni@x.co', { city: 'חיפה', gender: 'גבר', kids: 'אין לי ילדים' }))).status, 201);
  assert.equal((await c('POST', '/api/register', person('gal@x.co', { city: 'ירושלים' }))).status, 201);
  assert.equal((await a('POST', '/api/register', person('dana@x.co'))).status, 409);

  // Photos: non-images are rejected, real ones stored and only served to members
  assert.equal((await a('POST', '/api/me/photos', null, { form: photoForm(Buffer.from('hello'), 'image/png') })).status, 400);
  for (const cl of [a, b, c]) assert.equal((await cl('POST', '/api/me/photos', null, { form: photoForm() })).status, 201);
  const me = (await a('GET', '/api/me')).data;
  assert.equal(me.photos.length, 1);
  assert.equal((await fetch(base + me.photos[0].url)).status, 401);
  assert.equal((await b('GET', me.photos[0].url)).status, 200);

  // Profile update with prompts
  const upd = await b('PUT', '/api/me', { job: 'מורה', height: 180, prompts: [{ q: 'יום ראשון מושלם', a: 'ים וקפה' }] });
  assert.equal(upd.status, 200);
  assert.equal(upd.data.prompts[0].a, 'ים וקפה');

  // Discover with city search; email is never exposed
  let d = (await a('GET', '/api/discover?city=' + encodeURIComponent('חיפה'))).data;
  assert.equal(d.total, 1);
  assert.equal(d.profiles[0].name, 'yoni');
  assert.equal(d.profiles[0].email, undefined);
  assert.equal(d.cityCounts['ירושלים'], 1);
  d = (await a('GET', '/api/discover?kids=has')).data;
  assert.equal(d.total, 0);
  // Age range and marital status filters
  assert.equal((await b('PUT', '/api/me', { status: 'גרוש/ה' })).status, 200);
  assert.equal((await a('GET', '/api/discover?status=' + encodeURIComponent('גרוש/ה'))).data.total, 1);
  assert.equal((await a('GET', '/api/discover?status=' + encodeURIComponent('רווק/ה'))).data.total, 0);
  assert.equal((await a('GET', '/api/discover?ageMin=40&ageMax=60')).data.total, 0);
  assert.equal((await a('GET', '/api/discover?ageMin=20&ageMax=40')).data.total, 2);
  assert.equal((await b('PUT', '/api/me', { status: 'נשוי' })).status, 400);

  // A likes B's prompt; B sees it and likes back -> match with the comment as first message
  const yoniId = (await b('GET', '/api/me')).data.id, danaId = me.id;
  let r = await a('POST', '/api/like', { toId: yoniId, kind: 'prompt', idx: 0, note: 'גם אני אוהבת ים' });
  assert.deepEqual(r.data, { match: false });
  const likes = (await b('GET', '/api/likes')).data;
  assert.equal(likes.length, 1);
  assert.equal(likes[0].about.prompt.q, 'יום ראשון מושלם');
  r = await b('POST', '/api/like', { toId: danaId, kind: 'profile' });
  assert.equal(r.data.match, true);
  const matchId = r.data.matchId;
  assert.equal((await a('GET', '/api/discover')).data.profiles.some(p => p.id === yoniId), false);

  assert.equal((await b('POST', `/api/matches/${matchId}/messages`, { body: 'היי!' })).status, 201);
  const msgs = (await a('GET', `/api/matches/${matchId}/messages`)).data;
  assert.deepEqual(msgs.map(m => [m.mine, m.body]), [[true, 'גם אני אוהבת ים'], [false, 'היי!']]);
  assert.equal((await c('GET', `/api/matches/${matchId}/messages`)).status, 404);

  // Report hides people from each other and ends the match
  assert.equal((await a('POST', '/api/report', { toId: yoniId, reason: 'test' })).status, 200);
  assert.equal((await b('GET', '/api/matches')).data.length, 0);

  // Login/logout and account deletion
  assert.equal((await c('POST', '/api/logout')).status, 200);
  assert.equal((await c('GET', '/api/me')).status, 401);
  assert.equal((await c('POST', '/api/login', { email: 'gal@x.co', password: 'wrong-pass' })).status, 401);
  assert.equal((await c('POST', '/api/login', { email: 'gal@x.co', password: 'secret123' })).status, 200);
  assert.equal((await c('DELETE', '/api/me')).status, 200);
  assert.equal((await a('GET', '/api/discover')).data.cityCounts['ירושלים'], undefined);
});
