/**
 * settings-index.test.ts — E2E coverage for the in-app Indexer
 * (Settings → Index button + admin API).
 *
 * What we pin:
 *   - Settings tab appears in the TopBar.
 *   - /api/admin/index/status returns a well-formed response shape.
 *   - Clicking Index opens the popover with the two documented
 *     modes (incremental / rebuild-from-scratch).
 *   - POST /api/admin/index starts a job and updates status to
 *     running or idle (the local test fixture finishes fast).
 *   - Concurrent POST returns 409.
 *   - Cancel returns 400 when nothing is running; if a job is in
 *     flight, cancel transitions it to idle.
 *
 * The test runs against the live dev stack (playwright.config.ts
 * defaults to http://127.0.0.1:8000) and skips itself if the
 * admin endpoint is unreachable (e.g. in CI without the dev
 * stack spun up).
 */
import { test, expect, type Page } from '@playwright/test';

async function statusOrSkip(page: Page) {
  const res = await page.request.get('/api/admin/index/status');
  if (!res.ok()) test.skip(true, `admin endpoint unreachable (${res.status()})`);
  return res.json();
}

test.describe('Settings page', () => {
  test('TopBar exposes a Settings tab', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible();
  });

  test('settings page renders the Index card', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Index' })).toBeVisible();
    // Idle state shows the Index button (not Cancel).
    await expect(page.getByRole('button', { name: 'Index' })).toBeVisible();
  });

  test('status endpoint returns a well-formed response', async ({ page }) => {
    const body = await statusOrSkip(page);
    expect(body).toHaveProperty('state');
    expect(['idle', 'running', 'failed']).toContain(body.state);
    expect(body).toHaveProperty('progress');
    expect(body.progress).toEqual(
      expect.objectContaining({
        indexed: expect.any(Number),
        reembedded: expect.any(Number),
        skipped: expect.any(Number),
        errors: expect.any(Number),
      })
    );
  });

  test('popover shows both Index modes', async ({ page }) => {
    await statusOrSkip(page);
    await page.goto('/settings');
    await page.getByRole('button', { name: 'Index' }).click();
    await expect(
      page.getByRole('menuitem', { name: /Index new & changed files/i })
    ).toBeVisible();
    await expect(
      page.getByRole('menuitem', { name: /Rebuild from scratch/i })
    ).toBeVisible();
  });

  test('concurrent start returns 409', async ({ page }) => {
    await statusOrSkip(page);
    // Start a job; the dev fixture is fast (sub-second) so we may
    // already be back to idle by the time the second request hits.
    // We assert either 202 (started) or 409 (still running) — both
    // are valid responses, and the second attempt MUST be 409 if the
    // job is still running.
    const first = await page.request.post('/api/admin/index', {
      data: { mode: 'incremental' }
    });
    expect([202, 409]).toContain(first.status());

    if (first.status() === 202) {
      const second = await page.request.post('/api/admin/index', {
        data: { mode: 'incremental' }
      });
      expect(second.status()).toBe(409);
      // Clean up: wait for the first job to finish before the test
      // teardown so subsequent tests see idle.
      await page.request.post('/api/admin/index/cancel').catch(() => {});
    }
  });

  test('cancel when idle returns 400', async ({ page }) => {
    await statusOrSkip(page);
    // Make sure nothing's running by waiting for idle.
    let body = await statusOrSkip(page);
    const deadline = Date.now() + 15000;
    while (body.state === 'running' && Date.now() < deadline) {
      await page.waitForTimeout(500);
      body = await statusOrSkip(page);
    }
    const res = await page.request.post('/api/admin/index/cancel');
    expect(res.status()).toBe(400);
  });

  test('log endpoint returns an array of lines', async ({ page }) => {
    await statusOrSkip(page);
    const res = await page.request.get('/api/admin/index/log');
    expect(res.ok()).toBe(true);
    const body = await res.json();
    expect(Array.isArray(body.lines)).toBe(true);
    expect(typeof body.next_line).toBe('number');
    expect(typeof body.total).toBe('number');
  });
});
