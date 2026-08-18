/**
 * The no-auth shortcut exists for local development while Supabase is down.
 * These tests pin the two properties that keep it from becoming a liability:
 * it works, and it cannot be switched on in production.
 */

import { describe, expect, it } from 'vitest';

import { buildApp } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { DEV_USER } from '../src/plugins/auth.js';

const baseEnv = {
  GATEWAY_SHARED_SECRET: 'test-secret',
  SUPABASE_JWT_JWKS_URL: '',
  NODE_ENV: 'development',
  LOG_LEVEL: 'silent',
  // Discard port: nothing can be listening, so a proxied request fails at
  // connect rather than reaching whatever happens to be on the dev machine's
  // backend port. Without this the assertion below passes or fails depending
  // on whether someone left the backend running.
  BACKEND_URL: 'http://127.0.0.1:9',
} as NodeJS.ProcessEnv;

describe('GATEWAY_DEV_NO_AUTH', () => {
  it('refuses to start in production', () => {
    expect(() =>
      loadConfig({ ...baseEnv, NODE_ENV: 'production', GATEWAY_DEV_NO_AUTH: 'true' }),
    ).toThrow(/refusing to start/i);
  });

  it('is off unless explicitly set to the string "true"', () => {
    expect(loadConfig(baseEnv).devNoAuth).toBe(false);
    expect(loadConfig({ ...baseEnv, GATEWAY_DEV_NO_AUTH: '1' }).devNoAuth).toBe(false);
    expect(loadConfig({ ...baseEnv, GATEWAY_DEV_NO_AUTH: 'yes' }).devNoAuth).toBe(false);
    expect(loadConfig({ ...baseEnv, GATEWAY_DEV_NO_AUTH: 'true' }).devNoAuth).toBe(true);
  });

  it('boots without a JWKS url, which would otherwise throw', async () => {
    const config = loadConfig({ ...baseEnv, GATEWAY_DEV_NO_AUTH: 'true' });
    const app = await buildApp({ config });
    await app.ready();
    const response = await app.inject({ method: 'GET', url: '/healthz' });
    expect(response.statusCode).toBe(200);
    await app.close();
  });

  it('serves a proxied route with no token instead of 401ing', async () => {
    const config = loadConfig({ ...baseEnv, GATEWAY_DEV_NO_AUTH: 'true' });
    const app = await buildApp({ config });
    await app.ready();
    // Reaching the (dead) upstream at all proves authenticate let the request
    // through: a gateway 401 would never have attempted the connection.
    const response = await app.inject({ method: 'GET', url: '/trails' });
    expect(response.statusCode).not.toBe(401);
    expect(response.statusCode).toBeGreaterThanOrEqual(500);
    await app.close();
  });

  it('still 401s a proxied route when auth is on and no token is sent', async () => {
    const config = loadConfig({
      ...baseEnv,
      SUPABASE_JWT_JWKS_URL: 'https://example.test/keys',
    });
    const app = await buildApp({ config });
    await app.ready();
    const response = await app.inject({ method: 'GET', url: '/trails' });
    expect(response.statusCode).toBe(401);
    await app.close();
  });

  it('uses a dev id that cannot collide with a Supabase uuid', () => {
    expect(DEV_USER.id).not.toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });
});
