'use client';

import { useEffect, useRef, useState } from 'react';

import { AppHeader } from '@/components/AppHeader';
import { AuthPanel } from '@/components/AuthPanel';
import { ChatPanel } from '@/components/ChatPanel';
import { FavoritesView } from '@/components/FavoritesView';
import { fetchFavorites, setFavorite, type FavoritesList } from '@/lib/api';
import { applyToggle, belongsToCurrentUser, savedIds } from '@/lib/favorites';
import { onSession, signOut, type AuthUser } from '@/lib/auth';
import { listConversations, loadMessages } from '@/lib/conversations';
import { isAuthConfigured } from '@/lib/supabaseClient';
import type { ChatMessage, Loop } from '@/lib/types';

export default function Home() {
  // undefined = session still resolving; render nothing rather than flashing
  // the sign-in form at an already signed-in user.
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  // Saved routes: the id set drives every card's bookmark; the view shows
  // the hydrated list. One state, however the toggle was reached.
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(new Set());
  // The hydrated list itself, kept rather than thrown away: the page fetched
  // it for the ids alone and the Saved-routes view fetched the identical list
  // again on every open. undefined = not loaded yet, null = the load failed.
  const [favoritesList, setFavoritesList] = useState<
    FavoritesList | null | undefined
  >(undefined);
  const [showFavorites, setShowFavorites] = useState(false);
  // ONE continuous conversation (owner decision, 2026-08-26): the app resumes
  // the most recent conversation on sign-in and history scrolls back. resumed
  // is set exactly once per sign-in, before the panel is shown, so the panel
  // key never changes mid-stream — a fresh chat's first turn assigning an id
  // does not remount the panel and destroy the answer as it arrives.
  // undefined = still resolving (render no panel yet), null = start fresh.
  const [resumed, setResumed] = useState<
    { id: string | null; messages: ChatMessage[] } | undefined
  >(undefined);
  // Bumped by the composer's Clear chat button. Part of the panel key, so a
  // fresh chat is an explicit remount — the key must never change from an SSE
  // event (that was the mid-stream remount bug).
  const [epoch, setEpoch] = useState(0);

  // Who is signed in NOW, readable from a continuation that started under
  // whoever was signed in THEN.
  const userRef = useRef(user);
  userRef.current = user;

  // Which saved-list refresh is the current one. Two quick toggles race, and
  // the earlier one's answer must not land on top of the later one's.
  const saves = useRef(0);

  useEffect(() => onSession(setUser), []);

  useEffect(() => {
    // Everything below belongs to ONE account. Clearing it up front, rather
    // than letting the next account's fetch overwrite it, is what stops a
    // second sign-in seeing the first one's saved routes -- for a moment if
    // the fetch succeeds, and indefinitely if it fails.
    setResumed(undefined);
    setEpoch(0);
    setFavoriteIds(new Set());
    setFavoritesList(undefined);
    if (!user) return;

    // ...and a response that arrives after the account changed again is not
    // this account's, so it is dropped rather than rendered.
    const forUser = user.id;
    const stillCurrent = () => belongsToCurrentUser(forUser, userRef.current?.id);
    // Resume the single conversation: newest id, its transcript, its cards
    // (loadMessages rehydrates them from the stored result_refs). Any failure
    // degrades to a fresh chat rather than blocking sign-in.
    void listConversations()
      .then(async (list) => {
        const id = list[0]?.id ?? null;
        const messages = id ? await loadMessages(id).catch(() => []) : [];
        if (stillCurrent()) setResumed({ id, messages });
      })
      .catch(() => stillCurrent() && setResumed({ id: null, messages: [] }));
    void fetchFavorites()
      .then((list) => stillCurrent() && receiveFavorites(list))
      .catch(() => stillCurrent() && setFavoritesList(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  function toggleFavoritesView() {
    setShowFavorites((open) => !open);
  }

  /** One saved list, one id set, whoever loaded it.
   *
   *  `missing` counts as saved. Those ids ARE in the ledger — their route is
   *  just out of the catalogue until the next export restores it — and
   *  dropping them showed the bookmark unfilled on a route saved long ago:
   *  one tap "saved" it (a no-op) and the next tap deleted it.
   */
  function receiveFavorites(list: FavoritesList) {
    setFavoritesList(list);
    setFavoriteIds(savedIds(list));
  }

  /** Optimistic: the bookmark flips at once, and flips back if the save
   *  fails — a favorite that silently did not stick is worse than a flicker.
   *
   *  The bookmark can be optimistic because the id is the whole of what it
   *  needs. The saved LIST cannot: the row comes from the server, and a drawn
   *  route does not exist there until this write persists its document —
   *  seconds, not milliseconds. Opening Saved routes inside that window
   *  fetched a list the save had not reached yet, and that view fetches once
   *  on open, so it stayed empty for good. So the list is refreshed when the
   *  write lands, whoever is looking at it.
   *
   *  Only on save. An unsave deliberately leaves the row where it is until
   *  something else reloads the list, so an accidental tap is undoable
   *  without hunting for the route again. */
  function toggleFavorite(loop: Loop, on: boolean) {
    setFavoriteIds((current) => applyToggle(current, loop.id, on));
    // The list this refresh will belong to, captured before it is in flight —
    // a response that outlived its sign-in is not this account's to render.
    const forUser = user?.id;
    const seq = (saves.current += 1);
    void setFavorite(loop.id, on).then(
      () => {
        if (!on) return;
        void fetchFavorites()
          .then((list) => {
            if (seq !== saves.current) return;
            if (forUser && belongsToCurrentUser(forUser, userRef.current?.id)) {
              receiveFavorites(list);
            }
          })
          // A refresh that fails leaves the optimistic set alone: the save
          // itself succeeded, and reverting the bookmark would lie about it.
          .catch(() => {});
      },
      () => {
        // The exact inverse flip, so a failed save leaves the set as it was.
        setFavoriteIds((current) => applyToggle(current, loop.id, !on));
      },
    );
  }

  const authRequired = isAuthConfigured();
  if (authRequired && user === undefined) return null;
  if (authRequired && !user) {
    return (
      <main className="shell centered">
        <AuthPanel />
      </main>
    );
  }

  return (
    <main className="shell">
      <AppHeader
        email={user?.email}
        onSignOut={user ? () => void signOut() : undefined}
        onFavorites={user ? toggleFavoritesView : undefined}
        favoritesOpen={showFavorites}
      />

      {/* The stage: the conversation, with Saved routes over it when open.
          The chat is never unmounted — that would throw away the transcript
          of the conversation you are having. Maps live inside the cards. */}
      <div className="stage">
        {showFavorites && (
          <FavoritesView
            initial={favoritesList}
            onLoaded={receiveFavorites}
            favorites={favoriteIds}
            onToggleFavorite={toggleFavorite}
          />
        )}
        {/* The chat panel is HIDDEN behind the favorites view, never replaced.
            Rendering one or the other unmounted the panel and remounted it
            seeded from the resume snapshot — so a look at Saved routes wiped
            the transcript of the conversation you were having. */}
        {(!authRequired || resumed !== undefined) && (
          <ChatPanel
            key={`${user?.id ?? 'anon'}:${epoch > 0 ? `new-${epoch}` : (resumed?.id ?? 'fresh')}`}
            hidden={showFavorites}
            // Asking is done on the conversation: a submit from behind Saved
            // routes brings the answer back into view.
            onAsk={() => setShowFavorites(false)}
            initialConversationId={epoch > 0 ? null : (resumed?.id ?? null)}
            initialMessages={epoch > 0 ? [] : (resumed?.messages ?? [])}
            onClear={() => setEpoch((current) => current + 1)}
            favorites={user ? favoriteIds : undefined}
            onToggleFavorite={user ? toggleFavorite : undefined}
          />
        )}
      </div>
    </main>
  );
}
