# frontend

Next.js chat-first app: Supabase sign-in, streaming chat, and a MapLibre map
that draws the trail the graph actually returned.

## Run

```bash
npm install
cp ../.env.example .env.local   # keep only the NEXT_PUBLIC_* lines
npm run dev                     # http://localhost:3000
npm test                        # vitest — pure logic, no browser needed
npm run build                   # also type-checks the whole app
```

It needs the gateway running on `NEXT_PUBLIC_GATEWAY_URL` (default
`http://localhost:3001`). Without Supabase configured, the app renders and warns
in-page; requests will be rejected by the gateway with 401, which is correct.

## Boundaries

- **The gateway is the only host this app talks to** (`lib/api.ts`). There is no
  code path to the backend, Neo4j, or OpenAI.
- **Only the Supabase anon key belongs in `NEXT_PUBLIC_*`.** The service-role and
  OpenAI keys are backend-only; a `NEXT_PUBLIC_` prefix ships a value to every
  visitor's browser.
- The map renders geometry fetched from `/trails/{id}/geojson` — the same
  segments the answer was grounded in, so the text and the line always agree.

## Layout

| Path | Role |
|---|---|
| `lib/sse.ts` | Incremental SSE parser — handles frames split across arbitrary network chunks (the subtle part; heavily tested) |
| `lib/api.ts` | Typed gateway client: chat stream, trail geometry, auth/rate-limit error mapping |
| `lib/format.ts` | Metres/minutes → human units. The only place conversion happens |
| `components/ChatPanel.tsx` | Conversation state, streaming updates, trail selection |
| `components/MapView.tsx` | MapLibre map, OSM raster tiles, fits bounds to the selection |
| `components/TrailCard.tsx` | Trail summary with difficulty, time, hazards, features |
