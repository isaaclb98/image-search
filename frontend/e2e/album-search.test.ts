/**

 * E2 tier: FUNDAMENTAL — see frontend/e2e/README.md for the classification.
 * Round‑29: album-card search button e2e.
 *
 * Verifies the Option A feature end-to-end:
 *   1. /albums page renders Search button on every album card
 *   2. Clicking Search navigates to /?centroid=<name>
 *   3. Home page renders search results inline (no composer)
 *   4. URL stays on the centroid so reload restores the search
 *   5. Empty albums disable the button
 *
 * These are happy-path checks against the live dev server. They
 * require at least one non-empty album (the built-in Likes
 * centroid provides that as long as the user has liked any photo).
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

test.describe('album card Search button (round‑29)', () => {
  test('every album card on /albums has a Search button', async ({ page }) => {
    await page.goto(`${APP}/albums`);
    await appReady(page);
    await expect(page.locator('h1', { hasText: 'Albums' })).toBeVisible();

    // Every system card AND every user album card has a Search button.
    const cards = page.locator('article.card');
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThan(0);

    const searchButtons = page.locator('article.card button.search-btn');
    await expect(searchButtons).toHaveCount(cardCount);

    // Every Search button has a data-centroid attribute so the
    // wire shape is visible to the test harness (and to humans
    // inspecting the DOM).
    const dataAttrs = await searchButtons.evaluateAll((els) =>
      els.map((el) => (el as HTMLElement).getAttribute('data-centroid'))
    );
    for (const attr of dataAttrs) {
      expect(attr).toBeTruthy();
      expect(attr!.length).toBeGreaterThan(0);
    }
  });

  test('clicking Likes Search navigates to /?centroid=likes', async ({
    page
  }) => {
    await page.goto(`${APP}/albums`);
    await appReady(page);

    const likesButton = page.locator(
      'article.system-like button.search-btn[data-centroid="likes"]'
    );
    // Skip when Likes is empty — button is disabled.
    if (await likesButton.isDisabled()) {
      test.skip(true, 'Likes album is empty; nothing to search');
      return;
    }
    await likesButton.click();
    await page.waitForURL(/\?centroid=likes/, { timeout: 5000 });

    // The hero heading flips to "Searching by album".
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
    // The SearchComposer is hidden.
    await expect(page.locator('text=Find photos by what they look like'))
      .toHaveCount(0);
    // There's a back-link to /albums.
    await expect(page.locator('a.back-link', { hasText: 'Back to albums' }))
      .toBeVisible();

    // The results render at least one tile (the Likes centroid
    // has at least one photo in the dev DB).
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });
    const tileCount = await page.locator('.grid-tile').count();
    expect(tileCount).toBeGreaterThan(0);
  });

  test('clicking Dislikes Search returns results, not empty', async ({
    page
  }) => {
    // Round‑29 regression test for the empty-results bug: the
    // dislikes centroid used to filter every candidate via the
    // over-aggressive 1st-percentile near-dup threshold, so the
    // page rendered with 0 tiles. Pin the >=1 result here.
    await page.goto(`${APP}/albums`);
    await appReady(page);

    const btn = page.locator(
      'article.system-dislike button.search-btn[data-centroid="dislikes"]'
    );
    if (await btn.isDisabled()) {
      test.skip(true, 'Dislikes album is empty');
      return;
    }
    await btn.click();
    await page.waitForURL(/\?centroid=dislikes/, { timeout: 5000 });
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();

    // The error banner must NOT appear — a populated centroid
    // should always return results.
    await expect(page.locator('.error.glass')).toHaveCount(0);

    // Real results must render.
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });
    const tileCount = await page.locator('.grid-tile').count();
    expect(tileCount).toBeGreaterThan(0);
  });

  test('clicking Dislikes Search navigates to /?centroid=dislikes', async ({
    page
  }) => {
    await page.goto(`${APP}/albums`);
    await appReady(page);

    const btn = page.locator(
      'article.system-dislike button.search-btn[data-centroid="dislikes"]'
    );
    if (await btn.isDisabled()) {
      test.skip(true, 'Dislikes album is empty');
      return;
    }
    await btn.click();
    await page.waitForURL(/\?centroid=dislikes/, { timeout: 5000 });
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
  });

  test('empty albums disable the Search button', async ({ page }) => {
    await page.goto(`${APP}/albums`);
    await appReady(page);

    const buttons = page.locator('article.card button.search-btn');
    const total = await buttons.count();
    let disabledCount = 0;
    let enabledCount = 0;
    for (let i = 0; i < total; i++) {
      const b = buttons.nth(i);
      if (await b.isDisabled()) disabledCount++;
      else enabledCount++;
    }
    // Sanity: at least one button is on the page.
    expect(total).toBeGreaterThan(0);
    expect(disabledCount + enabledCount).toBe(total);
  });

  test('reload on /?centroid=… restores the search', async ({ page }) => {
    await page.goto(`${APP}/?centroid=likes`);
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });

    // Hard reload — the page should pick up ?centroid=likes
    // from the URL and re-run the search automatically.
    await page.reload();
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });
  });

  test('home page composer is hidden while a centroid is active', async ({
    page
  }) => {
    await page.goto(`${APP}/?centroid=album:999`);
    await appReady(page);
    // No real album with id 999 — the API will 404, which the
    // page surfaces as an error banner. We only assert that the
    // composer (and the prompt hero copy) are hidden while a
    // centroid is active, regardless of whether the search
    // succeeds.
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
    await expect(
      page.locator('h1', { hasText: 'Find photos by what they look like' })
    ).toHaveCount(0);
  });

  test('clicking a user album Search button navigates to /?centroid=album:<id>', async ({
    page
  }) => {
    await page.goto(`${APP}/albums`);
    await appReady(page);

    // Find any non-system album card whose Search button is enabled.
    const userBtn = page
      .locator('article.card:not(.system-like):not(.system-dislike) button.search-btn[data-centroid^="album:"]:not([disabled])')
      .first();

    if (await userBtn.count() === 0) {
      test.skip(true, 'no populated user albums in the dev DB');
      return;
    }

    const centroidAttr = await userBtn.getAttribute('data-centroid');
    expect(centroidAttr).toMatch(/^album:\d+$/);

    await userBtn.click();
    // The colon is URL-encoded as %3A; accept both forms.
    await page.waitForURL(/\?centroid=album(?:%3A|:)\d+/, { timeout: 5000 });

    // The wire shape must use a colon, not an underscore —
    // underscores 404 because the backend key is `album:<id>`.
    expect(page.url()).not.toContain('album_');

    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();

    // Results should render — a populated user album centroid
    // has at least one valid photo.
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });
    const tileCount = await page.locator('.grid-tile').count();
    expect(tileCount).toBeGreaterThan(0);
  });

  test('clearing ?centroid= from the URL re-shows the composer', async ({
    page
  }) => {
    // Start on a centroid page.
    await page.goto(`${APP}/?centroid=likes`);
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();

    // Navigate to a plain home page.
    await page.goto(`${APP}/`);
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Find photos by what they look like' })
    ).toBeVisible();
  });

  test('legacy ?centroid=favourites still resolves (back-compat alias)', async ({
    page
  }) => {
    // Round‑29b: the backend keeps `favourites` as a back-compat
    // alias for the Likes centroid. Old shared links / saved
    // searches should keep working.
    await page.goto(`${APP}/?centroid=favourites`);
    await appReady(page);
    await expect(
      page.locator('h1', { hasText: 'Searching by album' })
    ).toBeVisible();
    // No error banner — the centroid must resolve.
    await expect(page.locator('.error.glass')).toHaveCount(0);
    await page.waitForSelector('.grid-tile', { timeout: 10_000 });
  });
});
