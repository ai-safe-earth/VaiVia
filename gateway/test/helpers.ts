/**
 * Test harness: a real RSA key pair signs real JWTs (no mocked verification —
 * the auth path under test is the production one), plus a stub upstream that
 * records what the gateway forwarded.
 */

import { createServer, type Server } from 'node:http';
import { type AddressInfo } from 'node:net';
import { exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey, type KeyLike } from 'jose';
import { createLocalJWKSet } from 'jose';

import { buildApp } from '../src/app.js';
import { loadConfig, type Config } from '../src/config.js';
import type { QuotaStore } from '../src/plugins/quota.js';

export interface Keys {
  privateKey: KeyLike;
  keyResolver: JWTVerifyGetKey;
}

export async function makeKeys(): Promise<Keys> {
  const { privateKey, publicKey } = await generateKeyPair('RS256');
  const jwk = await exportJWK(publicKey);
  jwk.kid = 'test-key';
  jwk.alg = 'RS256';
  return { privateKey, keyResolver: createLocalJWKSet({ keys: [jwk] }) };
}

/** Must match testConfig's SUPABASE_URL — the app pins the issuer from it. */
export const TEST_SUPABASE_URL = 'https://test-project.supabase.co';

export async function signToken(
  keys: Keys,
  claims: Record<string, unknown> = {},
  {
    expiresIn = '1h',
    subject = 'user-123',
    // Defaults mirror what Supabase actually mints, so every existing test
    // exercises the pinned-claims path; negative tests override these.
    issuer = `${TEST_SUPABASE_URL}/auth/v1`,
    audience = 'authenticated',
  }: { expiresIn?: string; subject?: string; issuer?: string; audience?: string } = {},
): Promise<string> {
  return new SignJWT({ email: 'rider@example.com', ...claims })
    .setProtectedHeader({ alg: 'RS256', kid: 'test-key' })
    .setSubject(subject)
    .setIssuer(issuer)
    .setAudience(audience)
    .setIssuedAt()
    .setExpirationTime(expiresIn)
    .sign(keys.privateKey);
}

export interface Upstream {
  url: string;
  requests: { url: string; headers: Record<string, string | string[] | undefined> }[];
  close: () => Promise<void>;
}

/** Minimal backend stub. /chat streams SSE so proxy streaming is exercised. */
export async function startUpstream(): Promise<Upstream> {
  const requests: Upstream['requests'] = [];

  const server: Server = createServer((req, res) => {
    requests.push({ url: req.url ?? '', headers: req.headers });

    if (req.url?.startsWith('/chat')) {
      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });
      res.write('data: {"delta":"Lago "}\n\n');
      res.write('data: {"delta":"Loop"}\n\n');
      res.write('data: [DONE]\n\n');
      res.end();
      return;
    }

    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true, path: req.url }));
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    requests,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve())),
      ),
  };
}

export function testConfig(overrides: Partial<Config> = {}): Config {
  return {
    ...loadConfig({
      NODE_ENV: 'test',
      GATEWAY_SHARED_SECRET: 'test-shared-secret',
      SUPABASE_URL: TEST_SUPABASE_URL,
      SUPABASE_JWT_JWKS_URL: 'http://localhost/jwks',
      ALLOWED_ORIGINS: 'http://localhost:3000,https://app.example.com',
      LOG_LEVEL: 'silent',
    }),
    ...overrides,
  };
}

export async function buildTestApp(options: {
  keys: Keys;
  backendUrl: string;
  quotaStore?: QuotaStore | null;
  config?: Partial<Config>;
}) {
  return buildApp({
    config: testConfig({ backendUrl: options.backendUrl, ...options.config }),
    keyResolver: options.keys.keyResolver,
    quotaStore: options.quotaStore ?? null,
  });
}
