/**
 * Daily LLM token quota, checked BEFORE the request reaches the backend.
 *
 * The backend writes actual usage to Supabase Postgres (daily_quotas) after
 * each OpenAI call; the gateway reads it here and refuses over-budget users.
 * Pre-check, not post-check: the point is to never spend the money.
 *
 * Fail-open on database errors is deliberate — a Postgres blip should degrade
 * cost control, not take the product down. It is logged at error level so the
 * blip is visible.
 */

import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';

export interface QuotaStore {
  tokensUsedToday(userId: string): Promise<number>;
}

export interface QuotaOptions {
  store: QuotaStore | null;
  dailyTokenQuota: number;
}

declare module 'fastify' {
  interface FastifyInstance {
    enforceQuota: (request: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

const quotaPlugin: FastifyPluginAsync<QuotaOptions> = async (app, options) => {
  app.decorate('enforceQuota', async (request: FastifyRequest, reply: FastifyReply) => {
    const userId = request.user?.id;
    if (!options.store || !userId) return;

    let used: number;
    try {
      used = await options.store.tokensUsedToday(userId);
    } catch (error) {
      request.log.error({ err: (error as Error).message }, 'quota lookup failed; allowing request');
      return;
    }

    if (used >= options.dailyTokenQuota) {
      await reply.code(429).send({
        error: 'quota_exceeded',
        message: 'Daily usage limit reached. It resets at midnight UTC.',
        used,
        limit: options.dailyTokenQuota,
      });
    }
  });
};

export default fp(quotaPlugin, { name: 'quota' });
