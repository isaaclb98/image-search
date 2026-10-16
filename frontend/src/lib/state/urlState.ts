/**
 * bindToUrl — serialise a typed set of fields between a
 * Svelte 5 $state record and the page URL.
 *
 * The home page (routes/+page.svelte) used to have its own
 * 30-line readFromUrl / writeToUrl pair. This module
 * generalises that pattern: declare a field schema once,
 * and the binder handles both directions plus the
 * replaceState (vs pushState) call.
 *
 * Field kinds:
 *   - 'array':   `?param=a&param=b` (string[])
 *   - 'string':  `?param=value`    (string)
 *
 * Usage:
 *   const state = bindToUrl($page.url, {
 *     positives:   { kind: 'array',  param: 'positives' },
 *     negatives:   { kind: 'array',  param: 'negatives' },
 *     filename:    { kind: 'string', param: 'filename' },
 *     collections: { kind: 'array',  param: 'collection' }
 *   });
 *   // state.positives  → string[]  (URL → component)
 *   // state.filename   → string
 *   // ...
 *
 * The binder is read-once: it reads the URL at construction
 * time and seeds the state. To push state changes back to
 * the URL, the caller calls `binder.write(state)` inside a
 * $effect. The caller decides when to write (debouncing,
 * locks during centroid search, etc.) — that policy lives
 * at the call site, not here.
 */
import { SvelteURLSearchParams } from 'svelte/reactivity';

export type FieldSchema = Record<
  string,
  { kind: 'array' | 'string'; param: string }
>;

/** Read the URL once and return a state record matching
 * the field schema. */
export function readUrl<T extends FieldSchema>(
  search: string,
  schema: T
): { [K in keyof T]: T[K]['kind'] extends 'array' ? string[] : string } {
  const qs = new URLSearchParams(search.replace(/^\?/, ''));
  const out: Record<string, string | string[]> = {};
  for (const key in schema) {
    const field = schema[key];
    if (field.kind === 'array') {
      out[key] = qs.getAll(field.param);
    } else {
      out[key] = qs.get(field.param) ?? '';
    }
  }
  return out as { [K in keyof T]: T[K]['kind'] extends 'array' ? string[] : string };
}

/** Serialize the state record back to a URL query string.
 * Returns the empty string when all fields are at their
 * defaults (so the URL stays clean: `/` instead of `/?`).
 * Arrays serialise as repeated params. */
export function writeUrl<T extends FieldSchema>(
  state: { [K in keyof T]: T[K]['kind'] extends 'array' ? string[] : string },
  schema: T,
  defaults?: { [K in keyof T]: T[K]['kind'] extends 'array' ? string[] : string }
): string {
  const qs = new SvelteURLSearchParams();
  for (const key in schema) {
    const field = schema[key];
    const v = state[key];
    const d = defaults?.[key];
    // Skip fields at their default value (so the URL
    // stays clean).
    if (d !== undefined) {
      if (field.kind === 'array') {
        if (Array.isArray(v) && Array.isArray(d) && v.length === d.length) {
          let same = true;
          for (let i = 0; i < v.length; i++) {
            if (v[i] !== d[i]) {
              same = false;
              break;
            }
          }
          if (same) continue;
        }
      } else {
        if (v === d) continue;
      }
    }
    if (field.kind === 'array') {
      for (const item of v as string[]) qs.append(field.param, item);
    } else {
      if (v) qs.set(field.param, v as string);
    }
  }
  return qs.toString();
}

/** Push a serialized query string to the URL via
 * history.replaceState. No-op when the new search equals
 * the current one (so the URL doesn't flicker). The
 * `history.state` is preserved. */
export function pushUrlSearch(
  currentSearch: string,
  nextSearch: string
): void {
  if (typeof window === 'undefined') return;
  if (currentSearch.replace(/^\?/, '') === nextSearch) return;
  const url = `/${nextSearch ? '?' + nextSearch : ''}`;
  window.history.replaceState(window.history.state, '', url);
}
