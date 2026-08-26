/**
 * Tiny typed fetch wrapper.
 *
 *  - Reads /api/* via the same origin (vite proxy or nginx in prod).
 *  - Optional Zod schema validates the response body in dev mode
 *    and throws on drift. In prod we trust the contract.
 *
 * The exported `apiGet`, `apiPost`, `apiDelete` all return typed
 * bodies (Generic T). For endpoints that mutate, supply TResponse
 * as the body type and an optional Z schema for runtime guards.
 *
 * Auth removed: credentials default to 'omit'. The backend has no
 * auth gate; access control is expected at the reverse-proxy layer.
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
    credentials: 'omit',
    headers: body !== undefined
      ? { 'content-type': 'application/json' }
      : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: opts.signal
  };

  const url = path.startsWith('http') ? path : BASE + path;
  const res = await fetch(url, init);

  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      // not JSON; fall through
    }
    throw new ApiError(res.status, parsed);
  }

  // 204 No Content etc.
  if (res.status === 204) return undefined as T;

  // Some endpoints (DELETE) legitimately have no body; tolerate an empty body.
  const text = await res.text();
  if (!text) return undefined as T;

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new ApiError(res.status, text, 'Non-JSON response from server');
  }

  if (dev && opts.schema) {
    assertSchema(opts.schemaName ?? path, opts.schema, parsed);
  }
  return parsed as T;
}

export const apiGet = <T>(
  path: string,
  schema?: z.ZodTypeAny,
  opts: Omit<FetchOpts, 'schema' | 'schemaName'> = {}
) => request<T>('GET', path, undefined, { ...opts, schema, schemaName: path });

export const apiPost = <T>(
  path: string,
  body: unknown,
  schema?: z.ZodTypeAny,
  opts: Omit<FetchOpts, 'schema' | 'schemaName'> = {}
) => request<T>('POST', path, body, { ...opts, schema, schemaName: path });

export const apiDelete = <T>(
  path: string,
  schema?: z.ZodTypeAny,
  opts: Omit<FetchOpts, 'schema' | 'schemaName'> = {}
) => request<T>('DELETE', path, undefined, { ...opts, schema, schemaName: path });
