/**
 * Verification that round-7 added keyboard shortcuts in the
 * Lightbox work correctly: Home/End jump to first/last,
 * PageUp/PageDown skip ±10, F toggles Like. (D is already
 * bound to Next; no new shortcut for Dislike — see Lightbox
 * source comment for why.)
 *
 * Reuses the same fixture as lightbox-prex.test.ts: open
 * /random, click first tile, wait for the lightbox to settle.
 */
import { test, expect } from '@playwright/test';

async function openLightboxAndSettle(page: import('@playwright/test').Page) {
  await page.goto('/random');
  await page.waitForSelector('.grid-tile');
  await page.locator('.grid-tile').first().click();
  await page.waitForSelector('[role="dialog"]');
  await page.waitForTimeout(1500);
}

test('Lightbox: End jumps to last photo', async ({ page }) => {
  await openLightboxAndSettle(page);
  await page.keyboard.press('End');
  await page.waitForTimeout(400);
  // The count display reads "{idx + 1} / {items.length}".
  // After End, idx = items.length - 1, so count = "N / N".
  const countText = await page.locator('[role="dialog"] .count').textContent();
  expect(countText).toMatch(/\/ \d+$/);
  const parts = countText?.trim().split(/\s*\/\s*/) ?? [];
  const current = parseInt(parts[0] ?? '0', 10);
  const total = parseInt(parts[1] ?? '0', 10);
  expect(current).toBe(total);
  expect(total).toBeGreaterThan(10); // need a non-trivial grid
});

test('Lightbox: Home jumps to first photo', async ({ page }) => {
  await openLightboxAndSettle(page);
  // Move away from idx 0 first.
  await page.keyboard.press('End');
  await page.waitForTimeout(400);
  // Then Home.
  await page.keyboard.press('Home');
  await page.waitForTimeout(400);
  const countText = await page.locator('[role="dialog"] .count').textContent();
  expect(countText?.trim()).toMatch(/^1\s*\/\s*\d+$/);
});

test('Lightbox: PageDown advances ~10 photos', async ({ page }) => {
  await openLightboxAndSettle(page);
  await page.keyboard.press('PageDown');
  await page.waitForTimeout(400);
  // Should be on photo ~11 (1 + 10).
  const countText = await page.locator('[role="dialog"] .count').textContent();
  const parts = countText?.trim().split(/\s*\/\s*/) ?? [];
  const current = parseInt(parts[0] ?? '0', 10);
  expect(current).toBeGreaterThanOrEqual(10);
  expect(current).toBeLessThanOrEqual(11);
});

test('Lightbox: F toggles Like', async ({ page }) => {
  await openLightboxAndSettle(page);
  const likeBtn = page.getByRole('button', { name: /^Like/ });
  const before = await likeBtn.getAttribute('aria-pressed');
  await page.keyboard.press('f');
  await page.waitForTimeout(400);
  const after = await likeBtn.getAttribute('aria-pressed');
  expect(after).not.toBe(before);
  // Restore so we don't pollute the next test.
  await page.keyboard.press('f');
  await page.waitForTimeout(400);
  const restored = await likeBtn.getAttribute('aria-pressed');
  expect(restored).toBe(before);
});