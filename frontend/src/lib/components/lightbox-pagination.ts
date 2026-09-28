/**
 * Pure navigation/pagination decisions for the Lightbox.
 *
 * The Lightbox shows a window (`items`) of a paginated result set
 * owned by the page. Arrowing forward must never dead-end at the
 * loaded boundary while more pages exist — instead the lightbox
 * asks the page to load more and continues.
 *
 * Extracted as pure functions so the state machine is unit-testable
 * without mounting Svelte (repo convention: logic in modules,
 * components thin).
 */

/** How many items before the loaded end we start prefetching the
 *  next page, so continuous arrow navigation rarely stalls. */
export const LOAD_MORE_LOOKAHEAD = 5;

/** Can `next()` move within the already-loaded window? */
export function canAdvance(idx: number, len: number): boolean {
  return idx < len - 1;
}

/**
 * True when the lightbox should ask the parent for another page:
 * the user is inside the lookahead window of the loaded end and a
 * request for the current length hasn't been made yet (dedupe —
 * `requestedAtLen` is the items length the last request was issued
 * at; a new length re-arms the trigger).
 */
export function shouldRequestMore(
  idx: number,
  len: number,
  hasMore: boolean,
  requestedAtLen: number | null
): boolean {
  if (!hasMore || len === 0) return false;
  return idx >= len - LOAD_MORE_LOOKAHEAD && requestedAtLen !== len;
}

/**
 * The user pressed `next()` while already at the loaded end. If more
 * pages exist, remember the length we stalled at: when `items` grows
 * past it, `resolveStall` auto-advances so the keypress isn't lost.
 * Returns null when the press should simply be dropped (end of data).
 */
export function stallForMore(
  idx: number,
  len: number,
  canLoadMore: boolean
): number | null {
  return idx === len - 1 && canLoadMore ? len : null;
}

/**
 * Resolve a pending stall after `items` changed. When the window
 * grew past the stall point the pending navigation completes
 * (advance to the first newly loaded item).
 */
export function resolveStall(
  stall: number | null,
  len: number
): { advance: boolean; stall: number | null } {
  if (stall === null) return { advance: false, stall: null };
  if (len > stall) return { advance: true, stall: null };
  return { advance: false, stall };
}
