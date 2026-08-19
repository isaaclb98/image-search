/**
 * tests/primitives.test.ts — logic-level tests for the prompt chips
 * state machine.
 *
 * The actual Svelte components are exercised by Playwright smoke
 * tests in e2e/. Unit tests here verify the contract: input
 * text + polarity → chip add → clears input; ± toggle changes
 * the polarity; remove a chip by index.
 */

// Mirror the SearchComposer's logic in pure TypeScript so we can
// test it without Svelte mounting.
function commit(
  input: string,
  positives: string[],
  negatives: string[],
  mode: 'pos' | 'neg'
): { positives: string[]; negatives: string[]; input: string } {
  const trimmed = input.trim();
  if (!trimmed) return { positives, negatives, input };
  if (mode === 'pos') {
    if (!positives.includes(trimmed)) positives = [...positives, trimmed];
  } else {
    if (!negatives.includes(trimmed)) negatives = [...negatives, trimmed];
  }
  return { positives, negatives, input: '' };
}
function removePositive(p: string[], i: number): string[] {
  return p.filter((_, idx) => idx !== i);
}
function removeNegative(n: string[], i: number): string[] {
  return n.filter((_, idx) => idx !== i);
}

import { describe, it, expect } from 'vitest';

describe('prompt chips state machine', () => {
  it('adds a positive prompt and clears the input', () => {
    const r = commit('beach', [], [], 'pos');
    expect(r.positives).toEqual(['beach']);
    expect(r.negatives).toEqual([]);
    expect(r.input).toBe('');
  });

  it('adds a negative prompt', () => {
    const r = commit('blurry', [], [], 'neg');
    expect(r.negatives).toEqual(['blurry']);
    expect(r.input).toBe('');
  });

  it('does not double-add the same positive', () => {
    const r = commit('beach', ['beach'], [], 'pos');
    expect(r.positives).toEqual(['beach']);
  });

  it('clears whitespace-only commits', () => {
    const r = commit('   ', [], [], 'pos');
    expect(r.positives).toEqual([]);
    expect(r.input).toBe('   ');
  });

  it('removes a positive by index', () => {
    expect(removePositive(['a', 'b', 'c'], 1)).toEqual(['a', 'c']);
  });

  it('removes a negative by index', () => {
    expect(removeNegative(['x', 'y'], 0)).toEqual(['y']);
  });
});
