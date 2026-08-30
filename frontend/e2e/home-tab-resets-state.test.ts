/**

 * E2 tier: EXPLORATORY — not a CI gate; failures allowed (see AGENTS.md and frontend/e2e/README.md).
 * Round‑29c: Home tab navigates back to a clean home (clears
 * ?positives=, ?negatives=, ?diversity=, ?centroid=, etc.) even
 * when the pathname is already "/".
 *
 * Background: a plain `<a href="/">` from `/?positives=blue`
 * is a no-op for the browser + SvelteKit because the pathname
 * is the same — the URL doesn't change, the page doesn't
 * re-load, and the user is stuck on the search results page.
 * The fix uses `goto('/')` from $app/navigation, which always
 * triggers a route load regardless of whether the URL changed.
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

test.describe('Home tab clears URL state (round‑29c)', () => {
  test('clicking Home from /?positives=… returns to default home', async ({
    page
  }) => {
    // Start on a populated search-results page.
    await page.goto(
      `${APP}/?positives=blue&positives=dress&positives=dynamic&negatives=blurry%2C+smeared&diversity=balanced`
    );
    await appReady(page);

    // The composer is visible (proves the search results page
    // is actually showing).
    await expect(
      page.locator('h1', { hasText: 'Find photos by what they look like' })
    ).toBeVisible();

    // Click Home in the top tab bar.
    await page.locator('header.topbar nav.tabs a.tab', { hasText: 'Home' })
      .click();

    // URL must be just "/" — query string cleared.
    await page.waitForURL((url) => url.pathname === '/' && url.search === '', {
      timeout: 5000
    });

    // Page should be in the default home state — composer
    // visible, no results grid (composer hasn't run a search).
    await expect(
      page.locator('h1', { hasText: 'Find photos by what they look like' })
    ).toBeVisible();
    // The "Searching by album" hero should NOT be visible.
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toHaveCount(0);
    // No tiles — composer state cleared, no auto-search ran.
    await expect(page.locator('.grid-tile')).toHaveCount(0);
  });

  test('clicking Home from /?centroid=album:3 returns to default home', async ({
    page
  }) => {
    // Same fix should apply to centroid URLs.
    await page.goto(`${APP}/?centroid=album:3`);
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();

    await page.locator('header.topbar nav.tabs a.tab', { hasText: 'Home' })
      .click();
    await page.waitForURL((url) => url.pathname === '/' && url.search === '', {
      timeout: 5000
    });
    await expect(
      page.locator('h1', { hasText: 'Find photos by what they look like' })
    ).toBeVisible();
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toHaveCount(0);
  });

  test('clicking Home from /random navigates correctly (regression)', async ({
    page
  }) => {
    // Make sure the Home tab doesn't accidentally clobber
    // navigation away from a different pathname.
    await page.goto(`${APP}/random`);
    await appReady(page);
    await page.locator('header.topbar nav.tabs a.tab', { hasText: 'Home' })
      .click();
    await page.waitForURL(`${APP}/`, { timeout: 5000 });
  });

  test('Home tab stays a real <a> with href "/" (modifier-click still works)', async ({
    page
  }) => {
    // Skipped if the browser doesn't expose the modifier reliably;
    // we just verify the tab is still a real <a> with an href so
    // Cmd/Ctrl-click opens it in a new tab via the browser default.
    await page.goto(`${APP}/`);
    await appReady(page);
    const homeTab = page.locator(
      'header.topbar nav.tabs a.tab[href="/"]'
    );
    await homeTab.waitFor();
    const href = await homeTab.getAttribute('href');
    expect(href).toBe('/');
  });
});
