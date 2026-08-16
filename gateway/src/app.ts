/**
 * Gateway application factory.
 *
 * This is the only publicly exposed service (docs/plan.md). Its job is narrow
 * and it must stay that way: authenticate, limit, check origin, check quota,
 * proxy. No business logic, no graph knowledge, no LLM calls.
 */

import cors from '@fastify/cors';
import proxy from '@fastify/http-proxy';
import rateLimit from '@fastify/rate-limit';
import Fastify, { type FastifyInstance, type FastifyRequest } from 'fastify';
import { randomUUID } from 'node:crypto';

import type { Config } from './config.js';
import authPlugin, { type AuthOptions } from './plugins/auth.js';
import quotaPlugin, { type QuotaStore } from './plugins/quota.js';

export const REQUEST_ID_HEADER = 'x-request-id';
export const GATEWAY_SECRET_HEADER = 'x-gateway-secret';

/** Proxied to the backend. Everything else 404s at the gateway. */
const PROXIED_PREFIXES = ['/trails', '/routes', '/chat'] as const;

/** Endpoints that spend LLM budget and therefore need a quota pre-check. */
const QUOTA_PREFIXES = ['/chat'] as const;

export interface BuildOptions {
  config: Config;
  quotaStore?: QuotaStore | null;
  /** Test seam: verify JWTs with a local key instead of remote JWKS. */
  keyResolver?: AuthOptions['keyResolver'];
}

function needsQuotaCheck(url: string): boolean {
  return QUOTA_PREFIXES.some((prefix) => url.startsWith(prefix));
}

export async function buildApp(options: BuildOptions): Promise<FastifyInstance> {
  const { config } = options;

  const app = Fastify({
    logger: config.logLevel === 'silent' ? false : { level: config.logLevel },
    // Adopt the caller's request id when present so a trace spans the whole stack.
    genReqId: (request) => (request.headers[REQUEST_ID_HEADER] as string) ?? randomUUID(),
    trustProxy: true,
  });

  // Browsers may only call us from known origins; credentials are allowed so the
  // frontend can send its Supabase session.
  await app.register(cors, {
    origin: config.allowedOrigins,
    credentials: true,
    methods: ['GET', 'POST', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization', REQUEST_ID_HEADER],
  });

  await app.register(authPlugin, {
    jwksUrl: config.supabaseJwksUrl,
    // Supabase mints tokens with iss <project-url>/auth/v1 and aud
    // "authenticated"; pin both whenever the project URL is configured.
    issuer: config.supabaseUrl ? `${config.supabaseUrl}/auth/v1` : undefined,
    audience: config.supabaseUrl ? 'authenticated' : undefined,
    keyResolver: options.keyResolver,
  });

  // Identify before limiting so limits key on the verified user id. Registration
  // order is hook order: this runs ahead of the rate limiter below.
  app.addHook('onRequest', async (request) => {
    if (request.method === 'OPTIONS') return;
    await app.identify(request);
  });

  // Per-user when authenticated, per-IP otherwise: one abusive IP cannot exhaust
  // a signed-in user's budget, and one account cannot hide behind many IPs.
  await app.register(rateLimit, {
    max: config.rateLimit.max,
    timeWindow: config.rateLimit.windowMs,
    keyGenerator: (request: FastifyRequest) => request.user?.id ?? request.ip ?? 'unknown',
    // The plugin throws whatever this returns, so it must carry statusCode —
    // a plain object surfaces as a 500 and hides the limit from the caller.
    errorResponseBuilder: (_request, context) => {
      const error = new Error(
        `Too many requests. Try again in ${context.after}.`,
      ) as Error & { statusCode: number; error: string; limit: number };
      error.statusCode = context.statusCode ?? 429;
      error.error = 'rate_limited';
      error.limit = context.max;
      return error;
    },
  });

  await app.register(quotaPlugin, {
    store: options.quotaStore ?? null,
    dailyTokenQuota: config.dailyTokenQuotaPerUser,
  });

  // Echo the request id on every response, including errors.
  app.addHook('onSend', async (request, reply, payload) => {
    void reply.header(REQUEST_ID_HEADER, request.id);
    return payload;
  });

  // The gateway's own health — deliberately does NOT probe the backend, so an
  // orchestrator restarting the gateway is never triggered by a backend blip.
  app.get('/healthz', async () => ({ status: 'ok', service: 'gateway' }));

  for (const prefix of PROXIED_PREFIXES) {
    await app.register(proxy, {
      upstream: config.backendUrl,
      prefix,
      rewritePrefix: prefix,
      httpMethods: ['GET', 'POST'],
      // SSE: no response buffering, and a long timeout so streamed chat replies
      // are not cut off mid-stream.
      undici: { bodyTimeout: config.requestTimeoutMs, headersTimeout: config.requestTimeoutMs },
      preHandler: async (request, reply) => {
        await app.authenticate(request, reply);
        if (reply.sent) return;
        if (needsQuotaCheck(request.url)) {
          await app.enforceQuota(request, reply);
        }
      },
      replyOptions: {
        rewriteRequestHeaders: (request, headers) => ({
          ...headers,
          // Prove to the backend this came through the gateway, and pass the
          // cryptographically verified user id — the backend never parses JWTs.
          [GATEWAY_SECRET_HEADER]: config.gatewaySharedSecret,
          [REQUEST_ID_HEADER]: String(request.id),
          'x-user-id': request.user?.id ?? '',
          'x-user-email': request.user?.email ?? '',
        }),
      },
    });
  }

  return app;
}
