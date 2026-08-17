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

import { getSupabase } from './supabaseClient';
import type { ChatMessage } from './types';

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

interface MessageRow {
  role: string;
  content: string;
}

/** Rows from the messages table -> the ChatPanel's message shape.
 *
 * System turns are dropped: they are prompt plumbing, not conversation. Rows
 * arrive in created_at order and stay in that order.
 */
export function toChatMessages(rows: MessageRow[]): ChatMessage[] {
  return rows
    .filter((row): row is MessageRow & { role: 'user' | 'assistant' } =>
      row.role === 'user' || row.role === 'assistant',
    )
    .map((row) => ({ role: row.role, content: row.content }));
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
    .order('updated_at', { ascending: false })
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
    .select('role, content, created_at')
    .eq('conversation_id', conversationId)
    .order('created_at', { ascending: true })
    .limit(200);
  if (error) throw new Error(error.message);
  return toChatMessages((data ?? []) as MessageRow[]);
}
