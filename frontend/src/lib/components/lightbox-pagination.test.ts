/**
 * Unit tests for the lightbox infinite-navigation state machine.
 * Mirrors the repo convention (primitives.test.ts): pure logic
 * tested without mounting Svelte; component behaviour is covered
 * by the Playwright E2E.
 */
import { describe, it, expect } from 'vitest';
import {
  LOAD_MORE_LOOKAHEAD,
  canAdvance,
  shouldRequestMore,
  stallForMore,
  resolveStall,
} from './lightbox-pagination';

describe('canAdvance', () => {
  it('advances anywhere before the last loaded item', () => {
    expect(canAdvance(0, 24)).toBe(true);
    expect(canAdvance(22, 24)).toBe(true);
  });
  it('refuses at the last loaded item', () => {
    expect(canAdvance(23, 24)).toBe(false);
  });
  it('refuses on an empty window', () => {
    expect(canAdvance(0, 0)).toBe(false);
  });
});

describe('shouldRequestMore (lookahead prefetch)', () => {
  it('does not request while far from the end', () => {
    expect(shouldRequestMore(0, 24, true, null)).toBe(false);
    expect(shouldRequestMore(24 - LOAD_MORE_LOOKAHEAD - 1, 24, true, null)).toBe(false);
  });
  it('requests once the user enters the lookahead window', () => {
    expect(shouldRequestMore(24 - LOAD_MORE_LOOKAHEAD, 24, true, null)).toBe(true);
    expect(shouldRequestMore(23, 24, true, null)).toBe(true);
  });
  it('does not request when there is no more data', () => {
    expect(shouldRequestMore(23, 24, false, null)).toBe(false);
  });
  it('dedupes: no second request for the same loaded length', () => {
    expect(shouldRequestMore(20, 24, true, 24)).toBe(false);
    expect(shouldRequestMore(23, 24, true, 24)).toBe(false);
  });
  it('re-arms after the window grows (new length)', () => {
    expect(shouldRequestMore(44, 48, true, 24)).toBe(true);
  });
  it('never requests on an empty window', () => {
    expect(shouldRequestMore(0, 0, true, null)).toBe(false);
  });
});

describe('stallForMore (next pressed exactly at the loaded end)', () => {
  it('stalls when at the end and more is available', () => {
    expect(stallForMore(23, 24, true)).toBe(24);
  });
  it('drops the press at the true end of data', () => {
    expect(stallForMore(23, 24, false)).toBeNull();
  });
  it('drops mid-window presses (those just advance)', () => {
    expect(stallForMore(10, 24, true)).toBeNull();
  });
});

describe('resolveStall (auto-advance when the page grows)', () => {
  it('no stall → no advance', () => {
    expect(resolveStall(null, 24)).toEqual({ advance: false, stall: null });
  });
  it('items grew past the stall → advance, stall cleared', () => {
    expect(resolveStall(24, 48)).toEqual({ advance: true, stall: null });
  });
  it('items not yet grown → keep waiting', () => {
    expect(resolveStall(24, 24)).toEqual({ advance: false, stall: 24 });
  });
  it('items shrank (new search while open) → keep waiting harmlessly', () => {
    expect(resolveStall(24, 10)).toEqual({ advance: false, stall: 24 });
  });
});

describe('continuous navigation walk', () => {
  it('arrowing through 3 pages never dead-ends and prefetches ahead', () => {
    const TOTAL = 72;
    let len = 24;
    let idx = 0;
    let requestedAtLen: number | null = null;
    const hasMore = () => len < TOTAL;

    // Walk 71 presses (24→72 items); each press = next() logic.
    for (let press = 0; press < 71; press++) {
      if (canAdvance(idx, len)) {
        idx += 1;
      } else {
        const stall = stallForMore(idx, len, hasMore());
        if (stall === null) throw new Error(`dead-ended at ${idx}/${len}`);
        // page "loads" more (async in the real app)
        len = Math.min(TOTAL, len + 24);
        const r = resolveStall(stall, len);
        expect(r.advance).toBe(true);
        idx += 1;
      }
      // lookahead prefetch bookkeeping — stays armed per length
      if (shouldRequestMore(idx, len, hasMore(), requestedAtLen)) {
        requestedAtLen = len;
      }
    }
    expect(idx).toBe(71);
    expect(len).toBe(TOTAL);
  });

  it('walk to the true end of data stops cleanly (no dead-end error, press dropped)', () => {
    let len = 24;
    let idx = 23;
    const hasMore = () => false;
    const stall = stallForMore(idx, len, hasMore());
    expect(stall).toBeNull(); // press is simply dropped at end of data
  });
});
