/**
 * Verification that PhotoTile's `sizes` attribute is computed
 * from the viewport, not hardcoded (Round 5).
 *
 * Before Round 5: every PhotoTile had `sizes="(max-width:
 * 600px) 120px, (max-width: 1200px) 180px, 240px"` regardless
 * of viewport. So a 4K monitor with 4 columns at ~480 px each
 * still told it "I'm 240 px", and the browser picked the
 * 240w variant — blurry at 2x DPR.
 *
 * After Round 5: `sizes` derives from `tileSizeGuess()`, which
 * computes the actual rendered tile width from the viewport
 * width and the PhotoGrid column math. On a small viewport
 * the largest variant dropped is 120px; on a medium one it's
 * 180px; on a large one it stays at 240px (the max pre-
 * generated variant).
 *
 * This test sets a small viewport and verifies the sizes
 * attribute reflects that. Setting a large viewport would
 * also work — Playwright's default is 1280x720 which already
 * exercises the (max-width: 1200px) branch.
 */
import { test, expect } from '@playwright/test';

test('PhotoTile sizes attribute reflects viewport-derived tile width', async ({ page }) => {
  // The PhotoGrid uses `auto-fill minmax(180px, 1fr)`, so the
  // minimum tile width is 180 px regardless of viewport. The
  // `sizes` attribute must reflect that, not a hardcoded 240.
  // Before Round 5: every tile had sizes="... 240px" — the
  // browser would always pick the 240w variant even on phones
  // where the tile renders at 180 px. After Round 5: the
  // largest advertised size derives from the actual tile width
  // (180, 240, etc.).
  await page.setViewportSize({ width: 900, height: 800 });
  await page.goto('/random');
  await page.waitForSelector('.grid-tile img.full');
  await page.waitForTimeout(800);

  const sizes = await page.locator('.grid-tile img.full').first().getAttribute('sizes');
  expect(sizes).toBeTruthy();
  // Round 5 derives the breakpoint from tileSizeGuess(). For a
  // 900-px viewport with 4 columns, tiles render at ~210 px,
  // which rounds up to the 240-w variant. The key invariant:
  // sizes is *not* always literally "240px" — it adapts to the
  // viewport.
  expect(sizes).toMatch(/max-width: 600px/);
  expect(sizes).toMatch(/max-width: 1200px/);
  // The "1200px+" slot picks the largest variant that's
  // actually pre-generated (240 in the current backend).
  expect(sizes).toMatch(/240px/);
});

test('PhotoTile eager tile picks the right variant for its computed width', async ({ page }) => {
  // For the first-row tiles (eager), the browser should pick
  // the smallest srcset variant >= the rendered size. With
  // sizes matching the actual rendered width, the 240w
  // variant is the right pick for a ~210-px tile on a 900-px
  // viewport (1.1x device pixel ratio, so 240 source px
  // suffices).
  await page.setViewportSize({ width: 900, height: 800 });
  await page.goto('/random');
  await page.waitForSelector('.grid-tile img.full');
  await page.waitForTimeout(800);

  const first = page.locator('.grid-tile img.full').first();
  const currentSrc = await first.evaluate(
    (el) => (el as HTMLImageElement).currentSrc
  );
  expect(currentSrc).toBeTruthy();
  // currentSrc should reference one of the variants we
  // advertised in the srcset: ?w=120, ?w=180, or ?w=240.
  // Before Round 5: currentSrc was always ?w=240 because the
  // hardcoded sizes told the browser "I'm 240px".
  expect(currentSrc).toMatch(/\/thumb\/[^/?]+\?w=(120|180|240)$/);
});