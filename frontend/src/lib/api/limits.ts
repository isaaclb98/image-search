/**
 * Centralised grid sizing — single source of truth for the page
 * size every infinite-scroll grid in the app uses. Per AGENTS.md,
 * grid behaviour is a primitive (not bespoke per page), so the
 * value lives here and is imported by every consumer instead of
 * being a literal scattered across routes and API wrappers.
 *
 * If a future grid genuinely needs a different page size (e.g.
 * one-page detail view that renders everything), add a separate
 * exported constant alongside this one rather than overriding.
 *
 * Round‑36: dropped from 28 → 24. The 6-col 4K grid (`auto-fill,
 * 384px` at the 2400px container cap) needs a multiple of 6 for
 * the last row to be full. 28 left an awkward 4-tile trailing
 * row; 24 = 4 clean rows on 4K, 8 on laptop (3 cols), 12 on
 * mobile (2 cols). Smallest multiple of every common col count,
 * so the page looks complete at every breakpoint.
 */
export const GRID_PAGE_SIZE = 24;
