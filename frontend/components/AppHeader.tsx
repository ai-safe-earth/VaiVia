'use client';

import { Icon, Mark } from './brand';

interface Props {
  region: string;
  email?: string | null;
  onSignOut?: () => void;
  /** Opens (or closes) the saved-routes view. Absent when signed out —
   *  favorites are account data, so the mark only exists with an account. */
  onFavorites?: () => void;
  /** Whether the saved-routes view is the one on screen. */
  favoritesOpen?: boolean;
}

/**
 * 54px of chrome: mark, wordmark, region, saved routes, account.
 *
 * The wordmark is live text rather than the SVG — it sits next to text, and the
 * spec asks for real type wherever it does, so it inherits the family and the
 * -0.05em the rest of the display scale uses.
 */
export function AppHeader({ region, email, onSignOut, onFavorites, favoritesOpen }: Props) {
  return (
    <header className="app-header">
      <Mark size={17} />
      <h1 className="wordmark">
        vai<span className="via">via</span>
      </h1>
      <span className="region vv-label">{region}</span>
      <span className="spacer" />
      {onFavorites && (
        <button
          type="button"
          className="header-saved"
          aria-pressed={favoritesOpen}
          aria-label="Saved routes"
          title="Saved routes"
          onClick={onFavorites}
        >
          <Icon name="saved" />
        </button>
      )}
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
