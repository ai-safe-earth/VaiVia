'use client';

import { Icon, Mark } from './brand';

interface Props {
  email?: string | null;
  onSignOut?: () => void;
  /** Opens (or closes) the saved-routes view. Absent when signed out —
   *  favorites are account data, so the mark only exists with an account. */
  onFavorites?: () => void;
  /** Whether the saved-routes view is the one on screen. */
  favoritesOpen?: boolean;
}

/**
 * 54px of chrome: mark, wordmark, saved routes, account.
 *
 * The account's e-mail is the title of Sign out rather than a block of its
 * own: at 360px the row is mark + wordmark + two icons + Sign out, and an
 * ellipsised address was the one thing in it saying nothing a tap needs.
 *
 * The wordmark is live text rather than the SVG — it sits next to text, and the
 * spec asks for real type wherever it does, so it inherits the family and the
 * -0.05em the rest of the display scale uses.
 */
export function AppHeader({
  email,
  onSignOut,
  onFavorites,
  favoritesOpen,
}: Props) {
  return (
    <header className="app-header">
      <Mark size={17} />
      <h1 className="wordmark">
        vai<span className="via">via</span>
      </h1>
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
      {onSignOut && (
        <button
          type="button"
          className="header-action"
          title={email ?? undefined}
          onClick={onSignOut}
        >
          Sign out
        </button>
      )}
    </header>
  );
}
