'use client';

import { useState } from 'react';

import { signIn, signUp } from '@/lib/auth';

type Mode = 'sign-in' | 'sign-up';

export function AuthPanel() {
  const [mode, setMode] = useState<Mode>('sign-in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'sign-in') {
        await signIn(email, password);
        // onSession in the page swaps this panel out; nothing to do here.
      } else {
        const signedIn = await signUp(email, password);
        if (!signedIn) setPendingConfirm(true);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  if (pendingConfirm) {
    return (
      <section className="auth">
        <h1>get-out-door</h1>
        <p className="note">
          Almost there — we sent a confirmation link to <strong>{email}</strong>. Click
          it, then sign in here.
        </p>
        <button
          type="button"
          className="link"
          onClick={() => {
            setPendingConfirm(false);
            setMode('sign-in');
          }}
        >
          Back to sign in
        </button>
      </section>
    );
  }

  return (
    <section className="auth">
      <h1>get-out-door</h1>
      <p className="note">Trail chat for Lake Como &amp; Lecco. Sign in to start.</p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
          aria-label="Email"
          autoComplete="email"
          required
          disabled={busy}
        />
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Password"
          aria-label="Password"
          autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
          required
          minLength={6}
          disabled={busy}
        />
        <button className="primary" type="submit" disabled={busy || !email || !password}>
          {busy ? '…' : mode === 'sign-in' ? 'Sign in' : 'Create account'}
        </button>
      </form>

      {error && <p className="banner">{error}</p>}

      <button
        type="button"
        className="link"
        onClick={() => {
          setMode(mode === 'sign-in' ? 'sign-up' : 'sign-in');
          setError(null);
        }}
      >
        {mode === 'sign-in' ? 'No account yet? Create one' : 'Have an account? Sign in'}
      </button>
    </section>
  );
}
