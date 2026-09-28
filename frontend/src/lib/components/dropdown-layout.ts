/**
 * Pure viewport-bounded layout maths for the Dropdown popover.
 *
 * The menu must never escape the viewport. Given the trigger rect,
 * the menu's natural (measured) height and the caller's preferred
 * side:
 *   1. if the menu fits on the preferred side — keep it there;
 *   2. else flip to the other side when that side is roomier;
 *   3. else (fits nowhere) shrink to the available space — the
 *      menu element has `overflow-y: auto`, so shrinking produces
 *      a scrollbar instead of an off-screen menu.
 *
 * Extracted from Dropdown.svelte so the decision table is
 * unit-testable without mounting Svelte (repo convention: logic in
 * modules, components thin).
 */

/** Gap between the trigger edge and the menu (px). Mirrors
 *  Dropdown.svelte's GAP. */
export const GAP = 8;
/** Minimum gap kept between the menu and the viewport edges. */
export const MARGIN = 8;

export type Rect = {
  top: number;
  bottom: number;
  left: number;
  width: number;
};

export type MenuLayout = {
  /** Viewport-relative top for the menu (position: fixed). */
  top: number;
  /** Viewport-relative left, clamped horizontally. */
  left: number;
  /**
   * Inline max-height in px. `null` means "don't override" — the
   * natural height fits, so the component's CSS cap (60vh) stands.
   */
  maxHeight: number | null;
  /** Which side the menu ended up on (after any flip). */
  side: 'up' | 'down';
};

/**
 * @param trigger        viewport rect of the trigger element
 * @param naturalHeight  menu height as measured after mount (CSS
 *                       60vh cap already applied)
 * @param menuWidth      measured menu width
 * @param viewportW/viewportH  window.innerWidth/innerHeight
 * @param align          caller preference: 'up' opens above the
 *                       trigger, 'down' below
 */
export function layoutMenu(
  trigger: Rect,
  naturalHeight: number,
  menuWidth: number,
  viewportW: number,
  viewportH: number,
  align: 'up' | 'down'
): MenuLayout {
  const spaceUp = trigger.top - GAP - MARGIN;
  const spaceDown = viewportH - trigger.bottom - GAP - MARGIN;
  const spaceOn = (side: 'up' | 'down') => (side === 'up' ? spaceUp : spaceDown);

  // 1./2. Prefer the caller's side; flip only when the menu does
  // not fit there and the other side is strictly roomier.
  let side = align;
  if (naturalHeight > spaceOn(side) && spaceOn(other(side)) > spaceOn(side)) {
    side = other(side);
  }

  // 3. Shrink when it fits nowhere (or the flip target is also too
  // small). The menu's overflow-y: auto turns the shrink into a
  // scrollbar. Never below zero — a degenerate viewport still gets
  // a clamped, scrollable sliver rather than an off-screen menu.
  const available = Math.max(0, spaceOn(side));
  const height = Math.min(naturalHeight, available);
  const maxHeight = height < naturalHeight ? height : null;

  const top =
    side === 'up'
      ? Math.max(MARGIN, trigger.top - GAP - height)
      : Math.min(trigger.bottom + GAP, Math.max(MARGIN, viewportH - MARGIN - height));

  // Horizontal clamp — same rule the component already applied.
  let left = trigger.left;
  if (left + menuWidth > viewportW - GAP) left = viewportW - GAP - menuWidth;
  if (left < GAP) left = GAP;

  return { top, left, maxHeight, side };
}

function other(side: 'up' | 'down'): 'up' | 'down' {
  return side === 'up' ? 'down' : 'up';
}
