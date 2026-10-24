// Netlify entry point: the whole Kesher API plus member-only photo serving.
import { getDatabase } from '@netlify/database';
import { getStore } from '@netlify/blobs';
import { createHandler } from '../../lib/app.js';
import { blobPhotos } from '../../lib/photos.js';
import { mailerFromEnv } from '../../lib/mail.js';

let handle;

export default async (req, context) => {
  const env = k => Netlify.env.get(k);
  handle ??= createHandler({
    pool: getDatabase().pool,
    photos: blobPhotos(getStore),
    mailer: mailerFromEnv(env),
    adminEmails: (env('ADMIN_EMAILS') || '').split(','),
    siteUrl: env('URL') || ''
  });
  return handle(req, { ip: context.ip });
};

export const config = { path: ['/api/*', '/uploads/*'] };
