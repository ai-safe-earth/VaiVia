/**
 * Supabase JWT verification.
 *
 * Tokens are verified against Supabase's JWKS endpoint (remote key set, cached
 * and auto-rotating). Verification is cryptographic — the gateway never trusts
 * a client-supplied user id. The verified `sub` becomes request.user.id and is
 * what rate limits, quotas, and the backend's ownership checks key on.
 */

import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';
import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from 'jose';

export interface AuthUser {
  id: string;
  email?: string;
  role?: string;
}

declare module 'fastify' {
  interface FastifyRequest {
    user?: AuthUser;
  }
  interface FastifyInstance {
    /** Populate request.user if a valid token is present. Never rejects. */
    identify: (request: FastifyRequest) => Promise<void>;
    /** Require an authenticated user; replies 401 when absent. */
    authenticate: (request: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

export interface AuthOptions {
  jwksUrl: string;
  /** Pin the token issuer (Supabase: `<project-url>/auth/v1`). Unset = not checked. */
  issuer?: string;
  /** Pin the token audience (Supabase: `authenticated`). Unset = not checked. */
  audience?: string;
  /** Injected in tests so verification runs without a network call. */
  keyResolver?: JWTVerifyGetKey;
}

function bearerToken(header: string | undefined): string | null {
  if (!header) return null;
  const [scheme, token] = header.split(' ');
  if (!token || scheme?.toLowerCase() !== 'bearer') return null;
  return token.trim() || null;
}

const authPlugin: FastifyPluginAsync<AuthOptions> = async (app, options) => {
  if (!options.keyResolver && !options.jwksUrl) {
    // config.ts only *requires* this in production, so outside production the
    // empty string used to reach `new URL` and kill boot with a bare
    // ERR_INVALID_URL that named neither the variable nor the gateway.
    throw new Error(
      'SUPABASE_JWT_JWKS_URL is not set — the gateway cannot verify Supabase tokens',
    );
  }

  const resolveKey: JWTVerifyGetKey =
    options.keyResolver ?? createRemoteJWKSet(new URL(options.jwksUrl));

  // Defence in depth beyond the project-specific signing key: a token signed by
  // the right key but minted for another purpose (wrong issuer or audience) is
  // rejected the same way a bad signature is.
  const verifyOptions = {
    ...(options.issuer ? { issuer: options.issuer } : {}),
    ...(options.audience ? { audience: options.audience } : {}),
  };

  // Identification runs early (before rate limiting) so limits can key on the
  // verified user id. It never rejects: enforcement is `authenticate`'s job,
  // which keeps unauthenticated traffic inside the IP-keyed rate limit instead
  // of bypassing it with an early 401.
  app.decorate('identify', async (request: FastifyRequest) => {
    const token = bearerToken(request.headers.authorization);
    if (!token) return;

    try {
      const { payload } = await jwtVerify(token, resolveKey, verifyOptions);
      if (!payload.sub) return;
      request.user = {
        id: payload.sub,
        email: typeof payload.email === 'string' ? payload.email : undefined,
        role: typeof payload.role === 'string' ? payload.role : undefined,
      };
    } catch (error) {
      request.log.warn({ err: (error as Error).message }, 'jwt verification failed');
    }
  });

  app.decorate('authenticate', async (request: FastifyRequest, reply: FastifyReply) => {
    if (!request.user) {
      await request.server.identify(request);
    }
    if (!request.user) {
      await reply
        .code(401)
        .send({ error: 'unauthorized', message: 'a valid bearer token is required' });
    }
  });
};

export default fp(authPlugin, { name: 'auth' });
