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

export function createQuotaStore(connectionString: string): {
  store: QuotaStore;
  close: () => Promise<void>;
} {
  const pool = new pg.Pool({ connectionString, max: 5, idleTimeoutMillis: 30_000 });

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
