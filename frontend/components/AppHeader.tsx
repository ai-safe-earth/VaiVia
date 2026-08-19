'use client';

import { Mark } from './brand';

interface Props {
  region: string;
  email?: string | null;
  onSignOut?: () => void;
}

/**
 * 54px of chrome: mark, wordmark, region, account.
 *
 * The wordmark is live text rather than the SVG — it sits next to text, and the
 * spec asks for real type wherever it does, so it inherits the family and the
 * -0.05em the rest of the display scale uses.
 */
export function AppHeader({ region, email, onSignOut }: Props) {
  return (
    <header className="app-header">
      <Mark size={17} />
      <h1 className="wordmark">
        vai<span className="via">via</span>
      </h1>
      <span className="region vv-label">{region}</span>
      <span className="spacer" />
      {email && (
        <span className="account vv-label" title={email}>
          {email}
        </span>
      )}
      {onSignOut && (
        <button type="button" className="header-action" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </header>
  );
}
