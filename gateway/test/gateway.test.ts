/**
 * Gateway contract: what must be true for the backend to be safe behind it.
 */

import type { FastifyInstance } from 'fastify';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { GATEWAY_SECRET_HEADER, REQUEST_ID_HEADER } from '../src/app.js';
import {
  buildTestApp,
  makeKeys,
  signToken,
  startUpstream,
  type Keys,
  type Upstream,
} from './helpers.js';

let keys: Keys;
let upstream: Upstream;
let app: FastifyInstance;

beforeAll(async () => {
  keys = await makeKeys();
  upstream = await startUpstream();
});

afterAll(async () => {
  await upstream.close();
});

afterEach(async () => {
  await app?.close();
  upstream.requests.length = 0;
});

async function build(options: Parameters<typeof buildTestApp>[0] extends never ? never : Partial<Parameters<typeof buildTestApp>[0]> = {}) {
  app = await buildTestApp({ keys, backendUrl: upstream.url, ...options });
  return app;
}

describe('authentication', () => {
  it('rejects an unauthenticated request with 401', async () => {
    await build();
    const response = await app.inject({ method: 'POST', url: '/trails/search', payload: {} });
    expect(response.statusCode).toBe(401);
    expect(response.json().error).toBe('unauthorized');
  });

  it('never forwards an unauthenticated request to the backend', async () => {
    await build();
    await app.inject({ method: 'POST', url: '/trails/search', payload: {} });
    expect(upstream.requests).toHaveLength(0);
  });

  it('rejects a malformed authorization header', async () => {
    await build();
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: 'Basic abc123' },
      payload: {},
    });
    expect(response.statusCode).toBe(401);
  });

  it('rejects a token signed by an unknown key', async () => {
    await build();
    const otherKeys = await makeKeys();
    const token = await signToken(otherKeys);
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}` },
      payload: {},
    });
    expect(response.statusCode).toBe(401);
  });

  it('rejects an expired token', async () => {
    await build();
    const token = await signToken(keys, {}, { expiresIn: '-1h' });
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}` },
      payload: {},
    });
    expect(response.statusCode).toBe(401);
  });

  it('accepts a valid token and proxies to the backend', async () => {
    await build();
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}` },
      payload: { activity: 'mtb' },
    });
    expect(response.statusCode).toBe(200);
    expect(upstream.requests).toHaveLength(1);
  });
});

describe('backend trust', () => {
  it('attaches the shared secret and verified user id when proxying', async () => {
    await build();
    const token = await signToken(keys, {}, { subject: 'user-abc' });
    await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}` },
      payload: {},
    });
    const forwarded = upstream.requests[0]!.headers;
    expect(forwarded[GATEWAY_SECRET_HEADER]).toBe('test-shared-secret');
    expect(forwarded['x-user-id']).toBe('user-abc');
  });

  it('never forwards the caller bearer token to the backend', async () => {
    await build();
    const token = await signToken(keys);
    await app.inject({
      method: 'POST',
      url: '/chat',
      headers: { authorization: `Bearer ${token}` },
      payload: { message: 'hi' },
    });
    // The backend trusts x-user-id; it must not need (or receive) raw credentials.
    expect(upstream.requests[0]!.headers['x-user-id']).toBe('user-123');
  });

  it('does not expose unknown paths to the backend', async () => {
    await build();
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'GET',
      url: '/admin/secrets',
      headers: { authorization: `Bearer ${token}` },
    });
    expect(response.statusCode).toBe(404);
    expect(upstream.requests).toHaveLength(0);
  });
});

describe('origin control', () => {
  it('reflects an allowed origin', async () => {
    await build();
    const response = await app.inject({
      method: 'OPTIONS',
      url: '/trails/search',
      headers: {
        origin: 'https://app.example.com',
        'access-control-request-method': 'POST',
      },
    });
    expect(response.headers['access-control-allow-origin']).toBe('https://app.example.com');
  });

  it('does not grant CORS access to an unlisted origin', async () => {
    await build();
    const response = await app.inject({
      method: 'OPTIONS',
      url: '/trails/search',
      headers: {
        origin: 'https://evil.example.com',
        'access-control-request-method': 'POST',
      },
    });
    expect(response.headers['access-control-allow-origin']).toBeUndefined();
  });
});

