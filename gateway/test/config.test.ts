import { describe, expect, it } from 'vitest';

import { loadConfig } from '../src/config.js';

describe('config', () => {
  it('throws in production when the gateway secret is missing', () => {
    expect(() =>
      loadConfig({ NODE_ENV: 'production', SUPABASE_JWT_JWKS_URL: 'https://x/jwks' }),
    ).toThrow(/GATEWAY_SHARED_SECRET/);
  });

  it('throws in production when the JWKS url is missing', () => {
    expect(() =>
      loadConfig({ NODE_ENV: 'production', GATEWAY_SHARED_SECRET: 'secret' }),
    ).toThrow(/SUPABASE_JWT_JWKS_URL/);
  });

  it('tolerates missing values outside production', () => {
    const config = loadConfig({ NODE_ENV: 'development' });
    expect(config.gatewaySharedSecret).toBe('');
    expect(config.port).toBe(3001);
  });

  it('parses a comma-separated origin allowlist', () => {
    const config = loadConfig({
      ALLOWED_ORIGINS: 'http://localhost:3000, https://app.example.com ',
    });
    expect(config.allowedOrigins).toEqual(['http://localhost:3000', 'https://app.example.com']);
  });
});
