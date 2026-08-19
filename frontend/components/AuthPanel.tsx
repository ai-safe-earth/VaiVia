'use client';

import { useState } from 'react';

import { signIn, signUp } from '@/lib/auth';

import { Mark } from './brand';

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
        <div className="auth-head">
          <Mark size={17} />
          <h1>
            vai<span className="via">via</span>
          </h1>
        </div>
        <p className="intro vv-body">
          We sent a confirmation link to {email}. Open it, then sign in here.
        </p>
        <button
          type="button"
          className="switch"
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
      <div className="auth-head">
        <Mark size={17} />
        <h1>
          vai<span className="via">via</span>
        </h1>
      </div>
      <p className="intro vv-body">Trails around Lecco and Lake Como. Sign in to start.</p>

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

      {error && (
        <div className="notice">
          <div className="notice-bar" />
          <div className="notice-body">
            <span className="vv-label vv-label-hazard">Not signed in</span>
            <p className="vv-body">{error}</p>
          </div>
        </div>
      )}

      <button
        type="button"
        className="switch"
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
