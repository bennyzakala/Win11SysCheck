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
const person = (email, extra = {}) => ({ email, password: 'secret123', name: email.split('@')[0], birthdate: '1995-05-05', city: 'חיפה', gender: 'אישה', acceptTerms: true, ...extra });

test('registration validates input and blocks under 18', async () => {
  const c = client();
  assert.equal((await c('POST', '/api/register', person('bad'))).status, 400);
  assert.equal((await c('POST', '/api/register', person('kid@x.co', { birthdate: '2015-01-01' }))).status, 400);
  assert.equal((await c('POST', '/api/register', person('short@x.co', { password: '123' }))).status, 400);
  assert.equal((await c('POST', '/api/register', person('ok@x.co', { relig: 'not-a-choice' }))).status, 400);
  assert.equal((await c('POST', '/api/register', person('noterms@x.co', { acceptTerms: false }))).status, 400);
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

// A second server with email turned on, to cover verification, password reset and the reports review
test('email verification, password reset and admin review of reports', async () => {
  const sent = [];
  const mailDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kesher-mail-'));
  const mailApp = await startServer({ port: 0, dataDir: mailDir, memory: true, quiet: true,
    mailer: { send: async m => { sent.push(m); } }, adminEmails: ['boss@x.co'], siteUrl: 'https://kesher.example' });
  const saved = base; base = `http://127.0.0.1:${mailApp.port}`;
  const linkToken = (to, kind) => new RegExp(`\\?${kind}=([a-f0-9]{64})`).exec(sent.filter(m => m.to === to).at(-1).text)[1];
  try {
    const a = client(), b = client(), boss = client();
    assert.equal((await a('POST', '/api/register', person('ann@x.co'))).status, 201);
    let me = (await a('GET', '/api/me')).data;
    assert.equal(me.verifyRequired, true);
    assert.equal(me.emailVerified, false);
    assert.match(sent[0].text, /https:\/\/kesher\.example\/\?verify=/);
    assert.equal((await a('GET', '/api/discover')).status, 403);
    assert.equal((await a('POST', '/api/verify', { token: 'f'.repeat(64) })).status, 400);
    assert.equal((await a('POST', '/api/verify', { token: linkToken('ann@x.co', 'verify') })).status, 200);
    assert.equal((await a('GET', '/api/me')).data.emailVerified, true);
    assert.equal((await a('GET', '/api/discover')).status, 200);
    await a('POST', '/api/me/photos', null, { form: photoForm() });

    // Unverified people stay out of other members' results
    await b('POST', '/api/register', person('ben@x.co', { gender: 'גבר' }));
    await b('POST', '/api/me/photos', null, { form: photoForm() });
    assert.equal((await a('GET', '/api/discover')).data.total, 0);
    await b('POST', '/api/verify', { token: linkToken('ben@x.co', 'verify') });
    assert.equal((await a('GET', '/api/discover')).data.total, 1);

    // Password reset: same answer for unknown addresses, token works once, old sessions end
    assert.equal((await b('POST', '/api/password/forgot', { email: 'nobody@x.co' })).status, 200);
    assert.equal((await b('POST', '/api/password/forgot', { email: 'ben@x.co' })).status, 200);
    const token = linkToken('ben@x.co', 'reset');
    const other = client();
    assert.equal((await other('POST', '/api/password/reset', { token, password: 'short' })).status, 400);
    assert.equal((await other('POST', '/api/password/reset', { token, password: 'brand-new-pass' })).status, 200);
    assert.equal((await other('POST', '/api/password/reset', { token, password: 'brand-new-pass' })).status, 400);
    assert.equal((await b('GET', '/api/me')).status, 401);
    assert.equal((await b('POST', '/api/login', { email: 'ben@x.co', password: 'brand-new-pass' })).status, 200);

    // Reports: only admins see them; banning signs the person out and hides them
    await boss('POST', '/api/register', person('boss@x.co'));
    const benId = (await b('GET', '/api/me')).data.id;
    assert.equal((await a('POST', '/api/report', { toId: benId, reason: 'פרופיל מזויף' })).status, 200);
    assert.equal((await a('GET', '/api/admin/reports')).status, 403);
    assert.equal((await boss('GET', '/api/admin/reports')).status, 403); // not verified yet
    await boss('POST', '/api/verify', { token: linkToken('boss@x.co', 'verify') });
    assert.equal((await boss('GET', '/api/me')).data.isAdmin, true);
    const reports = (await boss('GET', '/api/admin/reports')).data;
    assert.equal(reports.length, 1);
    assert.equal(reports[0].target.name, 'ben');
    assert.equal((await boss('POST', `/api/admin/reports/${reports[0].id}`, { action: 'ban' })).status, 200);
    assert.equal((await b('GET', '/api/me')).status, 401);
    assert.equal((await b('POST', '/api/login', { email: 'ben@x.co', password: 'brand-new-pass' })).status, 403);
    assert.equal((await boss('GET', '/api/discover')).data.total, 1); // ann only
    assert.equal((await boss('GET', '/api/admin/reports')).data.length, 0);
  } finally {
    base = saved;
    await mailApp.close();
    fs.rmSync(mailDir, { recursive: true, force: true });
  }
});
