/**
 * tests/preferences.test.ts — unit tests for the user preferences
 * store. Exercises the validation guard on `setSlideshowInterval`
 * (clamping nonsense values so the slideshow never auto-advances
 * every 0ms from a corrupted write) and the load/save round-trip
 * with a stub localStorage (Vitest's jsdom env doesn't expose
 * one by default in v2.x).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

// jsdom under Vitest 2.x doesn't expose `localStorage` on the
// global. The store reads it as a top-level global, so we have
// to stub one before importing the module under test.
class FakeStorage {
  private store = new Map<string, string>();
  get length() {
    return this.store.size;
  }
  clear() {
    this.store.clear();
  }
  getItem(key: string) {
    return this.store.get(key) ?? null;
  }
  key(i: number) {
    return Array.from(this.store.keys())[i] ?? null;
  }
  removeItem(key: string) {
    this.store.delete(key);
  }
  setItem(key: string, value: string) {
    this.store.set(key, value);
  }
}

beforeEach(() => {
  vi.resetModules();
  globalThis.localStorage = new FakeStorage() as unknown as Storage;
});

// Import after the stub is in place. vi re-evaluates modules per
// test thanks to vi.resetModules(), so the store re-initialises
// against the (new) fresh storage each time.
async function loadStore() {
  return import('./preferences');
}

describe('preferences store', () => {
  it('starts at the default cadence when localStorage is empty', async () => {
    const { preferences: store, DEFAULT_SLIDESHOW_MS } = await loadStore();
    expect(get(store).slideshowIntervalMs).toBe(DEFAULT_SLIDESHOW_MS);
    expect(DEFAULT_SLIDESHOW_MS).toBe(3000);
  });

  it('persists updates to localStorage on every set', async () => {
    const { preferences: store, setSlideshowInterval } = await loadStore();
    setSlideshowInterval(10_000);
    const raw = localStorage.getItem('image-search:preferences');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw ?? '{}');
    expect(parsed.slideshowIntervalMs).toBe(10_000);
    // The store itself reflects the same value.
    expect(get(store).slideshowIntervalMs).toBe(10_000);
  });

  it('rejects out-of-range values without mutating the store', async () => {
    const { preferences: store, setSlideshowInterval } = await loadStore();
    setSlideshowInterval(5000);
    expect(get(store).slideshowIntervalMs).toBe(5000);

    // Zero — would make the slideshow tick every millisecond and
    // freeze the browser. Reject.
    setSlideshowInterval(0);
    expect(get(store).slideshowIntervalMs).toBe(5000);

    // Negative — same.
    setSlideshowInterval(-1000);
    expect(get(store).slideshowIntervalMs).toBe(5000);

    // Too long — over a minute per photo would feel broken.
    setSlideshowInterval(120_000);
    expect(get(store).slideshowIntervalMs).toBe(5000);

    // NaN — invalid.
    setSlideshowInterval(Number.NaN);
    expect(get(store).slideshowIntervalMs).toBe(5000);
  });

  it('exposes every preset as a sane integer ms value', async () => {
    const { SLIDESHOW_PRESETS } = await loadStore();
    // Sanity: the UI renders the presets as chips on the
    // Settings page. Every chip must correspond to a valid
    // integer millisecond value inside the clamp range — the
    // sliders/list shouldn't offer anything that setSlideshowInterval
    // would silently drop.
    expect(SLIDESHOW_PRESETS.length).toBeGreaterThan(0);
    for (const preset of SLIDESHOW_PRESETS) {
      expect(preset.ms).toBeTypeOf('number');
      expect(Number.isInteger(preset.ms)).toBe(true);
      expect(preset.ms).toBeGreaterThanOrEqual(500);
      expect(preset.ms).toBeLessThanOrEqual(60_000);
      expect(preset.label).toBeTruthy();
    }
  });
});