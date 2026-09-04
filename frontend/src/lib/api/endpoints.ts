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

// For-you endpoints are loosely typed on the backend (no schema
// component), so we describe the shape here and rely on the zod
// validator in dev mode for drift detection.
export type ForYouFeedResponse = {
  results: SearchResult[];
  has_more?: boolean;
  n_likes?: number;
  n_dislikes?: number;
  freshest_feedback_ts?: string | null;
  ranker_state?: Record<string, unknown>;
};
export type ForYouState = {
  seen_count: number;
  liked_count: number;
  disliked_count: number;
  last_seen_at?: string | null;
};

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

/**
 * Most-similar photos for a given point ID — nearest neighbours in
 * the SigLIP2 embedding space. Reached by clicking "Most similar"
 * in the Lightbox; navigates to /similar/{id} which renders up to
 * GRID_PAGE_SIZE results in a dedicated page.
 */
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

export function forYouFeed(
  limit = GRID_PAGE_SIZE,
  diversity = 'balanced',
  diversityDepth = 'auto',
  signal?: AbortSignal,
  /**
   * Zero‑based page index. The backend returns a sliced batch
   * sized to `limit` and a `has_more` flag so the frontend can
   * append on scroll without deduping.
   */
  page = 0
) {
  const qs = new URLSearchParams();
  qs.set('limit', String(limit));
  qs.set('page', String(page));
  if (diversity && diversity !== 'off') qs.set('diversity', diversity);
  if (diversityDepth && diversityDepth !== 'auto') qs.set('diversity_depth', diversityDepth);
  return apiGet<ForYouFeedResponse>(
    `/api/for-you/feed?${qs.toString()}`,
    {
      signal,
      schema: Z.ForYouFeedResponse,
      schemaName: 'ForYouFeedResponse'
    }
  );
}

export function forYouState() {
  return apiGet<ForYouState>('/api/for-you/state', {
    schema: Z.ForYouState,
    schemaName: 'ForYouState'
  });
}

export async function resetForYou() {
  await apiPost('/api/for-you/reset');
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

export function getAlbum(albumId: number | string) {
  return apiGet<unknown>(`/api/albums/${albumId}`, {
    schema: Z.AlbumDetailResponse,
    schemaName: 'AlbumDetailResponse'
  });
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
