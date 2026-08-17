import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Unit tests only. e2e/*.spec.ts is Playwright's — vitest's default glob
    // would otherwise pick it up and fail on @playwright/test imports.
    include: ['test/**/*.test.ts'],
  },
});
