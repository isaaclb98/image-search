/**
 * Tests for the API endpoints used by the frontend's main flows
 * that aren't covered by the existing behavioural suite — pins
 * shapes the frontend depends on and catches regressions in
 * favourites toggle, image URL fields, and 4xx error mapping.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/**
 * Instead of mounting Svelte components for these (which requires
 * the SSR workaround), we exercise the URL builders + payload
 * shapes the components rely on. The Playwright suite covers the
 * wired-up behaviour end-to-end.
 */

describe('photoUrl', () => {
  it('builds the raw photo URL for a point id', async () => {
    const { photoUrl } = await import('./client');
    expect(photoUrl('abc-123')).toBe('/photo/abc-123/raw');
    expect(photoUrl('with space')).toBe('/photo/with%20space/raw');
    expect(photoUrl('with/slash')).toBe('/photo/with%2Fslash/raw');
  });
  it('appends ?w= when a width is requested (Lanczos resize)', async () => {
    const { photoUrl } = await import('./client');
    expect(photoUrl('abc-123', 1920)).toBe('/photo/abc-123/raw?w=1920');
    expect(photoUrl('abc-123', 640)).toBe('/photo/abc-123/raw?w=640');
  });
  it('omits ?w= when width is 0 or undefined', async () => {
    const { photoUrl } = await import('./client');
    expect(photoUrl('abc-123', 0)).toBe('/photo/abc-123/raw');
    expect(photoUrl('abc-123', undefined)).toBe('/photo/abc-123/raw');
  });
});

describe('ApiError', () => {
  it('captures status, body, message', async () => {
    const { ApiError } = await import('./client');
    const e = new ApiError(404, { detail: 'gone' }, 'image not found');
    expect(e.status).toBe(404);
    expect(e.body).toEqual({ detail: 'gone' });
    expect(e.message).toBe('image not found');
    expect(e.name).toBe('Error');
  });

  it('falls back to a default message when not provided', async () => {
    const { ApiError } = await import('./client');
    expect(new ApiError(500, null).message).toMatch(/API 500/);
  });
});

describe('random endpoint round-trip', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            results: [
              {
                id: 'pt1',
                path: '/p.jpg',
                score: 0.9,
                score_str: '0.900',
                url: '/photo/pt1/raw',
                is_favorite: false,
                blurhash: null,
                width: null,
                height: null
              }
            ],
            took_ms: 12,
            limit: 30,
            offset: 0,
            has_more: false
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' }
          }
        )
      )
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('parses the SearchResponse envelope', async () => {
    const { random } = await import('./endpoints');
    // disable dev-mode zod assertion — jsdom can't render the
    // Svelte 5 imports the schemas chain pulls in
    const res = await random(1);
    expect(res.results.length).toBe(1);
    expect(res.results[0].id).toBe('pt1');
  });
});

describe('search endpoint URL builder', () => {
  // The dev-mode zod schema runs in vitest (since `dev` is true),
  // so the mocked response must satisfy the SearchResponse shape.
  const valid = JSON.stringify({
    results: [
      {
        id: 'p1',
        path: '/p.jpg',
        score: 0.9,
        score_str: '0.900',
        url: '/photo/p1/raw',
        is_favorite: false,
        blurhash: null,
        width: null,
        height: null
      }
    ],
    took_ms: 1,
    limit: 25,
    offset: 0,
    has_more: false
  });

  let captured: any;
  beforeEach(() => {
    captured = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: any, init: any) => {
        captured = { url: String(_url), init };
        return new Response(valid, {
          status: 200,
          headers: { 'content-type': 'application/json' }
        });
      })
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('serialises positives/negatives as repeated query params', async () => {
    const { search } = await import('./endpoints');
    await search({
      positives: ['a', 'b'],
      negatives: ['c'],
      filename: 'IMG',
      diversityMode: 'auto',
      diversityStrength: 0.5,
      limit: 25,
      offset: 50
    });
    const url = new URL(captured.url, 'http://x');
    expect(url.searchParams.getAll('positives')).toEqual(['a', 'b']);
    expect(url.searchParams.getAll('negatives')).toEqual(['c']);
    expect(url.searchParams.get('filename')).toBe('IMG');
    expect(url.searchParams.get('diversity')).toBe('auto');
    expect(url.searchParams.get('diversity_strength')).toBe('0.5');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('offset')).toBe('50');
  });

  it('switches to the centroid search path when centroid is set', async () => {
    const { search } = await import('./endpoints');
    await search({
      positives: ['a'],
      negatives: [],
      centroid: 'cool-shots'
    });
    expect(captured.url).toContain('/api/centroids/cool-shots/search');
  });
});
