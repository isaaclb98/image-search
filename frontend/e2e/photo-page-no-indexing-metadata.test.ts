/**

 * E2 tier: EXPLORATORY — not a CI gate; failures allowed (see AGENTS.md and frontend/e2e/README.md).
 * Round‑31: dedicated photo page must NOT show indexing metadata.
 *
 * Removed: "Indexed …", "Indexed by" / model name, vector dim,
 * collection name, Revision, ID.
 * Kept: Dimensions + Size.
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

test.describe('photo page metadata (round‑31)', () => {
  test('photo page only shows Dimensions + Size, no indexing junk', async ({
    page
  }) => {
    // Pick a real photo from /random.
    await page.goto(`${APP}/random`);
    await appReady(page);
    const firstTile = page.locator('a.tile[href^="/photo/"]').first();
    await firstTile.waitFor();
    const href = await firstTile.getAttribute('href');
    expect(href).toBeTruthy();
    if (!href) return;
    await page.goto(`${APP}${href}`);
    await appReady(page);

    // Required rows still present.
    await expect(
      page.locator('.meta dl dt', { hasText: 'Dimensions' })
    ).toBeVisible();
    await expect(
      page.locator('.meta dl dt', { hasText: 'Size' })
    ).toBeVisible();

    // Removed rows must be gone.
    await expect(
      page.locator('.meta dl dt', { hasText: 'Indexed' })
    ).toHaveCount(0);
    await expect(
      page.locator('.meta h3', { hasText: 'Indexed by' })
    ).toHaveCount(0);
    await expect(
      page.locator('.meta dl dt', { hasText: 'Revision' })
    ).toHaveCount(0);
    await expect(page.locator('.meta dl dt', { hasText: 'ID' })).toHaveCount(
      0
    );

    // The "Indexing details" section is entirely gone.
    await expect(
      page.locator('.meta[aria-label="Indexing details"]')
    ).toHaveCount(0);
  });
});
