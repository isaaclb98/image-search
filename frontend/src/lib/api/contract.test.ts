/**
 * Logic-level unit tests.
 *
 *   - API client behaviour with mocked fetch (params, headers,
 *     error mapping)
 *   - Zod schemas against known-good + drift samples
 *
 * Component rendering is verified by Playwright smoke tests, not
 * here. @testing-library/svelte + Svelte 5 + jsdom has hydration
 * quirks that aren't worth fighting in a unit suite.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiGet, apiPost, apiDelete, ApiError } from './client';
import { Z } from './schemas';

// ---------- API client ----------

describe('apiGet / apiPost / apiDelete', () => {
  let origFetch: typeof fetch;

  beforeEach(() => {
    origFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = origFetch;
    vi.restoreAllMocks();
  });

  it('sends JSON body and credentials: include on POST', async () => {
    let captured: any;
    globalThis.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url: String(url), init };
      return new Response('{"ok":true}', { status: 200 });
    }) as any;

    await apiPost('/api/test', { foo: 'bar' });
    expect(captured.init.method).toBe('POST');
    expect(captured.init.credentials).toBe('include');
    expect(captured.init.headers['content-type']).toBe('application/json');
    expect(JSON.parse(captured.init.body)).toEqual({ foo: 'bar' });
  });

  it('skips body on GET, still attaches credentials', async () => {
    let captured: any;
    globalThis.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url: String(url), init };
      return new Response('{}', { status: 200 });
    }) as any;

    await apiGet('/api/test');
    expect(captured.init.method).toBe('GET');
    expect(captured.init.credentials).toBe('include');
    expect(captured.init.body).toBeUndefined();
  });

  it('throws ApiError with status + body on non-2xx', async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response('{"detail":"not authed"}', {
        status: 401,
        headers: { 'content-type': 'application/json' }
      })
    ) as any;

    await expect(apiGet('/api/private')).rejects.toBeInstanceOf(ApiError);
    try {
      await apiGet('/api/private');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(401);
      expect((e as ApiError).body).toEqual({ detail: 'not authed' });
    }
  });

  it('returns undefined for 204 No Content', async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 })) as any;
    const result = await apiDelete('/api/no-content');
    expect(result).toBeUndefined();
  });

  it('passes through AbortSignal to fetch', async () => {
    const controller = new AbortController();
    let captured: any;
    globalThis.fetch = vi.fn(async (_url: any, init: any) => {
      captured = init;
      return new Response('{}', { status: 200 });
    }) as any;
    await apiGet('/api/test', { signal: controller.signal });
    expect(captured.signal).toBe(controller.signal);
  });
});

// ---------- zod schemas ----------

describe('Z.SearchResponse', () => {
  it('accepts a well-formed SearchResponse with results array', () => {
    const r = Z.SearchResponse.safeParse({
      results: [
        {
          id: 'abc',
          path: '/p/a.jpg',
          score: 0.93,
          score_str: '0.930',
          url: '/photo/abc/raw',
          is_favorite: false
        }
      ],
      took_ms: 12,
      limit: 20,
      offset: 0,
      has_more: false,
      weights: [1, 0.5, 0.25],
      positives: ['beach'],
      negatives: []
    });
    expect(r.success).toBe(true);
  });

  it('accepts weights: null and weights: {}', () => {
    const r1 = Z.SearchResponse.safeParse({ results: [], weights: null });
    expect(r1.success).toBe(true);
    const r2 = Z.SearchResponse.safeParse({ results: [], weights: {} });
    expect(r2.success).toBe(true);
  });

  it('rejects weights as a non-empty array of strings', () => {
    const r = Z.SearchResponse.safeParse({ results: [], weights: ['x'] });
    expect(r.success).toBe(false);
  });

  it('flags when the API drops a required field the frontend depends on', () => {
    // simulate a backend regression that drops results
    const r = Z.SearchResponse.safeParse({ took_ms: 1, limit: 20 });
    expect(r.success).toBe(false);
  });
});

describe('Z.AlbumSummary', () => {
  it('accepts the demo-data shape (cover_favorite_id as empty string)', () => {
    const r = Z.AlbumSummary.safeParse({
      id: 1,
      name: 'Landscape picks',
      description: 'demo',
      cover_favorite_id: '',
      member_count: 3,
      first_member_id: 'abc',
      created_at: '2024-01-01',
      updated_at: '2024-01-01'
    });
    expect(r.success).toBe(true);
  });

  it('accepts cover_favorite_id: null too', () => {
    const r = Z.AlbumSummary.safeParse({
      id: 1,
      name: 'X',
      cover_favorite_id: null
    });
    expect(r.success).toBe(true);
  });
});

describe('Z.SavedSearch', () => {
  it('accepts positives/negatives as arrays of strings', () => {
    const r = Z.SavedSearch.safeParse({
      id: 1,
      name: 'beach',
      positives: ['sun', 'sand'],
      negatives: ['people'],
      created_at: '2024-01-01'
    });
    expect(r.success).toBe(true);
  });
});

describe('assertSchema', () => {
  it('throws on data that does not match the schema', async () => {
    const { assertSchema } = await import('./schemas');
    expect(() =>
      assertSchema('SearchResponse', Z.SearchResponse, { results: 'not an array' })
    ).toThrow(/SearchResponse/);
  });
});
