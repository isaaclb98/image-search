/**
 * Typed endpoint wrappers. Each function:
 *   - documents the shape of the response
 *   - opts the dev-mode zod schema for drift detection
 *   - returns the parsed result
 *
 * The generated types in ./types.gen.ts provide compile-time
 * safety; the schemas in ./schemas.ts provide runtime safety.
 * Both come from openapi.json so they don't drift.
 */

import { apiGet, apiPost, apiDelete, apiPatch, photoUrl, thumbUrl } from './client';
import { Z } from './schemas';
import type { components } from './types.gen';
import { GRID_PAGE_SIZE } from './limits';

export type SearchResult = components['schemas']['SearchResult'];
export type SearchResponse = components['schemas']['SearchResponse'];
export type SavedSearch = components['schemas']['SavedSearch'];
export type AlbumSummary = components['schemas']['AlbumSummary'];
export type AlbumDetail = components['schemas']['AlbumDetailResponse'];

// For-you endpoint returns SearchResponse (same shape as /api/random
// and /api/search). The Zod schema for SearchResponse (in schemas.ts)
// is used directly — no separate For You type needed.

// ---------- Search ----------

export type CentroidMode = 'centroid' | 'sample';

export type SearchParams = {
  positives?: string[];
  negatives?: string[];
  filename?: string;
  diversityMode?: string;
  diversityStrength?: number;
  diversityDepth?: string;
  limit?: number;
  offset?: number;
  /** When set, queries the centroid search endpoint. */
  centroid?: string;
  /**
   * Retrieval mode for centroid searches. Only meaningful when
   * `centroid` is also set. `centroid` (default) uses the full
   * mean of the seed set; `sample` picks a random K-subset and
   * uses the mean of THAT subset, re-rolled per request. The
   * backend's static .pt centroids reject `sample` with 400.
   */
  centroidMode?: CentroidMode;
  /** Restrict to one or more `collection` payload values. Empty/undefined = whole library. */
  collections?: string[];
};

export function search(params: SearchParams, signal?: AbortSignal) {
  const qs = new URLSearchParams();
  (params.positives ?? []).forEach((p) => qs.append('positives', p));
  (params.negatives ?? []).forEach((p) => qs.append('negatives', p));
  if (params.filename) qs.set('filename', params.filename);
  // Backend param name is `diversity` (NOT `diversity_mode`); see
  // search/app.py:/api/search signature.
  if (params.diversityMode) qs.set('diversity', params.diversityMode);
  if (params.diversityStrength !== undefined)
    qs.set('diversity_strength', String(params.diversityStrength));
  if (params.diversityDepth && params.diversityDepth !== 'auto')
    qs.set('diversity_depth', params.diversityDepth);
  if (params.collections?.length) {
    for (const c of params.collections) qs.append('collections', c);
  }
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  if (params.centroid && params.centroidMode) {
    // Only attach `mode=` for centroid searches — /api/search
    // doesn't accept it, and a stray `?mode=` there would 400.
    qs.set('mode', params.centroidMode);
  }
  const base = params.centroid
    ? `/api/centroids/${encodeURIComponent(params.centroid)}/search`
    : '/api/search';
  return apiGet<SearchResponse>(`${base}?${qs.toString()}`, {
    signal,
    schema: Z.SearchResponse,
    schemaName: 'SearchResponse'
  });
}

// ---------- Random / For You ----------

export interface RandomParams {
  /** Session id from a previous /api/random response. Pass it back
   * with the next `offset` to walk forward through the shuffled deck.
   * Omit on the first call to create a new session. */
  session?: string;
  /** Position in the shuffled deck to read from. The first batch is
   * at offset 0; subsequent calls should pass the previous batch's
   * count as the new offset (or use the response's `offset` + count). */
  offset?: number;
  /** Number of photos to return (1..200). Default 30. */
  limit?: number;
  /** Restrict to one or more collections. Empty = whole library. */
  collections?: string[];
  /** Result view: 'grid' (default) or 'feed'. */
  view?: string;
  /** Abort the request. */
  signal?: AbortSignal;
}

