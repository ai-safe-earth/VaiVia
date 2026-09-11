/**
 * Full-stack smoke: sign-in -> resume history -> (optionally) a live turn.
 *
 * Requires the whole stack running locally:
 *
 *   docker compose --env-file .env -f infra/docker-compose.yml up -d neo4j
 *   cd backend  && uv run uvicorn api.main:app --port 8000
 *   cd gateway  && npm start
 *   cd frontend && npm run build && npm start
 *
 * Credentials come from the environment — never the repo:
 *
 *   E2E_EMAIL / E2E_PASSWORD   a confirmed Supabase user (skipped when unset)
 *   E2E_LIVE=1                 also send a real chat turn (costs OpenAI money
 *                              and writes a conversation; off by default)
 *
 * Run from frontend/:  npm run test:e2e
 *
 * WHAT THIS SUITE CANNOT SEE — check by hand on a real device after any
 * change to the shell, the composer or the map layer (Playwright cannot raise
 * a soft keyboard, and headless Chromium has no safe-area insets):
 *
 *   iOS Safari      focus the composer: the field must NOT zoom the page in,
 *                   and the composer must stay above the keyboard. Raise the
 *                   map, then focus the composer: the canvas must not stretch.
 *                   If the composer ends up under the keyboard, the
 *                   visualViewport fallback in the plan (Phase 11 U3) is due.
 *   Chrome Android  the same, plus the browser's back gesture must lower the
 *                   map layer rather than leave the app.
 *   Notched device  in landscape, the header and the credit must clear the
 *                   notch and the home indicator (viewportFit: cover pays the
 *                   insets back as shell padding).
 *
 * Re-running within the same minute can trip the gateway's per-user rate
 * limit (RATE_LIMIT_MAX, default 60/min): a live run spends a few dozen
 * requests, so two runs back-to-back 429 the /chat call and the turn shows
 * an error instead of cards. Wait out the window rather than raising the
 * limit — the limit is production behaviour and the smoke should see it.
 */

import { expect, test } from '@playwright/test';

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
const LIVE = process.env.E2E_LIVE === '1';

