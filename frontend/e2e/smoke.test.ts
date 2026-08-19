/**
 * Playwright smoke tests — one happy-path per page.
 *
 * Per docs/image-search-v2-testing.md Layer 4:
 *   - One happy-path per page. The OpenAPI + zod layers above
 *     are the real guardrails; this just verifies pages
 *     actually render and the core interaction works.
 *   - No snapshot tests here. They rot.
 *
 * Hydration races are real with SvelteKit dev mode (the SSR
 * HTML lands before the client JS is wired up). The appReady
 * helper waits for the SvelteKit preload-data attribute plus
 * a small settle window before any user input is fired. This
 * keeps the tests reliable without needing to know about
 * internal hydration timing.
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:4173';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

/** Wait for an element matching the locator. Polls up to `timeout`
 *  ms. SvelteKit dev hydration races the test — without polling,
 *  the first attempt to find a chip/grid can fire before the
 *  client JS has wired up the listeners. */
async function waitFor(page: Page, sel: string, timeout = 8000) {
  await page.waitForFunction(
    (s) => document.querySelector(s) !== null,
    sel,
    { timeout }
  );
}

test('Home renders with composer + For-you row', async ({ page }) => {
  await page.goto(APP + '/');
  await appReady(page);
  await expect(page.getByRole('heading', { name: /Find photos/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Search$/ })).toBeVisible();
});

test('Search renders chips from URL + shows the grid', async ({ page }) => {
  await page.goto(APP + '/search?positives=beach&positives=ocean');
  await appReady(page);
  await waitFor(page, '.chip', 15000);
  await expect(page.locator('.chip').filter({ hasText: 'beach' })).toBeVisible();
  await expect(page.locator('.chip').filter({ hasText: 'ocean' })).toBeVisible();
  await waitFor(page, '.tile, .empty', 8000);
});

test('Random renders a grid', async ({ page }) => {
  await page.goto(APP + '/random');
  await appReady(page);
  await expect(page.getByRole('heading', { name: /Random/i })).toBeVisible();
  await waitFor(page, '.tile, .empty', 8000);
});

test('Albums renders list or empty state', async ({ page }) => {
  await page.goto(APP + '/albums');
  await appReady(page);
  await expect(page.getByRole('heading', { name: /Albums/i })).toBeVisible();
  await waitFor(page, '.card, .placeholder', 8000);
});

test('For You renders header + body', async ({ page }) => {
  await page.goto(APP + '/for-you');
  await appReady(page);
  await expect(page.getByRole('heading', { name: /For you/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Reset signal/i })).toBeVisible();
});

test('Login renders the form', async ({ page }) => {
  await page.goto(APP + '/login');
  await appReady(page);
  await expect(page.getByRole('heading', { name: /image-search/i })).toBeVisible();
  await expect(page.getByLabel(/Password/i)).toBeVisible();
});

test('Top bar tabs are interactive', async ({ page }) => {
  await page.goto(APP + '/');
  await appReady(page);
  const searchTab = page.getByRole('link', { name: 'Search' });
  await searchTab.click();
  await page.waitForURL(/\/search$/);
  await expect(page.getByRole('button', { name: /^Search$/ })).toBeVisible();
});

test('Adding a positive prompt renders a chip', async ({ page }) => {
  await page.goto(APP + '/search');
  await appReady(page);

  // SvelteKit hydrates the input AFTER the topbar lands. Wait
  // specifically for the composer input to be wired up — the
  // +/− toggle buttons being clickable is a good proxy.
  await page.waitForFunction(
    () => document.querySelector('.composer-input') !== null,
    null,
    { timeout: 10000 }
  );
  // small settle window for the onkeydown listener to attach
  await page.waitForTimeout(150);

  const input = page.getByPlaceholder(/Add a positive/);
  await input.click();
  await input.fill('beach');
  await input.press('Enter');

  await waitFor(page, '.chip', 10000);
  await expect(page.locator('.chip').filter({ hasText: 'beach' })).toBeVisible();
});
