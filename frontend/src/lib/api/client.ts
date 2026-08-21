/**
 * Tiny typed fetch wrapper.
 *
 *  - Reads /api/* via the same origin (vite proxy or nginx in prod).
 *  - Sends/credentials: 'include' so the auth cookie carries.
 *  - Optional Zod schema validates the response body in dev mode
 *    and throws on drift. In prod we trust the contract.
 *
 * The exported `apiGet`, `apiPost`, `apiDelete` all return typed
 * bodies (Generic T). For endpoints that mutate, supply TResponse
 * as the body type and an optional Z schema for runtime guards.
 */

import { browser, dev } from '$app/environment';
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
  /** Use 'omit' for endpoints that must NOT send cookies (login POST). */
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

  // 204 has no body
  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // non-JSON body — keep as text (e.g. image bytes misrouted)
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

// Auth helpers — used by login form and layout guards.
export type SessionState =
  | { kind: 'loading' }
  | { kind: 'anonymous' }
  | { kind: 'authed'; user?: string };

export async function checkSession(): Promise<SessionState> {
  if (!browser) return { kind: 'loading' };
  try {
    // The backend has a /healthz endpoint; use it as the auth probe.
    // A 401 means auth is required; a 200 means we're in.
    const res = await fetch('/healthz', {
      credentials: 'include',
      signal: AbortSignal.timeout(2000)
    });
    if (res.status === 200) return { kind: 'authed' };
    if (res.status === 401 || res.status === 403)
      return { kind: 'anonymous' };
    return { kind: 'anonymous' };
  } catch {
    return { kind: 'anonymous' };
  }
}

export async function login(password: string): Promise<void> {
  // Backend exposes /login (form) and /logout as plain FastAPI/Starlette
  // endpoints outside the OpenAPI surface. POST { password } -> sets
  // the signed session cookie via AuthGateMiddleware.
  const res = await fetch('/login', {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ password })
  });
  if (!res.ok) {
    throw new ApiError(res.status, null, 'login failed');
  }
}

export async function logout(): Promise<void> {
  await fetch('/logout', { method: 'POST', credentials: 'include' });
}

/** Build an absolute raw-image URL. Photos come from /photo/{id}/raw. */
/**
 * URL for the raw photo bytes. Pass `width` to ask the server to
 * Lanczos-resize the source on the fly and serve a smaller file
 * (bandwidth saver + crisper pixels than letting the browser
 * downscale). Cached on disk by the server, so repeat requests
 * hit the cache.
 */
export function photoUrl(pointId: string, width?: number): string {
  const base = `/photo/${encodeURIComponent(pointId)}/raw`;
  if (width && width > 0) {
    return `${base}?w=${width}`;
  }
  return base;
}
