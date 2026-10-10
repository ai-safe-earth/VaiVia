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

  it('does not require TLS for a container on a shared Docker network', () => {
    expect(shouldUseTls('postgresql://u:p@supabase_db_vaivia:5432/postgres')).toBe(false);
    expect(shouldUseTls('postgresql://u:p@Supabase_DB_Vaivia:5432/postgres')).toBe(false);
  });

  it('encrypts any dotted name or IP address', () => {
    const hosts = ['db.example.com', '10.0.0.5', '192.168.1.20', '[2001:db8::1]', 'host.docker.internal'];
    for (const host of hosts) {
      expect(shouldUseTls(`postgresql://u:p@${host}:5432/postgres`)).toBe(true);
    }
  });

  it('ignores sslmode in the URL', () => {
    const url = 'postgresql://u:p@aws-1-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=disable';
    expect(shouldUseTls(url)).toBe(true);
  });

  it('fails secure when the connection string cannot be parsed', () => {
    expect(shouldUseTls('not a url')).toBe(true);
  });
});
