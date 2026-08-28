/**
 * Round‑31: backdrop colour bleed.
 *
 * Two layers:
 *  1. Grid pages (/, /random, /for-you, /similar, /albums/*)
 *     push the most-recently-in-view tile's blurhash into the
 *     pageTint store, so the backdrop has a colour wash even
 *     with no lightbox open.
 *  2. Opening a lightbox pushes the active photo's full
 *     /photo/{id}/raw URL instead — a heavier, more detailed
 *     blur than the blurhash-derived 64×40 data URL.
 *  3. Closing the lightbox falls back to the grid tint (or
 *     fades to black on a page with no grid).
 */

import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

async function backdropImgSrc(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    const img = document.querySelector('.bg-backdrop img');
    return img ? (img as HTMLImageElement).src : null;
  });
}

test.describe('backdrop tint (round‑31)', () => {
  test('grid page has a colour tint before any lightbox opens', async ({
    page
  }) => {
    // Round‑31: every page with a PhotoGrid now pushes the
    // most-recently-in-view tile's blurhash to pageTint so the
    // backdrop shows a colour wash (not solid black) even
    // without a lightbox.
    await page.goto(`${APP}/random`);
    await appReady(page);
    // Allow the blurhash decode + push to settle.
    await page.waitForTimeout(500);

    const src = await backdropImgSrc(page);
    expect(src).toBeTruthy();
    // It's a blurhash-derived data URL, NOT a /photo/.../raw URL.
    expect(src).toMatch(/^data:image\/png/);
  });

  test('opening the lightbox swaps the grid tint for a /raw URL', async ({
    page
  }) => {
    await page.goto(`${APP}/random`);
    await appReady(page);
    await page.waitForTimeout(500);
    expect(await backdropImgSrc(page)).toMatch(/^data:image\/png/);

    await page.locator('a.tile').first().click();
    await page.waitForTimeout(500);

    const opened = await backdropImgSrc(page);
    expect(opened).toBeTruthy();
    // The lightbox wins — backdrop now points at the full raw
    // photo URL (heavier blur than the blurhash tint).
    expect(opened).toMatch(/\/photo\/[^/]+\/raw/);

    await page.screenshot({ path: '/tmp/round31-tint-open.png' });

    // Closing the lightbox: the grid tint effect re‑engages and
    // pushes the new top item's blurhash (which may or may not
    // match the originally‑closed‑lightbox photo). The crucial
    // assertion is that we leave the data-URL domain (no longer
    // the /raw URL of the lightbox photo).
    await page.keyboard.press('Escape');
    await page.waitForTimeout(600);
    const afterClose = await backdropImgSrc(page);
    expect(afterClose).toMatch(/^data:image\/png/);
  });

  test('lightbox keeps a /raw backdrop while open (open and after 1s)', async ({
    page
  }) => {
    // The lightbox owns its own idx state and navigates
    // internally without round‑tripping through PhotoGrid's
    // lightboxIndex. So the backdrop URL doesn't change on
    // arrow‑right navigation inside the lightbox — but it
    // stays pointed at the active photo's /raw. This test pins
    // the "stays pointed at a /raw URL" contract.
    await page.goto(`${APP}/random`);
    await appReady(page);
    await page.waitForTimeout(500);

    const firstTile = page.locator('a.tile').first();
    await firstTile.waitFor();
    await firstTile.click();
    await page.waitForTimeout(500);

    const first = await backdropImgSrc(page);
    expect(first).toMatch(/\/photo\/[^/]+\/raw/);

    // Wait + re-read; backdrop should still be a /raw URL (it
    // doesn't get cleared by the grid‑tint effect while a
    // lightbox is open).
    await page.waitForTimeout(1000);
    const later = await backdropImgSrc(page);
    expect(later).toMatch(/\/photo\/[^/]+\/raw/);
    expect(later).toBe(first);

    await page.keyboard.press('Escape');
  });

  test('/albums has a colour tint even without a PhotoGrid', async ({
    page
  }) => {
    // Round‑31 follow-up: /albums doesn't render a PhotoGrid,
    // so it doesn't trigger the grid‑tint effect. The page
    // explicitly calls pushRandomTint() in onMount to give the
    // backdrop a colour wash.
    await page.goto(`${APP}/albums`);
    await appReady(page);
    // Allow the /api/random fetch + blurhash decode to settle.
    await page.waitForTimeout(800);

    const src = await backdropImgSrc(page);
    expect(src).toBeTruthy();
    expect(src).toMatch(/^data:image\/png/);
  });

  test('/photo/{id} has a colour tint derived from its own blurhash', async ({
    page
  }) => {
    // The dedicated photo page pushes its own photo's blurhash
    // to pageTint (more relevant than a random one).
    await page.goto(`${APP}/random`);
    await appReady(page);
    const firstTile = page.locator('a.tile').first();
    await firstTile.waitFor();
    const href = await firstTile.getAttribute('href');
    expect(href).toBeTruthy();
    if (!href) return;
    await page.goto(`${APP}${href}`);
    await appReady(page);
    await page.waitForTimeout(800);

    const src = await backdropImgSrc(page);
    expect(src).toBeTruthy();
    expect(src).toMatch(/^data:image\/png/);
  });
});

