/**

 * E2 tier: FUNDAMENTAL — see frontend/e2e/README.md for the classification.
 * for-you.test.ts — Round 22: For You (formerly Shuffled For You, round 22; round 33 replaces the diversity-rerank pipeline with this one) feed.
 *
 * Verifies the new endpoint + page end-to-end against the live
 * dev server:
 *   1. Topbar shows the "Shuffled" tab between Random and For You
 *   2. /for-you page renders with PageHeader + grid
 *   3. Default request returns SearchResponse-shaped data with
 *      results, has_more, session_total
 *   4. Default top_pct=1 produces a non-zero session_total when
 *      the library is non-empty
 *   5. The top_pct query param works (?top_pct=2 returns a
 *      larger pool than ?top_pct=0.5)
 *   6. limit/page query params work (page=1 returns a different
 *      page of the same shuffled pool)
 *   7. Validation errors return 422 (top_pct=0, top_pct=200,
 *      limit=0, page=-1)
 *
 * Requires the search backend with the new endpoint included.
 * If running against a pre-update image, this suite will fail
 * at the validation cases (no /api/for-you/feed).
 */
import { test, expect, type Page } from '@playwright/test';

const APP = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8000';

async function appReady(page: Page) {
  await page.waitForSelector('header.topbar', { timeout: 10000 });
}

test.describe('For You (round 22 shuffled pool — round 33 replaced the diversity-rerank pipeline)', () => {
  test('Topbar shows the For You tab after Random', async ({ page }) => {
    await page.goto(`${APP}/`);
    await appReady(page);

    const tabs = page.locator('header.topbar nav a');
    const labels: string[] = [];
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      labels.push(((await tabs.nth(i).textContent()) ?? '').trim());
    }

    const randomIdx = labels.indexOf('Random');
    const forYouIdx = labels.indexOf('For You');

    expect(randomIdx).toBeGreaterThanOrEqual(0);
    expect(forYouIdx).toBeGreaterThanOrEqual(0);
    // Order: Random → For You (Shuffled tab removed in round 33)
    expect(randomIdx).toBeLessThan(forYouIdx);
    // For You must not be the same as Random — sanity check
    expect(labels).not.toContain('Shuffled');
  });

  test('/for-you renders the PageHeader and grid', async ({ page }) => {
    await page.goto(`${APP}/for-you`);
    await appReady(page);

    await expect(page.locator('h1', { hasText: 'For you' })).toBeVisible();

    // Page subtitle copy
    await expect(page.getByText(/random walk through photos matched to your taste/i)).toBeVisible();
  });

  test('default request returns SearchResponse-shaped data with non-zero pool', async ({ page }) => {
    const res = await page.request.get(`${APP}/api/for-you/feed?limit=10`);
    expect(res.ok()).toBe(true);

    const body = await res.json();
    // Shape contract
    expect(body).toHaveProperty('results');
    expect(body).toHaveProperty('has_more');
    expect(body).toHaveProperty('session_total');
    expect(body).toHaveProperty('offset');
    expect(body).toHaveProperty('limit');
    // session_id is None (no session cursor)
    expect(body.session_id).toBeNull();

    // Library must be non-empty in the dev environment. If
    // session_total is 0 the library is empty and this assertion
    // is a real failure, not a test artifact.
    expect(body.session_total).toBeGreaterThan(0);
    // Page-level metadata matches the request
    expect(body.limit).toBe(10);
    expect(body.offset).toBe(0);
  });

  test('top_pct=2 returns a larger pool than top_pct=0.5', async ({ page }) => {
    // Default limit=30; both calls fetch more candidates than 30
    // so pool size is the bound, not the page size. We compare
    // session_total directly.
    const low = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=0.5&limit=30`)).json();
    const high = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=2&limit=30`)).json();

    expect(high.session_total).toBeGreaterThan(low.session_total);
  });

  test('page=1 returns a different page than page=0', async ({ page }) => {
    // Demo data has ~200 photos; use top_pct=50 so pool=100 and
    // we have enough headroom to slice `limit=5` from offset=0 and
    // offset=5 both yielding 5 items. (top_pct=1 → pool=2, too
    // small for `limit=5` pages.)
    const SEED = 'walk-test-seed';
    const p0 = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=50&page=0&limit=5&seed=${SEED}`)).json();
    const p1 = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=50&page=1&limit=5&seed=${SEED}`)).json();

    // The two pages overlap zero ids (cached pool, server slices
    // start..start+limit deterministically).
    expect(p0.offset).toBe(0);
    expect(p1.offset).toBe(5);
    expect(p0.results.length).toBe(5);
    expect(p1.results.length).toBe(5);
    const ids0 = new Set((p0.results as Array<{ id: string }>).map((r) => r.id));
    const ids1 = new Set((p1.results as Array<{ id: string }>).map((r) => r.id));
    for (const id of ids0) {
      expect(ids1.has(id)).toBe(false);
    }
  });

  test('same seed returns same shuffle; different seeds differ', async ({ page }) => {
    // Regression guard for the round-34 bug where the 5-min TTL
    // cache kept the same shuffle alive across page reloads.
    // Same seed → same shuffle (coherent walk); different seeds
    // → different shuffles (fresh on reload).
    const SEED_A = 'reload-A';
    const SEED_B = 'reload-B';
    const a = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=50&limit=10&seed=${SEED_A}`)).json();
    const a2 = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=50&limit=10&seed=${SEED_A}`)).json();
    const b = await (await page.request.get(`${APP}/api/for-you/feed?top_pct=50&limit=10&seed=${SEED_B}`)).json();

    const aIds = (a.results as Array<{ id: string }>).map((r) => r.id);
    const a2Ids = (a2.results as Array<{ id: string }>).map((r) => r.id);
    const bIds = (b.results as Array<{ id: string }>).map((r) => r.id);

    // Same seed → identical order (server cache hit)
    expect(aIds).toEqual(a2Ids);
    // Different seeds → same set (deterministic pool size) but
    // different order. With 200 demo photos and top_pct=50, the
    // pool has 100 ids and a 6-of-6 birthday-paradox match is
    // essentially impossible for random.shuffle.
    expect(aIds).not.toEqual(bIds);
    expect(new Set(aIds)).toEqual(new Set(bIds));
  });

  test('validation errors return 422', async ({ page }) => {
    const cases = [
      { q: 'top_pct=0', desc: 'top_pct=0 below minimum (0.001)' },
      { q: 'top_pct=200', desc: 'top_pct=200 above maximum (100)' },
      { q: 'limit=0', desc: 'limit=0 below minimum (1)' },
      { q: 'page=-1', desc: 'page=-1 below minimum (0)' },
    ];
    for (const c of cases) {
      const res = await page.request.get(`${APP}/api/for-you/feed?${c.q}`);
      expect.soft(res.status(), c.desc).toBe(422);
    }
  });

  test('each result has the SearchResult-shape fields', async ({ page }) => {
    const res = await page.request.get(`${APP}/api/for-you/feed?limit=3`);
    const body = await res.json();

    for (const r of body.results as Array<Record<string, unknown>>) {
      expect(r).toHaveProperty('id');
      expect(r).toHaveProperty('path');
      expect(r).toHaveProperty('url');
      expect(r).toHaveProperty('blurhash');
      expect(r).toHaveProperty('width');
      expect(r).toHaveProperty('height');
      expect(r).toHaveProperty('is_favorite');
      expect(r).toHaveProperty('is_disliked');
      expect(typeof r.id).toBe('string');
    }
  });
});
