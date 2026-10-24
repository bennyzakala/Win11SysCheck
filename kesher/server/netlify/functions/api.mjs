// Netlify entry point: the whole Kesher API plus member-only photo serving.
import { getDatabase } from '@netlify/database';
import { getStore } from '@netlify/blobs';
import { createHandler } from '../../lib/app.js';
import { blobPhotos } from '../../lib/photos.js';

let handle;

export default async (req, context) => {
  handle ??= createHandler({ pool: getDatabase().pool, photos: blobPhotos(getStore) });
  return handle(req, { ip: context.ip });
};

export const config = { path: ['/api/*', '/uploads/*'] };
