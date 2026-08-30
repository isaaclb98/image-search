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
 */
export const GRID_PAGE_SIZE = 28;
