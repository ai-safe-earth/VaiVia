import { describe, expect, it } from 'vitest';

import { shouldUseTls } from '../src/quotaStore.js';

describe('shouldUseTls', () => {
  it('encrypts connections to a remote host', () => {
    expect(
      shouldUseTls('postgresql://u:p@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'),
    ).toBe(true);
  });

  it('does not require TLS for a local database', () => {
    expect(shouldUseTls('postgresql://u:p@localhost:5432/postgres')).toBe(false);
    expect(shouldUseTls('postgresql://u:p@127.0.0.1:5432/postgres')).toBe(false);
  });

  it('fails secure when the connection string cannot be parsed', () => {
    expect(shouldUseTls('not a url')).toBe(true);
  });
});
