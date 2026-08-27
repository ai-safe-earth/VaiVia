'use client';

import { Icon, Mark } from './brand';

interface Props {
  email?: string | null;
  onSignOut?: () => void;
  /** Starts a fresh conversation (the page remounts the panel). */
  onNewChat?: () => void;
  /** Opens (or closes) the saved-routes view. Absent when signed out —
   *  favorites are account data, so the mark only exists with an account. */
  onFavorites?: () => void;
  /** Whether the saved-routes view is the one on screen. */
  favoritesOpen?: boolean;
}

/**
 * 54px of chrome: mark, wordmark, new chat, saved routes, account.
 *
 * The wordmark is live text rather than the SVG — it sits next to text, and the
 * spec asks for real type wherever it does, so it inherits the family and the
 * -0.05em the rest of the display scale uses.
 */
export function AppHeader({
  email,
  onSignOut,
  onNewChat,
  onFavorites,
  favoritesOpen,
}: Props) {
  return (
    <header className="app-header">
      <Mark size={17} />
      <h1 className="wordmark">
        vai<span className="via">via</span>
      </h1>
      {onNewChat && (
        <button type="button" className="header-new" onClick={onNewChat}>
          New chat
        </button>
      )}
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
