/**
 * Verification that round-6 View Transitions API crossfade
 * fires on route navigation.
 *
 * Browser support for `document.startViewTransition`:
 *   - Chromium 111+ (Mar 2023) ✓
 *   - Safari 18.4+ (Mar 2025) ✓
 *   - Firefox 137+ (behind flag as of early 2026; default-on
 *     later) — Playwright's bundled Chromium supports it
 *     unconditionally for our purposes.
 *
 * Test asserts the API is invoked during a `goto()` between
 * routes by spying on it via init script.
 */
import { test, expect } from '@playwright/test';

test('document.startViewTransition is invoked during route navigation', async ({ page }) => {
  // Instrument before page scripts run.
  await page.addInitScript(() => {
    const orig = document.startViewTransition?.bind(document);
    const calls: unknown[] = [];
    // Cast to never so TS doesn't complain; the API exists
    // at runtime in Chromium.
    (document as unknown as { __vtCalls: unknown[] }).__vtCalls = calls;
    (document as unknown as { startViewTransition: (cb: () => void | Promise<void>) => unknown }).startViewTransition =
      function (cb: () => void | Promise<void>) {
        calls.push({ ts: Date.now() });
        return orig ? orig(cb) : { finished: Promise.resolve(), ready: Promise.resolve() };
      };
  });

  await page.goto('/');
  await page.waitForSelector('.composer');

  // Navigate to /random via the TopBar link.
  await page.locator('nav a[href="/random"]').first().click();
  await page.waitForURL('**/random');
  await page.waitForSelector('.grid-tile');

  const calls = await page.evaluate(
    () => (document as unknown as { __vtCalls: unknown[] }).__vtCalls.length
  );
  // At least one navigation between routes should have fired
  // the transition. (Some navigations are intercepted — e.g.
  // hash links — so >0 is the right assertion.)
  expect(calls).toBeGreaterThan(0);
});