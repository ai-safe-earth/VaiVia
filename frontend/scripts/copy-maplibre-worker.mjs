// Copies MapLibre's web worker next to the app, before every dev and build.
//
// maplibre-gl 6 is ESM-only and loads its worker as a real module URL. Under
// Next.js the bundler emits the worker without the sibling module it imports,
// so the worker fails to load: raster tiles still draw (they need no worker)
// but every GeoJSON line — the route — silently does not. The fix MapLibre
// documents for bundlers: serve both files from our own origin and point
// setWorkerUrl at them (components/MapView.tsx).
//
// Copied from the INSTALLED package each time, so the worker always matches
// the library version. public/maplibre/ is gitignored for the same reason.

import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FILES = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs'];

const dist = dirname(fileURLToPath(import.meta.resolve('maplibre-gl')));
const target = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'maplibre');

mkdirSync(target, { recursive: true });
for (const file of FILES) {
  const source = join(dist, file);
  if (!existsSync(source)) {
    // Loud on purpose: a missing worker is a map without route lines.
    throw new Error(`maplibre-gl ships no ${file} in ${dist} — check the worker setup`);
  }
  copyFileSync(source, join(target, file));
}
console.log(`maplibre worker copied to public/maplibre (${FILES.join(', ')})`);
