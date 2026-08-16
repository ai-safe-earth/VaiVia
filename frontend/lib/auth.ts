/**
 * Email/password auth on top of the Supabase browser client.
 *
 * Confirmation email is ON for this project (autoconfirm off), so sign-up does
 * not produce a session — the user must click the emailed link first. The UI
 * copy leans on that.
 */

import type { Session } from '@supabase/supabase-js';

import { getSupabase, isAuthConfigured } from './supabaseClient';

export interface AuthUser {
  id: string;
  email: string | null;
}

export function toAuthUser(session: Session | null): AuthUser | null {
  if (!session) return null;
  return { id: session.user.id, email: session.user.email ?? null };
}

/**
 * Supabase error strings are developer-facing ("Invalid login credentials",
 * "Email not confirmed"); map the ones users actually hit to sentences a
 * person can act on. Anything unrecognized passes through untouched rather
 * than being flattened into a generic failure.
 */
export function friendlyAuthError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes('invalid login credentials')) {
    return 'Wrong email or password.';
  }
  if (normalized.includes('email not confirmed')) {
    return 'Confirm your email first — check your inbox for the link.';
  }
  if (normalized.includes('user already registered')) {
    return 'That email already has an account — sign in instead.';
  }
  if (normalized.includes('password should be at least')) {
    return 'Password is too short — use at least 6 characters.';
  }
  if (normalized.includes('rate limit') || normalized.includes('too many requests')) {
    return 'Too many attempts — wait a minute and try again.';
  }
  return message;
}

export async function signIn(email: string, password: string): Promise<AuthUser> {
  const { data, error } = await getSupabase().auth.signInWithPassword({
    email,
    password,
  });
  if (error) throw new Error(friendlyAuthError(error.message));
  return toAuthUser(data.session)!;
}

/** Returns true when a session exists immediately, false when email confirmation is pending. */
export async function signUp(email: string, password: string): Promise<boolean> {
  const { data, error } = await getSupabase().auth.signUp({ email, password });
  if (error) throw new Error(friendlyAuthError(error.message));
  return data.session !== null;
}

export async function signOut(): Promise<void> {
  await getSupabase().auth.signOut();
}

/**
 * Subscribe to session changes, delivering the current session immediately.
 * Returns an unsubscribe function. No-op (signed out forever) when Supabase
 * is not configured, so the caller needs no special case.
 */
export function onSession(callback: (user: AuthUser | null) => void): () => void {
  if (!isAuthConfigured()) {
    callback(null);
    return () => {};
  }
  const supabase = getSupabase();
  void supabase.auth.getSession().then(({ data }) => callback(toAuthUser(data.session)));
  const { data } = supabase.auth.onAuthStateChange((_event, session) =>
    callback(toAuthUser(session)),
  );
  return () => data.subscription.unsubscribe();
}
