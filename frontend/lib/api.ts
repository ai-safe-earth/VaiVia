/**
 * Gateway client. This is the only network surface the app has — no direct
 * calls to the backend, Neo4j, or OpenAI exist anywhere in the frontend.
 */

import { readSSE } from './sse';
import { getAccessToken } from './supabaseClient';
import type { ChatResults, Loop, RouteDetail } from './types';

const GATEWAY_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? 'http://localhost:3001';

export type ChatStreamEvent =
  | { type: 'conversation'; conversationId: string }
  | { type: 'intent'; intent: Record<string, unknown> }
  | { type: 'results'; results: ChatResults }
  | { type: 'token'; delta: string }
  | { type: 'done'; usage: { input_tokens: number; output_tokens: number } }
  | { type: 'error'; error: string; message: string };

export class AuthRequiredError extends Error {}

/** Stream a chat turn. Yields typed events as the gateway forwards them. */
export async function* sendChat(
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const token = await getAccessToken();

  const response = await fetch(`${GATEWAY_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });

  if (response.status === 401) {
    throw new AuthRequiredError('Please sign in to keep chatting.');
  }
  if (response.status === 429) {
    const body = await response.json().catch(() => ({}));
    yield {
      type: 'error',
      error: body.error ?? 'rate_limited',
      message: body.message ?? 'Too many requests — give it a moment.',
    };
    return;
  }
  if (!response.ok) {
    yield { type: 'error', error: 'http_error', message: `Request failed (${response.status}).` };
    return;
  }

  for await (const frame of readSSE(response)) {
    const event = toEvent(frame.event, frame.data);
    if (event) yield event;
  }
}

function toEvent(name: string, raw: string): ChatStreamEvent | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(raw);
  } catch {
    return null; // a malformed frame must not tear down the stream
  }

  switch (name) {
    case 'conversation':
      return { type: 'conversation', conversationId: String(data.conversation_id) };
    case 'intent':
      return { type: 'intent', intent: data };
    case 'results':
      return { type: 'results', results: data as unknown as ChatResults };
    case 'token':
      return { type: 'token', delta: String(data.delta ?? '') };
    case 'done':
      return {
        type: 'done',
        usage: (data.usage as { input_tokens: number; output_tokens: number }) ?? {
          input_tokens: 0,
          output_tokens: 0,
        },
      };
    case 'error':
      return {
        type: 'error',
        error: String(data.error ?? 'error'),
        message: String(data.message ?? 'Something went wrong.'),
      };
    default:
      return null;
  }
}

/** Trail geometry for the map, fetched lazily when a trail is selected. */
export async function fetchTrailGeoJson(trailId: string): Promise<GeoJSON.Feature | null> {
  const token = await getAccessToken();
  const response = await fetch(`${GATEWAY_URL}/trails/${encodeURIComponent(trailId)}/geojson`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) return null;
  return (await response.json()) as GeoJSON.Feature;
}

/** Catalogue route geometry for the map.
 *
 * Unlike fetchTrailGeoJson this only swallows a 404. When several loops are
 * drawn at once a silent null on any failure blanks part of the map with no
 * explanation, so anything else throws and the caller decides.
 */
export async function fetchRouteGeoJson(routeId: string): Promise<GeoJSON.Feature | null> {
  const token = await getAccessToken();
  const response = await fetch(`${GATEWAY_URL}/routes/${encodeURIComponent(routeId)}/geojson`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`route geometry failed: ${response.status}`);
  }
  return (await response.json()) as GeoJSON.Feature;
}

/** The expandable card's payload: profile, measures, places, attribution.
 *
 * Same contract as fetchRouteGeoJson: a 404 (route left the catalogue) is
 * null, anything else throws — a 503 means the document store is unmounted
 * and silence would misreport it as "no detail".
 */
export async function fetchRouteDetail(routeId: string): Promise<RouteDetail | null> {
  const token = await getAccessToken();
  const response = await fetch(`${GATEWAY_URL}/routes/${encodeURIComponent(routeId)}/detail`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`route detail failed: ${response.status}`);
  }
  return (await response.json()) as RouteDetail;
}

/** This user's saved routes, hydrated into the same rows a search returns,
 *  plus the ids whose route left the catalogue (shown, never dropped). */
export interface FavoritesList {
  routes: Loop[];
  missing: string[];
}

export async function fetchFavorites(): Promise<FavoritesList> {
  const token = await getAccessToken();
  const response = await fetch(`${GATEWAY_URL}/routes/favorites`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401) throw new AuthRequiredError('Please sign in.');
  if (!response.ok) throw new Error(`favorites failed: ${response.status}`);
  return (await response.json()) as FavoritesList;
}

/** Idempotent toggle; unfavorite is a POST because the gateway forwards only
 *  GET and POST. */
export async function setFavorite(routeId: string, on: boolean): Promise<void> {
  const token = await getAccessToken();
  const response = await fetch(
    `${GATEWAY_URL}/routes/${encodeURIComponent(routeId)}/favorite`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ on }),
    },
  );
  if (!response.ok) throw new Error(`favorite toggle failed: ${response.status}`);
}
