/**
 * Postgres-backed quota store (Supabase). Reads the daily_quotas row written by
 * the backend's cost ledger — see infra/supabase/migrations/0001_chat_and_quotas.sql.
 */

import pg from 'pg';

import type { QuotaStore } from './plugins/quota.js';

const TOKENS_TODAY = `
  SELECT tokens_used
  FROM daily_quotas
  WHERE user_id = $1 AND day = CURRENT_DATE
`;

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

/**
 * Whether to negotiate TLS for this connection string.
 *
 * node-postgres sends no SSLRequest unless it is told to, so a bare Supabase
 * URL connects in plaintext over the public internet — it works, which is what
 * makes it dangerous. Deciding here rather than via `sslmode` in the URL means
 * a hand-edited connection string cannot silently downgrade the link.
 */
export function shouldUseTls(connectionString: string): boolean {
  try {
    return !LOCAL_HOSTS.has(new URL(connectionString).hostname);
  } catch {
    return true; // Unparseable: fail secure rather than fall back to plaintext.
  }
}

export function createQuotaStore(connectionString: string): {
  store: QuotaStore;
  close: () => Promise<void>;
} {
  const pool = new pg.Pool({
    connectionString,
    max: 5,
    idleTimeoutMillis: 30_000,
    // Supabase terminates TLS at the pooler with a certificate that does not
    // chain to a public root, so `sslmode=require` in the URL fails: this `pg`
    // version treats it as `verify-full`. Encrypt without verifying the chain,
    // matching what the backend's asyncpg client does.
    ...(shouldUseTls(connectionString) ? { ssl: { rejectUnauthorized: false } } : {}),
  });

  return {
    store: {
      async tokensUsedToday(userId: string): Promise<number> {
        const result = await pool.query<{ tokens_used: string }>(TOKENS_TODAY, [userId]);
        return Number(result.rows[0]?.tokens_used ?? 0);
      },
    },
    close: () => pool.end(),
  };
}
