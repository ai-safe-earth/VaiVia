/**
 * Conversation history, read straight from Supabase with the anon key.
 *
 * This is not a hole in the "gateway is the only network surface" rule: the
 * rule guards the backend, Neo4j, and OpenAI. Supabase is already a public
 * surface the browser talks to for auth, and the migration's row-level
 * security policies (select-only, `auth.uid() = user_id`) exist precisely so
 * clients can read their own history without a round-trip through the stack.
 * Writes still happen only in the backend via the service role.
 */

import { fetchRoutesByIds } from './api';
import { getSupabase } from './supabaseClient';
import type { ChatMessage, Loop } from './types';

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

interface MessageRow {
  id?: string;
  role: string;
  content: string;
  result_refs?: { loop_ids?: string[] } | null;
}

/** Rows from the messages table -> the ChatPanel's message shape.
 *
 * System turns are dropped: they are prompt plumbing, not conversation. Rows
 * arrive in created_at order and stay in that order.
 *
 * An assistant row's stored loop ids are rehydrated into cards through
 * `byId` (fetched once for the whole conversation): the transcript reads
 * back with the same cards the live answer had, in the same order. An id
 * whose route left the catalogue is simply absent from `byId` and drops out
 * of that turn's cards — never the whole turn. Trail ids are not rehydrated
 * (no by-ids template for trails); the prose still resumes.
 */
export function toChatMessages(
  rows: MessageRow[],
  byId: Map<string, Loop> = new Map(),
): ChatMessage[] {
  return rows
    .filter((row): row is MessageRow & { role: 'user' | 'assistant' } =>
      row.role === 'user' || row.role === 'assistant',
    )
    .map((row) => {
      const loops = (row.result_refs?.loop_ids ?? [])
        .map((id) => byId.get(id))
        .filter((loop): loop is Loop => Boolean(loop));
      return {
        role: row.role,
        content: row.content,
        // Only assistant turns take feedback; a user turn needs no handle.
        ...(row.role === 'assistant' && row.id && { messageId: row.id }),
        ...(loops.length > 0 && {
          results: { kind: 'loop_search' as const, loops },
        }),
      };
    });
}

/** A stored title, or a stable fallback so untitled rows are still clickable. */
export function conversationTitle(
  row: { title: string | null; created_at: string },
): string {
  if (row.title?.trim()) return row.title;
  const day = row.created_at.slice(0, 10);
  return `Conversation from ${day}`;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const { data, error } = await getSupabase()
    .from('conversations')
    .select('id, title, created_at, updated_at')
    // Newest CONVERSATION first. created_at, not updated_at: add_message
    // never bumps updated_at, so ordering on it is ordering on creation
    // anyway — spelled honestly here.
    .order('created_at', { ascending: false })
    .limit(50);
  if (error) throw new Error(error.message);
  return (data ?? []).map((row) => ({
    id: row.id as string,
    title: conversationTitle(row as { title: string | null; created_at: string }),
    updated_at: row.updated_at as string,
  }));
}

export async function loadMessages(conversationId: string): Promise<ChatMessage[]> {
  const { data, error } = await getSupabase()
    .from('messages')
    .select('id, role, content, result_refs, created_at')
    .eq('conversation_id', conversationId)
    .order('created_at', { ascending: true })
    .limit(200);
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as MessageRow[];
  // One hydration call for the whole conversation, not one per turn.
  const wanted = [
    ...new Set(rows.flatMap((row) => row.result_refs?.loop_ids ?? [])),
  ];
  const byId = new Map<string, Loop>();
  if (wanted.length > 0) {
    // A failed hydration degrades to prose-only history — the transcript
    // must never be held hostage by the cards.
    const hydrated = await fetchRoutesByIds(wanted).catch(() => null);
    for (const loop of hydrated?.routes ?? []) byId.set(loop.id, loop);
  }
  return toChatMessages(rows, byId);
}
