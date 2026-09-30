/**
 * Round‑30: dedicated photo page shows real source dimensions,
 * not "—".
 *
 * Background: the photo page's meta dl renders a "Dimensions" row
 * from `photo.width` × `photo.height`. Before this round the
 * indexer never wrote dims into the qdrant payload, so every
 * photo showed "—".
 *
 * Fix: `indexer.image_loader.load` now returns the source
 * `(width, height)` BEFORE the letterbox squashes the image, and
 * the indexer propagates those dims into the payload. The photo
 * page reads them straight from the qdrant payload.
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

test.describe('photo page dimensions (round‑30)', () => {
  test('dedicated photo page shows real source dimensions, not "—"', async ({
    page
  }) => {
    await page.goto(`${APP}/random`);
    await appReady(page);

    // Pick a photo and navigate to its dedicated page. Each
    // tile is an <a class="tile" href="/photo/{id}">.
    const firstTile = page.locator('a.tile[href^="/photo/"]').first();
    await firstTile.waitFor();
    const href = await firstTile.getAttribute('href');
    expect(href).toBeTruthy();
    if (!href) return;
    await page.goto(`${APP}${href}`);
    await appReady(page);

    // The meta dl includes a Dimensions row. It must NOT show
    // "—" — we just ingested with real dims and the random
    // photo is from the dev dataset.
    const dimsRow = page.locator('.meta dl dt', { hasText: 'Dimensions' });
    await dimsRow.waitFor({ timeout: 5000 });
    const ddText = (
      await page
        .locator('.meta dl dd')
        .filter({ hasText: /\d/ })
        .first()
        .textContent()
    )?.trim();
    expect(ddText).toBeTruthy();
    expect(ddText).not.toBe('—');
    // Match the "WIDTH × HEIGHT" or "WIDTHxHEIGHT" shape.
    expect(ddText).toMatch(/^\d+.*?[×x].*?\d+/);
  });
});
