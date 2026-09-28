/**
 * Unit tests for the Dropdown viewport-bounded layout maths.
 *
 * The regression: "Add to album" on /photo/[id] uses align="up" and
 * a long album list; the menu's top edge was computed as
 * trigger.top - GAP - height with no lower bound, so it ran off the
 * top of the screen. layoutMenu must flip sides when roomier and
 * otherwise clamp the height (the menu has overflow-y:auto, so a
 * clamp yields a scrollbar).
 */
import { describe, it, expect } from 'vitest';
import { layoutMenu, GAP, MARGIN } from './dropdown-layout';

const VW = 1280;
const VH = 720;
const MENU_W = 260;

/** Trigger sitting low on the page (typical /photo sidebar action). */
const lowTrigger = { top: 640, bottom: 664, left: 980, width: 120 };
/** Trigger near the top of the page. */
const highTrigger = { top: 90, bottom: 114, left: 40, width: 120 };
/** Trigger mid-viewport with little room on either side. */
const tightTrigger = { top: 330, bottom: 354, left: 500, width: 120 };

describe('align="up": the reported bug', () => {
  it('menu taller than the space above flips to below instead of going off-screen', () => {
    // 400px menu, only 640-8-8 = 624px above — fits, stays up.
    const fits = layoutMenu(lowTrigger, 400, MENU_W, VW, VH, 'up');
    expect(fits.side).toBe('up');
    expect(fits.top).toBeGreaterThanOrEqual(MARGIN);

    // Now a trigger high enough that 'up' cannot fit a 400px menu
    // but 'down' can: must flip, never go negative.
    const t = { top: 120, bottom: 144, left: 980, width: 120 };
    const r = layoutMenu(t, 400, MENU_W, VW, VH, 'up');
    expect(r.side).toBe('down');
    expect(r.top).toBeGreaterThanOrEqual(MARGIN);
    expect(r.top + 400).toBeLessThanOrEqual(VH - MARGIN);
  });

  it('never returns a negative top (off the top of the screen)', () => {
    // Worst case: trigger almost at the very top, huge menu.
    const t = { top: 20, bottom: 44, left: 100, width: 120 };
    for (const h of [50, 200, 400, 600, 1200]) {
      const r = layoutMenu(t, h, MENU_W, VW, VH, 'up');
      expect(r.top).toBeGreaterThanOrEqual(MARGIN - 0.001);
      const height = r.maxHeight ?? h;
      expect(r.top + height).toBeLessThanOrEqual(VH);
    }
  });

  it('clamps height and yields a scrollable menu when it fits nowhere', () => {
    // Mid-viewport trigger: ~314px above, ~350px below; a 600px
    // menu fits on neither side → flip to the roomier side and
    // clamp to what is available.
    const r = layoutMenu(tightTrigger, 600, MENU_W, VW, VH, 'up');
    expect(r.maxHeight).not.toBeNull();
    expect(r.maxHeight!).toBeLessThan(600);
    expect(r.maxHeight!).toBeGreaterThan(0);
    expect(r.top).toBeGreaterThanOrEqual(MARGIN - 0.001);
    expect(r.top + r.maxHeight!).toBeLessThanOrEqual(VH);
  });
});

describe('align="down"', () => {
  it('keeps the menu below when it fits', () => {
    const r = layoutMenu(highTrigger, 300, MENU_W, VW, VH, 'down');
    expect(r.side).toBe('down');
    expect(r.top).toBe(highTrigger.bottom + GAP);
    expect(r.maxHeight).toBeNull(); // natural height fits, CSS cap stands
  });

  it('flips up when there is no room below', () => {
    const r = layoutMenu(lowTrigger, 300, MENU_W, VW, VH, 'down');
    expect(r.side).toBe('up');
    expect(r.top).toBeLessThan(lowTrigger.top);
    expect(r.top).toBeGreaterThanOrEqual(MARGIN);
  });

  it('clamps when it fits on neither side', () => {
    const r = layoutMenu(tightTrigger, 900, MENU_W, VW, VH, 'down');
    expect(r.maxHeight).not.toBeNull();
    expect(r.top + r.maxHeight!).toBeLessThanOrEqual(VH);
    expect(r.top).toBeGreaterThanOrEqual(MARGIN - 0.001);
  });
});

describe('maxHeight contract', () => {
  it('is null whenever the natural height fits the chosen side', () => {
    expect(layoutMenu(lowTrigger, 400, MENU_W, VW, VH, 'up').maxHeight).toBeNull();
    expect(layoutMenu(highTrigger, 300, MENU_W, VW, VH, 'down').maxHeight).toBeNull();
  });

  it('equals the available space when clamped', () => {
    const r = layoutMenu(tightTrigger, 600, MENU_W, VW, VH, 'up');
    const spaceDown = VH - tightTrigger.bottom - GAP - MARGIN;
    expect(r.maxHeight).toBe(spaceDown); // flipped down, then clamped
  });

  it('never clamps below zero on a degenerate viewport', () => {
    const r = layoutMenu({ top: 5, bottom: 20, left: 5, width: 50 }, 400, MENU_W, 200, 40, 'up');
    expect(r.maxHeight ?? 400).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(r.top)).toBe(true);
  });
});

describe('horizontal clamp (preserved from the original component)', () => {
  it('shifts left when the menu would overflow the right edge', () => {
    const r = layoutMenu(lowTrigger, 100, MENU_W, VW, VH, 'up');
    expect(r.left + MENU_W).toBeLessThanOrEqual(VW - GAP);
  });

  it('shifts right when the menu would clip the left edge', () => {
    const r = layoutMenu({ top: 400, bottom: 424, left: 2, width: 120 }, 100, MENU_W, VW, VH, 'up');
    expect(r.left).toBe(GAP);
  });

  it('stays flush with the trigger when there is room', () => {
    const r = layoutMenu(highTrigger, 100, MENU_W, VW, VH, 'down');
    expect(r.left).toBe(highTrigger.left);
  });
});

describe('viewport-invariant sweep (the actual guarantee)', () => {
  it('for any trigger position and menu height, the menu stays on screen', () => {
    for (let top = 0; top <= VH; top += 40) {
      for (const h of [40, 150, 320, 432, 700, 1500]) {
        for (const align of ['up', 'down'] as const) {
          const trigger = { top, bottom: top + 24, left: 300, width: 100 };
          const r = layoutMenu(trigger, h, MENU_W, VW, VH, align);
          const height = r.maxHeight ?? h;
          expect(r.top, `top off-screen (trigger.top=${top}, h=${h}, ${align})`).toBeGreaterThanOrEqual(MARGIN - 0.001);
          expect(r.top + height, `bottom off-screen (trigger.top=${top}, h=${h}, ${align})`).toBeLessThanOrEqual(VH - MARGIN + 0.001);
          expect(r.left).toBeGreaterThanOrEqual(0);
          expect(r.left + MENU_W).toBeLessThanOrEqual(VW);
        }
      }
    }
  });
});
