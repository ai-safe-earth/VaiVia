import path from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    // The app's `@/` alias (tsconfig paths), so component tests render the
    // REAL components. A test that re-implements a component's rules is
    // worth nothing against a race — the lesson drawn-turn.test.ts records.
    alias: { '@': path.resolve(__dirname) },
  },
  // tsconfig says jsx: preserve (Next transforms it); tests run under node,
  // so the transform happens here.
  esbuild: { jsx: 'automatic' },
  test: {
    // Unit tests only. e2e/*.spec.ts is Playwright's — vitest's default glob
    // would otherwise pick it up and fail on @playwright/test imports.
    // Component tests are .tsx and declare jsdom per file.
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
