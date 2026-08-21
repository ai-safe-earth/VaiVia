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

    // Signed-in chrome appears...
    await expect(page.getByText(EMAIL!)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'VaiVia' })).toBeVisible();
    await expect(page.getByLabel('Your message')).toBeVisible();

    // ...and sign-out drops back to the auth gate.
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByLabel('Password')).toBeVisible();
  });

  test('resumes a stored conversation with its history', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByText(EMAIL!)).toBeVisible();

    const conversations = page.getByRole('navigation', { name: 'Conversations' });
    const stored = conversations.getByRole('button').nth(1); // 0 is "+ New chat"
    // The list loads asynchronously after sign-in — wait for it rather than
    // sampling visibility immediately, which raced the fetch and false-skipped.
    const hasStored = await stored
      .waitFor({ state: 'visible', timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasStored, 'no stored conversation for this account yet');

    await stored.click();
    // History renders at least one user turn without any network turn.
    await expect(page.locator('.turn-user').first()).toBeVisible();

    // "+ New chat" resets to the empty state with suggestions.
    await conversations.getByRole('button', { name: '+ New chat' }).click();
    await expect(page.getByText('Ask for a trail the way you would ask a local.')).toBeVisible();
  });

  test('streams a live turn end to end', async ({ page }) => {
    test.skip(!LIVE, 'set E2E_LIVE=1 to spend a real OpenAI turn');

    await page.goto('/');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByText(EMAIL!)).toBeVisible();

    const input = page.getByLabel('Your message');
    // Loop wording on purpose: it is what reaches the pipeline catalogue.
    // "a hike up to a peak" reads as a named-trail search and answers from the
    // much smaller (:Trail) graph instead.
    await input.fill('a loop hike past a peak, 8 to 16 km, nothing harder than T3');
    await input.press('Enter');

    // A real streamed answer selected from the catalogue. The route that comes
    // back is whichever scores best on the day, so this pins the shape — a
    // card with a name and a distance — not one route's name.
    const card = page.locator('.route-card').first();
    await expect(card).toBeVisible({ timeout: 45_000 });
    await expect(card.locator('.route-name')).not.toBeEmpty();
    await expect(card.getByText('km')).toBeVisible();

    // Selecting it draws the route document's geometry on the map.
    await card.click();
    await expect(page.getByText('Pick a trail to see it drawn here.')).toBeHidden();

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

    // Expanding reveals the full card, with the altitude profile fetched
    // from the route document via /routes/{id}/detail.
    await card.locator('.detail-toggle').click();
    await expect(card.locator('.route-detail')).toBeVisible();
    await expect(card.locator('.route-detail .profile i').first()).toBeVisible({
      timeout: 10_000,
    });

    // When the search found more than the prose narrates, the fold offers
    // the rest five at a time. Not every ask overflows, so this is
    // conditional — but when the control is there, it must reveal.
    const showMore = page.locator('.show-more');
    if (await showMore.isVisible()) {
      const before = await page.locator('.route-card').count();
      await showMore.click();
      await expect
        .poll(async () => page.locator('.route-card').count())
        .toBeGreaterThan(before);
    }

    // Favorites round-trip: save the first card, find it in the saved view,
    // unsave it there — leaving the account as we found it.
    await card.getByLabel('Save this route').click();
    await page.getByRole('button', { name: 'Saved routes', exact: true }).click();
    const savedCard = page.locator('.favorites-view .route-card').first();
    await expect(savedCard).toBeVisible({ timeout: 10_000 });
    await savedCard.getByLabel('Remove from saved routes').click();
    // The card stays until the list reloads (an accidental tap is undoable),
    // but the bookmark must read unsaved at once.
    await expect(savedCard.getByLabel('Save this route')).toBeVisible();
  });
});
