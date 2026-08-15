/**
 * Environment configuration. Fails fast on missing required values in
 * production — a gateway running without its shared secret or JWKS URL is a
 * security hole, not a degraded service.
 */

export interface Config {
  port: number;
  host: string;
  logLevel: string;
  nodeEnv: string;
  /** Origins allowed to call the gateway from a browser. */
  allowedOrigins: string[];
  backendUrl: string;
  /** Proves to the backend that a request came through this gateway. */
  gatewaySharedSecret: string;
  supabaseJwksUrl: string;
  supabaseUrl: string;
  /** Postgres connection string for quota checks; empty disables quotas. */
  databaseUrl: string;
  rateLimit: { max: number; windowMs: number };
  dailyTokenQuotaPerUser: number;
  /** Paths that require a valid Supabase JWT. Everything else is rejected. */
  requestTimeoutMs: number;
}

function required(name: string, value: string | undefined, isProd: boolean): string {
  if (!value) {
    if (isProd) {
      throw new Error(`${name} must be set in production`);
    }
    return '';
  }
  return value;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const nodeEnv = env.NODE_ENV ?? 'development';
  const isProd = nodeEnv === 'production';

  return {
    port: Number(env.GATEWAY_PORT ?? 3001),
    host: env.GATEWAY_HOST ?? '0.0.0.0',
    logLevel: env.LOG_LEVEL ?? 'info',
    nodeEnv,
    allowedOrigins: (env.ALLOWED_ORIGINS ?? 'http://localhost:3000')
      .split(',')
      .map((o) => o.trim())
      .filter(Boolean),
    backendUrl: env.BACKEND_URL ?? 'http://localhost:8000',
    gatewaySharedSecret: required('GATEWAY_SHARED_SECRET', env.GATEWAY_SHARED_SECRET, isProd),
    supabaseJwksUrl: required('SUPABASE_JWT_JWKS_URL', env.SUPABASE_JWT_JWKS_URL, isProd),
    supabaseUrl: env.SUPABASE_URL ?? '',
    databaseUrl: env.DATABASE_URL ?? '',
    rateLimit: {
      max: Number(env.RATE_LIMIT_MAX ?? 60),
      windowMs: Number(env.RATE_LIMIT_WINDOW_MS ?? 60_000),
    },
    dailyTokenQuotaPerUser: Number(env.DAILY_TOKEN_QUOTA_PER_USER ?? 50_000),
    requestTimeoutMs: Number(env.REQUEST_TIMEOUT_MS ?? 120_000),
  };
}
