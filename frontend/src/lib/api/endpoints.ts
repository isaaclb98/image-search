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

import { apiGet, apiPost, apiDelete, apiPatch, photoUrl } from './client';
import { Z } from './schemas';
import type { components } from './types.gen';

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
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
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

export function random(limit = 30, signal?: AbortSignal) {
  return apiGet<SearchResponse>(`/api/random?limit=${limit}`, {
    signal,
    schema: Z.SearchResponse,
    schemaName: 'SearchResponse (random)'
  });
}

/**
 * Most-similar photos for a given point ID — nearest neighbours in
 * the SigLIP2 embedding space. Reached by clicking "Most similar"
 * in the Lightbox; navigates to /similar/{id} which renders up to
 * MAX (100) results in a dedicated page.
 */
export function similarPhotos(
  pointId: string,
  limit = 100,
  signal?: AbortSignal
) {
  return apiGet<SearchResponse>(
    `/api/similar/${encodeURIComponent(pointId)}?limit=${limit}`,
    { signal, schema: Z.SearchResponse, schemaName: 'SearchResponse (similar)' }
  );
}

export function forYouFeed(
  limit = 20,
  diversity = 'balanced',
  diversityDepth = 'auto',
  signal?: AbortSignal
) {
  const qs = new URLSearchParams();
  qs.set('limit', String(limit));
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
export function listFavorites(limit = 60, offset = 0, signal?: AbortSignal) {
  return apiGet<unknown>(
    `/api/favorites?limit=${limit}&offset=${offset}&as_results=1`,
    { signal, schemaName: 'favorites-list' }
  );
}

/** Lightweight list — the Dislikes album view uses this. */
export function listDislikes(limit = 60, offset = 0, signal?: AbortSignal) {
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
  return apiGet<unknown>('/api/albums', {
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

// ---------- Discover ----------

export function startDiscovery() {
  return apiGet<unknown>('/api/discover/start', {
    schema: Z.DiscoveryStartResponse,
    schemaName: 'DiscoveryStartResponse'
  });
}

export function pickDiscovery(pairId: string, chosen: string) {
  return apiPost<unknown>(
    '/api/discover/pick',
    { pair_id: pairId, chosen },
    { schema: Z.DiscoveryPickResponse, schemaName: 'DiscoveryPickResponse' }
  );
}

// ---------- Misc ----------

export function listCollections() {
  return apiGet<unknown>('/api/collections', {
    schema: Z.CollectionsList,
    schemaName: 'CollectionsList'
  });
}

export function listCentroids() {
  return apiGet<unknown>('/api/centroids', {
    schema: Z.CentroidList,
    schemaName: 'CentroidList'
  });
}

// ---------- Photo URL passthrough ----------

export { photoUrl };
