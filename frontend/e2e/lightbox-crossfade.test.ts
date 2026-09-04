/**
 * Verification that the lightbox crossfade removes the navigation flash.
 *
 * The bug the user reported: clicking Next/Prev caused an "unpleasant
 * flash effect" because the `<img>` was inside `{#key it.id}`, which
 * unmounted and remounted the element on every nav. The browser
 * showed the dark backdrop until the new fetch resolved.
 *
 * The fix:
 *   1. Drop `{#key}` so the same `<img>` element is reused. The
 *      browser sees an `src` change and starts the new fetch
 *      WITHOUT tearing down the DOM, so the previous photo stays
 *      visible until the new one loads.
 *   2. Crossfade via a CSS opacity transition gated on
 *      `naturalWidth > 0` (we apply a `.photo-ready` class on the
 *      `load` event). The new photo fades in over the old one.
 *
 * What the tests assert:
 *   - The `<img>` DOM node is reused across navigations (no
 *     remount). If `{#key}` were still there, this would fail
 *     because every nav would tear down the old element.
 *   - Once a photo is visible, the next navigation does NOT drop
 *     the visible opacity to 0 for any sustained period. The
 *     crossfade keeps the layer visible during the swap.
 */
import { test, expect } from '@playwright/test';

async function openLightboxAndSettle(page: import('@playwright/test').Page) {
  await page.goto('/random');
  await page.waitForSelector('.grid-tile');
  await page.locator('.grid-tile').first().click();
  await page.waitForSelector('[role="dialog"]');
  // Wait for the initial load to complete and the crossfade-in to
  // finish. The CSS transition is 150 ms, plus the photo fetch.
  await page.waitForTimeout(1500);
}

test('Lightbox <img> DOM node is reused across navigations', async ({ page }) => {
  await openLightboxAndSettle(page);

  const photo = page.locator('[role="dialog"] img.photo');
  // Stamp the DOM node. If the {#key} ever comes back, the new
  // element won't carry our marker.
  const tag = `__lb_test_${Date.now()}_${Math.random()}`;
  await photo.evaluate((el, k) => {
    (el as HTMLElement & { [k: string]: unknown })[k] = true;
  }, tag);

  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(300);
  }

  const stillTagged = await photo.evaluate((el, k) => {
    return (el as HTMLElement & { [k: string]: unknown })[k] === true;
  }, tag);
  expect(stillTagged).toBe(true);
});

test('Lightbox has opacity transition + photo-ready class after load', async ({ page }) => {
  await openLightboxAndSettle(page);

  const photo = page.locator('[role="dialog"] img.photo');
  const transitionProperty = await photo.evaluate(
    (el) => getComputedStyle(el).transitionProperty
  );
  const transitionDuration = await photo.evaluate(
    (el) => getComputedStyle(el).transitionDuration
  );
  const opacity = await photo.evaluate(
    (el) => parseFloat(getComputedStyle(el).opacity)
  );

  expect(transitionProperty).toContain('opacity');
  expect(parseFloat(transitionDuration)).toBeGreaterThan(0);
  // After the initial fade-in completes, opacity should be ~1.
  expect(opacity).toBeGreaterThan(0.9);
});

test('Lightbox arrow nav does not flash to fully dark backdrop', async ({ page }) => {
  // Once the first photo is visible, every subsequent navigation
  // should keep the visible opacity above 0 (crossfade starts at
  // 0 and climbs, but the FADE-OUT of the OLD photo is implicit —
  // it's still on screen at opacity 1 until the new one is loaded,
  // so the perceived opacity never drops to 0).
  //
  // We assert: the median opacity across the first 200 ms of
  // navigation is >= 0.5. The bug would put it near 0.
  await openLightboxAndSettle(page);
  const photo = page.locator('[role="dialog"] img.photo');

  const samples: number[] = [];
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('ArrowRight');
    for (let j = 0; j < 5; j++) {
      await page.waitForTimeout(40);
      const op = await photo.evaluate(
        (el) => parseFloat(getComputedStyle(el).opacity)
      );
      samples.push(op);
    }
  }

  // Median, not min: we WANT some samples near 0 (the start of a
  // crossfade) but the median should reflect that the crossfade
  // dominates the navigation window.
  const sorted = [...samples].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  expect(median).toBeGreaterThan(0.5);
});
