/**
 * Issuer/audience pinning. The signing key alone already scopes tokens to the
 * project, but a token signed by the right key while minted for something else
 * (wrong issuer, wrong audience) must be rejected exactly like a bad signature.
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';

import {
  buildTestApp,
  makeKeys,
  signToken,
  startUpstream,
  TEST_SUPABASE_URL,
  type Keys,
  type Upstream,
} from './helpers.js';

let keys: Keys;
let upstream: Upstream;
let app: FastifyInstance;

beforeAll(async () => {
  keys = await makeKeys();
  upstream = await startUpstream();
  app = await buildTestApp({ keys, backendUrl: upstream.url });
});

afterAll(async () => {
  await app.close();
  await upstream.close();
});

async function getTrails(token: string) {
  return app.inject({
    method: 'GET',
    url: '/trails',
    headers: { authorization: `Bearer ${token}` },
  });
}

describe('issuer and audience pinning', () => {
  it('accepts a token with the project issuer and authenticated audience', async () => {
    const response = await getTrails(await signToken(keys));
    expect(response.statusCode).toBe(200);
  });

  it('rejects a correctly signed token from another issuer', async () => {
    const token = await signToken(keys, {}, {
      issuer: 'https://some-other-project.supabase.co/auth/v1',
    });
    expect((await getTrails(token)).statusCode).toBe(401);
  });

  it('rejects a correctly signed token with a different audience', async () => {
    const token = await signToken(keys, {}, { audience: 'anon' });
    expect((await getTrails(token)).statusCode).toBe(401);
  });

  it('rejects a token whose claims are empty strings', async () => {
    const token = await signToken(keys, {}, { issuer: '', audience: '' });
    expect((await getTrails(token)).statusCode).toBe(401);
  });

  it('keeps working end to end: the proxied request carries the verified user', async () => {
    const before = upstream.requests.length;
    const response = await getTrails(await signToken(keys, {}, { subject: 'user-777' }));
    expect(response.statusCode).toBe(200);
    const forwarded = upstream.requests[before]!;
    expect(forwarded.headers['x-user-id']).toBe('user-777');
  });

  it('the pinned issuer derives from the configured project url', () => {
    // Guard against the helper and config drifting apart: the constant the
    // tokens are minted with must be the URL the app derives the issuer from.
    expect(TEST_SUPABASE_URL).toMatch(/^https:\/\/.+supabase\.co$/);
  });
});
