# gateway

Fastify (Node/TS) API gateway — the only publicly exposed service.

Responsibilities (and nothing more): Supabase JWT validation, per-user/per-IP
rate limiting, origin/CORS control, daily LLM quota pre-check, request-ID
propagation, SSE-capable proxying to the backend. **No business logic.**

Scaffolded in Phase 3 — see `docs/plan.md`.