export function random(params: RandomParams | number = {}, signal?: AbortSignal) {
  // Back-compat: random(limit) → random({limit: 30})
  const p: RandomParams =
    typeof params === 'number' ? { limit: params } : params;
  const sig = signal ?? p.signal;
  const search = new URLSearchParams();
  if (p.session !== undefined) search.set('session', p.session);
  if (p.offset !== undefined) search.set('offset', String(p.offset));
  if (p.limit !== undefined) search.set('limit', String(p.limit));
  if (p.view !== undefined) search.set('view', p.view);
  if (p.collections) {
    for (const c of p.collections) search.append('collections', c);
  }
  return apiGet<SearchResponse>(`/api/random?${search.toString()}`, {
    signal: sig,
    schema: Z.SearchResponse,
    schemaName: 'SearchResponse (random)'
  });
}

// ---------- For You (shuffled-pool ranker) ----------

/**
 * Parameters for forYouFeed.
 *
 * `diversity` / `diversity_depth` were removed in the round-33
 * swap from diversity rerank to shuffled pool: the new
 * /api/for-you/feed ranks the top `top_pct`% of library by your
 * taste direction and returns a random walk, no MMR.
 *
 * `seed` is an opaque string forwarded to the server. Pass a
 * fresh random value on every page reload so the user sees a
 * different shuffle; reuse the same value for paginated scroll
 * calls within the same page-mount to keep the walk coherent.
 */
export interface ForYouFeedParams {
  /** Photos per page. 1..100. Default 30. */
  limit?: number;
  /** Zero-based offset into the shuffled pool. */
  page?: number;
  /** Percentile of library to use as the random pool. (0, 100].
   * Default 1.0 (1% → 8000 candidates at 800k library). */
  top_pct?: number;
  /** Result view: 'grid' (default) or 'feed'. */
  view?: string;
  /** Opaque shuffle seed. See interface docstring. */
  seed?: string;
  /** Abort the request. */
  signal?: AbortSignal;
}

/**
 * For You — random walk through the top `top_pct`% of library
 * ranked by your taste direction. Like /random but constrained
 * to "photos you'd probably have liked anyway, in a different
 * order." The server caches the shuffled pool keyed by
 * (fav_ids, dis_ids, top_pct, seed): same seed → same shuffle
 * (coherent pagination), different seed → fresh shuffle (page
 * reload).
 *
 * See: docs/architecture.md and search/for_you.py.
 */
export function forYouFeed(params: ForYouFeedParams = {}, signal?: AbortSignal) {
  const sig = signal ?? params.signal;
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set('limit', String(params.limit));
  if (params.page !== undefined) search.set('page', String(params.page));
  if (params.top_pct !== undefined) search.set('top_pct', String(params.top_pct));
  if (params.view !== undefined) search.set('view', params.view);
  if (params.seed !== undefined) search.set('seed', params.seed);
  return apiGet<SearchResponse>(`/api/for-you/feed?${search.toString()}`, {
    signal: sig,
    schema: Z.SearchResponse,
    schemaName: 'SearchResponse (for-you)'
  });
}
export function similarPhotos(
  pointId: string,
  limit = GRID_PAGE_SIZE,
  signal?: AbortSignal
) {
  return apiGet<SearchResponse>(
    `/api/similar/${encodeURIComponent(pointId)}?limit=${limit}`,
    { signal, schema: Z.SearchResponse, schemaName: 'SearchResponse (similar)' }
  );
}

export async function dislikePoint(pointId: string) {
  await apiPost(`/api/dislikes/${encodeURIComponent(pointId)}`);
}

export async function undislikePoint(pointId: string) {
  await apiDelete(`/api/dislikes/${encodeURIComponent(pointId)}`);
}

// ---------- Likes (formerly "favourites") ----------

export async function likePoint(pointId: string) {
  await apiPost(`/api/favorites/${encodeURIComponent(pointId)}`);
}

export async function unlikePoint(pointId: string) {
  await apiDelete(`/api/favorites/${encodeURIComponent(pointId)}`);
}

/** Lightweight list — the Likes album view uses this. */
export function listFavorites(limit = GRID_PAGE_SIZE, offset = 0, signal?: AbortSignal) {
  return apiGet<unknown>(
    `/api/favorites?limit=${limit}&offset=${offset}&as_results=1`,
    { signal, schemaName: 'favorites-list' }
  );
}