test.describe('VaiVia smoke', () => {
  test.skip(!EMAIL || !PASSWORD, 'set E2E_EMAIL and E2E_PASSWORD to run');

  test('rejects a wrong password with a human sentence', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill('definitely-not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByText('Wrong email or password.')).toBeVisible();
  });

  test('signs in, shows the session, and signs out', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Signed-in chrome appears. The account's address is the title of Sign
    // out rather than a block of its own — at 360px the header is mark,
    // wordmark, two icons and that button, and it must not overflow.
    const signOut = page.getByRole('button', { name: 'Sign out' });
    await expect(signOut).toBeVisible();
    await expect(signOut).toHaveAttribute('title', EMAIL!);
    await expect(page.getByRole('heading', { name: 'VaiVia' })).toBeVisible();
    await expect(page.getByLabel('Your message')).toBeVisible();
    const header = page.locator('.app-header');
    expect(await header.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);

    // ...and sign-out drops back to the auth gate.
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByLabel('Password')).toBeVisible();
  });

  test('resumes the stored conversation and Clear chat starts fresh', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();

    // The single conversation resumes on sign-in (no tabs). History loads
    // asynchronously — wait for a user turn rather than sampling immediately,
    // which raced the fetch and false-skipped.
    const hasStored = await page
      .locator('.turn-user')
      .first()
      .waitFor({ state: 'visible', timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasStored, 'no stored conversation for this account yet');

    // "Clear chat" resets to the empty state with suggestions.
    await page.getByRole('button', { name: 'Clear chat' }).click();
    await expect(page.getByText('Ask for a trail the way you would ask a local.')).toBeVisible();
    await expect(page.locator('.turn-user')).toHaveCount(0);
  });

  test('streams a live turn end to end', async ({ page }) => {
    test.skip(!LIVE, 'set E2E_LIVE=1 to spend a real OpenAI turn');

    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();

    const input = page.getByLabel('Your message');
    // Loop wording on purpose: it is what reaches the pipeline catalogue.
    // "a hike up to a peak" reads as a named-trail search and answers from the
    // much smaller (:Trail) graph instead.
    await input.fill('a loop hike past a peak, 8 to 16 km, nothing harder than T3');
    await input.press('Enter');

    // A real streamed answer selected from the catalogue. The route that comes
    // back is whichever scores best on the day, so this pins the shape — a
    // card with a name and a distance — not one route's name.
    // Scoped to the transcript: the map layer's route panel renders the SAME
    // card component with the same class and the same data-route-id, so an
    // unscoped .first() re-resolves to the panel after the first click.
    const card = page.locator('.messages .route-card').first();
    await expect(card).toBeVisible({ timeout: 45_000 });
    await expect(card.locator('.route-name')).not.toBeEmpty();
    await expect(card.getByText('km')).toBeVisible();

    // Selecting it draws THAT route's geometry on the map — asserted by id,
    // not by the empty-state text disappearing: the "any card shows any map"
    // defect drew a different answer's routes and still passed that check.
    // MapView surfaces the focused route id as a data attribute for exactly
    // this assertion.
    const clickedId = await card.getAttribute('data-route-id');
    const layer = page.locator('.map-layer');
    await card.click();
    await expect(layer).toBeVisible();
    await expect(page.locator('.map-empty')).toBeHidden();
    await expect(page.locator('[data-selected-route]')).toHaveAttribute(
      'data-selected-route',
      clickedId!,
    );

    // The panel under the canvas is the same card, for the route that was
    // tapped, opened on arrival — the tap WAS the ask for its numbers.
    const panelCard = page.locator('.route-panel .route-card');
    await expect(panelCard).toHaveAttribute('data-route-id', clickedId!);
    await expect(panelCard.locator('.route-detail .profile i').first()).toBeVisible({
      timeout: 10_000,
    });

    // The canvas was resized to the layer's box as it opened: a fitBounds
    // computed against the old box is the defect this pins.
    const [canvasWidth, boxWidth] = await page.evaluate(() => [
      document.querySelector('.maplibregl-canvas')?.clientWidth ?? -1,
      document.querySelector('.map-canvas')?.clientWidth ?? -2,
    ]);
    expect(canvasWidth).toBe(boxWidth);

    // ODbL is on screen in BOTH states, never behind a toggle (BRAND-SPEC
    // §12): the app's own credit row, and the map's attribution while the map
    // is up.
    await expect(page.locator('.maplibregl-ctrl-attrib')).toBeVisible();
    await expect(page.locator('.data-credit')).toBeVisible();

    // The way back a finger can find: a labelled button on the layer itself
    // (owner decision 2026-09-08 — Escape and Back were the only exits and
    // neither is discoverable on a phone).
    await page.locator('.map-back').click();
    await expect(layer).toBeHidden();
    await card.click();
    await expect(layer).toBeVisible();

    // The map is a layer over the conversation, not a page. Escape lowers it,
    // the card stays picked underneath, and the browser's Back lowers it too —
    // which is what makes Android's back gesture do the obvious thing.
    await page.keyboard.press('Escape');
    await expect(layer).toBeHidden();
    await expect(page.locator('.data-credit')).toBeVisible();
    await expect(card).toHaveAttribute('aria-pressed', 'true');
    await card.click();
    await expect(layer).toBeVisible();
    await page.goBack();
    await expect(layer).toBeHidden();

    // The answer prose carries no links (fragilities.md #14): the model used
    // to invent trailforks.com links onto OSM-derived routes, and the strip
    // that stops it lives in the backend, so only a live turn exercises it.
    // The transcript renders content as plain text, so a surviving link shows
    // up as its markdown source — which is exactly what to assert against.
    const answer = await page.locator('.turn-assistant').last().innerText();
    expect(answer).not.toMatch(/https?:\/\/|]\(|trailforks/i);

    // Every card says which kind of outing it is (owner rule 2026-08-21) —
    // and since the catalogue reload, never the pre-1.2 'Named route'.
    const kind = await card.locator('.route-kind').innerText();
    expect(['LOOP', 'OUT & BACK', 'LINEAR']).toContain(kind.toUpperCase());

    // The transcript's own card still expands in place — "See more" draws the
    // line but leaves the reader where they are, and never raises the map.
    await card.locator('.detail-toggle').click();
    await expect(layer).toBeHidden();
    await expect(card.locator('.route-detail')).toBeVisible();
    await expect(card.locator('.route-detail .profile i').first()).toBeVisible({
      timeout: 10_000,
    });

    // When the search found more than the prose narrates, the fold offers
    // the rest five at a time. Not every ask overflows, so this is
    // conditional — but when the control is there, it must reveal.
    // .last(): a resumed transcript can hold older answers with their own
    // folds; the live turn's control is the one under test.
    const showMore = page.locator('.messages .show-more').last();
    if (await showMore.isVisible()) {
      const before = await page.locator('.messages .route-card').count();
      await showMore.click();
      await expect
        .poll(async () => page.locator('.messages .route-card').count())
        .toBeGreaterThan(before);
    }

    // The feedback loop, end to end: thumbs render once the turn is stored
    // (messageId arrives on `done`), a downvote asks what could be improved,
    // and the comment posts through the gateway into message_feedback.
    // Scoped to .messages: an open card asks the same question about its own
    // route, and the map panel's card — still mounted, just lowered — would
    // otherwise be the last .feedback in the document.
    const feedback = page
      .locator('.messages .feedback')
      .filter({ has: page.getByLabel('Bad answer') })
      .last();
    await expect(feedback).toBeVisible();
    await feedback.getByLabel('Bad answer').click();
    const wrong = feedback.getByLabel("What's wrong?");
    await expect(wrong).toBeVisible();
    await wrong.fill('e2e: automated check, please ignore');
    await feedback
      .getByLabel('How should it be instead?')
      .fill('e2e: also automated, also ignore');
    await feedback.getByRole('button', { name: 'Send' }).click();
    await expect(feedback.getByText('Noted — thank you')).toBeVisible();

    // Favorites round-trip: save the first card, find it in the saved view,
    // unsave it there — leaving the account as we found it.
    await card.getByLabel('Save this route').click();
    await page.getByRole('button', { name: 'Saved routes', exact: true }).click();
    const savedCard = page.locator('.favorites-view .route-card').first();
    await expect(savedCard).toBeVisible({ timeout: 10_000 });
    // A saved card raises the map the same way a chat card does; Escape puts
    // it back and the bookmark is reachable again.
    await savedCard.click();
    await expect(layer).toBeVisible();
    await page.keyboard.press('Escape');
    await savedCard.getByLabel('Remove from saved routes').click();
    // The card stays until the list reloads (an accidental tap is undoable),
    // but the bookmark must read unsaved at once.
    await expect(savedCard.getByLabel('Save this route')).toBeVisible();
  });
});
