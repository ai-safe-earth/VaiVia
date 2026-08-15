# gateway

Fastify (Node/TS) API gateway — the only publicly exposed service.

Responsibilities, and nothing more: Supabase JWT verification, per-user/per-IP
rate limiting, origin/CORS control, daily LLM quota pre-check, request-ID
propagation, and SSE-capable proxying to the backend. **No business logic** — if
a change needs graph or domain knowledge, it belongs in `backend/`.

## Run

```bash
npm install
npm run dev          # tsx watch, port 3001
npm test             # vitest — no network, no database needed
npm run typecheck
npm run lint
```

## How a request flows

1. **CORS** — origin must be in `ALLOWED_ORIGINS`.
2. **Identify** (`onRequest`) — verifies the Supabase JWT against the JWKS and
   populates `request.user`. Deliberately does *not* reject: running before the
   rate limiter means limits key on the verified user id, and unauthenticated
   traffic still gets counted (by IP) instead of slipping past on an early 401.
3. **Rate limit** — keyed by `user.id`, falling back to IP.
4. **Authenticate** (route `preHandler`) — now rejects with 401 if no user.
5. **Quota** — for `/chat` only, reads `daily_quotas` from Supabase Postgres and
   returns 429 before any tokens are spent. Fails open (logged) if Postgres is
   unreachable: a database blip should degrade cost control, not the product.
6. **Proxy** — forwards to the backend with `X-Gateway-Secret` (proves the hop),
   `X-Request-ID`, and the verified `x-user-id`. The caller's bearer token is
   never needed downstream. Only `/trails`, `/routes`, and `/chat` are proxied;
   anything else 404s here.

## Environment

| Variable | Purpose |
|---|---|
| `GATEWAY_PORT` / `GATEWAY_HOST` | Listen address (default 3001 / 0.0.0.0) |
| `ALLOWED_ORIGINS` | Comma-separated browser origins |
| `BACKEND_URL` | Internal FastAPI address |
| `GATEWAY_SHARED_SECRET` | Proves the hop to the backend — **required in production** |
| `SUPABASE_JWT_JWKS_URL` | Supabase JWKS endpoint — **required in production** |
| `DATABASE_URL` | Supabase Postgres; unset disables quota enforcement (warned at boot) |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW_MS` | Request budget per key |
| `DAILY_TOKEN_QUOTA_PER_USER` | LLM tokens per user per day |
