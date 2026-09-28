/**
 * Core E2E: lightbox infinite navigation.
 *
 * Regression for the UX bug where arrowing forward in the lightbox
 * dead-ended at the last loaded grid item — the user had to close,
 * scroll the page, and find their place again. Now: navigating to
 * the loaded end with more pages available triggers loadMore and
 * continues seamlessly.
 *
 * Invariant under test: right-arrow ALWAYS advances the counter
 * (old behaviour: stuck at items.length-1 forever). We don't assert
 * exact totals — the grid sentinel plus the lightbox's own lookahead
 * prefetch grow `items` asynchronously, so any snapshot of total is
 * racy. Advancement is the contract; the stall/auto-advance state
 * machine is unit-tested in lightbox-pagination.test.ts.
 */
import { test, expect, type Locator, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173';

async function parseCount(box: Locator) {
  const t = await box.locator('.count').innerText();
  const [cur, total] = t.split('/').map((s) => parseInt(s.trim(), 10));
  return { cur, total };
}

async function openLightbox(page: Page) {
  await page.goto(`${APP}/?positives=portrait&diversity=off`);
  await page.waitForSelector('.tile', { timeout: 30000 });
  // The sentinel only mounts when hasMore — the test premise.
  await expect(page.locator('.sentinel')).toBeAttached();

  await page.locator('.tile').first().click();
  const box = page.locator('.overlay[role="dialog"]');
  await expect(box).toBeVisible({ timeout: 10000 });
  return box;
}

/** Park at the loaded end, then assert one right-arrow advances. */
async function assertArrowAdvancesFromEnd(page: Page, box: Locator) {
  await page.keyboard.press('End');
  await page.waitForTimeout(250);
  const before = await parseCount(box);
  await page.keyboard.press('ArrowRight');
  await expect
    .poll(async () => (await parseCount(box)).cur, {
      timeout: 30000,
      message: 'right-arrow must advance from the loaded end (dead-end regression)'
    })
    .toBe(before.cur + 1);
  return before;
}

test('right-arrow at the loaded end never dead-ends', async ({ page }) => {
  const box = await openLightbox(page);
  const start = await assertArrowAdvancesFromEnd(page, box);
  // Items grow monotonically; after crossing a boundary the window
  // is at least one page beyond the initial grid load.
  const now = await parseCount(box);
  expect(now.total).toBeGreaterThanOrEqual(start.total);
});

test('keeps advancing across consecutive page boundaries', async ({ page }) => {
  const box = await openLightbox(page);
  // Three End→ArrowRight rounds: each parks at the (grown) loaded
  // end and crosses it. Old code dead-ends on round 1.
  let prevCur = 0;
  for (let round = 0; round < 3; round++) {
    const before = await assertArrowAdvancesFromEnd(page, box);
    expect(before.cur).toBeGreaterThan(prevCur);
    prevCur = before.cur + 1;
  }
  const done = await parseCount(box);
  expect(done.cur).toBeGreaterThanOrEqual(prevCur);
});

test('next button stays enabled at the loaded end while more pages exist', async ({ page }) => {
  const box = await openLightbox(page);
  await page.keyboard.press('End');
  await page.waitForTimeout(250);
  const nextBtn = box.locator('.nav.next');
  await expect(nextBtn).toBeEnabled();

  const before = await parseCount(box);
  await nextBtn.click();
  await expect
    .poll(async () => (await parseCount(box)).cur, { timeout: 30000 })
    .toBe(before.cur + 1);
});
