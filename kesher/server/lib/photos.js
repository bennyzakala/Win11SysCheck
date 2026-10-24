// Photo storage: Netlify Blobs in production, a folder on disk for local runs and tests.
import fs from 'node:fs/promises';
import path from 'node:path';

export function blobPhotos(getStore) {
  const store = getStore({ name: 'photos', consistency: 'strong' });
  return {
    put: (name, data, type) => store.set(name, data, { metadata: { type } }),
    get: name => store.get(name, { type: 'arrayBuffer' }),
    remove: name => store.delete(name)
  };
}

export function diskPhotos(dir) {
  const file = name => path.join(dir, path.basename(name));
  return {
    async put(name, data) { await fs.mkdir(dir, { recursive: true }); await fs.writeFile(file(name), Buffer.from(data)); },
    async get(name) { try { return await fs.readFile(file(name)); } catch (e) { return null; } },
    async remove(name) { await fs.rm(file(name), { force: true }); }
  };
}
