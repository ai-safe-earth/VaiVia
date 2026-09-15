/**
 * End-to-end smoke configuration.
 *
 * These tests drive the real stack — Neo4j, backend, gateway, and the
 * production frontend build — so they are a local/pre-deploy check, not part
 * of CI (which is offline by design). See e2e/smoke.spec.ts for the required
 * services and environment variables.
 */

import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // One worker: the tests share a single user account and its conversation
  // history, so parallel runs would race on the same rows.
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    // One UI at every width (owner decision 2026-08-28), so one project: the
    // phone is the hard case — the header has to fit, the composer has to stay
    // on the bottom edge, and the map is a layer over the conversation rather
    // than a second column. A desktop pass would exercise the same DOM at a
    // width where nothing is tight.
    viewport: { width: 360, height: 640 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 3,
  },
  reporter: [['list']],
});
