/**
 * Core E2E: the "Add to album" dropdown on /photo/[id] must stay
 * inside the viewport — bounded with a scrollbar, never running off
 * the top of the screen.
 *
 * Regression: the menu (align="up") computed top = trigger.top -
 * GAP - height with no lower bound; with enough albums the CSS
 * 60vh cap still left the top edge negative on short viewports /
 * high triggers.
 *
 * Zero data pollution: /api/albums is route-mocked with 60 fake
 * albums; the photo page itself is a real (read-only) photo.
 */
import { test, expect } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173';

async function mockAlbums(page: import('@playwright/test').Page, n = 60) {
  await page.route('**/api/albums*', (route) => {
    const url = route.request().url();
    // Only mock the plain list; let /api/albums/{id} etc. pass.
    if (/\/api\/albums\/?\??.*$/.test(url) && !/\/api\/albums\/\d/.test(url)) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          albums: Array.from({ length: n }, (_, i) => ({
            id: 900000 + i,
            name: `Test Album ${String(i + 1).padStart(2, '0')}`,
            photo_count: 3,
          })),
        }),
      });
    }
    return route.continue();
  });
}

async function realPhotoId(page: import('@playwright/test').Page) {
  const r = await page.request.get(`${APP}/api/random?limit=1`);
  expect(r.ok()).toBeTruthy();
  const d = await r.json();
  const items = d.results ?? d.items ?? d;
  expect(items.length).toBeGreaterThan(0);
  return items[0].id as string;
}

async function openMenuAndGetRect(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /add to album/i }).click();
  const menu = page.locator('[role="menu"]');
  await expect(menu).toBeVisible({ timeout: 5000 });
  await page.waitForTimeout(150); // let the post-mount clamp settle
  const box = await menu.boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

test('add-to-album menu stays inside a normal viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockAlbums(page);
  const id = await realPhotoId(page);
  await page.goto(`${APP}/photo/${id}`);
  await page.waitForSelector('.actions', { timeout: 20000 });

  const box = await openMenuAndGetRect(page);
  const vh = 800;
  expect(box.y, 'menu top off-screen').toBeGreaterThanOrEqual(0);
  expect(box.y + box.height, 'menu bottom off-screen').toBeLessThanOrEqual(vh);
});

test('add-to-album menu stays inside a SHORT viewport (bounded + scrollable)', async ({ page }) => {
  // The stress case: little room above AND below the trigger.
  await page.setViewportSize({ width: 900, height: 420 });
  await mockAlbums(page);
  const id = await realPhotoId(page);
  await page.goto(`${APP}/photo/${id}`);
  await page.waitForSelector('.actions', { timeout: 20000 });

  const box = await openMenuAndGetRect(page);
  const vh = 420;
  expect(box.y, 'menu top off-screen').toBeGreaterThanOrEqual(0);
  expect(box.y + box.height, 'menu bottom off-screen').toBeLessThanOrEqual(vh);

  // Bounded ⇒ the list must be scrollable rather than truncated.
  const scrollable = await page.locator('[role="menu"]').evaluate((el) => {
    const s = getComputedStyle(el);
    return (
      (s.overflowY === 'auto' || s.overflowY === 'scroll') &&
      el.scrollHeight > el.clientHeight + 1
    );
  });
  expect(scrollable, 'clamped menu should scroll').toBeTruthy();
});
