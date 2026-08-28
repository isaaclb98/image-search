/**
 * Tiny typed fetch wrapper.
 *
 *  - Reads /api/* via the same origin (vite proxy or nginx in prod).
 *  - Optional Zod schema validates the response body in dev mode
 *    and throws on drift. In prod we trust the contract.
 *
 * The exported `apiGet`, `apiPost`, `apiPatch`, and `apiDelete` all
 * return typed bodies (Generic T).
 */
import { dev } from '$app/environment';
import { z } from 'zod';
import { assertSchema } from './schemas';

const BASE = ''; // empty: same origin. Vite/proxy handles /api/*.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `API ${status}`);
    this.status = status;
    this.body = body;
  }
}

export type FetchOpts = {
  credentials?: RequestCredentials;
  signal?: AbortSignal;
  /** Optional Zod schema for runtime validation in dev/test. */
  schema?: z.ZodTypeAny;
  /** Optional explicit schema-name for assertSchema error messages. */
  schemaName?: string;
};

async function request<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body: unknown | undefined,
  opts: FetchOpts = {}
): Promise<T> {
  const init: RequestInit = {
    method,
    credentials: opts.credentials ?? 'include',
    headers: body !== undefined
      ? { 'content-type': 'application/json' }
      : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: opts.signal
  };

  const url = path.startsWith('http') ? path : BASE + path;
  const res = await fetch(url, init);

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // Non-JSON body — keep it as text for a useful error below.
      data = text;
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, data, `API ${res.status} on ${path}`);
  }

  if (opts.schema && dev) {
    return assertSchema(opts.schemaName ?? path, opts.schema, data) as T;
  }
  return data as T;
}

export const apiGet = <T>(path: string, opts: FetchOpts = {}) =>
  request<T>('GET', path, undefined, opts);

export const apiPost = <T>(path: string, body?: unknown, opts: FetchOpts = {}) =>
  request<T>('POST', path, body, opts);

export const apiPatch = <T>(path: string, body?: unknown, opts: FetchOpts = {}) =>
  request<T>('PATCH', path, body, opts);

export const apiDelete = <T>(path: string, opts: FetchOpts = {}) =>
  request<T>('DELETE', path, undefined, opts);

/** Build the raw-photo URL used by the lightbox and photo page. */
export function photoUrl(pointId: string, width?: number): string {
  const base = `/photo/${encodeURIComponent(pointId)}/raw`;
  return width && width > 0 ? `${base}?w=${width}` : base;
}

/**
 * Build the thumbnail URL used by photo grid tiles.
 *
 * Round-perf (issue #2): pass an optional `w` to ask the backend for a
 * pre-generated sized variant (e.g. 240/360/480). The endpoint serves
 * the smallest variant that fits the rendered tile, falls back to the
 * canonical 256px file if the variant is missing, and 404s if neither
 * is on disk (frontend already handles 404 → blurhash).
 */
export function thumbUrl(pointId: string, w?: number): string {
  const base = `/thumb/${encodeURIComponent(pointId)}`;
  return w && w > 0 ? `${base}?w=${w}` : base;
}