describe('rate limiting', () => {
  it('returns 429 once the window limit is exceeded', async () => {
    await build({ config: { rateLimit: { max: 3, windowMs: 60_000 } } });
    const token = await signToken(keys);
    const headers = { authorization: `Bearer ${token}` };

    const codes: number[] = [];
    for (let i = 0; i < 5; i += 1) {
      const response = await app.inject({
        method: 'POST',
        url: '/trails/search',
        headers,
        payload: {},
      });
      codes.push(response.statusCode);
    }
    expect(codes.filter((c) => c === 200)).toHaveLength(3);
    expect(codes.filter((c) => c === 429)).toHaveLength(2);
  });

  it('limits per user, so one user cannot exhaust another', async () => {
    await build({ config: { rateLimit: { max: 2, windowMs: 60_000 } } });
    const noisy = await signToken(keys, {}, { subject: 'noisy-user' });
    const quiet = await signToken(keys, {}, { subject: 'quiet-user' });

    for (let i = 0; i < 3; i += 1) {
      await app.inject({
        method: 'POST',
        url: '/trails/search',
        headers: { authorization: `Bearer ${noisy}` },
        payload: {},
      });
    }
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${quiet}` },
      payload: {},
    });
    expect(response.statusCode).toBe(200);
  });
});

describe('LLM quota', () => {
  it('blocks a chat request when the daily budget is spent', async () => {
    await build({
      quotaStore: { tokensUsedToday: async () => 50_000 },
    });
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/chat',
      headers: { authorization: `Bearer ${token}` },
      payload: { message: 'find me a trail' },
    });
    expect(response.statusCode).toBe(429);
    expect(response.json().error).toBe('quota_exceeded');
    expect(upstream.requests).toHaveLength(0); // money never spent
  });

  it('allows a chat request under budget', async () => {
    await build({ quotaStore: { tokensUsedToday: async () => 10 } });
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/chat',
      headers: { authorization: `Bearer ${token}` },
      payload: { message: 'find me a trail' },
    });
    expect(response.statusCode).toBe(200);
  });

  it('does not quota-check non-LLM endpoints', async () => {
    await build({
      quotaStore: { tokensUsedToday: async () => 999_999 },
    });
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}` },
      payload: {},
    });
    expect(response.statusCode).toBe(200);
  });

  it('fails open when the quota store errors', async () => {
    await build({
      quotaStore: {
        tokensUsedToday: async () => {
          throw new Error('postgres unreachable');
        },
      },
    });
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/chat',
      headers: { authorization: `Bearer ${token}` },
      payload: { message: 'hi' },
    });
    expect(response.statusCode).toBe(200);
  });
});

describe('observability', () => {
  it('adopts an incoming request id', async () => {
    await build();
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/trails/search',
      headers: { authorization: `Bearer ${token}`, [REQUEST_ID_HEADER]: 'trace-me-42' },
      payload: {},
    });
    expect(response.headers[REQUEST_ID_HEADER]).toBe('trace-me-42');
    expect(upstream.requests[0]!.headers[REQUEST_ID_HEADER]).toBe('trace-me-42');
  });

  it('mints a request id when none is supplied', async () => {
    await build();
    const response = await app.inject({ method: 'GET', url: '/healthz' });
    expect(String(response.headers[REQUEST_ID_HEADER]).length).toBeGreaterThan(0);
  });

  it('serves gateway health without authentication', async () => {
    await build();
    const response = await app.inject({ method: 'GET', url: '/healthz' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: 'ok', service: 'gateway' });
  });
});

describe('SSE streaming', () => {
  it('passes an event stream through with its content type intact', async () => {
    await build();
    const token = await signToken(keys);
    const response = await app.inject({
      method: 'POST',
      url: '/chat',
      headers: { authorization: `Bearer ${token}` },
      payload: { message: 'find me a trail' },
    });
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toContain('text/event-stream');
    expect(response.body).toContain('data: {"delta":"Lago "}');
    expect(response.body).toContain('data: [DONE]');
  });
});
