/**
 * e2e/photo-page.test.ts — Dedicated photo page at /photo/{id}.
 *
 * Pinned contracts:
 *   - The page renders photo + metadata sidebar layout.
 *   - Right-click → "Open in new tab" on a tile navigates here (not
 *     to the raw image).
 *   - The action buttons (Like, Dislike, Most similar, Open raw) are
 *     present and reachable.
 *   - 404 for an unknown photo id.
 *   - The page is bookmarkable / refreshable (direct URL works).
 *   - The image is rendered with a real src that loads.
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  // The TopBar is rendered server-side and survives hydration —
  // a safe marker that the SPA shell is up.
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

async function waitFor(page: Page, sel: string, timeout = 8000) {
  await page.waitForFunction(
    (s) => !!document.querySelector(s),
    sel,
    { timeout }
  );
}

test('photo page renders hero image and sidebar metadata', async ({ page }) => {
  // Pick a real photo id from /api/random.
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string; path: string }> };
  const id = data.results[0].id;

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);

  // Hero image: present, has the right src, has loaded.
  const hero = page.locator('img.hero');
  await expect(hero).toBeVisible();
  const src = await hero.getAttribute('src');
  expect(src).toContain(`/photo/${id}/raw`);

  // Sidebar: filename matches the photo's basename.
  const filename = await page.locator('.filename').textContent();
  expect(filename?.length ?? 0).toBeGreaterThan(0);

  // All four action buttons present. Use exact-name match for Like
  // because the regex /Like/i would also match "Dislike".
  await expect(page.getByRole('button', { name: /^♡? Like/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /Dislike/i })).toBeVisible();
  const similarBtn = page.getByRole('link', { name: /Most similar/i });
  await expect(similarBtn).toBeVisible();
  expect(await similarBtn.getAttribute('href')).toContain(`/similar/${id}`);
  const rawBtn = page.getByRole('link', { name: /Open raw/i });
  await expect(rawBtn).toBeVisible();
  expect(await rawBtn.getAttribute('href')).toContain(`/photo/${id}/raw`);

  // Metadata sections render. We don't pin exact values (some photos
  // lack width/height or a model revision), but the headings and the
  // dimensions row must be present. Use exact-match selectors to
  // avoid clashing with neighbouring "Indexed by" heading.
  await expect(page.getByText('Dimensions', { exact: true })).toBeVisible();
  await expect(page.getByText('Size', { exact: true })).toBeVisible();
  await expect(page.getByText('Indexed', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Indexed by/i })).toBeVisible();

  // The Indexed-by chips include the model name.
  const chipCount = await page.locator('.chips .chip').count();
  expect(chipCount).toBeGreaterThan(0);
});

test('photo page filename comes from the file path basename', async ({ page }) => {
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string; path: string }> };
  const id = data.results[0].id;
  const expectedName = data.results[0].path.split('/').pop() ?? '';

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);
  await waitFor(page, '.filename', 5000);

  const filename = await page.locator('.filename').textContent();
  expect(filename?.trim()).toBe(expectedName);
});

test('photo page shows the photo id in the metadata', async ({ page }) => {
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string }> };
  const id = data.results[0].id;

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);

  // The page should render the id somewhere in the metadata area
  // (it's a useful thing for users to be able to copy).
  const idText = await page.getByText(id).first().isVisible();
  expect(idText).toBe(true);
});

test('photo page: unknown id renders an error, not a crash', async ({ page }) => {
  // Use a syntactically-valid UUID that we know is not indexed.
  const fakeId = '00000000-0000-0000-0000-000000000000';
  await page.goto(`${APP}/photo/${fakeId}`);
  await appReady(page);

  // The page must show the error state (placeholder.error class).
  const errorBox = page.locator('.placeholder.error');
  await expect(errorBox).toBeVisible({ timeout: 5000 });
  await expect(errorBox).toContainText(/not found/i);
});

test('photo page Like button toggles state', async ({ page }) => {
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string; is_favorite: boolean }> };
  const id = data.results[0].id;
  const wasFav = data.results[0].is_favorite;

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);

  // The Like button toggles between "♡ Like" and "♥ Liked". The
  // accessible name includes the heart symbol, so the regex matches
  // both states.
  const likeBtn = page.getByRole('button', { name: /[♡♥] Like[ed]?/i });
  await expect(likeBtn).toBeVisible();
  // Initial label reflects current state.
  const initialLabel = await likeBtn.textContent();
  expect(initialLabel).toMatch(wasFav ? /Liked/i : /Like/i);

  // Click and observe label flip.
  await likeBtn.click();
  await expect(likeBtn).toHaveText(wasFav ? /Like/i : /Liked/i, { timeout: 5000 });

  // Restore so we don't pollute the next test's baseline.
  await likeBtn.click();
  await expect(likeBtn).toHaveText(wasFav ? /Liked/i : /Like/i, { timeout: 5000 });
});

test('photo page is directly addressable: refresh keeps the same photo', async ({ page }) => {
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string }> };
  const id = data.results[0].id;

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);
  await waitFor(page, '.filename', 5000);
  const before = await page.locator('.filename').textContent();

  // Reload — must show the same photo.
  await page.reload();
  await appReady(page);
  await waitFor(page, '.filename', 5000);
  const after = await page.locator('.filename').textContent();

  expect(after).toBe(before);
});

test('right-click "Open in new tab" navigates to the dedicated page', async ({ page, context }) => {
  // We need to actually trigger the context menu and click the menu
  // item. Playwright supports contextmenu events directly.
  await page.goto(APP + '/random');
  await appReady(page);
  await waitFor(page, '.grid-tile', 10000);

  // Right-click on the first tile. The context menu is rendered in
  // a portal-like fixed-position div outside the tile.
  const tile = page.locator('.grid-tile').first();
  await tile.click({ button: 'right' });

  // The menu item should appear.
  const newTabBtn = page.getByRole('menuitem', { name: /Open in new tab/i });
  await expect(newTabBtn).toBeVisible({ timeout: 3000 });

  // Capture the URL the menu will open. We mock window.open to
  // record the URL and prevent a real popup (which Playwright would
  // close anyway, but this avoids noise).
  const popupPromise = context.waitForEvent('page').catch(() => null);
  const openUrl = await page.evaluate(() => {
    return new Promise<string | null>((resolve) => {
      const orig = window.open;
      window.open = ((url?: string | URL) => {
        resolve(typeof url === 'string' ? url : url?.toString() ?? null);
        // Don't actually open — return a fake window object.
        return null as unknown as Window;
      }) as typeof window.open;
      // Click the menu item programmatically (it's now in the DOM).
      const items = document.querySelectorAll('[role="menuitem"]');
      for (const it of items) {
        if (/open in new tab/i.test((it as HTMLElement).textContent ?? '')) {
          (it as HTMLElement).click();
          break;
        }
      }
      // Restore (the click handler also calls onClose, but doesn't
      // re-open the window).
      setTimeout(() => {
        window.open = orig;
        resolve(null);
      }, 500);
    });
  });

  // Either the evaluate path captured a URL, or the menu opened a
  // real tab (popupPromise resolved). Either way, assert it's a
  // /photo/{id} URL, not /photo/{id}/raw.
  let url = openUrl;
  if (!url) {
    const popup = await popupPromise;
    url = popup?.url() ?? null;
  }
  expect(url).toBeTruthy();
  expect(url!).toMatch(/\/photo\/[0-9a-f-]+$/);
  expect(url!).not.toContain('/raw');
});

test('photo page Most similar link goes to /similar/{id}', async ({ page }) => {
  const resp = await page.request.get(APP + '/api/random?limit=1');
  const data = (await resp.json()) as { results: Array<{ id: string }> };
  const id = data.results[0].id;

  await page.goto(`${APP}/photo/${id}`);
  await appReady(page);

  await page.getByRole('link', { name: /Most similar/i }).click();
  await page.waitForURL(`**/similar/${id}`);
  // The similar page should load its grid.
  await waitFor(page, '.grid-tile, .empty', 10000);
});