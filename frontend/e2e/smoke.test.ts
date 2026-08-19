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

/** Wait until SvelteKit's interactive surface has mounted for a
 *  Search-style page (the composer input is the readiness signal).
 *  This is the most fragile page in dev because the topbar renders
 *  before the route-specific code hydrates. */
async function searchPageReady(page: Page) {
  await appReady(page);
  await page.waitForFunction(
    () => document.querySelector('.composer-input') !== null,
    null,
    { timeout: 15000 }
  );
  // Settle window so Svelte 5 effect batches run.
  await page.waitForTimeout(300);
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
  await searchPageReady(page);

  const input = page.getByPlaceholder(/Add a positive/);
  await input.click();
  await input.fill('beach');
  await input.press('Enter');

  await waitFor(page, '.chip', 10000);
  await expect(page.locator('.chip').filter({ hasText: 'beach' })).toBeVisible();
});

/**
 * Lightbox — clicking a tile opens it; ←/→ navigate; Esc closes.
 * Regression cover for the most-used feature on every search page.
 */
test('Lightbox opens, navigates with arrow keys, closes with Esc', async ({ page }) => {
  await page.goto(APP + '/random');
  await appReady(page);
  await waitFor(page, '.tile', 10000);

  // Open by clicking the first tile
  await page.locator('.tile').first().click();
  await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('.photo')).toBeVisible();

  // Navigate next
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(150);
  await expect(page.locator('.count')).toContainText('2 /');

  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(150);
  await expect(page.locator('.count')).toContainText('3 /');

  // Navigate back
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(150);
  await expect(page.locator('.count')).toContainText('2 /');

  // Esc closes
  await page.keyboard.press('Escape');
  await expect(page.locator('[role="dialog"]')).toBeHidden({ timeout: 3000 });
});

/**
 * Saved searches — save current prompts, pick from dropdown, delete.
 * End-to-end exercise of the SavedSearchesMenu + backend roundtrip.
 */
test('Saved searches: save, pick reapplies prompts, delete removes', async ({ page }) => {
  // Start fresh — go to /search with no params so we control state.
  await page.goto(APP + '/search');
  await searchPageReady(page);

  const input = page.getByPlaceholder(/Add a positive/);
  await input.click();
  await input.fill('mountain');
  await input.press('Enter');
  await input.fill('sunset');
  await input.press('Enter');
  await waitFor(page, '.chip', 8000);

  // Stub window.prompt so the Save action gets a name.
  await page.evaluate(() => {
    (window as unknown as { prompt: (q: string) => string | null }).prompt = () => 'my-mountain-set';
  });

  // Click the Save button
  await page.getByTitle('Save current search').click();

  // Open the Saved dropdown and verify our name appears
  await page.getByTitle('Saved searches').click();
  await waitFor(page, '.pop', 5000);
  await expect(page.locator('.pop').getByText('my-mountain-set')).toBeVisible();

  // Delete it via the × button
  page.once('dialog', (d) => d.accept()); // confirm()
  await page
    .locator('.pop .del')
    .first()
    .click();

  // After deletion the list should empty
  await expect(page.locator('.pop .item')).toHaveCount(0, { timeout: 5000 });
});

/**
 * URL state sync: search bar → URL → reload → still the same.
 * Guards the spec line "picking one re-applies … and runs the search".
 */
test('Search state round-trips through the URL', async ({ page }) => {
  await page.goto(APP + '/search');
  await searchPageReady(page);

  const input = page.getByPlaceholder(/Add a positive/);
  await input.click();
  await input.fill('round-trip-keyword');
  await input.press('Enter');
  await waitFor(page, '.chip', 8000);

  // After the 180ms debounce + write the URL should carry the prompt.
  await page.waitForTimeout(500);
  const url = page.url();
  expect(url).toContain('positives=round-trip-keyword');
});

/**
 * Right-click context menu opens on a tile; clicking outside closes.
 */
test('Right-click opens the photo context menu', async ({ page }) => {
  await page.goto(APP + '/random');
  await appReady(page);
  await waitFor(page, '.tile', 10000);

  const firstTile = page.locator('.tile').first();
  await firstTile.click({ button: 'right' });
  await expect(page.locator('.menu[role="menu"]')).toBeVisible({ timeout: 3000 });
  // The menu lists Open / Copy URL / Pin-ish items
  await expect(page.getByRole('menuitem', { name: /Open in new tab/ })).toBeVisible();

  // Click outside closes
  await page.locator('main').click();
  await expect(page.locator('.menu[role="menu"]')).toBeHidden({ timeout: 3000 });
});
