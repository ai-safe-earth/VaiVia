import { describe, expect, it } from 'vitest';

import { friendlyAuthError, toAuthUser } from '../lib/auth';
import { conversationTitle, toChatMessages } from '../lib/conversations';
import type { Session } from '@supabase/supabase-js';

describe('friendlyAuthError', () => {
  it('translates the errors users actually hit', () => {
    expect(friendlyAuthError('Invalid login credentials')).toBe(
      'Wrong email or password.',
    );
    expect(friendlyAuthError('Email not confirmed')).toContain('inbox');
    expect(friendlyAuthError('User already registered')).toContain('sign in');
    expect(friendlyAuthError('Password should be at least 6 characters')).toContain(
      '6 characters',
    );
  });

  it('passes unknown messages through instead of flattening them', () => {
    expect(friendlyAuthError('Database connection lost')).toBe(
      'Database connection lost',
    );
  });
});

describe('toAuthUser', () => {
  it('maps a session to id and email', () => {
    const session = {
      user: { id: 'u1', email: 'a@b.c' },
    } as unknown as Session;
    expect(toAuthUser(session)).toEqual({ id: 'u1', email: 'a@b.c' });
  });

  it('is null when signed out and null-email-safe when the session has none', () => {
    expect(toAuthUser(null)).toBeNull();
    const session = { user: { id: 'u1' } } as unknown as Session;
    expect(toAuthUser(session)).toEqual({ id: 'u1', email: null });
  });
});

describe('toChatMessages', () => {
  it('keeps user and assistant turns in order and drops system rows', () => {
    const messages = toChatMessages([
      { role: 'system', content: 'prompt plumbing' },
      { role: 'user', content: 'easy trails?' },
      { role: 'assistant', content: 'Two loops nearby.' },
    ]);
    expect(messages).toEqual([
      { role: 'user', content: 'easy trails?' },
      { role: 'assistant', content: 'Two loops nearby.' },
    ]);
  });

  it('is empty for an empty conversation', () => {
    expect(toChatMessages([])).toEqual([]);
  });
});

describe('conversationTitle', () => {
  it('prefers the stored title', () => {
    expect(
      conversationTitle({ title: 'Lake loop hunt', created_at: '2026-08-16T10:00:00Z' }),
    ).toBe('Lake loop hunt');
  });

  it('falls back to the creation date for untitled or blank rows', () => {
    expect(conversationTitle({ title: null, created_at: '2026-08-16T10:00:00Z' })).toBe(
      'Conversation from 2026-08-16',
    );
    expect(conversationTitle({ title: '  ', created_at: '2026-08-16T10:00:00Z' })).toBe(
      'Conversation from 2026-08-16',
    );
  });
});