/** Lightweight list — the Dislikes album view uses this. */
export function listDislikes(limit = GRID_PAGE_SIZE, offset = 0, signal?: AbortSignal) {
  return apiGet<unknown>(
    `/api/dislikes?limit=${limit}&offset=${offset}&as_results=1`,
    { signal, schemaName: 'dislikes-list' }
  );
}

// ---------- Saved searches ----------

export function listSavedSearches() {
  return apiGet<unknown>('/api/saved-searches', {
    schema: Z.SavedSearchListResponse,
    schemaName: 'SavedSearchListResponse'
  });
}

export function createSavedSearch(body: {
  name: string;
  positives: string[];
  negatives: string[];
}) {
  return apiPost<unknown>('/api/saved-searches', body, {
    schema: Z.SavedSearch,
    schemaName: 'SavedSearch'
  });
}

export async function deleteSavedSearch(id: number) {
  await apiDelete(`/api/saved-searches/${id}`);
}

// ---------- Albums ----------

export function listAlbums() {
  return apiGet<{ albums: AlbumSummary[] }>('/api/albums', {
    schema: Z.AlbumsListResponse,
    schemaName: 'AlbumsListResponse'
  });
}

/**
 * Fetch a single album's metadata + members, with optional
 * limit/offset for pagination. The album detail page walks the
 * members in batches of GRID_PAGE_SIZE so very large albums
 * (hundreds/thousands of photos) still render quickly.
 *
 * The backend's AlbumDetailResponse carries `member_total` so
 * callers can compare against the running member count to drive
 * their own `has_more` flag.
 */
export function getAlbum(
  albumId: number | string,
  limit: number = GRID_PAGE_SIZE,
  offset: number = 0,
  signal?: AbortSignal,
) {
  return apiGet<unknown>(
    `/api/albums/${albumId}?limit=${limit}&offset=${offset}`,
    {
      schema: Z.AlbumDetailResponse,
      schemaName: 'AlbumDetailResponse',
      signal,
    }
  );
}

export async function createAlbum(body: { name: string; description?: string }) {
  await apiPost('/api/albums', body, {
    schema: Z.AlbumSummary,
    schemaName: 'AlbumSummary'
  });
}

export async function updateAlbum(
  albumId: number,
  body: { name?: string; description?: string; cover_favorite_id?: number | null }
) {
  await apiPatch(`/api/albums/${albumId}`, body);
}

export async function deleteAlbum(albumId: number) {
  await apiDelete(`/api/albums/${albumId}`);
}

/** Add a photo to an album. Idempotent — backend no-ops on duplicate. */
export async function addPhotoToAlbum(albumId: number, pointId: string) {
  await apiPost(`/api/albums/${albumId}/members/${encodeURIComponent(pointId)}`);
}

/** Remove a photo from an album. */
export async function removePhotoFromAlbum(albumId: number, pointId: string) {
  await apiDelete(`/api/albums/${albumId}/members/${encodeURIComponent(pointId)}`);
}

/**
 * List every album a given photo belongs to.
 *
 * Used by the per-photo "Add to album" dropdown so the menu can
 * render an "already in this album" indicator on each row and
 * toggle on click instead of always-adding. Returns the album
 * summaries directly; the consumer can read `.id` from each.
 *
 * Returns an empty `albums: []` (never throws) if the photo is in
 * no albums or the backend rejects the query — callers can
 * render an "empty membership" UI without a try/catch.
 */
export async function listAlbumsForFavorite(pointId: string): Promise<AlbumSummary[]> {
  const r = await apiGet<{ favorite_id: string; albums: AlbumSummary[] }>(
    `/api/albums/by-favorite/${encodeURIComponent(pointId)}`,
  );
  return r?.albums ?? [];
}

// ---------- Misc ----------

export function listCollections() {
  return apiGet<{
    collections: { name: string; count: number }[];
  }>('/api/collections', {
    schema: Z.CollectionsList,
    schemaName: 'CollectionsList'
  });
}

export function listCentroids() {
  return apiGet<{
    centroids: { name: string; kind?: string; member_count?: number }[];
  }>('/api/centroids', {
    schema: Z.CentroidList,
    schemaName: 'CentroidList'
  });
}

// ---------- Photo URL passthrough ----------

export { photoUrl, thumbUrl };
