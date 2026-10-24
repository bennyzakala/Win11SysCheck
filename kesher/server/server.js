// Local / self-hosted Kesher server. Serves the app from public/ and the API from lib/app.js.
// Database: DATABASE_URL (any Postgres) if set, otherwise an embedded Postgres-compatible
// database stored in DATA_DIR/db. Photos are stored in DATA_DIR/uploads.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { NetlifyDB, applyMigrations } from '@netlify/database-dev';
import { createHandler } from './lib/app.js';
import { diskPhotos } from './lib/photos.js';
import { mailerFromEnv } from './lib/mail.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const MIGRATIONS = path.join(here, 'netlify/database/migrations');
const PUBLIC = path.join(here, 'public');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

async function openDatabase(dataDir, { memory = false, quiet = false } = {}) {
  if (process.env.DATABASE_URL) {
    const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
    const exec = { exec: s => pool.query(s), query: (s, p) => pool.query(s, p),
      async transaction(fn) {
        const cl = await pool.connect();
        try { await cl.query('BEGIN'); const r = await fn({ exec: s => cl.query(s), query: (s, p) => cl.query(s, p) }); await cl.query('COMMIT'); return r; }
        catch (e) { await cl.query('ROLLBACK'); throw e; } finally { cl.release(); }
      } };
    await applyMigrations(exec, MIGRATIONS);
    return { pool, stop: () => pool.end() };
  }
  const db = new NetlifyDB({ directory: memory ? undefined : path.join(dataDir, 'db'), logger: quiet ? () => {} : console.log });
  const url = await db.start();
  await db.applyMigrations(MIGRATIONS);
  const pool = new pg.Pool({ connectionString: url, max: 1 });
  return { pool, stop: async () => { await pool.end(); await db.stop(); } };
}

// Converts between Node's request/response and the web Request/Response the handler uses
function toWebRequest(req) {
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (v !== undefined) headers.set(k, Array.isArray(v) ? v.join(', ') : v);
  const hasBody = !['GET', 'HEAD'].includes(req.method);
  return new Request(`http://${req.headers.host || 'localhost'}${req.url}`, { method: req.method, headers, body: hasBody ? Readable.toWeb(req) : undefined, duplex: hasBody ? 'half' : undefined });
}
async function sendWebResponse(res, response) {
  const headers = {};
  response.headers.forEach((v, k) => { if (k !== 'set-cookie') headers[k] = v; });
  const cookies = response.headers.getSetCookie();
  if (cookies.length) headers['set-cookie'] = cookies;
  res.writeHead(response.status, headers);
  res.end(Buffer.from(await response.arrayBuffer()));
}

async function serveStatic(req, res) {
  const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = path.join(PUBLIC, rel.endsWith('/') ? rel + 'index.html' : rel);
  if (!file.startsWith(PUBLIC)) { res.writeHead(404).end(); return; }
  try {
    const data = await fs.readFile(file);
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream', 'x-content-type-options': 'nosniff' }).end(data);
  } catch (e) { res.writeHead(404, { 'content-type': 'text/plain' }).end('Not found'); }
}

export async function startServer({ port = 3000, dataDir = path.join(here, 'data'), memory = false, quiet = false,
  mailer = mailerFromEnv(k => process.env[k]), adminEmails = (process.env.ADMIN_EMAILS || '').split(','), siteUrl = process.env.SITE_URL || '' } = {}) {
  const { pool, stop } = await openDatabase(dataDir, { memory, quiet });
  const handle = createHandler({ pool, photos: diskPhotos(path.join(dataDir, 'uploads')), mailer, adminEmails, siteUrl });
  const server = http.createServer(async (req, res) => {
    try {
      if (req.url.startsWith('/api/') || req.url.startsWith('/uploads/')) {
        const ip = process.env.TRUST_PROXY ? String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() : req.socket.remoteAddress;
        await sendWebResponse(res, await handle(toWebRequest(req), { ip }));
      } else await serveStatic(req, res);
    } catch (e) { console.error(e); if (!res.headersSent) res.writeHead(500).end(); }
  });
  await new Promise(r => server.listen(port, r));
  return { server, port: server.address().port, close: async () => { await new Promise(r => server.close(r)); await stop(); } };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { port } = await startServer({ port: Number(process.env.PORT) || 3000, dataDir: process.env.DATA_DIR || undefined });
  console.log(`Kesher is running on http://localhost:${port}`);
}
