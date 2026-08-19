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
    await input.fill('show me an intermediate mountain bike trail');
    await input.press('Enter');

    // A real streamed answer grounded in the graph: the fixture's mtb trail
    // surfaces as a card, and selecting it draws geometry on the map.
    const card = page.getByRole('heading', { name: 'Lago Loop' });
    await expect(card).toBeVisible({ timeout: 45_000 });
    await card.click();
    await expect(page.getByText('Pick a trail to see it drawn here.')).toBeHidden();
  });
});
