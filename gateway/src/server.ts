/** Gateway entrypoint. */

import { buildApp } from './app.js';
import { loadConfig } from './config.js';
import { createQuotaStore } from './quotaStore.js';

const config = loadConfig();
const quota = config.databaseUrl ? createQuotaStore(config.databaseUrl) : null;

const app = await buildApp({ config, quotaStore: quota?.store ?? null });

if (!quota) {
  app.log.warn('DATABASE_URL not set — daily LLM quotas are NOT enforced');
}

async function shutdown(signal: string): Promise<void> {
  app.log.info({ signal }, 'shutting down');
  await app.close();
  await quota?.close();
  process.exit(0);
}

process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));

try {
  await app.listen({ port: config.port, host: config.host });
} catch (error) {
  app.log.error(error, 'failed to start');
  process.exit(1);
}
